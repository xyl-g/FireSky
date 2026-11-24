//! Full, pinned timezonefinder data without a Python runtime.
//! Ordinary containment uses geo; exact boundary ownership follows the owning
//! timezonefinder integer convention (third_party/timezonefinder-LICENSE).
use crate::{
    request_file,
    tensor_bundle::{regular_identity, SourceIdentity},
};
use geo::{
    coordinate_position::{CoordPos, CoordinatePosition},
    Coord, LineString, Polygon,
};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs,
    io::{BufReader, Read, Seek, SeekFrom},
    path::{Path, PathBuf},
    sync::{Arc, Mutex, OnceLock},
};

const SCALE: i64 = 10_000_000;
const MAX_BYTES: u64 = 512 * 1024 * 1024;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Array {
    file: String,
    dtype: String,
    shape: Vec<u64>,
    bytes: u64,
    sha256: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Manifest {
    contract: String,
    exporter: String,
    data_package_version: String,
    data_version: String,
    scale: i64,
    root_columns: usize,
    block_side: usize,
    timezone_names: Vec<String>,
    arrays: BTreeMap<String, Array>,
    licenses: BTreeMap<String, SourceIdentity>,
}
struct Data {
    root: PathBuf,
    identity: SourceIdentity,
    manifest: Manifest,
    coordinates: Vec<i32>,
    rings: Vec<u32>,
    polygons: Vec<u32>,
    zones: Vec<u32>,
    table: Vec<i32>,
    blocks: Vec<i32>,
    starts: Vec<u32>,
    ends: Vec<u32>,
    payload: Vec<u32>,
    stop: Vec<u32>,
    bounds: Vec<[i32; 4]>,
    geometry: Vec<OnceLock<Polygon<f64>>>,
}
fn require(ok: bool, message: &str) -> Result<(), String> {
    if ok {
        Ok(())
    } else {
        Err(message.into())
    }
}
fn directory(path: &Path) -> Result<(), String> {
    require(
        fs::symlink_metadata(path)
            .map_err(|e| e.to_string())?
            .is_dir(),
        "timezone source must be a nonsymlink directory",
    )
}
struct HashingReader<R> {
    inner: R,
    hasher: Sha256,
    bytes: u64,
}

impl<R: Read> Read for HashingReader<R> {
    fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
        let count = self.inner.read(buf)?;
        if count > 0 {
            self.hasher.update(&buf[..count]);
            self.bytes = self.bytes.saturating_add(count as u64);
        }
        Ok(count)
    }
}

fn array<T: npyz::Deserialize>(
    root: &Path,
    name: &str,
    manifest: &Manifest,
    dtype: &str,
) -> Result<Vec<T>, String> {
    let entry = manifest.arrays.get(name).ok_or("missing timezone array")?;
    require(
        entry.file == format!("{name}.npy")
            && entry.dtype == dtype
            && !entry.shape.is_empty()
            && entry.shape.len() <= 2,
        "invalid timezone array descriptor",
    )?;
    let count = entry
        .shape
        .iter()
        .try_fold(1u64, |n, &d| n.checked_mul(d))
        .ok_or("timezone dimension overflow")?;
    let bytes = count.checked_mul(4).ok_or("timezone byte overflow")?;
    require(
        count > 0 && bytes <= MAX_BYTES && entry.bytes <= MAX_BYTES,
        "timezone array exceeds bounds",
    )?;
    let path = root.join(&entry.file);
    let mut options = fs::OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW);
    }
    let mut file = options.open(&path).map_err(|e| e.to_string())?;
    let metadata = file.metadata().map_err(|e| e.to_string())?;
    require(
        metadata.is_file() && metadata.len() == entry.bytes,
        "timezone array checksum mismatch",
    )?;
    let mut prefix = [0u8; 12];
    file.read_exact(&mut prefix).map_err(|e| e.to_string())?;
    require(&prefix[..6] == b"\x93NUMPY", "invalid timezone NPY magic")?;
    let (size, header) = match &prefix[6..8] {
        [1, 0] => (
            10,
            u16::from_le_bytes(prefix[8..10].try_into().unwrap()) as u64,
        ),
        [2, 0] => (
            12,
            u32::from_le_bytes(prefix[8..12].try_into().unwrap()) as u64,
        ),
        _ => return Err("unsupported timezone NPY version".into()),
    };
    require(
        header <= 4096 && size + header + bytes == entry.bytes,
        "invalid timezone NPY extent",
    )?;
    file.seek(SeekFrom::Start(0)).map_err(|e| e.to_string())?;
    let mut reader = BufReader::new(HashingReader {
        inner: file,
        hasher: Sha256::new(),
        bytes: 0,
    });
    let npy = npyz::NpyFile::new(&mut reader).map_err(|e| e.to_string())?;
    require(
        npy.shape() == entry.shape
            && npy.order() == npyz::Order::C
            && npy.dtype().descr() == format!("'{dtype}'"),
        &format!(
            "timezone NPY axes/dtype mismatch for {name}: {:?}/{:?}/{:?}",
            npy.shape(),
            npy.order(),
            npy.dtype().descr()
        ),
    )?;
    let data = npy.into_vec::<T>().map_err(|e| e.to_string())?;
    // Drain any trailing bytes (none expected) so the digest covers the entire file descriptor.
    let mut tail = [0u8; 64];
    while reader.read(&mut tail).map_err(|e| e.to_string())? > 0 {}
    let inner = reader.into_inner();
    let digest: String = inner
        .hasher
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect();
    require(
        inner.bytes == entry.bytes && digest == entry.sha256,
        "timezone array checksum mismatch",
    )?;
    Ok(data)
}
impl Data {
    fn load(root: PathBuf, identity: SourceIdentity, manifest: Manifest) -> Result<Self, String> {
        require(
            manifest.contract == "firesky-timezone-source-v1"
                && manifest.exporter == "timezonefinder-9.2.0"
                && manifest.scale == SCALE
                && manifest.root_columns == 480
                && manifest.block_side == 4
                && !manifest.data_version.is_empty()
                && manifest.data_version.len() <= 64
                && !manifest.data_package_version.is_empty()
                && manifest.data_package_version.len() <= 64
                && (1..=4096).contains(&manifest.timezone_names.len())
                && manifest
                    .timezone_names
                    .iter()
                    .all(|s| !s.is_empty() && s.len() <= 128 && s.parse::<chrono_tz::Tz>().is_ok()),
            "unsupported timezone source contract",
        )?;
        let names = [
            "blocks",
            "coordinates",
            "ends",
            "last_change",
            "payload",
            "polygon_offsets",
            "ring_offsets",
            "starts",
            "table",
            "zones",
        ];
        require(
            manifest
                .arrays
                .keys()
                .map(String::as_str)
                .collect::<Vec<_>>()
                == names,
            "timezone source must retain every array",
        )?;
        let total = manifest
            .arrays
            .values()
            .try_fold(0u64, |n, a| n.checked_add(a.bytes))
            .ok_or("timezone size overflow")?;
        require(
            total <= MAX_BYTES && manifest.licenses.len() == 2,
            "timezone source exceeds bounds",
        )?;
        for name in ["CODE_LICENSE", "DATA_LICENSE"] {
            let expected = manifest
                .licenses
                .get(name)
                .ok_or("missing timezone license")?;
            require(
                expected.bytes <= 64 * 1024 && regular_identity(&root.join(name))? == *expected,
                "timezone license identity mismatch",
            )?;
        }
        let coordinates = array::<i32>(&root, "coordinates", &manifest, "<i4")?;
        let rings = array::<u32>(&root, "ring_offsets", &manifest, "<u4")?;
        let polygons = array::<u32>(&root, "polygon_offsets", &manifest, "<u4")?;
        let zones = array::<u32>(&root, "zones", &manifest, "<u4")?;
        let table = array::<i32>(&root, "table", &manifest, "<i4")?;
        let blocks = array::<i32>(&root, "blocks", &manifest, "<i4")?;
        let starts = array::<u32>(&root, "starts", &manifest, "<u4")?;
        let ends = array::<u32>(&root, "ends", &manifest, "<u4")?;
        let payload = array::<u32>(&root, "payload", &manifest, "<u4")?;
        let stop = array::<u32>(&root, "last_change", &manifest, "<u4")?;
        require(
            manifest.arrays["coordinates"].shape == [coordinates.len() as u64 / 2, 2]
                && coordinates.len() % 2 == 0
                && (2..=1_000_000).contains(&rings.len())
                && rings[0] == 0
                && *rings.last().unwrap() as usize == coordinates.len() / 2
                && rings
                    .windows(2)
                    .all(|w| w[1].checked_sub(w[0]).is_some_and(|d| d >= 3))
                && (1..=100_000).contains(&zones.len())
                && polygons.len() == zones.len() + 1
                && polygons[0] == 0
                && *polygons.last().unwrap() as usize == rings.len() - 1
                && polygons.windows(2).all(|w| w[0] < w[1])
                && zones
                    .iter()
                    .all(|&z| (z as usize) < manifest.timezone_names.len()),
            "invalid timezone polygon/ring axes",
        )?;
        for (name, length) in [
            ("ring_offsets", rings.len()),
            ("polygon_offsets", polygons.len()),
            ("zones", zones.len()),
            ("table", table.len()),
            ("blocks", blocks.len()),
            ("starts", starts.len()),
            ("ends", ends.len()),
            ("payload", payload.len()),
            ("last_change", stop.len()),
        ] {
            require(
                manifest.arrays[name].shape == [length as u64],
                "timezone vector rank mismatch",
            )?;
        }
        require(
            coordinates.as_chunks::<2>().0.iter().all(|p| {
                (-180 * SCALE..=180 * SCALE).contains(&i64::from(p[0]))
                    && (-90 * SCALE..=90 * SCALE).contains(&i64::from(p[1]))
            }),
            "timezone coordinates out of range",
        )?;
        require(
            table.len() == 480 * 240
                && blocks.len() % 16 == 0
                && starts.len() == ends.len()
                && starts.len() == stop.len()
                && payload.iter().all(|&p| (p as usize) < zones.len()),
            "invalid timezone shortcut axes",
        )?;
        let valid_entry = |v: i32, negative: usize| {
            v >= 0 && (v as usize) < manifest.timezone_names.len()
                || v == -1
                || v <= -2 && (-(i64::from(v) + 2) as usize) < negative
        };
        require(
            table.iter().all(|&v| valid_entry(v, blocks.len() / 16))
                && blocks.iter().all(|&v| valid_entry(v, starts.len())),
            "invalid timezone shortcut references",
        )?;
        for i in 0..starts.len() {
            let (a, b) = (starts[i] as usize, ends[i] as usize);
            require(
                a < b && b <= payload.len(),
                "invalid timezone candidate span",
            )?;
            let expected = payload[a..b]
                .windows(2)
                .enumerate()
                .filter(|(_, w)| zones[w[0] as usize] != zones[w[1] as usize])
                .map(|(i, _)| i + 1)
                .next_back()
                .unwrap_or(0);
            require(
                stop[i] as usize == expected,
                "timezone candidate ordering/stop mismatch",
            )?;
        }
        let mut bounds = Vec::with_capacity(zones.len());
        for polygon in polygons.windows(2) {
            let a = rings[polygon[0] as usize] as usize;
            let b = rings[polygon[0] as usize + 1] as usize;
            let mut bbox = [i32::MAX, i32::MIN, i32::MAX, i32::MIN];
            for p in coordinates[2 * a..2 * b].as_chunks::<2>().0 {
                bbox = [
                    bbox[0].min(p[0]),
                    bbox[1].max(p[0]),
                    bbox[2].min(p[1]),
                    bbox[3].max(p[1]),
                ];
            }
            bounds.push(bbox);
        }
        require(
            regular_identity(&root.join("manifest.json"))? == identity,
            "timezone manifest changed",
        )?;
        let geometry = (0..zones.len()).map(|_| OnceLock::new()).collect();
        Ok(Self {
            root,
            identity,
            manifest,
            coordinates,
            rings,
            polygons,
            zones,
            table,
            blocks,
            starts,
            ends,
            payload,
            stop,
            bounds,
            geometry,
        })
    }
    fn ring(&self, index: usize) -> impl Iterator<Item = [i32; 2]> + Clone + '_ {
        let (a, b) = (self.rings[index] as usize, self.rings[index + 1] as usize);
        self.coordinates[2 * a..2 * b]
            .as_chunks::<2>()
            .0
            .iter()
            .map(|p| [p[0], p[1]])
    }
    fn inside(&self, id: usize, x: i32, y: i32) -> bool {
        let [xmin, xmax, ymin, ymax] = self.bounds[id];
        if x < xmin || x > xmax || y < ymin || y > ymax {
            return false;
        }
        let (a, b) = (self.polygons[id] as usize, self.polygons[id + 1] as usize);
        let shape = self.geometry[id].get_or_init(|| {
            let ring = |i| {
                LineString::from(
                    self.ring(i)
                        .map(|p| (f64::from(p[0]), f64::from(p[1])))
                        .collect::<Vec<_>>(),
                )
            };
            Polygon::new(ring(a), (a + 1..b).map(ring).collect())
        });
        match shape.coordinate_position(&Coord {
            x: f64::from(x),
            y: f64::from(y),
        }) {
            CoordPos::Inside => true,
            CoordPos::Outside => false,
            CoordPos::OnBoundary => {
                half_open(self.ring(a), x, y) && !(a + 1..b).any(|i| half_open(self.ring(i), x, y))
            }
        }
    }
    fn timezone(&self, lon: f64, lat: f64) -> Option<&str> {
        let mut x = (lon * SCALE as f64) as i64;
        let mut y = (lat * SCALE as f64) as i64;
        if x == -180 * SCALE {
            x = 180 * SCALE;
        }
        if y == -90 * SCALE {
            y += 1;
        }
        let (sx, sy) = (
            (x + 180 * SCALE - 1) as usize,
            (y + 90 * SCALE - 1) as usize,
        );
        let width = (360 * SCALE / 480) as usize;
        let mut entry = self.table[(sy / width) * 480 + sx / width];
        if entry <= -2 {
            let block = (-(i64::from(entry) + 2)) as usize;
            entry = self.blocks[block * 16 + ((sy / (width / 4)) % 4) * 4 + (sx / (width / 4)) % 4];
        }
        if entry == -1 {
            return None;
        }
        let zone = if entry >= 0 {
            entry as usize
        } else {
            let id = (-(i64::from(entry) + 2)) as usize;
            let candidates = &self.payload[self.starts[id] as usize..self.ends[id] as usize];
            let found = candidates
                .iter()
                .take(self.stop[id] as usize)
                .find(|&&p| self.inside(p as usize, x as i32, y as i32))
                .unwrap_or(candidates.last().unwrap());
            self.zones[*found as usize] as usize
        };
        Some(&self.manifest.timezone_names[zone])
    }
}
// Compatibility only for points geo reports exactly on a ring boundary.
// Preserve timezonefinder's north/east-owned, south/west-excluded convention.
fn half_open(points: impl Iterator<Item = [i32; 2]> + Clone, x: i32, y: i32) -> bool {
    let mut previous = points.clone().last().unwrap();
    let mut inside = false;
    for current in points {
        let (a, b) = (previous.map(i128::from), current.map(i128::from));
        let (px, py) = (i128::from(x), i128::from(y));
        if (py > a[1]) != (py > b[1]) && (px <= a[0] || px <= b[0]) {
            let left = (b[1] - py) * (b[0] - a[0]);
            let right = (b[1] - a[1]) * (b[0] - px);
            if px <= a[0] && px <= b[0]
                || if py > a[1] {
                    left <= right
                } else {
                    left >= right
                }
            {
                inside = !inside;
            }
        }
        previous = current;
    }
    inside
}
fn resident(path: &Path) -> Result<Arc<Data>, String> {
    require(
        path.file_name().and_then(|s| s.to_str()) == Some("manifest.json"),
        "timezone source must name manifest.json",
    )?;
    let path = std::path::absolute(path).map_err(|e| e.to_string())?;
    let root = path.parent().ok_or("missing timezone source directory")?;
    directory(root)?;
    let (manifest, identity): (Manifest, _) = request_file::read(&path, 128 * 1024)?;
    static RESIDENT: OnceLock<Mutex<Option<Arc<Data>>>> = OnceLock::new();
    let mut slot = RESIDENT
        .get_or_init(|| Mutex::new(None))
        .lock()
        .map_err(|_| "timezone source lock poisoned")?;
    let data = match slot
        .as_ref()
        .filter(|v| v.root == root && v.identity == identity)
    {
        Some(data) => data.clone(),
        None => {
            let data = Arc::new(Data::load(root.into(), identity, manifest)?);
            *slot = Some(data.clone());
            data
        }
    };
    drop(slot);
    Ok(data)
}
fn proof(data: &Data) -> Value {
    json!({"contract":"firesky-timezone-source-v1","manifest_identity":data.identity,
        "data_version":data.manifest.data_version,"data_package_version":data.manifest.data_package_version,
        "source_freshness":"immutable-verified-resident-dataset","runtime_python_used":false})
}
pub(crate) fn lookup_batch(
    path: &Path,
    points: &[[f64; 2]],
) -> Result<(Vec<String>, Value), String> {
    require(
        (1..=4096).contains(&points.len())
            && points.iter().all(|&[lat, lon]| {
                lat.is_finite()
                    && (-90.0..=90.0).contains(&lat)
                    && lon.is_finite()
                    && (-180.0..=180.0).contains(&lon)
            }),
        "timezone batch requires 1..4096 finite bounded [lat,lon] points",
    )?;
    let data = resident(path)?;
    let zones = points
        .iter()
        .map(|&[lat, lon]| {
            data.timezone(lon, lat)
                .map(str::to_owned)
                .ok_or_else(|| "local timezone data does not cover this coordinate".to_owned())
        })
        .collect::<Result<Vec<_>, _>>()?;
    Ok((zones, proof(&data)))
}
pub(crate) fn lookup(path: &Path, lat: f64, lon: f64) -> Result<(String, Value), String> {
    let (mut zones, proof) = lookup_batch(path, &[[lat, lon]])?;
    Ok((zones.remove(0), proof))
}

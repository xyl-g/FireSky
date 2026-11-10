//! CAMS run selection, ADS batching/fallback and validated atomic raw caching.
use crate::{
    ads::{Ads, Config},
    cache_lock::Lock,
    cams, cams_cache,
    grib::{EcCodes, Message},
    tensor_bundle::source_identity,
    time_contract,
};
use chrono::{DateTime, Duration, NaiveDate, NaiveTime, TimeZone, Timelike, Utc};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, File, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
    sync::{Condvar, Mutex, OnceLock},
    time::{Duration as StdDuration, Instant},
};

const MAX_TARGETS: usize = 256;
static SLOTS: OnceLock<(Mutex<usize>, Condvar, usize)> = OnceLock::new();
struct Slot;
impl Slot {
    fn acquire(seconds: f64) -> Result<Self, String> {
        let (active, cond, limit) = SLOTS.get_or_init(|| {
            let limit = std::env::var("FIRESKY_CAMS_DOWNLOAD_WORKERS")
                .ok()
                .and_then(|v| v.trim().parse::<i32>().ok())
                .unwrap_or(2)
                .clamp(1, 4) as usize;
            (Mutex::new(0), Condvar::new(), limit)
        });
        let deadline = Instant::now() + StdDuration::from_secs_f64(seconds);
        let mut value = active.lock().unwrap();
        while *value >= *limit {
            let left = deadline.saturating_duration_since(Instant::now());
            if left.is_zero() {
                return Err("CAMS download slot timed out".into());
            }
            value = cond.wait_timeout(value, left).unwrap().0;
        }
        *value += 1;
        Ok(Self)
    }
}
impl Drop for Slot {
    fn drop(&mut self) {
        let (active, cond, _) = SLOTS.get().unwrap();
        *active.lock().unwrap() -= 1;
        cond.notify_one();
    }
}

fn default_exact() -> bool {
    true
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Params {
    pub operation: String,
    pub cache_dir: PathBuf,
    pub area: Value,
    #[serde(default)]
    pub target_utcs: Vec<String>,
    pub target_utc: Option<String>,
    #[serde(default)]
    pub config: Config,
    #[serde(default)]
    pub allow_download: bool,
    #[serde(default = "default_exact")]
    pub require_exact_valid_time: bool,
    pub refresh_before_cycle: Option<String>,
}

struct Area {
    bounds: [f64; 4],
    raw: Value,
    key: String,
    tuple: String,
}
impl Area {
    fn new(raw: Value) -> Result<Self, String> {
        let array = raw
            .as_array()
            .filter(|a| a.len() == 4)
            .ok_or("CAMS area must contain four numbers")?;
        let mut bounds = [0.0; 4];
        let mut encoded = Vec::new();
        for (i, value) in array.iter().enumerate() {
            let number = value
                .as_number()
                .ok_or("CAMS area must contain four numbers")?;
            bounds[i] = number
                .as_f64()
                .filter(|v| v.is_finite())
                .ok_or("CAMS area numbers must be finite")?;
            let text = if number.is_i64() || number.is_u64() {
                number
                    .as_i64()
                    .map(|n| n.to_string())
                    .unwrap_or_else(|| number.as_u64().unwrap().to_string())
            } else {
                let v = bounds[i];
                if v != 0.0 && v.abs() < 1e-4 {
                    let text = format!("{v:e}");
                    let (mantissa, exponent) = text.split_once('e').unwrap();
                    let exponent: i32 = exponent.parse().unwrap();
                    format!("{mantissa}e{exponent:+03}")
                } else {
                    serde_json::to_string(&v).map_err(|e| e.to_string())?
                }
            };
            encoded.push(text);
        }
        if bounds[0] > 90.0
            || bounds[2] < -90.0
            || bounds[0] <= bounds[2]
            || bounds[1] >= bounds[3]
            || bounds[1] < -540.0
            || bounds[3] > 540.0
        {
            return Err("invalid CAMS cache area".into());
        }
        let key = Sha256::digest(format!("[{}]", encoded.join(", ")).as_bytes())
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect::<String>()[..12]
            .to_owned();
        Ok(Self {
            bounds,
            raw,
            key,
            tuple: format!("({})", encoded.join(", ")),
        })
    }
}
pub(crate) fn area_identity(raw: Value) -> Result<([f64; 4], String, String), String> {
    let area = Area::new(raw)?;
    Ok((area.bounds, area.key, area.tuple))
}
fn base_query(area: &Area) -> Value {
    json!({"variable":["total_aerosol_optical_depth_550nm","aerosol_extinction_coefficient_532nm"],
        "pressure_level":firesky_native::cams::LEVELS.iter().map(ToString::to_string).collect::<Vec<_>>(),
        "type":["forecast"],"data_format":"grib","area":area.raw})
}

fn dates(value: &Value, target: NaiveDate) -> Result<Vec<NaiveDate>, String> {
    let specs = value
        .as_array()
        .ok_or("ADS date constraints must be a list")?;
    if specs.len() > 4096 {
        return Err("ADS date constraints exceed 4096 specifications".into());
    }
    let first = target
        .checked_sub_signed(Duration::days(5))
        .ok_or("CAMS date search underflow")?;
    let mut result = BTreeSet::new();
    for spec in specs {
        let text = spec.as_str().ok_or("invalid ADS date constraint")?;
        let (start, end) = match text.split_once('/') {
            Some((a, b)) => (time_contract::parse_date(a)?, time_contract::parse_date(b)?),
            None => {
                let day = time_contract::parse_date(text)?;
                (day, day)
            }
        };
        let mut day = start.max(first);
        let stop = end.min(target);
        while day <= stop {
            result.insert(day);
            if day == stop {
                break;
            }
            day = day.succ_opt().ok_or("CAMS date search overflow")?;
        }
    }
    Ok(result.into_iter().collect())
}
fn strings(value: Option<&Value>, default: &[&str]) -> Result<Vec<String>, String> {
    match value {
        Some(value) => {
            let array = value.as_array().ok_or("ADS constraints must be lists")?;
            if array.len() > 4096 {
                return Err("ADS constraint exceeds 4096 values".into());
            }
            array
                .iter()
                .map(|v| {
                    v.as_str()
                        .map(str::to_owned)
                        .ok_or("ADS constraint must be a string".into())
                })
                .collect()
        }
        None => Ok(default.iter().map(|v| (*v).into()).collect()),
    }
}

#[derive(Clone)]
struct Run {
    day: NaiveDate,
    clock: String,
    time: DateTime<Utc>,
    lead: u32,
}
struct Availability {
    base: Value,
    constrained: Value,
    days: BTreeMap<NaiveDate, Value>,
}
impl Availability {
    fn from_base(ads: &mut Ads, base: Value) -> Result<Self, String> {
        let constrained = ads.constraints(&base)?;
        Ok(Self {
            base,
            constrained,
            days: BTreeMap::new(),
        })
    }
    fn new(ads: &mut Ads, area: &Area) -> Result<Self, String> {
        Self::from_base(ads, base_query(area))
    }
    fn choose(&mut self, ads: &mut Ads, target: DateTime<Utc>) -> Result<Option<Run>, String> {
        let mut best = None;
        let mut rank = None;
        for day in dates(
            self.constrained.get("date").unwrap_or(&json!([])),
            target.date_naive(),
        )? {
            if !self.days.contains_key(&day) {
                let mut query = self.base.clone();
                query["variable"] = json!(["aerosol_extinction_coefficient_532nm"]);
                query["date"] = json!([day.to_string()]);
                self.days.insert(day, ads.constraints(&query)?);
            }
            let constraints = &self.days[&day];
            let clocks = strings(constraints.get("time"), &["00:00", "12:00"])?;
            let leads = strings(constraints.get("leadtime_hour"), &["0"])?;
            if clocks.len() > 48 || leads.len() > 1024 {
                return Err("ADS day constraints exceed 48 run times or 1024 leads".into());
            }
            for clock in &clocks {
                if clock.len() != 5 || clock.as_bytes()[2] != b':' {
                    return Err("invalid ADS run time".into());
                }
                let time = NaiveTime::parse_from_str(clock, "%H:%M")
                    .map_err(|_| "invalid ADS run time")?;
                let run = Utc.from_utc_datetime(&day.and_time(time));
                // Existing choose_best_cams_selection ranks using run_hour.
                let ranked_run = run.with_minute(0).unwrap();
                if ranked_run > target {
                    continue;
                }
                for lead in &leads {
                    let lead: u32 = lead.parse().map_err(|_| "invalid ADS lead constraint")?;
                    if lead > 999 {
                        return Err("ADS lead exceeds three-digit CAMS cache range".into());
                    }
                    let valid = time_contract::checked(
                        ranked_run
                            .checked_add_signed(Duration::hours(lead.into()))
                            .ok_or("ADS valid time overflow")?,
                    )?;
                    let delta = (valid - target)
                        .num_microseconds()
                        .ok_or("ADS date span overflow")?
                        .unsigned_abs();
                    let age = (target - ranked_run)
                        .num_microseconds()
                        .ok_or("ADS date span overflow")?;
                    let key = (delta, age, day, clock.clone(), lead);
                    if rank.as_ref().is_none_or(|r| key < *r) {
                        rank = Some(key);
                        best = Some(Run {
                            day,
                            clock: clock.clone(),
                            time: run,
                            lead,
                        });
                    }
                }
            }
        }
        Ok(best)
    }
}

fn current_cache(
    root: &Path,
    area: &Area,
    targets: &[DateTime<Utc>],
    exact: bool,
    refresh: Option<DateTime<Utc>>,
) -> Result<BTreeMap<DateTime<Utc>, PathBuf>, String> {
    let selected = if exact {
        cams_cache::select_with_key(
            root,
            area.bounds,
            &targets.iter().copied().collect(),
            &area.key,
        )?
    } else {
        BTreeMap::new()
    };
    let mut result = BTreeMap::new();
    for &target in targets {
        let selection = if exact {
            selected.get(&target).cloned()
        } else {
            cams_cache::nearest_with_key(root, area.bounds, target, &area.key)?
        };
        if let Some(value) = selection {
            if exact && refresh.is_some_and(|r| r <= target && value.run_time_utc < r) {
                continue;
            }
            result.insert(target, value.path);
        }
    }
    Ok(result)
}

fn step(message: &Message<'_>) -> Result<u32, String> {
    let parse = |text: &str| -> Result<u32, String> {
        let value: f64 = text
            .trim()
            .parse()
            .map_err(|_| "invalid GRIB forecast step")?;
        if !value.is_finite() || value.fract() != 0.0 || !(0.0..=999.0).contains(&value) {
            return Err("invalid GRIB forecast step".into());
        }
        Ok(value as u32)
    };
    let range = (|| {
        let text = message.string(b"stepRange\0")?;
        match text.trim().split_once('-') {
            Some((a, b)) => {
                let a = parse(a)?;
                let b = parse(b)?;
                if b < a {
                    return Err("reversed GRIB forecast step range".into());
                }
                Ok(b)
            }
            None => parse(&text),
        }
    })();
    range.or_else(|_| parse(&message.string(b"step\0")?))
}

fn split(
    codes: &EcCodes,
    bundle: &Path,
    parent: &Path,
    requested: &BTreeSet<u32>,
) -> Result<BTreeMap<u32, tempfile::TempPath>, String> {
    let mut files = BTreeMap::<u32, tempfile::TempPath>::new();
    codes.visit_file(bundle, 4096, |message| {
        let hour = step(message)?;
        if !requested.contains(&hour) {
            return Err("ADS bundle contains an unrequested lead".into());
        }
        if let std::collections::btree_map::Entry::Vacant(entry) = files.entry(hour) {
            entry.insert(
                tempfile::Builder::new()
                    .prefix(".firesky-cams-split-")
                    .tempfile_in(parent)
                    .map_err(|e| e.to_string())?
                    .into_temp_path(),
            );
        }
        // Close each append immediately; do not retain one descriptor per lead.
        OpenOptions::new()
            .append(true)
            .open(&files[&hour])
            .map_err(|e| e.to_string())?
            .write_all(message.encoded()?)
            .map_err(|e| e.to_string())?;
        Ok(())
    })?;
    if files.is_empty() {
        return Err("ADS bundle has no GRIB messages".into());
    }
    Ok(files)
}

fn target_path(root: &Path, area: &Area, run: &Run, lead: u32) -> Result<PathBuf, String> {
    Ok(root
        .join(run.day.to_string())
        .join(format!("{:02}Z", run.time.hour()))
        .join(format!(
            "cams-optics-v2-{}-{}-{lead:03}-{}.grib",
            run.day,
            run.clock.replace(':', ""),
            area.key
        )))
}

struct Session {
    ads: Ads,
    codes: Option<EcCodes>,
    local_work_ms: f64,
    decoder_load_ms: f64,
    decode_ms: f64,
}

fn retrieve_group(
    session: &mut Session,
    root: &Path,
    area: &Area,
    run: &Run,
    leads: &BTreeSet<u32>,
    config: &Config,
    base: &Value,
) -> Result<BTreeMap<u32, PathBuf>, String> {
    let started = Instant::now();
    let network_before = session.ads.retrieval_ms;
    let result = retrieve_group_inner(session, root, area, run, leads, config, base);
    session.local_work_ms +=
        started.elapsed().as_secs_f64() * 1000.0 - (session.ads.retrieval_ms - network_before);
    result
}

fn retrieve_group_inner(
    session: &mut Session,
    root: &Path,
    area: &Area,
    run: &Run,
    leads: &BTreeSet<u32>,
    config: &Config,
    base: &Value,
) -> Result<BTreeMap<u32, PathBuf>, String> {
    let first = target_path(
        root,
        area,
        run,
        *leads.first().ok_or("empty CAMS download group")?,
    )?;
    let parent = first.parent().unwrap();
    fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    let lock = parent.join(format!(
        ".cams-{}-{}.lock",
        run.clock.replace(':', ""),
        area.key
    ));
    let _lock = Lock::acquire(&lock, config.lock_timeout_seconds).map_err(|e| {
        if e == "GFS cache lock timed out" {
            "CAMS cache lock timed out".into()
        } else {
            e
        }
    })?;
    if session.codes.is_none() {
        let started = Instant::now();
        session.codes = Some(EcCodes::load()?);
        session.decoder_load_ms += started.elapsed().as_secs_f64() * 1000.0;
    }
    let codes = session.codes.as_ref().unwrap();
    let mut result = BTreeMap::new();
    let mut missing = BTreeSet::new();
    for &lead in leads {
        let path = target_path(root, area, run, lead)?;
        match fs::symlink_metadata(&path) {
            Ok(m) if !m.is_file() => {
                return Err("CAMS destination must be a regular nonsymlink file".into())
            }
            Ok(m) if m.len() > 0 => {
                let started = Instant::now();
                let valid = cams::decode(codes, &path);
                session.decode_ms += started.elapsed().as_secs_f64() * 1000.0;
                if valid.is_ok_and(|v| v.valid_time == run.time + Duration::hours(lead.into())) {
                    result.insert(lead, path);
                    continue;
                }
            }
            Err(e) if e.kind() != std::io::ErrorKind::NotFound => return Err(e.to_string()),
            _ => (),
        }
        missing.insert(lead);
    }
    if missing.is_empty() {
        return Ok(result);
    }
    let mut query = base.clone();
    query["date"] = json!([run.day.to_string()]);
    query["time"] = json!([run.clock]);
    query["leadtime_hour"] = json!(missing.iter().map(ToString::to_string).collect::<Vec<_>>());
    let mut bundle = tempfile::Builder::new()
        .prefix(".firesky-cams-bundle-")
        .tempfile_in(parent)
        .map_err(|e| e.to_string())?;
    session.ads.retrieve(&query, &mut bundle)?;
    let files = split(codes, bundle.path(), parent, &missing)?;
    for (lead, temporary) in files {
        let started = Instant::now();
        let valid = cams::decode(codes, &temporary);
        session.decode_ms += started.elapsed().as_secs_f64() * 1000.0;
        if !valid.is_ok_and(|v| v.valid_time == run.time + Duration::hours(lead.into())) {
            continue;
        }
        let path = target_path(root, area, run, lead)?;
        if fs::symlink_metadata(&path).is_ok_and(|m| !m.is_file()) {
            return Err("CAMS destination changed to a nonregular file".into());
        }
        File::open(&temporary)
            .and_then(|f| f.sync_all())
            .map_err(|e| e.to_string())?;
        temporary.persist(&path).map_err(|e| e.error.to_string())?;
        #[cfg(unix)]
        File::open(parent)
            .and_then(|f| f.sync_all())
            .map_err(|e| e.to_string())?;
        result.insert(lead, path);
    }
    Ok(result)
}

fn single(
    session: &mut Session,
    availability: &mut Availability,
    root: &Path,
    area: &Area,
    target: DateTime<Utc>,
    exact: bool,
    config: &Config,
) -> Result<PathBuf, String> {
    let run = availability
        .choose(&mut session.ads, target)?
        .ok_or("no CAMS forecast run is available for target")?;
    if exact && run.time + Duration::hours(run.lead.into()) != target {
        return Err("CAMS did not provide the exact requested valid time".into());
    }
    let eligible: BTreeSet<u32> = strings(
        availability.constrained.get("leadtime_hour"),
        &[&run.lead.to_string()],
    )?
    .into_iter()
    .map(|v| v.parse().map_err(|_| "invalid ADS lead constraint".into()))
    .collect::<Result<_, String>>()?;
    let mut candidates: Vec<_> = [
        Some(run.lead),
        run.lead.checked_add(3),
        run.lead.checked_sub(3),
        run.lead.checked_add(6),
    ]
    .into_iter()
    .flatten()
    .filter(|v| *v <= 999 && (eligible.is_empty() || eligible.contains(v)))
    .collect();
    if candidates.is_empty() {
        candidates.push(run.lead);
    }
    for candidate in candidates {
        let result = retrieve_group(
            session,
            root,
            area,
            &run,
            &[candidate].into_iter().collect(),
            config,
            &availability.base,
        )?;
        if let Some(path) = result.get(&candidate) {
            return Ok(path.clone());
        }
    }
    Err(
        "downloaded CAMS optical profile failed strict validation at every eligible fallback lead"
            .into(),
    )
}

pub(crate) fn resolve(
    root: &Path,
    area: [f64; 4],
    targets: Vec<DateTime<Utc>>,
    config: Config,
) -> Result<Value, String> {
    run(Params {
        operation: "resolve".into(),
        cache_dir: root.to_owned(),
        area: json!(area),
        target_utcs: targets.iter().copied().map(time_contract::iso).collect(),
        target_utc: None,
        config,
        allow_download: true,
        require_exact_valid_time: true,
        refresh_before_cycle: None,
    })
}

pub fn execute(params: Params) -> Result<Value, String> {
    run(params)
}
fn run(params: Params) -> Result<Value, String> {
    let started = Instant::now();
    params.config.validate()?;
    let exact = params.require_exact_valid_time;
    if params.cache_dir.as_os_str().is_empty() {
        return Err("invalid CAMS cache area/root".into());
    }
    let area = Area::new(params.area)?;
    let mut targets: Vec<_> = params
        .target_utcs
        .iter()
        .map(|v| time_contract::parse_utc(v))
        .collect::<Result<_, _>>()?;
    if let Some(value) = &params.target_utc {
        targets.push(time_contract::parse_utc(value)?);
    }
    if targets.is_empty() || targets.len() > MAX_TARGETS {
        return Err("CAMS requires 1..256 target times".into());
    }
    targets.sort();
    targets.dedup();
    if exact
        && targets.iter().any(|t| {
            t.hour() % 3 != 0
                || t.minute() != 0
                || t.second() != 0
                || t.timestamp_subsec_nanos() != 0
        })
    {
        return Err("exact CAMS profile downloads require a 3-hour valid time".into());
    }
    let refresh = params
        .refresh_before_cycle
        .as_deref()
        .map(time_contract::parse_utc)
        .transpose()?;
    if !["batch", "download", "resolve"].contains(&params.operation.as_str())
        || params.operation == "download" && targets.len() != 1
    {
        return Err("CAMS operation must be batch, resolve or single-target download".into());
    }
    let mut results = current_cache(&params.cache_dir, &area, &targets, exact, refresh)?;
    let cache_hits = results.len();
    let missing: Vec<_> = targets
        .iter()
        .copied()
        .filter(|t| !results.contains_key(t))
        .collect();
    let mut errors = Vec::new();
    let mut ads = None;
    let mut groups_count = 0;
    let mut fallbacks = 0;
    if params.allow_download && !missing.is_empty() {
        let _slot = Slot::acquire(params.config.lock_timeout_seconds)?;
        let mut client = Ads::new(params.config.clone())?;
        let mut availability = Availability::new(&mut client, &area)?;
        let mut session = Session {
            ads: client,
            codes: None,
            local_work_ms: 0.0,
            decoder_load_ms: 0.0,
            decode_ms: 0.0,
        };
        if params.operation == "download" {
            results.insert(
                missing[0],
                single(
                    &mut session,
                    &mut availability,
                    &params.cache_dir,
                    &area,
                    missing[0],
                    exact,
                    &params.config,
                )?,
            );
            fallbacks = 1;
        } else {
            let mut groups = BTreeMap::<(NaiveDate, String), Vec<(DateTime<Utc>, Run)>>::new();
            for &target in &missing {
                if let Some(run) = availability.choose(&mut session.ads, target)? {
                    groups
                        .entry((run.day, run.clock.clone()))
                        .or_default()
                        .push((target, run));
                }
            }
            groups_count = groups.len();
            for group in groups.values() {
                let run = &group[0].1;
                let leads: BTreeSet<_> = group
                    .iter()
                    .filter_map(|(target, _)| {
                        let seconds = (*target - run.time).num_seconds();
                        (seconds >= 0).then_some((seconds / 3600) as u32)
                    })
                    .collect();
                match retrieve_group(
                    &mut session,
                    &params.cache_dir,
                    &area,
                    run,
                    &leads,
                    &params.config,
                    &availability.base,
                ) {
                    Ok(written) => {
                        for (target, _) in group {
                            let lead = ((*target - run.time).num_seconds() / 3600) as u32;
                            if let Some(path) = written.get(&lead) {
                                results.insert(*target, path.clone());
                            }
                        }
                    }
                    Err(error) => errors.push(error),
                }
            }
            if params.operation == "resolve" {
                for &target in &missing {
                    if results.contains_key(&target) {
                        continue;
                    }
                    fallbacks += 1;
                    results.insert(
                        target,
                        single(
                            &mut session,
                            &mut availability,
                            &params.cache_dir,
                            &area,
                            target,
                            exact,
                            &params.config,
                        )?,
                    );
                }
            }
        }
        ads = Some(session);
    }
    let mut rows = Vec::new();
    for (target, path) in &results {
        let parsed = cams_cache::parse(path).ok_or("invalid selected CAMS cache filename")?;
        rows.push(json!({"requested_valid_time":time_contract::iso(*target),"path":path,
            "valid_time":time_contract::iso(parsed.valid_time),"run_time_utc":time_contract::iso(parsed.run_time_utc),
            "lead_hours":parsed.lead_hours,"exact":parsed.valid_time==*target,
            "identity":source_identity(path)?}));
    }
    let missing: Vec<_> = targets
        .iter()
        .filter(|t| !results.contains_key(t))
        .copied()
        .map(time_contract::iso)
        .collect();
    Ok(
        json!({"contract":"rust-cams-data-v1","files":rows,"missing_valid_times":missing,
        "cache_hits":cache_hits,"groups":groups_count,"single_fallbacks":fallbacks,"group_errors":errors,
        "network_enabled":params.allow_download,"constraints_requests":ads.as_ref().map_or(0,|a|a.ads.constraints_requests),
        "execution_requests":ads.as_ref().map_or(0,|a|a.ads.submissions),"asset_downloads":ads.as_ref().map_or(0,|a|a.ads.downloads),
        "timings_ms":{"constraints":ads.as_ref().map_or(0.0,|a|a.ads.constraints_ms),
            "job_poll_transfer":ads.as_ref().map_or(0.0,|a|a.ads.retrieval_ms),
            "local_lock_decode_split_publish":ads.as_ref().map_or(0.0,|a|a.local_work_ms),
            "decoder_load":ads.as_ref().map_or(0.0,|a|a.decoder_load_ms),
            "profile_decode":ads.as_ref().map_or(0.0,|a|a.decode_ms)},
        "downloaded_files_clock_validated":true,"cache_hit_grib_time_verified":false,
        "public_forecast_complete":false,"elapsed_ms":started.elapsed().as_secs_f64()*1000.0}),
    )
}

pub(crate) fn latest_publication_cycle(
    now: DateTime<Utc>,
    config: crate::ads::Config,
) -> Result<DateTime<Utc>, String> {
    let area = json!({
        "variable": [
            "total_aerosol_optical_depth_550nm",
            "aerosol_extinction_coefficient_532nm",
        ],
        "pressure_level": firesky_native::cams::LEVELS
            .iter()
            .map(ToString::to_string)
            .collect::<Vec<_>>(),
        "type": ["forecast"],
        "data_format": "grib",
    });
    let mut ads = Ads::new(config)?;
    let mut availability = Availability::from_base(&mut ads, area)?;
    let target = now
        .checked_add_signed(Duration::hours(36))
        .ok_or("CAMS publication target overflow")?;
    availability
        .choose(&mut ads, target)?
        .map(|run| run.time)
        .ok_or_else(|| "CAMS provider returned no available forecast run".into())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{
        atomic::{AtomicUsize, Ordering},
        Arc, Barrier,
    };

    #[test]
    fn download_slots_bound_concurrency_timeout_and_release() {
        let prime = Slot::acquire(0.0).unwrap();
        let limit = SLOTS.get().unwrap().2;
        let mut held = vec![prime];
        for _ in 1..limit {
            held.push(Slot::acquire(0.0).unwrap());
        }
        assert!(Slot::acquire(0.0).is_err());
        drop(held);
        let active = Arc::new(AtomicUsize::new(0));
        let peak = Arc::new(AtomicUsize::new(0));
        let barrier = Arc::new(Barrier::new(8));
        let threads: Vec<_> = (0..8)
            .map(|_| {
                let (active, peak, barrier) = (active.clone(), peak.clone(), barrier.clone());
                std::thread::spawn(move || {
                    barrier.wait();
                    let _slot = Slot::acquire(2.0).unwrap();
                    let count = active.fetch_add(1, Ordering::SeqCst) + 1;
                    peak.fetch_max(count, Ordering::SeqCst);
                    std::thread::sleep(StdDuration::from_millis(10));
                    active.fetch_sub(1, Ordering::SeqCst);
                })
            })
            .collect();
        for thread in threads {
            thread.join().unwrap();
        }
        assert!((1..=limit).contains(&peak.load(Ordering::SeqCst)));
        assert_eq!(active.load(Ordering::SeqCst), 0);
        assert!(Slot::acquire(0.0).is_ok());
    }
}

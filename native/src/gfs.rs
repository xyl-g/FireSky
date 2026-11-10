//! Canonical GFS field identities and observer input assembly.

use std::collections::BTreeMap;

pub const PRESSURE_LEVELS: [i32; 11] = [200, 250, 300, 400, 500, 600, 700, 750, 800, 850, 925];
pub const FEATURE_FIELDS: usize = 20;
pub const MAX_FEATURE_ROWS: usize = 500_000;
pub type FieldKey = (String, i32, String);

pub fn keep_field(level_type: &str, level: i32, name: &str) -> bool {
    match level_type {
        "isobaricInhPa" => {
            PRESSURE_LEVELS.contains(&level) || [1000, 975, 950, 900].contains(&level)
        }
        "surface" => level == 0 && ["orog", "vis", "prate", "t", "dpt"].contains(&name),
        "highCloudLayer" | "middleCloudLayer" | "lowCloudLayer" => [
            "hcc", "avg_hcc", "hcdc", "tcc", "tcdc", "mcc", "avg_mcc", "mcdc", "lcc", "avg_lcc",
            "lcdc",
        ]
        .contains(&name),
        "highCloudBottom" | "highCloudTop" | "middleCloudBottom" | "middleCloudTop"
        | "lowCloudBottom" | "lowCloudTop" => ["pres", "avg_pres"].contains(&name),
        _ => false,
    }
}

fn round_coordinate(value: f32) -> f32 {
    (value * 10000.0).round_ties_even() / 10000.0
}

pub fn coordinates(latitudes: &[f64], longitudes: &[f64]) -> Result<(Vec<f32>, Vec<f32>), String> {
    if latitudes.len() != longitudes.len()
        || latitudes.is_empty()
        || latitudes
            .iter()
            .any(|x| !x.is_finite() || !(-90.0..=90.0).contains(x))
        || longitudes.iter().any(|x| !x.is_finite() || x.abs() > 360.0)
    {
        return Err("GFS coordinates must be aligned, nonempty and finite".into());
    }
    let latitudes = latitudes
        .iter()
        .map(|&x| round_coordinate(x as f32))
        .collect();
    let longitudes = longitudes
        .iter()
        .map(|&x| round_coordinate(((x as f32 + 180.0).rem_euclid(360.0)) - 180.0))
        .collect();
    Ok((latitudes, longitudes))
}

#[derive(Debug)]
pub struct GfsGrid {
    pub latitudes: Vec<f32>,
    pub longitudes: Vec<f32>,
    pub fields: BTreeMap<FieldKey, Vec<f32>>,
}

impl GfsGrid {
    pub fn new(latitudes: Vec<f32>, longitudes: Vec<f32>) -> Self {
        Self {
            latitudes,
            longitudes,
            fields: BTreeMap::new(),
        }
    }

    pub fn insert(&mut self, key: FieldKey, values: Vec<f32>) -> Result<(), String> {
        if values.len() != self.latitudes.len() || self.latitudes.len() != self.longitudes.len() {
            return Err("GFS field has a different grid size".into());
        }
        // Preserve Python's last-message-wins behavior for duplicate identities.
        self.fields.insert(key, values);
        Ok(())
    }

    fn field(&self, level_type: &str, level: i32, names: &[&str]) -> Option<&[f32]> {
        names.iter().find_map(|&name| {
            self.fields
                .get(&(level_type.to_owned(), level, name.to_owned()))
                .map(Vec::as_slice)
        })
    }

    pub fn native_features(
        &self,
        observer: [f64; 2],
        radius_km: f64,
    ) -> Result<Vec<[f64; FEATURE_FIELDS]>, String> {
        if !observer[0].is_finite()
            || !(-90.0..=90.0).contains(&observer[0])
            || !observer[1].is_finite()
            || !(-180.0..=180.0).contains(&observer[1])
            || !radius_km.is_finite()
            || radius_km <= 0.0
            || radius_km > 20000.0
        {
            return Err("invalid observer coordinates or radius_km (0..20000]".into());
        }
        let lat_pad = radius_km / 111.0;
        let lon_pad = radius_km / (111.0 * observer[0].to_radians().cos()).max(25.0);
        let normalize = |lon: f64| {
            let value = (lon + 180.0).rem_euclid(360.0) - 180.0;
            if value == -180.0 && lon > 0.0 {
                180.0
            } else {
                value
            }
        };
        let left = normalize(observer[1] - lon_pad);
        let right = normalize(observer[1] + lon_pad);
        let top = (observer[0] + lat_pad).min(90.0);
        let bottom = (observer[0] - lat_pad).max(-90.0);
        let indices: Vec<usize> = self
            .latitudes
            .iter()
            .zip(&self.longitudes)
            .enumerate()
            .filter_map(|(i, (&lat, &lon))| {
                // NumPy compares float32 grid arrays with weak Python scalars
                // in float32, including points exactly on a bbox boundary.
                (lat <= top as f32
                    && lat >= bottom as f32
                    && if left <= right {
                        lon >= left as f32 && lon <= right as f32
                    } else {
                        lon >= left as f32 || lon <= right as f32
                    })
                .then_some(i)
            })
            .collect();
        if indices.is_empty() {
            return Ok(Vec::new());
        }
        let geometry: Vec<(f64, f64)> = indices
            .iter()
            .map(|&i| {
                distance_and_bearing(
                    observer,
                    f64::from(self.latitudes[i]),
                    f64::from(self.longitudes[i]),
                )
            })
            .collect();
        let visibility = self.field("surface", 0, &["vis"]);
        let precipitation = self.field("surface", 0, &["prate"]);
        let high = self.field("highCloudLayer", 0, &["hcc", "hcdc", "tcc", "tcdc"]);
        let mut rows = Vec::new();
        for level in PRESSURE_LEVELS {
            let height = self.field("isobaricInhPa", level, &["gh"]);
            let rh = self.field("isobaricInhPa", level, &["r"]);
            if height.is_none() && rh.is_none() {
                continue;
            }
            let height = height
                .ok_or_else(|| format!("GFS vertical prior field missing at {level} hPa: gh"))?;
            let rh =
                rh.ok_or_else(|| format!("GFS vertical prior field missing at {level} hPa: r"))?;
            if indices
                .iter()
                .any(|&i| !height[i].is_finite() || !rh[i].is_finite())
            {
                return Err(format!(
                    "GFS vertical prior field is non-finite at {level} hPa"
                ));
            }
            let fields: Vec<Option<&[f32]>> =
                ["gh", "r", "t", "w", "u", "v", "tcc", "clwmr", "icmr"]
                    .iter()
                    .map(|&name| {
                        if name == "tcc" {
                            self.field("isobaricInhPa", level, &["tcc", "tcdc"])
                        } else {
                            self.field("isobaricInhPa", level, &[name])
                        }
                    })
                    .collect();
            if rows.len() + indices.len() > MAX_FEATURE_ROWS {
                return Err("GFS feature row limit exceeded (500000)".into());
            }
            for (&i, &(distance, bearing)) in indices.iter().zip(&geometry) {
                let mut row = [f64::NAN; FEATURE_FIELDS];
                row[..5].copy_from_slice(&[
                    f64::from(self.latitudes[i]),
                    f64::from(self.longitudes[i]),
                    distance,
                    bearing,
                    f64::from(level),
                ]);
                for (j, field) in fields.iter().enumerate() {
                    if let Some(field) = field {
                        row[5 + j] = f64::from(field[i]);
                    }
                }
                if level == 300 && !row[11].is_finite() {
                    if let Some(high) = high {
                        row[11] = f64::from(high[i]);
                    }
                }
                if let Some(visibility) = visibility {
                    row[18] = f64::from(visibility[i]);
                }
                if let Some(precipitation) = precipitation {
                    row[19] = f64::from(precipitation[i]);
                }
                rows.push(row);
            }
        }
        Ok(rows)
    }
}

fn distance_and_bearing(observer: [f64; 2], lat: f64, lon: f64) -> (f64, f64) {
    let (lat1, lat2) = (observer[0].to_radians(), lat.to_radians());
    let delta_lat = lat2 - lat1;
    let delta_lon = (lon.to_radians() - observer[1].to_radians() + std::f64::consts::PI)
        .rem_euclid(2.0 * std::f64::consts::PI)
        - std::f64::consts::PI;
    let a =
        (delta_lat / 2.0).sin().powi(2) + lat1.cos() * lat2.cos() * (delta_lon / 2.0).sin().powi(2);
    let distance = 6371.0 * 2.0 * a.sqrt().atan2((1.0 - a).max(0.0).sqrt());
    let y = delta_lon.sin() * lat2.cos();
    let x = lat1.cos() * lat2.sin() - lat1.sin() * lat2.cos() * delta_lon.cos();
    let bearing = (y.atan2(x).to_degrees() + 360.0).rem_euclid(360.0);
    (distance, bearing)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn field_contract_keeps_diagnostics_but_not_unsupported_levels() {
        assert!(keep_field("isobaricInhPa", 975, "rwmr"));
        assert!(!keep_field("isobaricInhPa", 100, "r"));
        assert!(keep_field("highCloudTop", 0, "avg_pres"));
        assert!(!keep_field("heightAboveGround", 2, "t"));
    }

    #[test]
    fn coordinate_normalization_and_dateline_selection() {
        let (lat, lon) = coordinates(&[0.0, 0.0], &[179.0, 181.0]).unwrap();
        assert_eq!(lon, vec![179.0, -179.0]);
        let mut grid = GfsGrid::new(lat, lon);
        for name in ["gh", "r"] {
            grid.insert(("isobaricInhPa".into(), 300, name.into()), vec![90.0; 2])
                .unwrap();
        }
        assert_eq!(grid.native_features([0.0, 180.0], 300.0).unwrap().len(), 2);
        assert!(grid.native_features([0.0, 0.0], 10.0).unwrap().is_empty());
    }

    #[test]
    fn vertical_gaps_fail_and_high_cloud_is_only_a_fallback() {
        let mut grid = GfsGrid::new(vec![0.0], vec![0.0]);
        grid.insert(("isobaricInhPa".into(), 300, "gh".into()), vec![9000.0])
            .unwrap();
        assert!(grid.native_features([0.0, 0.0], 100.0).is_err());
        grid.insert(("isobaricInhPa".into(), 300, "r".into()), vec![90.0])
            .unwrap();
        grid.insert(("highCloudLayer".into(), 0, "hcc".into()), vec![80.0])
            .unwrap();
        assert_eq!(
            grid.native_features([0.0, 0.0], 100.0).unwrap()[0][11],
            80.0
        );
        grid.insert(("isobaricInhPa".into(), 300, "tcc".into()), vec![11.0])
            .unwrap();
        assert_eq!(
            grid.native_features([0.0, 0.0], 100.0).unwrap()[0][11],
            11.0
        );
    }
}

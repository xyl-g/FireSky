//! Expanded 31-field CAMS diagnostics, separate from compact production optics.

use crate::grib::EcCodes;
use crate::native::spectral::{self, AOD_WAVELENGTHS, EXTINCTION_WAVELENGTHS, LEVELS};
use crate::tensor_bundle::{source_identity, BundleWriter, SourceIdentity};
use chrono::{DateTime, SecondsFormat, Utc};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::path::Path;

pub const SCHEMA: &str = "cams-expanded-optics-v1";
const MAX_RETAINED_VALUES: usize = 100_000_000;

pub struct Optics {
    pub latitudes: Vec<f64>,
    pub longitudes: Vec<f64>,
    pub aod: BTreeMap<i32, Vec<f64>>,
    aod_order: Vec<i32>,
    pub extinction: BTreeMap<i32, Vec<f64>>,
    pub ssa: Vec<f64>,
    pub asymmetry: Vec<f64>,
    pub valid_time: Option<DateTime<Utc>>,
}

#[derive(Clone, Copy)]
enum Field {
    Aod(i32),
    Extinction(i32),
    Ssa,
    Asymmetry,
}

fn field(name: &str) -> Option<Field> {
    if let Some(wave) = name.strip_prefix("aod").and_then(|v| v.parse().ok()) {
        if AOD_WAVELENGTHS.contains(&wave) {
            return Some(Field::Aod(wave));
        }
    }
    if let Some(wave) = name.strip_prefix("aerext").and_then(|v| v.parse().ok()) {
        if EXTINCTION_WAVELENGTHS.contains(&wave) {
            return Some(Field::Extinction(wave));
        }
    }
    match name {
        "ssa550" => Some(Field::Ssa),
        "asymmetry550" => Some(Field::Asymmetry),
        _ => None,
    }
}

pub fn decode(
    codes: &EcCodes,
    path: &Path,
    max_messages: usize,
    max_points: usize,
    expected_valid_time: Option<&str>,
) -> Result<Optics, String> {
    if !(1..=5_000_000).contains(&max_points) {
        return Err("spectral max_points must be 1..5000000".into());
    }
    let expected = expected_valid_time
        .map(|v| {
            DateTime::parse_from_rfc3339(v)
                .map(|t| t.with_timezone(&Utc))
                .map_err(|e| e.to_string())
        })
        .transpose()?;
    let mut lats = None;
    let mut lons = None;
    let mut aod = BTreeMap::new();
    let mut aod_order = Vec::new();
    let mut extinction = BTreeMap::new();
    let mut retained = 0;
    let mut ssa = None;
    let mut asymmetry = None;
    let mut time = None;
    codes.visit_file(path, max_messages, |message| {
        let name = message.string(b"shortName\0")?;
        let Some(kind) = field(&name) else {
            return Ok(());
        };
        let level = message.long(b"level\0")?;
        let level_type = message.string(b"typeOfLevel\0")?;
        if let Field::Extinction(_) = kind {
            if level_type != "isobaricInhPa"
                || message.string(b"units\0")? != "m**-1"
                || !LEVELS.iter().any(|&p| i64::from(p) == level)
            {
                return Err("expanded CAMS extinction level/unit contract is invalid".into());
            }
        } else if level_type != "surface" || level != 0 {
            return Err("expanded CAMS surface fields must use surface level 0".into());
        }
        match kind {
            Field::Aod(w) if aod.contains_key(&w) => {
                return Err("duplicate expanded CAMS AOD".into())
            }
            Field::Extinction(w) if extinction.contains_key(&(w, level as i32)) => {
                return Err("duplicate expanded CAMS extinction level".into())
            }
            Field::Ssa if ssa.is_some() => return Err("duplicate expanded CAMS SSA".into()),
            Field::Asymmetry if asymmetry.is_some() => {
                return Err("duplicate expanded CAMS asymmetry".into())
            }
            _ => {}
        }
        let date = message.optional_long(b"validityDate\0")?;
        let clock = message.optional_long(b"validityTime\0")?;
        if let (Some(date), Some(clock)) = (date, clock) {
            let valid = crate::cams::validity(date, clock)?;
            if time.is_some_and(|t| t != valid) {
                return Err("expanded CAMS messages use different valid times".into());
            }
            time = Some(valid);
        }
        let cells = usize::try_from(message.long(b"numberOfPoints\0")?)
            .map_err(|_| "invalid spectral cell count")?;
        if cells == 0
            || cells > max_points
            || cells
                .checked_mul(33)
                .is_none_or(|n| n > MAX_RETAINED_VALUES)
        {
            return Err("expanded CAMS cell/retained-value limit exceeded".into());
        }
        retained += cells;
        if retained > MAX_RETAINED_VALUES {
            return Err("expanded CAMS retained-value limit exceeded".into());
        }
        let lat = message.array(b"latitudes\0", cells)?;
        let lon: Vec<_> = message
            .array(b"longitudes\0", cells)?
            .into_iter()
            .map(spectral::normalize_longitude)
            .collect();
        let values = message.values_f64(cells)?;
        if lat
            .iter()
            .chain(&lon)
            .chain(&values)
            .any(|v| !v.is_finite())
        {
            return Err("expanded CAMS contains missing/nonfinite coordinates or values".into());
        }
        if lats.as_ref().is_some_and(|v| v != &lat) || lons.as_ref().is_some_and(|v| v != &lon) {
            return Err("expanded CAMS messages use different normalized grids".into());
        }
        if lats.is_none() {
            lats = Some(lat);
            lons = Some(lon);
        }
        let (lower, upper) = match kind {
            Field::Aod(_) => (0.0, 10.0),
            Field::Extinction(_) => (0.0, f64::INFINITY),
            Field::Ssa => (0.0, 1.0),
            Field::Asymmetry => (-1.0, 1.0),
        };
        if values.iter().any(|&v| v < lower || v > upper) {
            return Err("expanded CAMS optical values outside physical contract".into());
        }
        match kind {
            Field::Aod(w) => {
                aod_order.push(w);
                aod.insert(w, values);
            }
            Field::Extinction(w) => {
                extinction.insert((w, level as i32), values);
            }
            Field::Ssa => ssa = Some(values),
            Field::Asymmetry => asymmetry = Some(values),
        }
        Ok(())
    })?;
    if aod.len() != 5 || extinction.len() != 24 || ssa.is_none() || asymmetry.is_none() {
        return Err("incomplete expanded CAMS optical file: all 31 fields are required".into());
    }
    if expected.is_some() && time != expected {
        return Err("expanded CAMS GRIB valid time does not match expected valid time".into());
    }
    let cells = aod[&550].len();
    let mut stacked = BTreeMap::new();
    for wave in EXTINCTION_WAVELENGTHS {
        let mut profiles = Vec::with_capacity(8 * cells);
        for level in LEVELS {
            profiles.extend(
                extinction
                    .remove(&(wave, level))
                    .ok_or("missing spectral pressure level")?,
            );
        }
        stacked.insert(wave, profiles);
    }
    Ok(Optics {
        latitudes: lats.ok_or("missing spectral grid")?,
        longitudes: lons.ok_or("missing spectral grid")?,
        aod,
        aod_order,
        extinction: stacked,
        ssa: ssa.unwrap(),
        asymmetry: asymmetry.unwrap(),
        valid_time: time,
    })
}

impl Optics {
    pub fn write(&self, output: &Path, source: SourceIdentity) -> Result<BundleWriter, String> {
        let cells = self.latitudes.len() as u64;
        let mut writer = BundleWriter::create(
            output,
            "cams-spectral-v1",
            source,
            json!({
                "schema_version": SCHEMA, "decoder_backend": "rust-cams-spectral-v1",
                "aod_wavelengths_nm": self.aod_order,
                "extinction_wavelengths_nm": EXTINCTION_WAVELENGTHS, "pressure_levels_hpa": LEVELS,
                "valid_time_utc": self.valid_time.map(|t| t.to_rfc3339_opts(SecondsFormat::AutoSi, true)),
                "phase_fields": ["ssa550", "asymmetry550"], "score_consumed": false,
            }),
        )?;
        writer.f64("latitudes", &[cells], self.latitudes.iter().copied())?;
        writer.f64("longitudes", &[cells], self.longitudes.iter().copied())?;
        writer.f64(
            "aod",
            &[5, cells],
            self.aod_order
                .iter()
                .flat_map(|w| self.aod[w].iter().copied()),
        )?;
        for wave in EXTINCTION_WAVELENGTHS {
            writer.f64(
                &format!("aerext{wave}"),
                &[8, cells],
                self.extinction[&wave].iter().copied(),
            )?;
        }
        writer.f64("ssa550", &[cells], self.ssa.iter().copied())?;
        writer.f64("asymmetry550", &[cells], self.asymmetry.iter().copied())?;
        Ok(writer)
    }

    pub fn diagnose(
        &self,
        observer: [f64; 2],
        wavelengths: &[f64],
        elevation: f64,
        angle: f64,
    ) -> Result<Value, String> {
        if wavelengths.len() > spectral::MAX_AOD_POINTS
            || observer
                .iter()
                .chain([&elevation, &angle])
                .any(|v| !v.is_finite())
        {
            return Err(
                "spectral diagnostics require bounded wavelengths and finite geometry".into(),
            );
        }
        let index = spectral::nearest(&self.latitudes, &self.longitudes, observer[0], observer[1])?;
        let points: Vec<_> = self
            .aod
            .iter()
            .map(|(&w, v)| (w as f64, v[index]))
            .collect();
        let aod: BTreeMap<_, _> = self
            .aod
            .iter()
            .map(|(w, v)| (w.to_string(), v[index]))
            .collect();
        let mut profiles = BTreeMap::new();
        let mut depths = BTreeMap::new();
        let cells = self.latitudes.len();
        let levels = LEVELS.map(f64::from);
        for wave in EXTINCTION_WAVELENGTHS {
            let values: Vec<_> = (0..8)
                .map(|p| self.extinction[&wave][p * cells + index])
                .collect();
            depths.insert(wave.to_string(), spectral::integrate(&values, &levels)?);
            profiles.insert(wave.to_string(), values);
        }
        let middle = spectral::middle_layer(&profiles["532"], &levels, 2000.0, 8000.0)?;
        let interpolated: Vec<_> = wavelengths.iter().map(|&w| {
            match spectral::interpolate_aod(&points, w) {
                Ok(aod) => json!({"wavelength_nm": w, "aod": aod,
                    "direct_transmission": spectral::direct_transmission(aod, elevation), "error": null}),
                Err(error) => json!({"wavelength_nm": w, "aod": null, "direct_transmission": null, "error": error}),
            }
        }).collect();
        let exponent =
            match spectral::angstrom(self.aod[&440][index], self.aod[&865][index], 440.0, 865.0) {
                Ok(value) => json!({"value": value, "error": null}),
                Err(error) => json!({"value": null, "error": error}),
            };
        let air_mass = spectral::relative_airmass(elevation);
        Ok(json!({
            "schema_version": SCHEMA, "backend": "rust-cams-spectral-v1", "score_consumed": false,
            "valid_time_utc": self.valid_time.map(|t| t.to_rfc3339_opts(SecondsFormat::AutoSi, true)),
            "nearest": {"index": index, "lat": self.latitudes[index], "lon": self.longitudes[index],
                "aod": aod, "extinction": profiles, "pressure_levels_hpa": LEVELS,
                "single_scattering_albedo_550": self.ssa[index], "asymmetry_factor_550": self.asymmetry[index]},
            "interpolated": interpolated, "angstrom_exponent_440_865": exponent,
            "profile_optical_depth": depths,
            "middle_layer_532": {"level_count": middle.count, "peak_extinction_km_inv": middle.peak_km_inv,
                "optical_depth": middle.optical_depth, "seasoning_level_fraction": middle.seasoning_fraction,
                "overloaded_level_fraction": middle.overloaded_fraction},
            "relative_airmass": air_mass, "relative_airmass_finite": air_mass.is_finite(),
            "henyey_greenstein_phase_550": spectral::hg_phase(self.asymmetry[index], angle)?,
            "phase_function_model": "Henyey-Greenstein approximation from CAMS first moment; not observed angle-resolved scattering",
        }))
    }
}

pub fn diagnose_file(
    codes: &EcCodes,
    path: &Path,
    observer: [f64; 2],
    wavelengths: &[f64],
    elevation: f64,
    angle: f64,
    expected_valid_time: Option<&str>,
) -> Result<Value, String> {
    let source = source_identity(path)?;
    let optics = decode(codes, path, 4096, 5_000_000, expected_valid_time)?;
    let mut result = optics.diagnose(observer, wavelengths, elevation, angle)?;
    if source_identity(path)? != source {
        return Err("spectral source changed during diagnostics".into());
    }
    result["source"] = json!(source);
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn catalog_does_not_confuse_compact_extra_levels_or_unrequested_waves() {
        assert!(matches!(field("aerext532"), Some(Field::Extinction(532))));
        assert!(matches!(field("ssa550"), Some(Field::Ssa)));
        assert!(field("aod1020").is_none());
        assert!(field("aodinvalid").is_none());
        assert!(!LEVELS.contains(&950));
        assert!(!LEVELS.contains(&800));
    }
}

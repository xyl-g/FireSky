//! Physical sunset/sunrise spectral color calculation (CIE 1931 CMF + Planck + Rayleigh/Mie).
//!
//! Computes the transmitted sunlight chromaticity reaching cloud bases along the tangent
//! grazing optical path, producing sRGB hex colors and sky gradients directly from the
//! forecast step's solar elevation, aerosol optical depth, and cloud-base height.

use serde_json::{json, Value};

const R_EARTH_KM: f64 = 6371.0088;
const RAYLEIGH_TAU_550: f64 = 0.098;
const AEROSOL_ANGSTROM: f64 = 1.3;
const AEROSOL_SCALE_HEIGHT_KM: f64 = 1.4;
const RAYLEIGH_SCALE_HEIGHT_KM: f64 = 7.5;
const AEROSOL_AIRMASS_GAIN: f64 = 12.0;
const SUN_TEMP_K: f64 = 5778.0;

#[derive(Clone, Copy, Debug)]
pub(crate) struct SpectralSample {
    pub r: u8,
    pub g: u8,
    pub b: u8,
    pub luminance: f64,
    pub label_zh: &'static str,
    pub label_en: &'static str,
}

impl SpectralSample {
    pub(crate) fn hex(self) -> String {
        format!("#{:02X}{:02X}{:02X}", self.r, self.g, self.b)
    }
}

fn planck(lam_nm: f64) -> f64 {
    let lam = lam_nm * 1e-9;
    let h = 6.626_070_15e-34;
    let c = 2.997_924_58e8;
    let k = 1.380_649e-23;
    let exponent = (h * c / (lam * k * SUN_TEMP_K)).min(700.0);
    (2.0 * h * c * c / lam.powi(5)) / (exponent.exp() - 1.0)
}

fn gauss_lobe(x: f64, mu: f64, s1: f64, s2: f64) -> f64 {
    let s = if x < mu { s1 } else { s2 };
    let z = (x - mu) / s;
    (-0.5 * z * z).exp()
}

/// Wyman, Sloan & Shirley (2013) analytic Gaussian fit to CIE 1931 color matching functions.
fn cie_xyz(lam_nm: f64) -> (f64, f64, f64) {
    let x = 1.056 * gauss_lobe(lam_nm, 599.8, 37.9, 31.0)
        + 0.362 * gauss_lobe(lam_nm, 442.0, 16.0, 26.7)
        - 0.065 * gauss_lobe(lam_nm, 501.1, 20.4, 26.2);
    let y = 0.821 * gauss_lobe(lam_nm, 568.8, 46.9, 40.5)
        + 0.286 * gauss_lobe(lam_nm, 530.9, 16.3, 31.1);
    let z = 1.217 * gauss_lobe(lam_nm, 437.0, 11.8, 36.0)
        + 0.681 * gauss_lobe(lam_nm, 459.0, 26.0, 13.8);
    (x, y, z)
}

fn gamma_srgb(c: f64) -> u8 {
    let c = c.clamp(0.0, 1.0);
    let srgb = if c <= 0.003_130_8 {
        12.92 * c
    } else {
        1.055 * c.powf(1.0 / 2.4) - 0.055
    };
    (srgb.clamp(0.0, 1.0) * 255.0).round() as u8
}

fn airmass(sun_elev_deg: f64, cloud_base_km: f64) -> (f64, f64) {
    let h = cloud_base_km.clamp(0.5, 18.0);
    let tangent_ray = 38.0 * (-h / RAYLEIGH_SCALE_HEIGHT_KM).exp();
    let tangent_aer = AEROSOL_AIRMASS_GAIN * (-h / AEROSOL_SCALE_HEIGHT_KM).exp();
    if sun_elev_deg >= 0.0 {
        let s = sun_elev_deg.to_radians().sin().max(0.005);
        return (tangent_ray.min(1.0 / s), tangent_aer.min(0.75 / s));
    }
    let alpha = (-sun_elev_deg).to_radians();
    let h_t = (h - R_EARTH_KM * alpha * alpha / 2.0).max(0.0);
    let m_ray = 38.0 * (-h_t / RAYLEIGH_SCALE_HEIGHT_KM).exp();
    let m_aer = AEROSOL_AIRMASS_GAIN * (-h_t / AEROSOL_SCALE_HEIGHT_KM).exp();
    (m_ray, m_aer)
}

fn classify_glow(r: u8, g: u8, b: u8, sun_elev_deg: f64) -> (&'static str, &'static str) {
    let rf = f64::from(r) / 255.0;
    let gf = f64::from(g) / 255.0;
    let bf = f64::from(b) / 255.0;
    if sun_elev_deg < -6.0 || rf < 0.25 {
        return ("暮蓝微晖", "Dusk Blue");
    }
    let rg_ratio = if gf > 0.05 { rf / gf } else { 4.0 };
    if bf > gf * 1.15 && rf > 0.55 {
        return ("紫红余晖", "Magenta Twilight");
    }
    if rg_ratio >= 2.25 {
        ("绯红火烧", "Crimson Glow")
    } else if rg_ratio >= 1.48 {
        ("赤橙彤霞", "Fiery Tangerine")
    } else if rg_ratio >= 1.12 {
        ("金橙暖辉", "Golden Amber")
    } else {
        ("浅金微霞", "Soft Gold")
    }
}

/// Computes the physical sunset/sunrise chromaticity at the cloud base.
pub(crate) fn compute_color(sun_elev_deg: f64, aod550: f64, cloud_base_km: f64) -> SpectralSample {
    let aod = aod550.clamp(0.02, 2.5);
    let (m_ray, m_aer) = airmass(sun_elev_deg, cloud_base_km);

    let mut x_sum = 0.0_f64;
    let mut y_sum = 0.0_f64;
    let mut z_sum = 0.0_f64;
    let mut white_y = 0.0_f64;

    for i in 0..=60 {
        let lam = 400.0 + f64::from(i * 5);
        let radiance = planck(lam);
        let tau_ray = RAYLEIGH_TAU_550 * (550.0 / lam).powi(4);
        let tau_aer = aod * (550.0 / lam).powf(AEROSOL_ANGSTROM);
        let transmittance = (-(tau_ray * m_ray + tau_aer * m_aer)).exp();
        let (cx, cy, cz) = cie_xyz(lam);
        let weight = radiance * transmittance;
        x_sum += weight * cx;
        y_sum += weight * cy;
        z_sum += weight * cz;
        white_y += radiance * cy;
    }

    let lum = if white_y > 0.0 {
        (y_sum / white_y).clamp(0.0, 1.0)
    } else {
        0.0
    };

    let lin_r = (3.2406 * x_sum - 1.5372 * y_sum - 0.4986 * z_sum).max(0.0);
    let lin_g = (-0.9689 * x_sum + 1.8758 * y_sum + 0.0415 * z_sum).max(0.0);
    let lin_b = (0.0557 * x_sum - 0.2040 * y_sum + 1.0570 * z_sum).max(0.0);

    let max_chan = lin_r.max(lin_g).max(lin_b).max(1e-12);
    // Normalize chroma so sunset hue stays vivid while subtle twilight attenuation still registers.
    let brightness = (0.48 + 0.52 * lum.sqrt()).clamp(0.32, 1.0);
    let r = gamma_srgb(lin_r / max_chan * brightness);
    let g = gamma_srgb(lin_g / max_chan * brightness);
    let b = gamma_srgb(lin_b / max_chan * brightness);
    let (label_zh, label_en) = classify_glow(r, g, b, sun_elev_deg);

    SpectralSample {
        r,
        g,
        b,
        luminance: (lum * 1000.0).round() / 1000.0,
        label_zh,
        label_en,
    }
}

fn sky_gradient(peak: SpectralSample, sun_elev_deg: f64) -> Vec<String> {
    let twilight = ((-sun_elev_deg + 2.0) / 8.0).clamp(0.0, 1.0);
    let zenith_r = (26.0 * (1.0 - twilight) + 14.0 * twilight).round() as u8;
    let zenith_g = (52.0 * (1.0 - twilight) + 22.0 * twilight).round() as u8;
    let zenith_b = (108.0 * (1.0 - twilight) + 56.0 * twilight).round() as u8;

    let mid_r = ((f64::from(zenith_r) * 0.45 + f64::from(peak.r) * 0.55).round() as u8).max(zenith_r);
    let mid_g = (f64::from(zenith_g) * 0.55 + f64::from(peak.g) * 0.45).round() as u8;
    let mid_b = (f64::from(zenith_b) * 0.55 + f64::from(peak.b) * 0.45).round() as u8;

    let horizon_r = peak.r.saturating_add(16);
    let horizon_g = ((f64::from(peak.g) * 0.85 + 24.0).min(255.0)).round() as u8;
    let horizon_b = ((f64::from(peak.b) * 0.65 + 12.0).min(255.0)).round() as u8;

    vec![
        format!("#{:02X}{:02X}{:02X}", zenith_r, zenith_g, zenith_b),
        format!("#{:02X}{:02X}{:02X}", mid_r, mid_g, mid_b),
        peak.hex(),
        format!("#{:02X}{:02X}{:02X}", horizon_r, horizon_g, horizon_b),
    ]
}

/// Attaches `spectral_color` summary + per-step chromaticity to a point forecast payload.
pub(crate) fn enrich_point_report(result: &mut Value) {
    let Some(steps) = result["steps"].as_array() else {
        return;
    };
    if steps.is_empty() {
        return;
    }
    let cloud_base_km = result["dominant_burnable_cloud_base_agl_mean_m"]
        .as_f64()
        .filter(|v| v.is_finite() && *v > 100.0)
        .map(|m| m / 1000.0)
        .unwrap_or(5.0);
    let default_aod = result["aerosol"]["aod550"]
        .as_f64()
        .filter(|v| v.is_finite())
        .unwrap_or(0.25);

    let best_time = result["best_time_local"].as_str().map(str::to_owned);
    let mut best_idx = 0usize;
    let mut best_q = f64::NEG_INFINITY;

    let mut step_colors = Vec::with_capacity(steps.len());
    for (idx, step) in steps.iter().enumerate() {
        let elev = step["solar_elevation_deg"].as_f64().unwrap_or(0.0);
        let aod = step["quality_aod550"]
            .as_f64()
            .filter(|v| v.is_finite())
            .unwrap_or(default_aod);
        let sample = compute_color(elev, aod, cloud_base_km);
        let q = step["instant_quality_index"].as_f64().unwrap_or(0.0);
        let time_match = best_time
            .as_deref()
            .is_some_and(|bt| step["valid"].as_str() == Some(bt));
        if time_match || (best_time.is_none() && q > best_q) || (best_time.is_some() && q > best_q && best_idx == 0 && idx > 0 && !time_match) {
            if time_match || q > best_q {
                best_q = q;
                best_idx = idx;
            }
        }
        step_colors.push((sample, step));
    }

    if let Some(bt) = best_time.as_deref() {
        if let Some(found) = steps.iter().position(|s| s["valid"].as_str() == Some(bt)) {
            best_idx = found;
        }
    }

    let (peak_sample, peak_step) = step_colors[best_idx];
    let peak_elev = peak_step["solar_elevation_deg"].as_f64().unwrap_or(0.0);
    let peak_az = peak_step["solar_azimuth_deg"].as_f64().unwrap_or(270.0);
    let peak_aod = peak_step["quality_aod550"]
        .as_f64()
        .filter(|v| v.is_finite())
        .unwrap_or(default_aod);

    let step_rows: Vec<Value> = step_colors
        .iter()
        .map(|(sample, step)| {
            json!({
                "offset_minutes": step["offset_minutes"],
                "hex": sample.hex(),
                "label_zh": sample.label_zh,
                "label_en": sample.label_en,
                "luminance": sample.luminance,
            })
        })
        .collect();

    result["spectral_color"] = json!({
        "contract": "firesky-spectral-color-v1",
        "peak_hex": peak_sample.hex(),
        "peak_label_zh": peak_sample.label_zh,
        "peak_label_en": peak_sample.label_en,
        "sky_gradient_hex": sky_gradient(peak_sample, peak_elev),
        "solar_elevation_deg": (peak_elev * 100.0).round() / 100.0,
        "solar_azimuth_deg": (peak_az * 100.0).round() / 100.0,
        "cloud_base_km": (cloud_base_km * 100.0).round() / 100.0,
        "aod550": (peak_aod * 1000.0).round() / 1000.0,
        "steps": step_rows,
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn negative_solar_elevation_reddens_spectrum_more_than_high_sun() {
        let high = compute_color(8.0, 0.20, 6.0);
        let low = compute_color(-2.0, 0.45, 6.0);
        assert!(high.r >= high.b);
        assert!(low.r > low.g, "expected warm shift at -2 deg: {low:?}");
        assert!(low.g < high.g || low.b < high.b);
    }

    #[test]
    fn enrich_point_report_attaches_valid_spectral_contract() {
        let mut payload = json!({
            "best_time_local": "2026-11-26T17:15:00+08:00",
            "dominant_burnable_cloud_base_agl_mean_m": 6200.0,
            "aerosol": { "aod550": 0.35 },
            "steps": [
                {
                    "offset_minutes": -10,
                    "valid": "2026-11-26T17:05:00+08:00",
                    "solar_elevation_deg": 0.8,
                    "solar_azimuth_deg": 245.0,
                    "quality_aod550": 0.35,
                    "instant_quality_index": 1.1
                },
                {
                    "offset_minutes": 0,
                    "valid": "2026-11-26T17:15:00+08:00",
                    "solar_elevation_deg": -1.4,
                    "solar_azimuth_deg": 247.2,
                    "quality_aod550": 0.38,
                    "instant_quality_index": 1.85
                }
            ]
        });
        enrich_point_report(&mut payload);
        let spectral = &payload["spectral_color"];
        assert_eq!(spectral["contract"], "firesky-spectral-color-v1");
        let hex = spectral["peak_hex"].as_str().unwrap();
        assert_eq!(hex.len(), 7);
        assert!(hex.starts_with('#'));
        assert_eq!(spectral["sky_gradient_hex"].as_array().unwrap().len(), 4);
        assert_eq!(spectral["steps"].as_array().unwrap().len(), 2);
        assert_eq!(spectral["solar_azimuth_deg"], 247.2);
    }
}

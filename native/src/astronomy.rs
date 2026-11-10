// Solar formulae adapted from Astral 3.2 (Simon Kennedy, Apache-2.0).
// Copyright 2009-2021, Simon Kennedy, sffjunkie+code@gmail.com
// See third_party/astral/NOTICE and LICENSE. Preserve its second-resolution
// Julian time and geometric-centre position convention.

#[derive(Clone, Copy)]
pub struct SolarTerms {
    declination: f64,
    equation_of_time: f64,
}

pub fn julian_day(year: i32, month: u32, day: u32, seconds: u32) -> f64 {
    let (year, month) = if month <= 2 {
        (year - 1, month + 12)
    } else {
        (year, month)
    };
    let century = year / 100;
    (365.25 * f64::from(year + 4716)).trunc()
        + (30.6001 * f64::from(month + 1)).trunc()
        + f64::from(day)
        + f64::from(seconds) / 86400.0
        + f64::from(2 - century + century / 4)
        - 1524.5
}

impl SolarTerms {
    pub fn noon_minutes(self, longitude: f64) -> f64 {
        720.0 - 4.0 * longitude - self.equation_of_time
    }

    pub fn at(jd: f64) -> Self {
        let t = (jd - 2451545.0) / 36525.0;
        let longitude = (280.46646 + t * (36000.76983 + 0.0003032 * t)).rem_euclid(360.0);
        let anomaly = (357.52911 + t * (35999.05029 - 0.0001537 * t)).to_radians();
        let eccentricity = 0.016708634 - t * (0.000042037 + 0.0000001267 * t);
        let center = anomaly.sin() * (1.914602 - t * (0.004817 + 0.000014 * t))
            + (anomaly + anomaly).sin() * (0.019993 - 0.000101 * t)
            + (anomaly + anomaly + anomaly).sin() * 0.000289;
        let omega = (125.04 - 1934.136 * t).to_radians();
        let apparent_longitude =
            (longitude + center - 0.00569 - 0.00478 * omega.sin()).to_radians();
        let seconds = 21.448 - t * (46.815 + t * (0.00059 - t * 0.001813));
        let obliquity =
            (23.0 + (26.0 + seconds / 60.0) / 60.0 + 0.00256 * omega.cos()).to_radians();
        let declination = (obliquity.sin() * apparent_longitude.sin())
            .asin()
            .to_degrees();
        let y = (obliquity / 2.0).tan().powi(2);
        let longitude = longitude.to_radians();
        let equation_of_time = (y * (2.0 * longitude).sin() - 2.0 * eccentricity * anomaly.sin()
            + 4.0 * eccentricity * y * anomaly.sin() * (2.0 * longitude).cos()
            - 0.5 * y * y * (4.0 * longitude).sin()
            - 1.25 * eccentricity * eccentricity * (2.0 * anomaly).sin())
        .to_degrees()
            * 4.0;
        Self {
            declination,
            equation_of_time,
        }
    }

    pub fn position(self, latitude: f64, longitude: f64, utc_minutes: f64) -> [f64; 2] {
        self.position_impl(latitude, longitude, utc_minutes, false)
    }

    pub fn regional_position(self, latitude: f64, longitude: f64, utc_minutes: f64) -> [f64; 2] {
        self.position_impl(latitude, longitude, utc_minutes, true)
    }

    fn position_impl(
        self,
        latitude: f64,
        longitude: f64,
        utc_minutes: f64,
        regional: bool,
    ) -> [f64; 2] {
        let latitude = latitude.clamp(-89.8, 89.8);
        let mut solar_time = utc_minutes + self.equation_of_time + 4.0 * longitude;
        if regional {
            solar_time = solar_time.rem_euclid(1440.0);
        } else {
            while solar_time > 1440.0 {
                solar_time -= 1440.0;
            }
        }
        let mut hour_angle = solar_time / 4.0 - 180.0;
        if hour_angle < -180.0 {
            hour_angle += 360.0;
        }
        let cl = latitude.to_radians().cos();
        let sl = latitude.to_radians().sin();
        let sd = self.declination.to_radians().sin();
        let cd = self.declination.to_radians().cos();
        let zenith = (cl * cd * hour_angle.to_radians().cos() + sl * sd)
            .clamp(-1.0, 1.0)
            .acos()
            .to_degrees();
        let denominator = cl * zenith.to_radians().sin();
        let mut azimuth = if denominator.abs() > 0.001 {
            let ratio = ((sl * zenith.to_radians().cos() - sd) / denominator).clamp(-1.0, 1.0);
            let angle = 180.0 - ratio.acos().to_degrees();
            if hour_angle > 0.0 {
                -angle
            } else {
                angle
            }
        } else if latitude > 0.0 {
            180.0
        } else {
            0.0
        };
        if azimuth < 0.0 {
            azimuth += 360.0;
        }
        if regional {
            azimuth = azimuth.rem_euclid(360.0);
            if azimuth == 0.0 {
                azimuth = 0.0;
            }
        }
        [90.0 - zenith, azimuth]
    }
}

pub fn transit_minutes(
    jd: f64,
    latitude: f64,
    longitude: f64,
    elevation_m: f64,
    zenith: f64,
    rising: bool,
) -> Result<f64, String> {
    transit(jd, latitude, longitude, elevation_m, zenith, rising, false)
}

pub fn regional_transit_minutes(
    jd: f64,
    latitude: f64,
    longitude: f64,
    elevation_m: f64,
    rising: bool,
) -> Result<f64, String> {
    transit(
        jd,
        latitude,
        longitude,
        elevation_m,
        90.0 + 16.0 / 60.0,
        rising,
        true,
    )
}

pub fn apparent_event_zenith(elevation_m: f64) -> f64 {
    let horizon = if elevation_m > 0.0 {
        (6356900.0 / (6356900.0 + elevation_m)).acos().to_degrees()
    } else {
        0.0
    };
    let zenith = 90.0 + 16.0 / 60.0 + horizon;
    zenith + super::solar_refraction_deg(90.0 - zenith)
}

fn transit(
    jd: f64,
    latitude: f64,
    longitude: f64,
    elevation_m: f64,
    zenith: f64,
    rising: bool,
    clip_polar: bool,
) -> Result<f64, String> {
    let latitude = latitude.clamp(-89.8, 89.8).to_radians();
    let horizon = if elevation_m > 0.0 {
        (6356900.0 / (6356900.0 + elevation_m)).acos().to_degrees()
    } else {
        0.0
    };
    let zenith = zenith + horizon;
    let zenith = (zenith + super::solar_refraction_deg(90.0 - zenith)).to_radians();
    let mut adjustment = 0.0;
    let mut minutes = 0.0;
    for _ in 0..2 {
        let terms = SolarTerms::at(jd + adjustment);
        let declination = terms.declination.to_radians();
        let cosine = (zenith.cos() - latitude.sin() * declination.sin())
            / (latitude.cos() * declination.cos());
        if !clip_polar && !(-1.0..=1.0).contains(&cosine) {
            return Err("solar transit does not occur on this date at this location".into());
        }
        let angle = cosine.clamp(-1.0, 1.0).acos().to_degrees() * if rising { 1.0 } else { -1.0 };
        let mut offset = (-longitude - angle) * 4.0 - terms.equation_of_time;
        if offset < -720.0 {
            offset += 1440.0;
        }
        minutes = 720.0 + offset;
        adjustment = minutes / 1440.0;
    }
    Ok(minutes)
}

pub fn geometric_elevation(effective: f64) -> f64 {
    let (mut lower, mut upper) = (-90.0, 90.0);
    for _ in 0..48 {
        let middle = 0.5 * (lower + upper);
        if super::solar_illumination_elevation_deg(middle) < effective {
            lower = middle;
        } else {
            upper = middle;
        }
    }
    0.5 * (lower + upper)
}

/// # Safety
/// `out` must point to two writable f64 values (geometric elevation, azimuth).
#[no_mangle]
pub unsafe extern "C" fn firesky_solar_position_v1(
    latitude: f64,
    longitude: f64,
    year: i32,
    month: u32,
    day: u32,
    seconds: u32,
    out: *mut f64,
) -> i32 {
    let leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
    let days = match month {
        2 => {
            if leap {
                29
            } else {
                28
            }
        }
        4 | 6 | 9 | 11 => 30,
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        _ => return -1,
    };
    if out.is_null()
        || !latitude.is_finite()
        || !longitude.is_finite()
        || !(-90.0..=90.0).contains(&latitude)
        || !(-180.0..=180.0).contains(&longitude)
        || !(1..=9999).contains(&year)
        || day == 0
        || day > days
        || seconds >= 86400
    {
        return -1;
    }
    let result = SolarTerms::at(julian_day(year, month, day, seconds)).position(
        latitude,
        longitude,
        f64::from(seconds) / 60.0,
    );
    std::ptr::copy_nonoverlapping(result.as_ptr(), out, 2);
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn invalid_ffi_arguments_are_rejected() {
        let mut output = [0.0; 2];
        for (year, month, day, second) in [
            (0, 1, 1, 0),
            (2025, 2, 29, 0),
            (2024, 13, 1, 0),
            (2024, 2, 0, 0),
            (2024, 2, 29, 86400),
        ] {
            assert_eq!(
                unsafe {
                    firesky_solar_position_v1(
                        0.0,
                        0.0,
                        year,
                        month,
                        day,
                        second,
                        output.as_mut_ptr(),
                    )
                },
                -1
            );
        }
        assert_eq!(
            unsafe { firesky_solar_position_v1(0.0, 0.0, 2024, 2, 29, 0, std::ptr::null_mut()) },
            -1
        );
        assert_eq!(
            unsafe { firesky_solar_position_v1(0.0, 0.0, 2024, 2, 29, 0, output.as_mut_ptr()) },
            0
        );
    }

    #[test]
    fn julian_and_polar_transit() {
        assert_eq!(julian_day(2000, 1, 1, 43200), 2451545.0);
        assert!(transit_minutes(julian_day(2026, 6, 21, 0), 89.0, 0.0, 0.0, 96.0, true).is_err());
    }
}

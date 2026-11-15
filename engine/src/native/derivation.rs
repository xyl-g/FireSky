//! Canonical pressure-grid to regional cloud-column/profile derivation.

use crate::native::{cloud, cloud_structure, gfs};
use rayon::prelude::*;
use std::slice;

pub const RAW_FIELDS: usize = 9;
pub const EXTRA_FIELDS: usize = 15;
pub const COLUMN_FIELDS: usize = 36;
pub const PROFILE_FIELDS: usize = 24;
pub const MAX_CELLS: usize = 500_000;
/// Wrapped (antimeridian) envelopes decode one bounded grid per longitude
/// segment and stitch them; the union may reach two segment budgets while
/// every individual decode stays within MAX_CELLS.
pub const MAX_WRAPPED_CELLS: usize = 2 * MAX_CELLS;
const DEPTH: [f32; 11] = [
    2200., 2000., 1800., 1500., 1200., 900., 700., 550., 500., 450., 300.,
];

fn clean(v: f32) -> f32 {
    if v.is_nan() {
        0.0
    } else {
        v.clamp(f32::MIN, f32::MAX)
    }
}
fn unit(v: f32) -> f32 {
    clean(v).clamp(0.0, 1.0)
}
fn max(a: f32, b: f32) -> f32 {
    if a.is_nan() || b.is_nan() {
        f32::NAN
    } else {
        a.max(b)
    }
}
fn min(a: f32, b: f32) -> f32 {
    if a.is_nan() || b.is_nan() {
        f32::NAN
    } else {
        a.min(b)
    }
}
fn close(a: f32, b: f32) -> bool {
    (a - b).abs() <= 1e-8 + 1e-5 * b.abs()
}
fn base_family(level: i32) -> usize {
    if level <= 400 {
        0
    } else if level <= 700 {
        1
    } else {
        2
    }
}

/// Cell-major raw rows: fraction, RH, liquid, ice, height, temperature, omega, u, v.
/// Extras: terrain, visibility, precipitation, three aggregate fractions,
/// three raw high-cloud products, then three bottom/top pressure pairs in Pa.
pub struct RawGrid {
    pub levels: Vec<i32>,
    pub latitudes: Vec<f32>,
    pub longitudes: Vec<f32>,
    pub rows: Vec<[f32; RAW_FIELDS]>,
    pub extras: Vec<[f32; EXTRA_FIELDS]>,
    /// 0: missing, 1: instantaneous, 2: average.
    pub semantics: [u8; 3],
}

pub struct DerivedGrid {
    pub levels: Vec<i32>,
    pub columns: Vec<[f32; COLUMN_FIELDS]>,
    /// Cell-major, then pressure-level order.
    pub profiles: Vec<[f32; PROFILE_FIELDS]>,
}

impl RawGrid {
    pub fn from_gfs(grid: &gfs::GfsGrid) -> Result<Self, String> {
        let cells = grid.latitudes.len();
        if cells == 0 || cells > MAX_WRAPPED_CELLS || grid.longitudes.len() != cells {
            return Err("invalid canonical grid dimensions (1..1000000 cells)".into());
        }
        for ((kind, level, name), values) in &grid.fields {
            if values.len() != cells {
                return Err("canonical field size mismatch".into());
            }
            let bounds = if [
                "tcc", "tcdc", "hcc", "avg_hcc", "hcdc", "mcc", "avg_mcc", "mcdc", "lcc",
                "avg_lcc", "lcdc", "r",
            ]
            .contains(&name.as_str())
            {
                Some((0.0, 100.0))
            } else {
                match name.as_str() {
                    "clwmr" | "icmr" => Some((0.0, 1.0)),
                    "gh" => Some((-1000.0, 50000.0)),
                    "orog" => Some((-500.0, 9000.0)),
                    "pres" | "avg_pres" => Some((100.0, 110000.0)),
                    _ => None,
                }
            };
            if let Some((lower, upper)) = bounds {
                if values.iter().any(|&v| {
                    v.is_finite()
                        && !(lower..=upper).contains(&v)
                        && !(["pres", "avg_pres"].contains(&name.as_str()) && v == 9999.0)
                }) {
                    return Err(format!(
                        "canonical field {kind}/{level}/{name} outside [{lower}, {upper}]"
                    ));
                }
            }
        }
        let field = |kind: &str, level: i32, name: &str| {
            grid.fields.get(&(kind.into(), level, name.into()))
        };
        let levels: Vec<i32> = gfs::PRESSURE_LEVELS
            .into_iter()
            .filter(|&p| {
                field("isobaricInhPa", p, "gh").is_some_and(|v| v.iter().any(|x| x.is_finite()))
            })
            .collect();
        if levels.is_empty() {
            return Err("GFS decoded grid has no supported pressure levels".into());
        }
        let lookup = |kind: &str, level: i32, names: &[&str]| {
            std::array::from_fn::<_, 2, _>(|i| {
                names
                    .get(i)
                    .and_then(|name| field(kind, level, name))
                    .map(Vec::as_slice)
            })
        };
        let value = |choices: [Option<&[f32]>; 2], cell: usize| {
            let mut result = f32::NAN;
            for values in choices {
                if result.is_finite() {
                    break;
                }
                if let Some(values) = values {
                    result = values[cell];
                }
            }
            result
        };
        // Resolve keys once; the cell loop must not allocate or search field names.
        let pressure_names: [&[&str]; RAW_FIELDS] = [
            &["tcc", "tcdc"],
            &["r"],
            &["clwmr"],
            &["icmr"],
            &["gh"],
            &["t", "tmp"],
            &["w", "vvel"],
            &["u", "ugrd"],
            &["v", "vgrd"],
        ];
        let pressure_fields: Vec<_> = levels
            .iter()
            .map(|&level| pressure_names.map(|names| lookup("isobaricInhPa", level, names)))
            .collect();
        let surface_fields = ["orog", "vis", "prate"].map(|name| lookup("surface", 0, &[name]));
        let high_fields = ["hcdc", "tcc", "tcdc"].map(|name| lookup("highCloudLayer", 0, &[name]));
        let aggregate_names = [
            ("highCloudLayer", ["hcc", "avg_hcc"]),
            ("middleCloudLayer", ["mcc", "avg_mcc"]),
            ("lowCloudLayer", ["lcc", "avg_lcc"]),
        ];
        let selected = aggregate_names.map(|(kind, names)| {
            names
                .into_iter()
                .find(|name| field(kind, 0, name).is_some())
        });
        let semantics = selected
            .map(|name| name.map_or(0, |name| if name.starts_with("avg_") { 2 } else { 1 }));
        let aggregate_fields: [Option<&[f32]>; 3] = std::array::from_fn(|family| {
            selected[family]
                .and_then(|name| field(aggregate_names[family].0, 0, name))
                .map(Vec::as_slice)
        });
        let boundary_fields: [[[Option<&[f32]>; 2]; 2]; 3] = std::array::from_fn(|family| {
            std::array::from_fn(|boundary| {
                let kind = format!(
                    "{}Cloud{}",
                    ["high", "middle", "low"][family],
                    ["Bottom", "Top"][boundary]
                );
                let selected = if field(&kind, 0, "pres").is_some() {
                    "pres"
                } else {
                    "avg_pres"
                };
                lookup(&kind, 0, &[selected])
            })
        });
        let mut rows = Vec::with_capacity(cells * levels.len());
        let mut extras = Vec::with_capacity(cells);
        for cell in 0..cells {
            for fields in &pressure_fields {
                rows.push(fields.map(|choices| value(choices, cell)));
            }
            let mut extra = [f32::NAN; EXTRA_FIELDS];
            for i in 0..3 {
                extra[i] = value(surface_fields[i], cell);
                extra[6 + i] = value(high_fields[i], cell);
            }
            for family in 0..3 {
                if let Some(values) = aggregate_fields[family] {
                    extra[3 + family] = values[cell];
                }
                for boundary in 0..2 {
                    extra[9 + family * 2 + boundary] =
                        value(boundary_fields[family][boundary], cell);
                }
            }
            extras.push(extra);
        }
        Ok(Self {
            levels,
            latitudes: grid.latitudes.clone(),
            longitudes: grid.longitudes.clone(),
            rows,
            extras,
            semantics,
        })
    }
}

fn boundary_height(
    pressure: f32,
    levels: &[i32],
    rows: &[[f32; RAW_FIELDS]],
    sanitized: bool,
) -> f32 {
    if levels.len() < 2
        || !pressure.is_finite()
        || pressure <= 0.0
        || (f64::from(pressure) - 9999.0).abs() <= 0.5 + 1e-5 * 9999.0
        || (!sanitized && rows.iter().any(|row| !row[4].is_finite()))
    {
        return f32::NAN;
    }
    let target = f64::from(pressure) / 100.0;
    let first = f64::from(levels[0]);
    let last = f64::from(*levels.last().unwrap());
    let log_target = target.clamp(first * 0.02, last * 2.0).ln();
    let upper = levels
        .partition_point(|&p| f64::from(p).ln() <= log_target)
        .clamp(1, levels.len() - 1);
    let lower = upper - 1;
    let height = |i: usize| {
        f64::from(if sanitized {
            clean(rows[i][4])
        } else {
            rows[i][4]
        })
    };
    let temperature = |i: usize| {
        if rows[i][5].is_finite() {
            f64::from(rows[i][5])
        } else {
            250.0
        }
    };
    let result = if target < first {
        height(0) + 287.05 * temperature(lower) / 9.80665 * (first / target.max(1e-6)).ln()
    } else if target > last {
        height(levels.len() - 1)
            - 287.05 * temperature(upper) / 9.80665 * (target.max(1e-6) / last).ln()
    } else {
        let ratio = (log_target - f64::from(levels[lower]).ln())
            / (f64::from(levels[upper]).ln() - f64::from(levels[lower]).ln());
        height(lower) + ratio * (height(upper) - height(lower))
    };
    result as f32
}

fn boundaries(
    levels: &[i32],
    rows: &[[f32; RAW_FIELDS]],
    extra: &[f32; EXTRA_FIELDS],
    sanitized: bool,
) -> [[f32; 2]; 3] {
    std::array::from_fn(|family| {
        let base = boundary_height(extra[9 + family * 2], levels, rows, sanitized);
        let top = boundary_height(extra[10 + family * 2], levels, rows, sanitized);
        if base.is_finite() && top.is_finite() && top > base {
            let base = max(base, clean(extra[0]));
            [base, max(top, base)]
        } else {
            [f32::NAN; 2]
        }
    })
}

fn sparse_support(levels: &[i32], rows: &[[f32; RAW_FIELDS]], aggregate: f32) -> f32 {
    if levels.iter().filter(|&&p| p <= 400).count() < 2 {
        return 0.0;
    }
    let mut amount = 0.0_f32;
    let mut count = 0;
    let mut representative = 0;
    let mut rh = 0.0_f32;
    let mut condensate = 0.0_f32;
    for (&level, row) in levels.iter().zip(rows) {
        if level <= 400 && row[0].is_finite() && row[0] >= 0.5 {
            amount = amount.max(row[0]);
            count += 1;
            representative += usize::from(level == 300);
            rh = rh.max(clean(row[1]));
            condensate = condensate.max(clean(row[2]).max(clean(row[3])));
        }
    }
    let aggregate = clean(aggregate);
    let sparse = (70.0..=80.0).contains(&aggregate)
        && amount > 0.0
        && amount <= 8.0
        && amount < aggregate * 0.20
        && rh >= 55.0
        && condensate >= 3e-7;
    let isolated = (55.0..=100.0).contains(&aggregate)
        && count == 1
        && representative == 1
        && amount >= 15.0
        && amount < aggregate * 0.80
        && rh >= 95.0
        && condensate >= 2.5e-6;
    if isolated {
        aggregate
    } else if sparse {
        aggregate * 0.20
    } else {
        0.0
    }
}

fn coherent_support(values: &[f32], latitudes: &[f32], longitudes: &[f32]) -> Vec<f32> {
    let unique = |values: &[f32]| {
        let mut result = values.to_vec();
        result.sort_by(f32::total_cmp);
        result.dedup_by(|a, b| *a == *b);
        result
    };
    let lats = unique(latitudes);
    let lons = unique(longitudes);
    if lats.len() < 2 || lons.len() < 2 || lats.len().checked_mul(lons.len()) != Some(values.len())
    {
        return values.to_vec();
    }
    let indices: Vec<_> = latitudes
        .iter()
        .zip(longitudes)
        .map(|(&lat, &lon)| {
            (
                lats.binary_search_by(|v| v.partial_cmp(&lat).unwrap())
                    .unwrap(),
                lons.binary_search_by(|v| v.partial_cmp(&lon).unwrap())
                    .unwrap(),
            )
        })
        .collect();
    let mut matrix = vec![false; values.len()];
    for (&value, &(row, col)) in values.iter().zip(&indices) {
        matrix[row * lons.len() + col] = value > 0.0;
    }
    indices
        .iter()
        .zip(values)
        .map(|(&(row, col), &value)| {
            let mut count = 0;
            for r in row.saturating_sub(1)..=(row + 1).min(lats.len() - 1) {
                for c in col.saturating_sub(1)..=(col + 1).min(lons.len() - 1) {
                    count += usize::from(matrix[r * lons.len() + c]);
                }
            }
            count -= usize::from(matrix[row * lons.len() + col]);
            if value > 0.0 && count >= 2 {
                value
            } else {
                0.0
            }
        })
        .collect()
}

fn resolve_fractions(
    levels: &[i32],
    rows: &[[f32; RAW_FIELDS]],
    extra: &[f32; EXTRA_FIELDS],
    semantics: [u8; 3],
    bounds: [[f32; 2]; 3],
) -> Vec<f32> {
    let mut result: Vec<_> = rows.iter().map(|row| row[0]).collect();
    for family in 0..3 {
        let aggregate = extra[3 + family];
        let [base, top] = bounds[family];
        if semantics[family] != 1
            || !aggregate.is_finite()
            || aggregate < 5.0
            || !base.is_finite()
            || !top.is_finite()
            || top <= base
        {
            continue;
        }
        let members: Vec<_> = levels
            .iter()
            .enumerate()
            .filter_map(|(i, &p)| {
                (base_family(p) == family
                    && rows[i][4].is_finite()
                    && rows[i][4] > clean(extra[0]) + 25.0)
                    .then_some(i)
            })
            .collect();
        let inside = members
            .iter()
            .any(|&i| rows[i][4] >= base - 150.0 && rows[i][4] <= top + 150.0);
        let lower = members.iter().any(|&i| rows[i][4] <= base + 150.0);
        let upper = members.iter().any(|&i| rows[i][4] >= top - 150.0);
        if inside || !lower || !upper {
            continue;
        }
        if let Some(i) = levels.iter().position(|&p| p == [300, 500, 850][family]) {
            if !result[i].is_finite() {
                result[i] = aggregate;
            }
        }
        let midpoint = 0.5 * (base + top);
        let nearest = *members
            .iter()
            .min_by(|&&a, &&b| {
                (rows[a][4] - midpoint)
                    .abs()
                    .total_cmp(&(rows[b][4] - midpoint).abs())
                    .then_with(|| a.cmp(&b))
            })
            .unwrap();
        result[nearest] = if result[nearest].is_finite() {
            result[nearest].max(aggregate)
        } else {
            aggregate
        };
    }
    result
}

fn gate(value: f32, low: f32, high: f32) -> f32 {
    let ratio = ((clean(value) - low) / (high - low).max(1e-6)).clamp(0.0, 1.0);
    ratio * ratio * (3.0 - 2.0 * ratio)
}

fn aggregate_risk(
    amount: &[f32],
    levels: &[i32],
    extra: &[f32; EXTRA_FIELDS],
    bounds: [[f32; 2]; 3],
) -> f32 {
    let mut high = 0.0_f32;
    let mut middle = 0.0_f32;
    for (&amount, &level) in amount.iter().zip(levels) {
        if level <= 400 {
            high = high.max(unit(amount));
        } else if level <= 700 {
            middle = middle.max(unit(amount));
        }
    }
    let high_fraction = unit(clean(extra[3]) / 100.0);
    let middle_fraction = unit(clean(extra[4]) / 100.0);
    let valid = |[base, top]: [f32; 2]| base.is_finite() && top.is_finite() && top > base;
    if middle_fraction < 0.60
        || high_fraction < 0.60
        || high <= 0.005
        || middle <= 0.005
        || !valid(bounds[0])
        || !valid(bounds[1])
    {
        return 0.0;
    }
    let depth = gate((bounds[1][1] - bounds[1][0]).max(0.0), 1500.0, 5000.0);
    let precipitation = gate(clean(extra[2]).max(0.0) * 3600.0, 0.10, 1.00);
    let missing = ((middle_fraction - middle).max(0.0) / (1.0 - middle).max(1e-6)).clamp(0.0, 1.0);
    (middle_fraction * missing * depth * precipitation).clamp(0.0, 1.0)
}

struct Deck {
    members: Vec<usize>,
    amount: f32,
    peak: f32,
}

fn derive_cell(
    levels: &[i32],
    raw: &[[f32; RAW_FIELDS]],
    extra: &[f32; EXTRA_FIELDS],
    semantics: [u8; 3],
    sparse: f32,
    raw_sparse: f32,
    any_sparse: bool,
) -> Result<([f32; COLUMN_FIELDS], Vec<[f32; PROFILE_FIELDS]>), String> {
    let surface = clean(extra[0]);
    let bounds = boundaries(levels, raw, extra, false);
    let column_bounds = boundaries(levels, raw, extra, true);
    let resolved = resolve_fractions(levels, raw, extra, semantics, bounds);
    let n = levels.len();
    let mut profiles = Vec::with_capacity(n);
    let mut amount = vec![0.0; n];
    let mut presence = vec![0.0; n];
    let mut opacity = vec![0.0; n];
    let heights: Vec<_> = raw.iter().map(|row| clean(row[4])).collect();
    let mut fractions = vec![f32::NAN; n];
    let mut above = vec![false; n];
    for (i, (&level, row)) in levels.iter().zip(raw).enumerate() {
        let profile_above = row[4].is_finite() && row[4] > surface + 25.0;
        let effective = if level == 300 && sparse > 0.0 {
            clean(resolved[i]).max(sparse)
        } else {
            resolved[i]
        };
        let profile_fraction = if effective.is_finite() {
            effective
        } else if level == 300 && semantics[0] == 1 {
            extra[3]
        } else {
            effective
        };
        let profile_presence = cloud::optics(row[1], profile_fraction, row[2], row[3])[0];
        profiles.push([
            clean(row[1]),
            clean(row[4]),
            row[5],
            row[6],
            row[7],
            row[8],
            if profile_above { effective } else { 0.0 },
            extra[3],
            extra[6],
            extra[7],
            extra[8],
            if profile_above { row[2] } else { 0.0 },
            if profile_above { row[3] } else { 0.0 },
            if profile_above { profile_presence } else { 0.0 },
            level as f32,
            extra[4],
            extra[5],
            bounds[0][0],
            bounds[0][1],
            bounds[1][0],
            bounds[1][1],
            bounds[2][0],
            bounds[2][1],
            surface,
        ]);
        let fallback_family = match level {
            300 => Some(0),
            500 => Some(1),
            850 => Some(2),
            _ => None,
        };
        let fallback = fallback_family.map_or(f32::NAN, |family| {
            if family == 0 && sparse > 0.0 {
                sparse
            } else if semantics[family] == 1 {
                extra[3 + family]
            } else {
                f32::NAN
            }
        });
        let mut fraction = if resolved[i].is_finite() {
            resolved[i]
        } else {
            fallback
        };
        if level == 300 && any_sparse {
            fraction = clean(fraction).max(sparse);
        }
        fractions[i] = fraction;
        above[i] = heights[i].is_finite() && heights[i] > surface + 25.0;
        let optics = cloud::optics(row[1], fraction, row[2], row[3]);
        presence[i] = if above[i] { optics[0] } else { 0.0 };
        opacity[i] = if above[i] { optics[1] } else { 0.0 };
        amount[i] = if !above[i] {
            0.0
        } else if fraction.is_finite() {
            clean(fraction) / 100.0
        } else {
            presence[i]
        };
    }
    let support_max = |predicate: &dyn Fn(i32) -> bool| {
        levels
            .iter()
            .zip(&amount)
            .filter(|(p, _)| predicate(**p))
            .map(|(_, &v)| v)
            .fold(0.0_f32, f32::max)
    };
    let upper_support = support_max(&|p| p <= 300);
    let middle_support = support_max(&|p| p > 400 && p <= 700);
    let boundary = support_max(&|p| p == 400);
    let pair = if levels.contains(&250) && levels.contains(&300) {
        levels
            .iter()
            .zip(&amount)
            .filter(|(p, _)| **p == 250 || **p == 300)
            .map(|(_, &v)| v)
            .fold(1.0_f32, f32::min)
    } else {
        0.0
    };
    let continuous = boundary >= 0.60 && (middle_support < 0.40 || pair >= 0.80);
    let families: Vec<_> = levels
        .iter()
        .map(|&p| {
            if p == 400 && upper_support >= 0.30 && !continuous {
                1
            } else {
                base_family(p)
            }
        })
        .collect();
    let mut family_decks: [Vec<Deck>; 3] = std::array::from_fn(|_| Vec::new());
    let mut family_amount = [0.0_f32; 3];
    let mut deck_amount = vec![0.0; n];
    let mut local_upper = 0.0_f32;
    for family in 0..3 {
        let mut i = 0;
        while i < n {
            if families[i] != family || !amount[i].is_finite() || amount[i] <= 0.005 {
                i += 1;
                continue;
            }
            let start = i;
            while i < n && families[i] == family && amount[i].is_finite() && amount[i] > 0.005 {
                i += 1;
            }
            let members: Vec<_> = (start..i).collect();
            let peak = members.iter().map(|&j| amount[j]).fold(0.0_f32, f32::max);
            let mut deck = peak;
            if family == 0 {
                let backed = sparse > 0.0 && members.iter().any(|&j| levels[j] == 300);
                let supported: Vec<_> = members
                    .iter()
                    .copied()
                    .filter(|&j| amount[j] >= (0.20 * peak).max(0.005))
                    .collect();
                let rh = supported
                    .iter()
                    .map(|&j| clean(raw[j][1]))
                    .fold(0.0_f32, f32::max);
                let pressure = supported.iter().map(|&j| levels[j]).max().unwrap_or(0);
                let minimum = supported
                    .iter()
                    .map(|&j| amount[j])
                    .fold(f32::INFINITY, f32::min);
                let confidence = if supported.len() == 1 {
                    if rh >= 90.0 {
                        (0.10 + 0.40 * ((rh - 90.0) / 10.0).clamp(0.0, 1.0)).clamp(0.0, 0.5)
                    } else {
                        0.10
                    }
                } else if supported.len() == 2 && pressure <= 400 {
                    0.20 + 0.80 * if peak > 0.0 { minimum / peak } else { 0.0 }
                } else {
                    1.0
                };
                deck = if backed {
                    deck.max(sparse / 100.0)
                } else if pressure <= 300 {
                    deck * confidence
                } else {
                    deck
                };
                local_upper = local_upper.max(if backed {
                    sparse / 100.0
                } else if supported.len() == 2 && pressure <= 400 && minimum >= 0.20 {
                    minimum
                } else {
                    0.0
                });
            }
            for &j in &members {
                deck_amount[j] = deck;
            }
            family_amount[family] =
                (1.0 - (1.0 - family_amount[family]) * (1.0 - deck)).clamp(0.0, 1.0);
            family_decks[family].push(Deck {
                members,
                amount: deck,
                peak,
            });
        }
    }
    if sparse > 0.0 {
        family_amount[0] = sparse / 100.0;
    }
    local_upper = local_upper.max(raw_sparse / 100.0);
    let mut geometry = vec![[false; 3]; n];
    let mut family_opacity = [0.0_f32; 3];
    for family in 0..3 {
        let dominant = family_decks[family]
            .iter()
            .map(|d| d.amount)
            .fold(0.0_f32, f32::max);
        for deck in &family_decks[family] {
            let significant = close(deck.amount, dominant)
                || (deck.amount >= 0.10 && deck.amount >= 0.20 * dominant);
            let mut deck_opacity = 0.0_f32;
            for &j in &deck.members {
                let member = significant
                    && (close(amount[j], deck.peak)
                        || (amount[j] >= 0.10 && amount[j] >= 0.20 * deck.peak));
                geometry[j][family] |= member;
                if member {
                    deck_opacity = deck_opacity.max(clean(opacity[j]));
                }
            }
            family_opacity[family] =
                (1.0 - (1.0 - family_opacity[family]) * (1.0 - deck_opacity)).clamp(0.0, 1.0);
        }
    }
    let mut winds = [[0.0_f32; 2]; 3];
    for (family, wind) in winds.iter_mut().enumerate() {
        let mut cloud_weights = vec![0.0_f64; n];
        let mut fallback_weights = vec![0.0_f64; n];
        for j in 0..n {
            if above[j] && families[j] == family && raw[j][7].is_finite() && raw[j][8].is_finite() {
                if amount[j].is_finite() && amount[j] > 0.0 {
                    cloud_weights[j] = f64::from(amount[j]);
                }
                let fallback = unit(presence[j]).max(unit(clean(raw[j][1]) / 100.0));
                fallback_weights[j] = f64::from(if fallback > 0.0 { fallback } else { 1.0 });
            }
        }
        let cloud_sum: f64 = cloud_weights.iter().sum();
        let weights = if cloud_sum > 0.0 {
            &cloud_weights
        } else {
            &fallback_weights
        };
        let denominator: f64 = weights.iter().sum();
        if denominator > 0.0 {
            for axis in 0..2 {
                let numerator: f64 = weights
                    .iter()
                    .enumerate()
                    .map(|(j, w)| w * f64::from(clean(raw[j][7 + axis])))
                    .sum();
                wind[axis] = (numerator / denominator) as f32;
            }
        }
    }
    let bases: Vec<_> = levels
        .iter()
        .enumerate()
        .map(|(j, &p)| {
            let base = if j + 1 < n {
                0.5 * (heights[j] + max(heights[j + 1], surface))
            } else {
                heights[j]
                    - DEPTH[gfs::PRESSURE_LEVELS
                        .iter()
                        .position(|&level| level == p)
                        .unwrap()]
            };
            max(base, surface)
        })
        .collect();
    let mut family_bounds = [[0.0_f32; 2]; 3];
    for family in 0..3 {
        if !geometry.iter().any(|m| m[family]) {
            continue;
        }
        let mut base = f32::INFINITY;
        let mut top = f32::NEG_INFINITY;
        for j in 0..n {
            base = min(
                base,
                if geometry[j][family] {
                    max(bases[j], surface)
                } else {
                    f32::INFINITY
                },
            );
            top = max(top, if geometry[j][family] { heights[j] } else { 0.0 });
        }
        let [direct_base, direct_top] = column_bounds[family];
        if direct_base.is_finite() && direct_top.is_finite() && direct_top > direct_base {
            base = min(base, direct_base);
            top = max(top, direct_top);
        }
        family_bounds[family] = [if base.is_finite() { base } else { 0.0 }, top];
    }
    let aggregate = aggregate_risk(&amount, levels, extra, column_bounds);
    let classifier_rows: Vec<_> = raw
        .iter()
        .enumerate()
        .map(|(j, row)| {
            [
                amount[j],
                heights[j],
                row[2],
                row[3],
                row[6],
                f32::from(
                    (fractions[j].is_finite() || row[2].is_finite() || row[3].is_finite())
                        && above[j],
                ),
            ]
        })
        .collect();
    let pressure: Vec<_> = levels.iter().map(|&p| p as f32).collect();
    let classification =
        cloud_structure::classify(&classifier_rows, &pressure, Some(aggregate), None, true)?;
    let family_geometry = std::array::from_fn(|family| {
        [
            family_amount[family],
            family_bounds[family][0],
            family_bounds[family][1],
        ]
    });
    let body =
        cloud_structure::body_risks(&amount, &pressure, aggregate, &geometry, family_geometry);
    let connected_deep = (classification[5] == 4.0 || classification[5] == 8.0)
        && cloud_structure::connected(family_geometry[0], family_geometry[1])
        && cloud_structure::connected(family_geometry[1], family_geometry[2]);
    let material: [f32; 3] = std::array::from_fn(|family| {
        if connected_deep {
            min(classification[7 + family], classification[6])
        } else {
            classification[7 + family]
        }
    });
    let deep = classification[3];
    let mut selected = 0;
    let mut suitability = f32::NEG_INFINITY;
    for j in 0..n {
        let value = if levels[j] <= 700 {
            amount[j] * if families[j] == 0 { 1.0 } else { 0.86 } * (1.0 - 0.45 * deep)
        } else {
            -1.0
        };
        if value > suitability {
            selected = j;
            suitability = value;
        }
    }
    let output = [
        family_amount[0],
        family_amount[1],
        family_amount[2],
        deep,
        material[0],
        bases[selected],
        heights[selected],
        deck_amount[selected],
        levels[selected] as f32,
        body[0],
        body[1],
        body[2],
        raw[selected][3],
        extra[1],
        extra[2],
        family_opacity[1],
        family_opacity[2],
        family_bounds[0][0],
        family_bounds[0][1],
        family_bounds[1][0],
        family_bounds[1][1],
        family_bounds[2][0],
        family_bounds[2][1],
        family_opacity[0],
        surface,
        f32::NAN,
        f32::NAN,
        local_upper,
        material[1],
        material[2],
        winds[0][0],
        winds[0][1],
        winds[1][0],
        winds[1][1],
        winds[2][0],
        winds[2][1],
    ];
    Ok((output, profiles))
}

pub fn derive(raw: &RawGrid) -> Result<DerivedGrid, String> {
    derive_with_parallelism(raw, true)
}

/// The serial mode is retained for bitwise verification and profiling.
pub fn derive_with_parallelism(raw: &RawGrid, parallel: bool) -> Result<DerivedGrid, String> {
    let cells = raw.latitudes.len();
    let levels = raw.levels.len();
    if cells > MAX_WRAPPED_CELLS
        || raw.longitudes.len() != cells
        || raw.extras.len() != cells
        || levels == 0
        || levels > gfs::PRESSURE_LEVELS.len()
        || raw.rows.len() != cells * levels
        || raw.levels.windows(2).any(|p| p[0] >= p[1])
        || raw.levels.iter().any(|p| !gfs::PRESSURE_LEVELS.contains(p))
        || raw.semantics.iter().any(|&s| s > 2)
        || raw
            .latitudes
            .iter()
            .chain(&raw.longitudes)
            .any(|v| !v.is_finite())
    {
        return Err("invalid raw cloud grid layout, semantics or pressure levels".into());
    }
    let raw_sparse: Vec<_> = raw
        .extras
        .iter()
        .enumerate()
        .map(|(cell, extra)| {
            sparse_support(
                &raw.levels,
                &raw.rows[cell * levels..(cell + 1) * levels],
                if raw.semantics[0] == 1 {
                    extra[3]
                } else {
                    f32::NAN
                },
            )
        })
        .collect();
    let sparse = coherent_support(&raw_sparse, &raw.latitudes, &raw.longitudes);
    let any_sparse = sparse.iter().any(|&v| v > 0.0);
    let mut columns = vec![[0.0; COLUMN_FIELDS]; cells];
    let mut profiles = vec![[0.0; PROFILE_FIELDS]; cells * levels];
    let derive_row = |cell: usize,
                      output: &mut [f32; COLUMN_FIELDS],
                      output_profile: &mut [[f32; PROFILE_FIELDS]]|
     -> Result<(), String> {
        let (column, profile) = derive_cell(
            &raw.levels,
            &raw.rows[cell * levels..(cell + 1) * levels],
            &raw.extras[cell],
            raw.semantics,
            sparse[cell],
            raw_sparse[cell],
            any_sparse,
        )?;
        *output = column;
        output_profile.copy_from_slice(&profile);
        Ok(())
    };
    if parallel && cells >= 1024 {
        // Spatial support is already resolved; cells now write disjoint rows.
        // Collect in index order so errors preserve the serial first failure.
        let results: Vec<_> = columns
            .par_iter_mut()
            .zip(profiles.par_chunks_mut(levels))
            .enumerate()
            .with_min_len(128)
            .map(|(cell, (column, profile))| derive_row(cell, column, profile))
            .collect();
        for result in results {
            result?;
        }
    } else {
        for (cell, (column, profile)) in columns
            .iter_mut()
            .zip(profiles.chunks_mut(levels))
            .enumerate()
        {
            derive_row(cell, column, profile)?;
        }
    }
    Ok(DerivedGrid {
        levels: raw.levels.clone(),
        columns,
        profiles,
    })
}

/// # Safety
/// Input buffers: cells * levels * 9 raw floats, cells * 15 extras,
/// cells * 2 coordinates, levels i32 pressures and 3 semantic bytes.
/// Outputs: cells * 36 columns and cells * levels * 24 profile floats.
/// All buffers must be aligned; outputs must not overlap inputs or each other.
#[no_mangle]
pub unsafe extern "C" fn firesky_derive_grid_v1(
    rows: *const f32,
    extras: *const f32,
    coordinates: *const f32,
    pressure: *const i32,
    semantics: *const u8,
    cells: usize,
    levels: usize,
    columns: *mut f32,
    profiles: *mut f32,
) -> i32 {
    if cells > MAX_CELLS || levels == 0 || levels > gfs::PRESSURE_LEVELS.len() {
        return -1;
    }
    if cells == 0 {
        return 0;
    }
    if rows.is_null()
        || extras.is_null()
        || coordinates.is_null()
        || pressure.is_null()
        || semantics.is_null()
        || columns.is_null()
        || profiles.is_null()
    {
        return -1;
    }
    let coordinates = slice::from_raw_parts(coordinates.cast::<[f32; 2]>(), cells);
    let raw = RawGrid {
        levels: slice::from_raw_parts(pressure, levels).to_vec(),
        latitudes: coordinates.iter().map(|v| v[0]).collect(),
        longitudes: coordinates.iter().map(|v| v[1]).collect(),
        rows: slice::from_raw_parts(rows.cast::<[f32; RAW_FIELDS]>(), cells * levels).to_vec(),
        extras: slice::from_raw_parts(extras.cast::<[f32; EXTRA_FIELDS]>(), cells).to_vec(),
        semantics: slice::from_raw_parts(semantics, 3).try_into().unwrap(),
    };
    match derive(&raw) {
        Ok(result) => {
            slice::from_raw_parts_mut(columns.cast::<[f32; COLUMN_FIELDS]>(), cells)
                .copy_from_slice(&result.columns);
            slice::from_raw_parts_mut(profiles.cast::<[f32; PROFILE_FIELDS]>(), cells * levels)
                .copy_from_slice(&result.profiles);
            0
        }
        Err(_) => -1,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn prebound_fields_preserve_alias_and_missing_semantics() {
        let mut fields = std::collections::BTreeMap::new();
        for (kind, name, values) in [
            ("isobaricInhPa", "gh", [10000.0; 3]),
            ("isobaricInhPa", "r", [80.0; 3]),
            ("isobaricInhPa", "tcc", [f32::NAN, 0.0, f32::INFINITY]),
            ("isobaricInhPa", "tcdc", [50.0, 60.0, f32::NEG_INFINITY]),
            ("isobaricInhPa", "t", [f32::NAN, 250.0, f32::INFINITY]),
            ("isobaricInhPa", "tmp", [260.0, 270.0, 280.0]),
            (
                "isobaricInhPa",
                "w",
                [f32::INFINITY, f32::NEG_INFINITY, f32::NAN],
            ),
            ("highCloudLayer", "hcc", [f32::NAN, f32::INFINITY, 0.0]),
            ("highCloudLayer", "avg_hcc", [95.0; 3]),
            ("highCloudBottom", "pres", [f32::NAN, 9999.0, f32::INFINITY]),
            ("highCloudBottom", "avg_pres", [50000.0; 3]),
        ] {
            fields.insert(
                (
                    kind.into(),
                    if kind == "isobaricInhPa" { 300 } else { 0 },
                    name.into(),
                ),
                values.to_vec(),
            );
        }
        let raw = RawGrid::from_gfs(&gfs::GfsGrid {
            latitudes: vec![0.0; 3],
            longitudes: vec![100.0, 101.0, 102.0],
            fields,
        })
        .unwrap();
        assert_eq!(
            raw.rows.iter().map(|row| row[0]).collect::<Vec<_>>(),
            [50.0, 0.0, f32::NEG_INFINITY]
        );
        assert_eq!(
            raw.rows.iter().map(|row| row[5]).collect::<Vec<_>>(),
            [260.0, 250.0, 280.0]
        );
        assert_eq!(raw.rows[0][6], f32::INFINITY);
        assert_eq!(raw.rows[1][6], f32::NEG_INFINITY);
        assert!(raw.rows[2][6].is_nan());
        assert_eq!(raw.semantics, [1, 0, 0]);
        assert!(raw.extras[0][3].is_nan());
        assert_eq!(raw.extras[1][3], f32::INFINITY);
        assert_eq!(raw.extras[2][3], 0.0);
        assert!(raw.extras[0][9].is_nan());
        assert_eq!(raw.extras[1][9], 9999.0);
        assert_eq!(raw.extras[2][9], f32::INFINITY);
    }

    #[test]
    fn invalid_ffi_dimensions_are_rejected_before_slicing() {
        unsafe {
            assert_eq!(
                firesky_derive_grid_v1(
                    std::ptr::null(),
                    std::ptr::null(),
                    std::ptr::null(),
                    std::ptr::null(),
                    std::ptr::null(),
                    usize::MAX,
                    11,
                    std::ptr::null_mut(),
                    std::ptr::null_mut()
                ),
                -1
            );
        }
    }

    #[test]
    fn pressure_boundaries_keep_missing_sentinel_and_extrapolate() {
        let rows = [
            [0., 80., 0., 0., 9000., 250., 0., 0., 0.],
            [0., 80., 0., 0., 3000., 270., 0., 0., 0.],
        ];
        assert!(boundary_height(9999.0, &[300, 700], &rows, false).is_nan());
        assert!(boundary_height(20000., &[300, 700], &rows, false) > 9000.);
        assert!(boundary_height(85000., &[300, 700], &rows, false) < 3000.);
    }

    #[test]
    fn published_column_layout_keeps_aerosol_placeholders_and_winds() {
        let mut extra = [f32::NAN; EXTRA_FIELDS];
        extra[0] = 0.0;
        let raw = RawGrid {
            levels: vec![300],
            latitudes: vec![30.0],
            longitudes: vec![120.0],
            rows: vec![[50., 90., 1e-6, 1e-6, 9000., 250., 0., 3., -2.]],
            extras: vec![extra],
            semantics: [0; 3],
        };
        let result = derive(&raw).unwrap();
        let row = result.columns[0];
        assert!(row[25].is_nan() && row[26].is_nan());
        assert_eq!(row[27], 0.0);
        assert_eq!(&row[30..], &[3., -2., 0., 0., 0., 0.]);
        assert_eq!(result.profiles[0][14], 300.0);
    }

    #[test]
    fn spatial_support_treats_signed_zero_as_one_coordinate() {
        let result = coherent_support(&[15.; 4], &[-0., 0., 1., 1.], &[0., 1., 0., 1.]);
        assert_eq!(result, vec![15.; 4]);
    }

    #[test]
    fn standalone_validation_rejects_out_of_range_fields() {
        let mut grid = gfs::GfsGrid::new(vec![30.0], vec![120.0]);
        grid.insert(("isobaricInhPa".into(), 300, "gh".into()), vec![9000.])
            .unwrap();
        grid.insert(("isobaricInhPa".into(), 300, "r".into()), vec![101.])
            .unwrap();
        assert!(RawGrid::from_gfs(&grid).err().unwrap().contains("outside"));
    }

    #[test]
    fn parallel_derivation_matches_every_serial_output_bit() {
        for (cells, levels, semantics) in [
            (1023, vec![300], [0, 0, 0]),
            (1024, vec![300, 500, 700], [1, 1, 1]),
            (4096, gfs::PRESSURE_LEVELS.to_vec(), [2, 1, 0]),
        ] {
            let mut raw = RawGrid {
                levels,
                latitudes: (0..cells).map(|i| 30.0 + (i / 64) as f32 * 0.25).collect(),
                longitudes: (0..cells).map(|i| 100.0 + (i % 64) as f32 * 0.25).collect(),
                rows: Vec::new(),
                extras: Vec::new(),
                semantics,
            };
            for cell in 0..cells {
                for &level in &raw.levels {
                    let height = 44330.0 * (1.0 - (level as f32 / 1013.25).powf(0.190263));
                    raw.rows.push([
                        if cell % 7 == 0 {
                            f32::NAN
                        } else {
                            (cell % 101) as f32
                        },
                        85.0,
                        if cell % 5 == 0 { 2e-6 } else { 0.0 },
                        if level <= 400 { 3e-6 } else { f32::NAN },
                        height,
                        255.0,
                        if cell % 11 == 0 { f32::INFINITY } else { -0.1 },
                        4.0,
                        -2.0,
                    ]);
                }
                let mut extra = [f32::NAN; EXTRA_FIELDS];
                extra[..6].copy_from_slice(&[100.0, 40000.0, 0.0, 30.0, 20.0, 10.0]);
                raw.extras.push(extra);
            }
            let expected = derive_with_parallelism(&raw, false).unwrap();
            for threads in [1, 4] {
                let pool = rayon::ThreadPoolBuilder::new()
                    .num_threads(threads)
                    .build()
                    .unwrap();
                let actual = pool.install(|| derive(&raw)).unwrap();
                assert_eq!(actual.levels, expected.levels);
                for (a, b) in actual
                    .columns
                    .iter()
                    .flatten()
                    .zip(expected.columns.iter().flatten())
                    .chain(
                        actual
                            .profiles
                            .iter()
                            .flatten()
                            .zip(expected.profiles.iter().flatten()),
                    )
                {
                    assert_eq!(a.to_bits(), b.to_bits());
                }
            }
        }
    }
}

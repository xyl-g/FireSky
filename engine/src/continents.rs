//! Continental download envelopes for full-cycle CAMS retrieval.
//!
//! User-directed full-cycle scope (2026-11-12): one ADS job per run downloads
//! the whole continent of the requested area so later point/regional
//! predictions resolve from cache without re-queueing ADS jobs. Envelopes are
//! ordinary rectangles (none crosses the antimeridian) that fully contain
//! typical requests; the preflight covering check accepts them for
//! single-area plans, and wrapped multi-area plans keep the exact per-part
//! subset contract.

const CONTINENTS: &[(&str, [f64; 4])] = &[
    ("africa", [38.0, -18.0, -35.0, 52.0]),
    ("asia", [81.0, 26.0, -11.0, 180.0]),
    ("europe", [72.0, -25.0, 34.0, 45.0]),
    ("north_america", [84.0, -179.0, 7.0, -52.0]),
    ("south_america", [13.0, -92.0, -56.0, -34.0]),
    ("oceania", [6.0, 110.0, -50.0, 180.0]),
    ("antarctica", [-60.0, -180.0, -90.0, 180.0]),
];

const EPS: f64 = 1e-9;

fn validate(area: [f64; 4]) -> Result<(), String> {
    let [north, west, south, east] = area;
    if area.iter().any(|v| !v.is_finite())
        || !(-90.0..=90.0).contains(&north)
        || !(-90.0..=90.0).contains(&south)
        || !(-180.0..=180.0).contains(&west)
        || !(-180.0..=180.0).contains(&east)
        || south >= north
        || west >= east
    {
        return Err("continental envelope requires an ordinary finite rectangle".into());
    }
    Ok(())
}

fn contains(envelope: &[f64; 4], area: &[f64; 4]) -> bool {
    let [n, w, s, e] = *envelope;
    let [north, west, south, east] = *area;
    n + EPS >= north && w - EPS <= west && s - EPS <= south && e + EPS >= east
}

fn intersects(envelope: &[f64; 4], area: &[f64; 4]) -> bool {
    let [n, w, s, e] = *envelope;
    let [north, west, south, east] = *area;
    s <= north + EPS && south <= n + EPS && w <= east + EPS && west <= e + EPS
}

fn union(areas: &[[f64; 4]]) -> [f64; 4] {
    let mut result = areas[0];
    for area in &areas[1..] {
        result[0] = result[0].max(area[0]);
        result[1] = result[1].min(area[1]);
        result[2] = result[2].min(area[2]);
        result[3] = result[3].max(area[3]);
    }
    result
}

/// The continental download envelope for one ordinary request area: the
/// smallest continent fully containing the request, else the union bbox of
/// every intersecting continent, else the request area itself (ocean points
/// keep the legacy per-request subset scope).
pub(crate) fn envelope_for(area: [f64; 4]) -> Result<[f64; 4], String> {
    validate(area)?;
    let containing: Vec<_> = CONTINENTS
        .iter()
        .filter(|(_, envelope)| contains(envelope, &area))
        .collect();
    if !containing.is_empty() {
        let smallest = containing
            .iter()
            .min_by(|a, b| {
                let area = |bounds: &[f64; 4]| (bounds[0] - bounds[2]) * (bounds[3] - bounds[1]);
                area(&a.1).total_cmp(&area(&b.1))
            })
            .expect("nonempty container list");
        return Ok(smallest.1);
    }
    let matched: Vec<_> = CONTINENTS
        .iter()
        .filter(|(_, envelope)| intersects(envelope, &area))
        .map(|(_, envelope)| *envelope)
        .collect();
    Ok(if matched.is_empty() {
        area
    } else {
        union(&matched)
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn china_requests_use_the_asia_envelope() {
        assert_eq!(
            envelope_for([31.5, 119.5, 29.5, 121.5]).unwrap(),
            [81.0, 26.0, -11.0, 180.0]
        );
        assert_eq!(
            envelope_for([54.0, 120.0, 50.0, 130.0]).unwrap(),
            [81.0, 26.0, -11.0, 180.0]
        );
    }

    #[test]
    fn transcontinental_points_prefer_the_smaller_continent() {
        // Istanbul sits in both the Europe and Asia rectangles; Europe is smaller.
        assert_eq!(
            envelope_for([41.2, 28.6, 40.8, 29.4]).unwrap(),
            [72.0, -25.0, 34.0, 45.0]
        );
    }

    #[test]
    fn spanning_requests_use_the_union_of_intersecting_continents() {
        // A bbox overlapping Europe, Asia and Africa without fitting any one
        // of them downloads their union rectangle.
        assert_eq!(
            envelope_for([76.0, 24.0, 32.0, 60.0]).unwrap(),
            [81.0, -25.0, -35.0, 180.0]
        );
    }

    #[test]
    fn ocean_requests_fall_back_to_the_request_area() {
        // Mid-south Pacific: outside every continent rectangle.
        assert_eq!(
            envelope_for([-20.0, -160.0, -30.0, -150.0]).unwrap(),
            [-20.0, -160.0, -30.0, -150.0]
        );
    }

    #[test]
    fn invalid_areas_are_rejected() {
        assert!(envelope_for([0.0, 100.0, 1.0, 102.0]).is_err());
        assert!(envelope_for([31.0, 121.0, 30.0, 120.0]).is_err());
        assert!(envelope_for([31.0, f64::NAN, 30.0, 120.0]).is_err());
    }
}

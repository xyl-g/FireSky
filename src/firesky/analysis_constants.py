"""Shared limits for the directional cloud and light-path analysis.

The radius below is an operational search envelope, not a claim that every
cloud inside the envelope can be illuminated.  A local cloud *deck span* gets
    its own one-sided edge limit from ``d ~= sqrt(2 * R * h_eff)``.  A separate
    two-sided deck-span diagnostic is ``D ~= 2 * d``.  With ``R = 6371 km``,
    1000 km corresponds to an effective cloud-base height of about 78.5 km and
    is therefore only a search envelope.  A 6 km cloud base with 2 km of
    observer elevation has 4 km of effective height and an edge limit of about
    226 km (or a 451 km two-sided span).
"""

import math


# The 1000 km domain is intentionally a data/search envelope.  Each cloud still
# gets its own physical reach from its effective cloud-base height.  It also
# matches the solar incoming trace cap, so no scorer-visible cloud lies beyond
# the envelope.
DEFAULT_ANALYSIS_RADIUS_KM = 1000.0
MAX_ANALYSIS_RADIUS_KM = 1000.0
SOLAR_INCOMING_TRACE_MAX_KM = 1000.0
SOLAR_INCOMING_TRACE_HALO_KM = 400.0
PATH_SAMPLE_STEP_KM = 25.0


def source_domain_radius_km(candidate_radius_km: float) -> float:
    """Return the model/CAMS source radius for one candidate-field radius."""
    candidate = float(candidate_radius_km)
    if not math.isfinite(candidate) or candidate <= 0.0:
        raise ValueError("candidate analysis radius must be a finite positive number")
    incoming_trace = min(
        SOLAR_INCOMING_TRACE_MAX_KM,
        candidate + SOLAR_INCOMING_TRACE_HALO_KM,
    )
    return candidate + incoming_trace

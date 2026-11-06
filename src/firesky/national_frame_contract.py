from __future__ import annotations

import base64
import binascii
import math
import struct
from numbers import Real
from typing import Any

from firesky.national_batch import (
    EC_SELECTED_COLUMN_DIAGNOSTIC_SOURCE,
    NATIONAL_AEROSOL_PROFILE_MODE,
    NATIONAL_FRAME_VERSION,
    NATIONAL_SELECTED_COLUMN_DIAGNOSTIC_SOURCE,
    cloud_optics_metadata_for_model,
)


NATIONAL_SELECTED_COLUMN_DIAGNOSTIC_SOURCES = frozenset({
    NATIONAL_SELECTED_COLUMN_DIAGNOSTIC_SOURCE,
    EC_SELECTED_COLUMN_DIAGNOSTIC_SOURCE,
})
_MODEL_BY_SELECTED_COLUMN_DIAGNOSTIC_SOURCE = {
    NATIONAL_SELECTED_COLUMN_DIAGNOSTIC_SOURCE: "gfs",
    EC_SELECTED_COLUMN_DIAGNOSTIC_SOURCE: "ec",
}


NATIONAL_FRAME_DATA_FIELDS = frozenset({
    "version",
    "encoding",
    "selected_column_diagnostic_source",
    "point_count",
    "height_scale_m",
    "cloud_optics",
    "precision",
    "performance",
    "frames",
})
NATIONAL_FRAME_PRECISION_FIELDS = frozenset({
    "mode",
    "point_spacing_deg",
    "model_grid_spacing_deg",
    "point_count",
    "processed_sample_count",
    "candidate_limit_per_sample",
    "vertical_profiles",
    "full_point_coverage",
    "aerosol_profile_mode",
})
NATIONAL_FRAME_PERFORMANCE_FIELDS = frozenset({
    "instant_scoring_seconds",
    "assembly_seconds",
    "total_seconds",
    "points_per_second",
    "samples_per_second",
})
NATIONAL_FRAME_U8_CHANNEL_FIELDS = frozenset({
    "valid",
    "physically_burnable",
    "burnable_cloud_coverage",
    "forward_view_cloud_field_amount",
    "forward_burnable_cloud_field_amount",
    "forward_candidate_cloud_field_amount",
    "forward_target_eligible_cloud_field_amount",
    "forward_solar_geometry_reachable_cloud_field_amount",
    "forward_external_cloud_path_transmitted_field_amount",
    "forward_unoccluded_burn_potential_upper_bound",
    "solar_incoming_block_risk",
    "observer_local_cloud_field_amount",
    "observer_local_burnable_cloud_field_amount",
    "forward_burnable_cloud_ratio",
    "burn_score",
    "intensity_score",
    "extent_score",
    "vividness_score",
    "high_cloud",
    "mid_cloud",
    "low_cloud",
    "high_burn",
    "mid_burn",
    "low_burn",
    "cloud_evidence",
    "selected_column_cloud_material_class_code",
    "convective_risk",
    "dominant_burnable_pressure_family_code",
    "illumination_score",
    "path_clearance_score",
    "blocking_risk",
    "target_score",
    "target_deck_connected",
    "target_deck_attenuated_fraction",
    "target_deep_risk",
    "peak_blocking",
    "observer_return_transmission",
    "observer_local_return_transmission",
})
NATIONAL_FRAME_U16_CHANNEL_FIELDS = frozenset({
    "instant_quality_index",
    "forward_burnable_cloud_load",
    "forward_burnable_cloud_material_load",
    "forward_burnable_cloud_incoming_aerosol_load",
    "observer_local_burnable_cloud_load",
    "observer_local_burnable_cloud_material_load",
    "observer_local_burnable_cloud_incoming_aerosol_load",
    "local_aod550",
    "cloud_optical_depth",
    "clear_gap_count",
    "observer_return_optical_depth",
    "observer_local_return_optical_depth",
})
NATIONAL_FRAME_I16_CHANNEL_FIELDS = frozenset({
    "dominant_burnable_cloud_base_asl_mean_m",
    "dominant_burnable_cloud_top_asl_mean_m",
    "dominant_burnable_target_terrain_asl_mean_m",
})
NATIONAL_FRAME_FIELDS = frozenset({
    "selected_column_diagnostic_source",
    "offset_minutes",
    *NATIONAL_FRAME_U8_CHANNEL_FIELDS,
    *NATIONAL_FRAME_U16_CHANNEL_FIELDS,
    *NATIONAL_FRAME_I16_CHANNEL_FIELDS,
})


def _finite_number(value: Any, field: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"current national frame {field} must be a finite number")
    number = float(value)
    if not math.isfinite(number) or (minimum is not None and number < minimum):
        raise ValueError(f"current national frame {field} must be a finite number")
    return number


def decode_current_national_frame(
    encoded_frame: Any,
    *,
    frame_index: int,
    point_count: int,
) -> dict[str, bytes]:
    if not isinstance(encoded_frame, dict) or set(encoded_frame) != NATIONAL_FRAME_FIELDS:
        raise ValueError(f"current national frame {frame_index} has invalid fields")
    if (
        encoded_frame["selected_column_diagnostic_source"]
        not in NATIONAL_SELECTED_COLUMN_DIAGNOSTIC_SOURCES
    ):
        raise ValueError(
            f"current national frame {frame_index} has invalid selected_column_diagnostic_source"
        )
    offset = encoded_frame["offset_minutes"]
    if not isinstance(offset, int) or isinstance(offset, bool):
        raise ValueError(f"current national frame {frame_index} offset_minutes must be an integer")
    decoded: dict[str, bytes] = {}
    for fields, width in (
        (NATIONAL_FRAME_U8_CHANNEL_FIELDS, 1),
        (NATIONAL_FRAME_U16_CHANNEL_FIELDS | NATIONAL_FRAME_I16_CHANNEL_FIELDS, 2),
    ):
        for field in sorted(fields):
            encoded = encoded_frame[field]
            if not isinstance(encoded, str):
                raise ValueError(f"current national frame {frame_index} has invalid {field}")
            try:
                values = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ValueError(
                    f"current national frame {frame_index} has invalid {field}"
                ) from exc
            if len(values) != point_count * width:
                raise ValueError(
                    f"current national frame {frame_index} {field} length does not match point_count"
                )
            decoded[field] = values
    for field in ("valid", "physically_burnable"):
        if any(value not in {0, 1} for value in decoded[field]):
            raise ValueError(f"current national frame {frame_index} has invalid {field} codes")
    pressure_family_field = "dominant_burnable_pressure_family_code"
    if any(value > 3 for value in decoded[pressure_family_field]):
        raise ValueError(
            f"current national frame {frame_index} has invalid {pressure_family_field} codes"
        )
    material_class_field = "selected_column_cloud_material_class_code"
    if any(value > 8 for value in decoded[material_class_field]):
        raise ValueError(
            f"current national frame {frame_index} has invalid {material_class_field} codes"
        )
    veto_fields = (
        "forward_candidate_cloud_field_amount",
        "forward_target_eligible_cloud_field_amount",
        "forward_solar_geometry_reachable_cloud_field_amount",
        "forward_external_cloud_path_transmitted_field_amount",
        "forward_unoccluded_burn_potential_upper_bound",
    )
    for point_index, (valid, candidate, eligible, geometry, path, burn, view, upper) in enumerate(
        zip(
            decoded["valid"],
            decoded[veto_fields[0]],
            decoded[veto_fields[1]],
            decoded[veto_fields[2]],
            decoded[veto_fields[3]],
            decoded["forward_burnable_cloud_field_amount"],
            decoded["forward_view_cloud_field_amount"],
            decoded[veto_fields[4]],
        )
    ):
        if not valid and any(decoded[field][point_index] != 0 for field in veto_fields):
            raise ValueError(
                f"current national frame {frame_index} point {point_index} invalid sample carries "
                "nonzero pre-veto ledger"
            )
        if valid and not (burn <= path <= geometry <= eligible <= view <= candidate):
            raise ValueError(
                f"current national frame {frame_index} point {point_index} violates "
                "candidate>=view>=eligible>=geometry>=path>=burn"
            )
        if valid and upper < burn:
            raise ValueError(
                f"current national frame {frame_index} point {point_index} violates "
                "unoccluded upper bound>=burn"
            )
    family_codes = decoded[pressure_family_field]
    base_values = struct.unpack(
        f"<{point_count}h", decoded["dominant_burnable_cloud_base_asl_mean_m"]
    )
    top_values = struct.unpack(
        f"<{point_count}h", decoded["dominant_burnable_cloud_top_asl_mean_m"]
    )
    terrain_values = struct.unpack(
        f"<{point_count}h", decoded["dominant_burnable_target_terrain_asl_mean_m"]
    )
    for point_index, (family, base, top, terrain) in enumerate(
        zip(family_codes, base_values, top_values, terrain_values)
    ):
        if family == 0 and any(value != 0 for value in (base, top, terrain)):
            raise ValueError(
                f"current national frame {frame_index} point {point_index} without a pressure "
                "family must not carry cloud geometry"
            )
        if family > 0 and top < base:
            raise ValueError(
                f"current national frame {frame_index} point {point_index} cloud top is below base"
            )
    return decoded


def validate_current_national_frame_data(
    frame_data: Any,
) -> tuple[int, list[dict[str, Any]], list[dict[str, bytes]]]:
    point_count, frames = validate_current_national_frame_metadata(frame_data)
    decoded: list[dict[str, bytes]] = []
    previous_offset: int | None = None
    for frame_index, frame in enumerate(frames):
        frame_decoded = decode_current_national_frame(
            frame,
            frame_index=frame_index,
            point_count=point_count,
        )
        offset = frame["offset_minutes"]
        if previous_offset is not None and offset <= previous_offset:
            raise ValueError("current national frame offsets must be unique and strictly increasing")
        previous_offset = offset
        decoded.append(frame_decoded)
    return point_count, frames, decoded


def validate_current_national_frame_metadata(
    frame_data: Any,
) -> tuple[int, list[dict[str, Any]]]:
    """Validate the current national frame structure without decoding channel buffers."""
    if not isinstance(frame_data, dict) or set(frame_data) != NATIONAL_FRAME_DATA_FIELDS:
        raise ValueError("frame_data fields do not match current national frame")
    if frame_data["version"] != NATIONAL_FRAME_VERSION:
        raise ValueError(f"frame_data version must be {NATIONAL_FRAME_VERSION}")
    if frame_data["encoding"] != "base64-quantized-row-major-v1":
        raise ValueError("frame_data encoding does not match current national frame")
    if (
        frame_data["selected_column_diagnostic_source"]
        not in NATIONAL_SELECTED_COLUMN_DIAGNOSTIC_SOURCES
    ):
        raise ValueError("frame_data selected_column_diagnostic_source is invalid")
    point_count = frame_data["point_count"]
    if not isinstance(point_count, int) or isinstance(point_count, bool) or point_count <= 0:
        raise ValueError("frame_data point_count is invalid")
    if _finite_number(frame_data["height_scale_m"], "height_scale_m", minimum=0.0) != 10.0:
        raise ValueError("frame_data height_scale_m does not match current national frame")
    model_source = _MODEL_BY_SELECTED_COLUMN_DIAGNOSTIC_SOURCE[
        frame_data["selected_column_diagnostic_source"]
    ]
    if frame_data["cloud_optics"] != cloud_optics_metadata_for_model(model_source):
        raise ValueError("frame_data cloud_optics does not match the current contract")

    precision = frame_data["precision"]
    if not isinstance(precision, dict) or set(precision) != NATIONAL_FRAME_PRECISION_FIELDS:
        raise ValueError("frame_data precision fields do not match current national frame")
    if not isinstance(precision["mode"], str) or not precision["mode"]:
        raise ValueError("frame_data precision.mode is invalid")
    point_spacing = precision["point_spacing_deg"]
    if point_spacing is not None:
        _finite_number(point_spacing, "precision.point_spacing_deg", minimum=0.0)
    _finite_number(
        precision["model_grid_spacing_deg"],
        "precision.model_grid_spacing_deg",
        minimum=0.0,
    )
    for field in ("point_count", "processed_sample_count", "candidate_limit_per_sample"):
        value = precision[field]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"frame_data precision.{field} is invalid")
    if precision["point_count"] != point_count:
        raise ValueError("frame_data precision.point_count does not match point_count")
    for field in ("vertical_profiles", "full_point_coverage"):
        if not isinstance(precision[field], bool):
            raise ValueError(f"frame_data precision.{field} is invalid")
    if precision["aerosol_profile_mode"] != NATIONAL_AEROSOL_PROFILE_MODE:
        raise ValueError(
            "frame_data precision.aerosol_profile_mode does not match current contract"
        )

    performance = frame_data["performance"]
    if not isinstance(performance, dict) or set(performance) != NATIONAL_FRAME_PERFORMANCE_FIELDS:
        raise ValueError("frame_data performance fields do not match current national frame")
    for field in NATIONAL_FRAME_PERFORMANCE_FIELDS:
        _finite_number(performance[field], f"performance.{field}", minimum=0.0)

    frames = frame_data["frames"]
    if not isinstance(frames, list) or not frames:
        raise ValueError("frame_data frames must be a non-empty array")
    previous_offset: int | None = None
    for frame_index, frame in enumerate(frames):
        if not isinstance(frame, dict) or set(frame) != NATIONAL_FRAME_FIELDS:
            raise ValueError(f"current national frame {frame_index} has invalid fields")
        if (
            frame["selected_column_diagnostic_source"]
            != frame_data["selected_column_diagnostic_source"]
        ):
            raise ValueError(
                f"current national frame {frame_index} has invalid selected_column_diagnostic_source"
            )
        offset = frame["offset_minutes"]
        if not isinstance(offset, int) or isinstance(offset, bool):
            raise ValueError(
                f"current national frame {frame_index} offset_minutes must be an integer"
            )
        if previous_offset is not None and offset <= previous_offset:
            raise ValueError("current national frame offsets must be unique and strictly increasing")
        previous_offset = offset
        for field in (
            NATIONAL_FRAME_U8_CHANNEL_FIELDS
            | NATIONAL_FRAME_U16_CHANNEL_FIELDS
            | NATIONAL_FRAME_I16_CHANNEL_FIELDS
        ):
            if not isinstance(frame[field], str):
                raise ValueError(f"current national frame {frame_index} has invalid {field}")
    return point_count, frames

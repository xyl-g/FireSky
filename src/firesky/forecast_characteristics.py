from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Sequence


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def fire_cloud_quality_level(index: float) -> str:
    if not math.isfinite(index):
        return "无明显火烧云"
    thresholds = (
        (0.001, "无明显火烧云"),
        (0.05, "微微烧"),
        (0.20, "小烧"),
        (0.40, "小烧到中等烧"),
        (0.60, "中等烧"),
        (0.80, "中等烧到大烧"),
        (1.00, "不完美大烧"),
        (1.50, "典型大烧"),
        (2.00, "优质大烧"),
        (float("inf"), "世纪大烧"),
    )
    return next(label for upper, label in thresholds if index < upper)


def scene_diagnosis(
    *,
    local_deck: float,
    distant_target: float,
    remote_boundary: float,
    cloud_hole: float,
    convective_risk: float,
) -> dict[str, Any]:
    scores = {
        "local_deck": clamp01(local_deck),
        "distant_target": clamp01(distant_target),
        "remote_boundary": clamp01(remote_boundary),
        "cloud_hole": clamp01(cloud_hole),
        "convective": clamp01(convective_risk),
    }
    positive = {key: value for key, value in scores.items() if key != "convective"}
    selected_regime = max(positive, key=positive.get)
    positive_score = positive[selected_regime]
    if positive_score < 0.08:
        if scores["convective"] < 0.35:
            selected_regime = "clear"
            scores["clear"] = clamp01(1.0 - max(positive_score, scores["convective"]))
        else:
            selected_regime = "convective"
    elif scores["convective"] >= 0.62 and scores["convective"] >= positive_score:
        selected_regime = "convective"
    remote_score = max(
        scores["distant_target"],
        scores["remote_boundary"],
        scores["cloud_hole"],
    )
    return {
        "selected_regime": selected_regime,
        "scene_scores": scores,
        "local_sky_score": scores["local_deck"],
        "remote_sky_score": remote_score,
        "convective_risk": scores["convective"],
    }


def cloud_phase_diagnosis(
    cloud_ice_kg_kg: float | None,
    cloud_liquid_kg_kg: float | None,
) -> dict[str, Any]:
    ice = max(0.0, float(cloud_ice_kg_kg or 0.0))
    liquid = max(0.0, float(cloud_liquid_kg_kg or 0.0))
    total = ice + liquid
    if total <= 1e-10:
        return {"label": "unknown", "ice_fraction": None, "confidence": 0.0}
    ice_fraction = ice / total
    if ice_fraction >= 0.72:
        label = "ice"
    elif ice_fraction <= 0.28:
        label = "water"
    else:
        label = "mixed"
    return {
        "label": label,
        "ice_fraction": ice_fraction,
        "confidence": clamp01(abs(ice_fraction - 0.5) * 1.7 + 0.25),
    }


def cloud_genus_diagnosis(
    *,
    dominant_pressure_family: str | None,
    cloud_phase: dict[str, Any] | str | None,
    cloud_base_agl_m: float | None,
    cloud_top_agl_m: float | None,
    convective_risk: float,
    model_source: str,
) -> dict[str, Any] | None:
    if dominant_pressure_family is None:
        return None
    compatible_by_family = {
        "high": ["cirrus", "cirrocumulus", "cirrostratus"],
        "middle": ["altocumulus", "altostratus", "nimbostratus"],
        "low": ["stratocumulus", "stratus", "cumulus", "cumulonimbus"],
    }
    if dominant_pressure_family not in compatible_by_family:
        raise ValueError(f"unsupported pressure family: {dominant_pressure_family}")
    phase_label = (
        cloud_phase.get("label") if isinstance(cloud_phase, dict) else cloud_phase
    )
    return {
        "label": "unknown",
        "status": "compatible-only",
        "confidence": 0.0,
        "scoring_effect": False,
        "compatible_genera": list(compatible_by_family[dominant_pressure_family]),
        "reason": "pressure-family-and-column-proxies-cannot-resolve-actual-target-layer-wmo-cloud-genus;compatible-genera-are-conventional-non-probabilistic-and-non-exhaustive",
        "source": "numerical-model-pressure-family-compatible-genera-v1",
        "model_source": str(model_source),
        "evidence": {
            "dominant_pressure_family": dominant_pressure_family,
            "cloud_phase": phase_label,
            "cloud_base_agl_m": cloud_base_agl_m,
            "cloud_top_agl_m": cloud_top_agl_m,
            "convective_risk": float(convective_risk),
        },
    }


def cloud_genus_proxy_diagnosis(
    *,
    pressure_family: str | None,
    cloud_phase: dict[str, Any] | str | None,
    cloud_base_agl_m: float | None,
    cloud_top_agl_m: float | None,
    convective_risk: float,
    model_source: str,
) -> dict[str, Any] | None:
    """Return a bounded model proxy for cloud genus.

    Pressure-level numerical models do not resolve WMO genus directly. This
    output is therefore deliberately separate from ``cloud_genus_diagnosis``:
    it ranks compatible genera for display and downstream inspection, but it
    never changes the fire-cloud score.

    v2 evidence: WMO genus reduces to height family (given), phase (ice vs
    water), and form. Form is discriminated by layer thickness using the
    observed banding from the SunsetBot field tutorial (卷云/卷层云 split at
    2–3 km; 高层云 >1 km vs 高积云 a few hundred metres; 层云 base ≤500 m).
    Thickness comes from the same base/top inputs already carried in
    ``evidence``.
    """
    if pressure_family is None:
        return None
    phase = (
        cloud_phase.get("label")
        if isinstance(cloud_phase, dict)
        else cloud_phase
    ) or "unknown"
    base = float(cloud_base_agl_m) if cloud_base_agl_m is not None else None
    top = float(cloud_top_agl_m) if cloud_top_agl_m is not None else None
    convective = clamp01(convective_risk)
    thickness = (
        float(top - base)
        if (base is not None and top is not None and top >= base)
        else None
    )
    ice_like = phase in {"ice", "mixed"}
    water_like = phase in {"water", "mixed"}
    if pressure_family == "high":
        # 教程经验带：卷云厚 ≤2–3 km，卷层云厚 2–3 km 以上且云顶近对流层顶。
        thin = thickness is not None and thickness <= 2500.0
        thick = thickness is not None and thickness >= 3000.0
        scores = {
            "cirrus": (0.62 if thin else 0.38 if not thick else 0.30)
            if ice_like
            else 0.26,
            "cirrostratus": (0.26 if thin else 0.42 if not thick else 0.56)
            if ice_like
            else 0.30,
            "cirrocumulus": (0.14 if thin else 0.24) if ice_like else 0.24,
        }
    elif pressure_family == "middle":
        # 教程经验带：高积云为几百米厚的过冷水薄层，高层云厚常 >1 km 且
        # 为过冷水+冰晶混合；雨层云需要降水证据，仅对流高企时上调。
        thick = thickness is not None and thickness >= 2000.0
        scores = {
            "altocumulus": (
                0.56 if (water_like and not thick) else 0.24
            ),
            "altostratus": (0.24 if water_like else 0.46) if not thick else 0.52,
            "nimbostratus": clamp01(
                0.10 + 0.22 * convective + (0.10 if thick else 0.0)
            ),
        }
    else:
        # 教程经验带：层云云底 ≤500 m 贴地；层积云 0.5–2.5 km；积雨云云顶
        # ≥5–6 km（不稳定层贯穿对流层）。
        shallow = base is not None and base <= 500.0
        deep = convective >= 0.62 or (
            thickness is not None and thickness >= 3500.0
        )
        scores = {
            "cumulonimbus": (
                clamp01(0.10 + 0.55 * convective) if deep else 0.06
            ),
            "stratus": (
                0.50
                if (shallow and not deep and (thickness is None or thickness <= 700.0))
                else 0.20
            ),
            "stratocumulus": 0.46 if (not deep and not shallow) else 0.24,
            "cumulus": 0.34 if (not shallow and not deep) else 0.18,
        }
    label = max(scores, key=scores.get)
    confidence = clamp01(
        0.18
        + 0.35 * float(scores[label])
        + 0.20 * (1.0 if phase != "unknown" else 0.0)
        + 0.15 * (1.0 if base is not None and top is not None else 0.0)
    )
    return {
        "label": label,
        "status": "proxy",
        "confidence": confidence,
        "scoring_effect": False,
        "candidate_scores": scores,
        "compatible_genera": list(scores),
        "source": "pressure-family+phase+geometry-proxy-v2",
        "model_source": str(model_source),
        "evidence": {
            "pressure_family": pressure_family,
            "cloud_phase": phase,
            "cloud_base_agl_m": base,
            "cloud_top_agl_m": top,
            "convective_risk": convective,
        },
    }


def color_diagnosis(
    *,
    burn_score: float | None = None,
    quality_score: float | None = None,
    vividness_score: float,
    solar_elevation_deg: float,
    aod550: float,
    solar_incoming_illumination_score: float,
    deep_cloud_risk: float,
    cloud_phase: str,
) -> dict[str, Any]:
    strength = clamp01(quality_score if quality_score is not None else burn_score or 0.0)
    vividness = clamp01(vividness_score)
    transmission = clamp01(solar_incoming_illumination_score)
    deep = clamp01(deep_cloud_risk)
    aod = max(0.0, float(aod550))
    if strength < 0.08 or transmission < 0.16 or deep > 0.66:
        color_type = "muted_glow"
    elif solar_elevation_deg > -1.6 and aod < 0.32:
        color_type = "golden"
    elif solar_elevation_deg > -3.8 or cloud_phase == "water":
        color_type = "orange_red"
    else:
        color_type = "deep_red"
    confidence = clamp01(
        0.30 * strength
        + 0.30 * vividness
        + 0.25 * transmission
        + 0.15 * (1.0 - deep)
    )
    return {
        "type": color_type,
        "confidence": confidence,
        "cloud_phase": cloud_phase,
        "source": "instant-quality+solar-elevation+aod+solar-incoming-illumination+cloud-phase",
        "inputs": {
            "quality_strength": strength,
            "brightness_color": vividness,
            "solar_elevation_deg": float(solar_elevation_deg),
            "aod550": aod,
            "solar_incoming_illumination_score": transmission,
            "deep_cloud_risk": deep,
            "cloud_phase": cloud_phase,
        },
    }


def cloud_evolution_diagnosis(
    steps: Sequence[dict[str, Any]],
    best_index: int | None = None,
) -> dict[str, Any]:
    if not steps:
        return {
            "trend": "unknown",
            "target_score_change": 0.0,
            "cloud_hole_closure_risk": 0.0,
            "source": "model-time-interpolation",
        }
    index = (
        max(
            range(len(steps)),
            key=lambda item: float(
                steps[item].get("instant_quality_index")
                if steps[item].get("instant_quality_index") is not None
                else steps[item].get("burn_score") or 0.0
            ),
        )
        if best_index is None else max(0, min(len(steps) - 1, best_index))
    )
    left = steps[max(0, index - 2)]
    right = steps[min(len(steps) - 1, index + 2)]

    def target_score(step: dict[str, Any]) -> float:
        scenes = step.get("scene_scores") or {}
        return max(
            float(step.get("remote_sky_score") or 0.0),
            float(step.get("local_sky_score") or 0.0),
            float(scenes.get("cloud_hole") or 0.0),
        )

    change = target_score(right) - target_score(left)
    if change >= 0.14:
        trend = "forming"
    elif change >= 0.05:
        trend = "strengthening"
    elif change <= -0.14:
        trend = "dissipating"
    elif change <= -0.05:
        trend = "weakening"
    else:
        trend = "steady"
    peak_hole = float((steps[index].get("scene_scores") or {}).get("cloud_hole") or 0.0)
    right_hole = float((right.get("scene_scores") or {}).get("cloud_hole") or 0.0)
    closure_risk = clamp01((peak_hole - right_hole) * 2.2) if peak_hole > 0.12 else 0.0
    return {
        "trend": trend,
        "target_score_change": change,
        "cloud_hole_closure_risk": closure_risk,
        "source": "wind-aware-model-time-interpolation",
        "comparison_start_local": str(left.get("valid_time_local") or ""),
        "comparison_end_local": str(right.get("valid_time_local") or ""),
        "comparison_minutes": abs(
            (
                datetime.fromisoformat(str(right["valid_time_local"]))
                - datetime.fromisoformat(str(left["valid_time_local"]))
            ).total_seconds()
        ) / 60.0,
    }


def score_curve_window(
    steps: Sequence[dict[str, Any]],
    *,
    threshold: float = 0.25,
    regime: str = "unknown",
) -> dict[str, Any] | None:
    if not steps:
        return None
    ordered = sorted(steps, key=lambda step: datetime.fromisoformat(str(step["valid_time_local"])))
    uses_quality = any(step.get("instant_quality_index") is not None for step in ordered)

    def score(step: dict[str, Any]) -> float:
        # A NaN index is truthy and would silently break ``max``'s key
        # comparisons, so non-finite values fall back to 0.0 explicitly.
        if uses_quality:
            value = float(step.get("instant_quality_index") or 0.0)
        else:
            value = float(step.get("burn_score") or 0.0)
        return value if math.isfinite(value) else 0.0

    def active(step: dict[str, Any]) -> bool:
        if uses_quality:
            return bool(step.get("physically_burnable")) and float(
                step.get("burnable_cloud_coverage") or 0.0
            ) >= 0.003
        return score(step) >= threshold

    best = max(ordered, key=score)
    if not any(active(step) for step in ordered):
        return None

    def crossing(left: dict[str, Any], right: dict[str, Any]) -> datetime:
        left_time = datetime.fromisoformat(str(left["valid_time_local"]))
        right_time = datetime.fromisoformat(str(right["valid_time_local"]))
        if uses_quality:
            # Interpolate the 0.003 coverage threshold between the bracketing
            # steps instead of always taking the midpoint: the hardcoded
            # midpoint biased every quality-path window edge by up to half a
            # step and disagreed with the legacy threshold crossing.
            left_value = float(left.get("burnable_cloud_coverage") or 0.0)
            right_value = float(right.get("burnable_cloud_coverage") or 0.0)
            delta = right_value - left_value
            fraction = (
                0.5
                if abs(delta) < 1e-9
                else clamp01((0.003 - left_value) / delta)
            )
            return left_time + (right_time - left_time) * fraction
        left_score = score(left)
        right_score = score(right)
        delta = right_score - left_score
        fraction = 0.5 if abs(delta) < 1e-9 else clamp01((threshold - left_score) / delta)
        return left_time + (right_time - left_time) * fraction

    best_index = ordered.index(best)
    start = datetime.fromisoformat(str(best["valid_time_local"]))
    end = start
    for index in range(best_index, 0, -1):
        current, previous = ordered[index], ordered[index - 1]
        if not active(previous):
            start = crossing(previous, current)
            break
        start = datetime.fromisoformat(str(previous["valid_time_local"]))
    for index in range(best_index, len(ordered) - 1):
        current, following = ordered[index], ordered[index + 1]
        if not active(following):
            end = crossing(current, following)
            break
        end = datetime.fromisoformat(str(following["valid_time_local"]))
    peak = datetime.fromisoformat(str(best["valid_time_local"]))
    return {
        "regime": regime,
        "source": "quality-curve-interpolated" if uses_quality else "score-curve-interpolated",
        "threshold": 0.003 if uses_quality else threshold,
        "start_time_local": start.isoformat(),
        "peak_time_local": peak.isoformat(),
        "end_time_local": end.isoformat(),
        "duration_minutes": max(0.0, (end - start).total_seconds() / 60.0),
    }

"""Pure anomaly selection and counter pairing for existing A/B observations."""

from __future__ import annotations

import math
from typing import Any, Iterable

from .metrics import degradation_percent


def select_slice_candidates(
    slices: list[dict[str, Any]],
    workload_anomaly: bool,
    slice_threshold_pct: float,
    contribution_top_n: int,
) -> list[dict[str, Any]]:
    """Rank degradation and weighted contribution independently, preserving ties."""
    by_degradation = sorted(
        slices,
        key=lambda row: row["cpi_degradation_percent"]
        if row["cpi_degradation_percent"] is not None
        else -math.inf,
        reverse=True,
    )
    degradation_rank = {
        row["slice_id"]: rank for rank, row in enumerate(by_degradation, 1)
    }
    positive_contributors = sorted(
        (row for row in slices if row["weighted_cpi_contribution"] > 0),
        key=lambda row: row["weighted_cpi_contribution"],
        reverse=True,
    )
    contribution_rank = {
        row["slice_id"]: rank for rank, row in enumerate(positive_contributors, 1)
    }
    candidates = []
    for row in slices:
        reasons = []
        degradation = row["cpi_degradation_percent"]
        if degradation is not None and degradation > slice_threshold_pct:
            reasons.append("slice_cpi_degradation")
        rank = contribution_rank.get(row["slice_id"])
        if workload_anomaly and rank is not None and rank <= contribution_top_n:
            reasons.append("top_weighted_cpi_contributor")
        if reasons:
            candidates.append({
                **row,
                "reasons": reasons,
                "degradation_rank": degradation_rank[row["slice_id"]],
                "contribution_rank": rank,
            })
    candidates.sort(
        key=lambda row: (
            "top_weighted_cpi_contributor" not in row["reasons"],
            -row["weighted_cpi_contribution"],
            -(
                row["cpi_degradation_percent"]
                if row["cpi_degradation_percent"] is not None
                else -math.inf
            ),
        )
    )
    return candidates


def counter_clues(
    rows: Iterable[dict[str, Any]],
    run_a: str,
    run_b: str,
    threshold_pct: float,
    top_n: int,
) -> list[dict[str, Any]]:
    """Pair available counters with matching semantics and measurement windows."""
    paired: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for row in rows:
        paired.setdefault((row["metric_id"], row["semantic_version"]), {})[
            row["run_id"]
        ] = row
    clues = []
    for pair in paired.values():
        a = pair.get(run_a)
        b = pair.get(run_b)
        if (
            not a
            or not b
            or a["availability"] != "available"
            or b["availability"] != "available"
            or a["value"] is None
            or b["value"] is None
            or a["window_id"] != b["window_id"]
        ):
            continue
        change = degradation_percent(float(a["value"]), float(b["value"]), a["direction"])
        if change is None or abs(change) < threshold_pct:
            continue
        clues.append({
            "metric_id": a["metric_id"],
            "semantic_version": a["semantic_version"],
            "display_name": a["display_name"],
            "category": a["category"],
            "unit": a["unit"],
            "direction": a["direction"],
            "value_a": a["value"],
            "value_b": b["value"],
            "delta": float(b["value"]) - float(a["value"]),
            "directional_degradation_percent": change,
            "window_id": a["window_id"],
        })
    clues.sort(key=lambda row: abs(row["directional_degradation_percent"]), reverse=True)
    return clues[:top_n]

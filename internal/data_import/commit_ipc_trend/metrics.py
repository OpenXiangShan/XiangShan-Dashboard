"""Performance calculations used by data import and A/B analysis."""

from __future__ import annotations

import math
from typing import Iterable, Mapping, Any


COVERAGE_TOLERANCE = 1e-5


def aggregate_cpi(
    rows: Iterable[Mapping[str, Any]], total_weight: float
) -> dict[str, float | int | str | None]:
    rows = list(rows)
    valid = [row for row in rows if row.get("status") == "valid" and row.get("cpi") is not None]
    numerator = math.fsum(float(row["weight"]) * float(row["cpi"]) for row in valid)
    covered_weight = math.fsum(float(row["weight"]) for row in valid)
    coverage = covered_weight / total_weight if total_weight else 0.0
    complete = len(valid) == len(rows) and abs(covered_weight - total_weight) <= COVERAGE_TOLERANCE
    value = numerator if complete and total_weight else None
    return {
        "weighted_cpi": value,
        "equivalent_ipc": 1.0 / value if value else None,
        "numerator": numerator,
        "denominator": total_weight,
        "coverage_weight": coverage,
        "valid_member_count": len(valid),
        "status": "valid" if complete else "partial",
    }


def degradation_percent(
    base: float, current: float, direction: str, near_zero: float = 1e-12
) -> float | None:
    if abs(base) < near_zero:
        return None
    if direction == "higher_is_better":
        return (base / current - 1.0) * 100.0 if abs(current) >= near_zero else None
    if direction == "lower_is_better":
        return (current / base - 1.0) * 100.0
    raise ValueError(f"unknown metric direction: {direction}")


def cpi_contributions(
    rows_a: Iterable[Mapping[str, Any]],
    rows_b: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    by_a = {str(row["slice_id"]): row for row in rows_a}
    result: list[dict[str, Any]] = []
    for row_b in rows_b:
        row_a = by_a.get(str(row_b["slice_id"]))
        if not row_a or row_a.get("cpi") is None or row_b.get("cpi") is None:
            continue
        cpi_a = float(row_a["cpi"])
        cpi_b = float(row_b["cpi"])
        weight = float(row_b["weight"])
        result.append(
            {
                "slice_id": row_b["slice_id"],
                "slice": row_b["slice"],
                "checkpoint": row_b["checkpoint"],
                "weight": weight,
                "cpi_a": cpi_a,
                "cpi_b": cpi_b,
                "cpi_delta": cpi_b - cpi_a,
                "cpi_degradation_percent": degradation_percent(
                    cpi_a, cpi_b, "lower_is_better"
                ),
                "weighted_cpi_contribution": weight * (cpi_b - cpi_a),
                "source_out_uri_a": row_a.get("source_out_uri"),
                "source_out_uri_b": row_b.get("source_out_uri"),
            }
        )
    return result

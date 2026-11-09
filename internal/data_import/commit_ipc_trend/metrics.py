"""Weighted CPI aggregation for imported performance results."""

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

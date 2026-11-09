#!/usr/bin/env python3
"""Summarize a comparable A/B regression with evidence-backed counter changes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from commit_ipc_trend.service import TrendService  # noqa: E402
from commit_ipc_trend.store import Store  # noqa: E402


def analyze(store: Store, a: str, b: str, workload: str, limit: int) -> dict:
    comparison = TrendService(store).compare_points(a, b, object_id=workload)
    if comparison["status"] != "comparable":
        return {"status": "incomparable", "comparison": comparison, "insights": []}
    slices = [s for s in comparison["slices"] if s["weighted_cpi_contribution"] > 0][:limit]
    evidence = []
    for s in slices:
        rows = store.connection.execute(
            """SELECT c.run_id, c.metric_id, c.semantic_version, c.value,
                      c.availability, c.window_id, c.source_uri, m.unit
                 FROM counter_values c
                 JOIN metric_definitions m USING (metric_id, semantic_version)
                WHERE c.slice_id = ? AND c.run_id IN (?, ?)""",
            (s["slice_id"], a, b),
        ).fetchall()
        paired = {}
        for row in rows:
            paired.setdefault((row["metric_id"], row["semantic_version"]), {})[row["run_id"]] = dict(row)
        counters = []
        for (metric, version), pair in paired.items():
            base, target = pair.get(a), pair.get(b)
            if not base or not target or base["availability"] != "available" or target["availability"] != "available":
                continue
            if base["window_id"] != target["window_id"] or base["value"] is None or target["value"] is None:
                continue
            counters.append({"metric_id": metric, "semantic_version": version,
                             "a": base["value"], "b": target["value"],
                             "delta": target["value"] - base["value"],
                             "unit": base["unit"], "window_id": base["window_id"],
                             "source_a": base["source_uri"], "source_b": target["source_uri"]})
        evidence.append({"slice": s["slice"], "weight": s["weight"],
                         "weighted_cpi_contribution": s["weighted_cpi_contribution"],
                         "counter_pairs": sorted(counters, key=lambda item: abs(item["delta"]), reverse=True)})
    # This is a lead for investigation, not a conclusion about causal RTL behavior.
    counts = {}
    for slice_data in evidence:
        for metric in slice_data["counter_pairs"]:
            if metric["delta"]:
                counts.setdefault(metric["metric_id"], {"up": 0, "down": 0})[
                    "up" if metric["delta"] > 0 else "down"] += 1
    insights = [{"kind": "common_counter_direction", "metric_id": metric,
                 "observations": direction, "qualifier": "raw same-window count; check denominator and RTL event definition"}
                for metric, direction in counts.items() if max(direction.values()) >= 2]
    insights.sort(key=lambda item: -max(item["observations"].values()))
    return {"status": "analyzed" if comparison["mode"] == "full_slice_set" else "diagnostic_subset",
            "comparison": comparison, "regressed_slices": evidence,
            "insights": insights}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", type=Path, required=True)
    p.add_argument("--a", required=True)
    p.add_argument("--b", required=True)
    p.add_argument("--workload", required=True)
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    with Store(args.db, read_only=True) as store:
        result = analyze(store, args.a, args.b, args.workload, args.top)
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload)
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

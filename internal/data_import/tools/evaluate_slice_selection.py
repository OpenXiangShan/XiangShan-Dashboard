#!/usr/bin/env python3
"""Evaluate slice selection policies on chronological Commit holdouts."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from commit_ipc_trend.replay import evaluate_selection
from commit_ipc_trend.sensitivity import (
    DEFAULT_MIN_COVERAGE,
    DEFAULT_THRESHOLD_CPI,
    build_parent_transitions,
    load_change_labels,
    load_inputs,
)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    rows = list(rows)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--comparison-key", action="append", default=[])
    parser.add_argument("--slice-set", action="append", default=[], dest="slice_sets")
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--train-count", type=int)
    parser.add_argument("--train-ratio", type=float, default=0.6)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--random-repeats", type=int, default=10)
    parser.add_argument("--threshold-cpi", type=float, default=DEFAULT_THRESHOLD_CPI)
    parser.add_argument("--min-coverage", type=float, default=DEFAULT_MIN_COVERAGE)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--bootstrap-repeats", type=int, default=200)
    args = parser.parse_args()
    if args.train_count is not None and args.train_count < 1:
        raise SystemExit("--train-count must be positive")
    labels = load_change_labels(args.labels)
    with sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        data = load_inputs(
            connection,
            comparison_keys=args.comparison_key,
            slice_set_ids=args.slice_sets,
            statuses=("published",),
        )
    transitions = build_parent_transitions(
        data["runs"], data["observations"], data["members"], min_coverage=args.min_coverage
    )
    result = evaluate_selection(
        transitions,
        labels,
        train_count=args.train_count,
        train_ratio=args.train_ratio,
        top_k=args.top_k,
        random_repeats=args.random_repeats,
        threshold_cpi=args.threshold_cpi,
        seed=args.seed,
        bootstrap_repeats=args.bootstrap_repeats,
    )
    result["input"] = {
        "database": str(args.db.resolve()),
        "comparison_keys": sorted(args.comparison_key),
        "slice_sets": sorted(args.slice_sets),
        "run_count": len(data["runs"]),
        "transition_count": len(transitions),
        "labels_file": str(args.labels) if args.labels else None,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    _write_json(args.output / "replay.json", result)
    _write_csv(args.output / "replay_detail.csv", result["detail"])
    _write_csv(args.output / "replay_summary.csv", result["summary"])
    report = [
        "# Slice selection historical replay",
        "",
        f"status: `{result['status']}`",
        f"runs: {len(data['runs'])}; parent transitions: {len(transitions)}; top-k: {args.top_k}",
        "",
        "| Strategy | Test transitions | Recall (95% CI) | Contribution capture (95% CI) | Direction accuracy | Cost ratio |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in result["summary"]:
        report.append(
            f"| `{row['strategy']}` | {row['test_transitions']} | "
            f"{row['mean_major_regression_recall']} [{row['recall_ci_low']}, {row['recall_ci_high']}] | "
            f"{row['mean_positive_contribution_capture']} [{row['capture_ci_low']}, {row['capture_ci_high']}] | "
            f"{row['mean_direction_accuracy']} | {row['mean_cost_ratio']} |"
        )
    (args.output / "REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "summary_rows": len(result["summary"]), "detail_rows": len(result["detail"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

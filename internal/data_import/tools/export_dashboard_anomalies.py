#!/usr/bin/env python3
"""Export deterministic performance anomalies for the static dashboard."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from commit_ipc_trend.service import TrendService  # noqa: E402
from commit_ipc_trend.store import Store  # noqa: E402


def _run_bounds(store: Store) -> tuple[str, str]:
    rows = store.connection.execute(
        """
        SELECT c.short_sha
          FROM runs r JOIN commits c ON c.commit_sha = r.commit_sha
         WHERE r.status = 'published'
         ORDER BY c.commit_epoch, r.snapshot_at
        """
    ).fetchall()
    if not rows:
        raise RuntimeError("database has no published runs")
    return str(rows[0][0]), str(rows[-1][0])


def export_dashboard_anomalies(
    database: Path,
    output: Path,
    current: str | None = None,
    fixed_baseline: str | None = None,
    workload_threshold_pct: float = 0.5,
    slice_threshold_pct: float = 0.5,
    contribution_top_n: int = 10,
    counter_top_n: int = 5,
    counter_change_threshold_pct: float = 5.0,
    git_repo: Path | None = None,
) -> dict[str, Any]:
    with Store(database, read_only=True) as store:
        store.validate_schema()
        first, latest = _run_bounds(store)
        report = TrendService(store).detect_anomalies(
            current or latest,
            fixed_baseline=fixed_baseline or first,
            workload_threshold_pct=workload_threshold_pct,
            slice_threshold_pct=slice_threshold_pct,
            contribution_top_n=contribution_top_n,
            counter_top_n=counter_top_n,
            counter_change_threshold_pct=counter_change_threshold_pct,
            git_repo=git_repo,
        )
    payload = {
        "schema_version": "performance-anomalies/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **report,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "database": str(database),
        "output": str(output),
        "current": report["current"]["short_sha"],
        "comparison_count": report["comparison_count"],
        "comparisons": [
            {"label": row["label"], **row["summary"]}
            for row in report["comparisons"]
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--current")
    parser.add_argument("--fixed-baseline")
    parser.add_argument("--git-repo", type=Path, help="select a tested first-parent ancestor")
    parser.add_argument("--workload-threshold-pct", type=float, default=0.5)
    parser.add_argument("--slice-threshold-pct", type=float, default=0.5)
    parser.add_argument("--contribution-top-n", type=int, default=10)
    parser.add_argument("--counter-top-n", type=int, default=5)
    parser.add_argument("--counter-change-threshold-pct", type=float, default=5.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.db.is_file():
        raise SystemExit(f"database does not exist: {args.db}")
    summary = export_dashboard_anomalies(
        args.db,
        args.output,
        current=args.current,
        fixed_baseline=args.fixed_baseline,
        workload_threshold_pct=args.workload_threshold_pct,
        slice_threshold_pct=args.slice_threshold_pct,
        contribution_top_n=args.contribution_top_n,
        counter_top_n=args.counter_top_n,
        counter_change_threshold_pct=args.counter_change_threshold_pct,
        git_repo=args.git_repo,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

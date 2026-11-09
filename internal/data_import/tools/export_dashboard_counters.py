#!/usr/bin/env python3
"""Export registered counter data from SQLite for the static dashboard."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def rows(connection: sqlite3.Connection, sql: str) -> list[dict[str, object]]:
    return [dict(row) for row in connection.execute(sql)]


def export_dashboard_counters(database: Path, output: Path) -> dict[str, object]:
    """Export every registered counter observation for the static dashboard."""
    if not database.is_file():
        raise FileNotFoundError(f"database does not exist: {database}")
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        metrics = rows(
            connection,
            """
            SELECT metric_id, semantic_version, display_name, category, kind, unit,
                   direction, source_match_json, aggregation_rule
              FROM metric_definitions
             ORDER BY category, display_name, metric_id
            """,
        )
        for metric in metrics:
            metric["source_match"] = json.loads(str(metric.pop("source_match_json")))
        commits = rows(
            connection,
            """
            SELECT r.run_id, c.commit_sha, c.short_sha AS short_commit, c.commit_time,
                   c.commit_epoch, c.subject, r.config, r.status
              FROM runs r JOIN commits c ON c.commit_sha = r.commit_sha
             ORDER BY c.commit_epoch, r.snapshot_at
            """,
        )
        slices = rows(
            connection,
            """
            SELECT s.slice_id, s.slice_name AS slice, s.benchmark, s.workload,
                   s.checkpoint, sm.weight, sm.ordinal
              FROM slices s
              JOIN slice_set_members sm ON sm.slice_id = s.slice_id
             GROUP BY s.slice_id
             ORDER BY s.workload, sm.ordinal
            """,
        )
        values = rows(
            connection,
            """
            SELECT cv.run_id, c.short_sha AS short_commit, s.slice_name AS slice,
                   cv.metric_id, cv.semantic_version, cv.value, cv.availability,
                   cv.window_id, cv.dump_time
              FROM counter_values cv
              JOIN runs r ON r.run_id = cv.run_id
              JOIN commits c ON c.commit_sha = r.commit_sha
              JOIN slices s ON s.slice_id = cv.slice_id
             ORDER BY c.commit_epoch, s.checkpoint, cv.metric_id
            """,
        )
    finally:
        connection.close()
    payload = {
        "schema_version": "counter-dashboard/v1",
        "database": str(database),
        "scope": "all imported runs and slices",
        "metric_count": len(metrics),
        "observation_count": len(values),
        "metrics": metrics,
        "commits": commits,
        "slices": slices,
        "values": values,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    return {
        "output": str(output), "metrics": len(metrics), "slices": len(slices),
        "commits": len(commits), "values": len(values),
        "available": sum(row["availability"] == "available" for row in values),
    }


def main() -> int:
    args = parse_args()
    try:
        summary = export_dashboard_counters(args.db, args.output)
    except FileNotFoundError as error:
        raise SystemExit(str(error)) from error
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Export all selected SQLite measurements, retaining missing values."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any, Sequence

from .schema import canonical_hash

DEFAULT_PROFILE = "original-verified-spec06"
ALL_PROFILE = "all-measurements"
SUMMARY_FIELDS = (
    "run_count",
    "slice_observations",
    "valid_observations",
    "counter_count",
    "available_counters",
)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Publish complete JSON so concurrent page loads never read a partial file."""
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _group_runs(
    connection: sqlite3.Connection, runs: Sequence[str], configs: Sequence[str],
) -> dict[str, list[dict[str, Any]]]:
    grouped = defaultdict(list)
    for row in connection.execute(
        """SELECT run_id, commit_sha, config, snapshot_at, source_uri, status,
                  comparison_key, slice_set_id, experiment_json
             FROM runs r
             ORDER BY snapshot_at, run_id"""
    ):
        run = dict(row)
        if runs and not any(
            run["run_id"] == value or Path(run["source_uri"]).name == value
            or run["commit_sha"].startswith(value)
            for value in runs
        ):
            continue
        if configs and run["config"] not in configs:
            continue
        experiment = json.loads(run.pop("experiment_json"))
        profile = (
            experiment.get("comparison_dataset")
            or experiment.get("checkpoint_profile")
            or experiment.get("checkpoint_identity")
            or DEFAULT_PROFILE
        )
        run["conditions_verified"] = (experiment.get("comparison_conditions") != "unverified"
                                      and experiment.get("comparison_status") != "provisional")
        run["runtime_conditions"] = {key: experiment.get(key) for key in (
            "emulator", "warmup", "max_instr", "max_cycles", "dram_config",
            "cpu_frequency_mhz", "dram_frequency_mhz", "build_options",
        )}
        for field in (
            "comparison_status",
            "comparison_policy",
            "comparison_label",
            "score_coverage",
            "score_publication_status",
            "actions_run_id",
            "actions_run_number",
            "actions_url",
            "workflow",
            "ci_failed_points",
            "observed_emulators",
        ):
            run[field] = experiment.get(field)
        grouped[profile].append(run)
    return grouped


def _dataset(
    connection: sqlite3.Connection,
    profile: str,
    runs: list[dict[str, Any]],
    metric_index: dict[str, int],
) -> tuple[dict[str, Any], dict[str, Any]]:
    runs = [dict(run) for run in runs]
    slices, slice_index = [], {}
    counter_count = available_count = result_count = valid_count = 0
    for run in runs:
        run["scores"] = [
            dict(row)
            for row in connection.execute(
                """SELECT a.object_id, a.value, a.status, a.source_uri,
                          CASE WHEN a.level='benchmark' THEN a.coverage_weight ELSE
                            (SELECT MIN(b.coverage_weight) FROM aggregate_results b
                              WHERE b.run_id=a.run_id AND b.level='benchmark'
                                AND b.metric_id='score' AND b.source_uri=a.source_uri)
                          END AS minimum_coverage
                     FROM aggregate_results a
                    WHERE a.run_id=? AND ((a.level='suite' AND a.metric_id='score_per_ghz')
                       OR (a.level='benchmark' AND a.metric_id='score'))""",
                (run["run_id"],),
            )
        ]
        scopes = {match.group(1) + "c" for score in run["scores"]
                  if (match := re.search(r"-(\d+(?:\.\d+)?)c\.txt$", score["source_uri"]))}
        if len(scopes) == 1:
            run["score_coverage"] = scopes.pop()
        scope_points = None
        if run.get("score_coverage") == "0.3c":
            source = Path(connection.execute("SELECT source_uri FROM slice_sets WHERE slice_set_id=?",
                                             (run["slice_set_id"],)).fetchone()[0])
            if source.name == "checkpoints_all.json":
                subset = json.loads(source.with_name("checkpoints_cov0.3.json").read_text())
                scope_points = {(workload, int(checkpoint)) for workload, entry in subset.items()
                                for checkpoint in entry["points"]}
        members = list(
            connection.execute(
                """SELECT s.slice_id, s.slice_name AS name, s.benchmark, s.workload, s.checkpoint,
                      sm.weight, sm.ordinal, COALESCE(sr.status, 'missing') AS status, sr.ipc_computed,
                      sr.window_id, sr.seed, s.checkpoint_identity
                 FROM runs r JOIN slice_set_members sm USING (slice_set_id)
                 JOIN slices s USING (slice_id)
                 LEFT JOIN slice_results sr
                   ON sr.run_id=r.run_id AND sr.slice_id=s.slice_id
                WHERE r.run_id=? ORDER BY s.workload, sm.ordinal""",
                (run["run_id"],),
            )
        )
        if scope_points is not None:
            members = [row for row in members if (row["workload"], row["checkpoint"]) in scope_points]
            if len(members) != len(scope_points):
                raise ValueError(f"0.3cov checkpoint membership differs from its profile: {run['run_id']}")
            run["slice_set_id"] = canonical_hash([(row["slice_id"], row["weight"]) for row in members])
        member_ids = {row["slice_id"] for row in members}
        run["results"] = []
        for row in members:
            if row["slice_id"] not in slice_index:
                slice_index[row["slice_id"]] = len(slices)
                slices.append(
                    {
                        key: row[key]
                        for key in (
                            "slice_id",
                            "name",
                            "benchmark",
                            "workload",
                            "checkpoint",
                            "checkpoint_identity",
                            "weight",
                            "ordinal",
                        )
                    }
                )
            valid = (row["status"] == "valid" and row["ipc_computed"] is not None
                     and math.isfinite(row["ipc_computed"]) and row["ipc_computed"] > 0)
            run["results"].append(
                [
                    slice_index[row["slice_id"]],
                    row["ipc_computed"] if valid else None,
                    row["status"], row["window_id"], row["seed"], row["weight"],
                ]
            )
        run["valid_slices"] = sum(row[1] is not None for row in run["results"])
        run["expected_slices"] = len(members)
        if scope_points is not None and run["status"] in {"published", "partial"}:
            run["status"] = "published" if run["valid_slices"] == len(members) else "partial"
        result_count += len(members)
        valid_count += run["valid_slices"]
        run["workloads"] = [
            dict(row)
            for row in connection.execute(
                """SELECT object_id AS workload, value AS weighted_ipc, status,
                      coverage_weight, valid_member_count, total_member_count
                 FROM aggregate_results WHERE run_id=? AND level='workload'
                   AND metric_id='equivalent_ipc' ORDER BY object_id""",
                (run["run_id"],),
            )
        ]
        if scope_points is not None:
            workloads = defaultdict(list)
            for row in members:
                workloads[row["workload"]].append(row)
            run["workloads"] = []
            for workload, points in sorted(workloads.items()):
                valid = [row for row in points if row["status"] == "valid" and row["ipc_computed"]]
                total = math.fsum(row["weight"] for row in points)
                covered = math.fsum(row["weight"] for row in valid)
                weighted_cpi = math.fsum(row["weight"] / row["ipc_computed"] for row in valid)
                complete = len(valid) == len(points)
                run["workloads"].append(dict(workload=workload,
                    weighted_ipc=total / weighted_cpi if complete and weighted_cpi else None,
                    status="valid" if complete else "partial", coverage_weight=covered / total,
                    valid_member_count=len(valid), total_member_count=len(points)))
        # Indexed columns keep the full counter export small enough for the browser.
        run["counters"] = [
            [
                slice_index[row["slice_id"]],
                metric_index[row["metric_id"]],
                row["value"],
                row["availability"],
                row["window_id"],
                row["dump_time"], row["semantic_version"],
            ]
            for row in connection.execute(
                """SELECT slice_id, metric_id, value, availability, window_id, dump_time, semantic_version
                     FROM counter_values WHERE run_id=?""",
                (run["run_id"],),
            ) if row["slice_id"] in member_ids
        ]
        counter_count += len(run["counters"])
        available_count += sum(row[3] == "available" for row in run["counters"])

    suite_names = sorted({row["object_id"] for run in runs for row in run["scores"]})
    entry = {
        "id": hashlib.sha256(profile.encode()).hexdigest()[:16],
        "profile": profile,
        "label": "全部测量" if profile == ALL_PROFILE else "SPEC2006 GCC16 · 主线（0.3cov / 1.0cov）"
        if profile == "spec06-gcc16-mainline" else runs[0].get("comparison_label")
        or ("原有 SPEC06 可比观测" if profile == DEFAULT_PROFILE else profile),
        "suite": next(
            (name for name in suite_names if name.removeprefix("SPEC").isdigit()),
            suite_names[0] if suite_names else "SPEC",
        ),
        "score_suites": suite_names,
        "run_count": len(runs),
        "commit_count": len({run["commit_sha"] for run in runs}),
        "slice_count": len(slices),
        "expected_observations": result_count,
        "valid_observations": valid_count,
        "counter_count": counter_count,
        "available_counters": available_count,
    }
    return entry, {"slices": slices, "runs": runs}


def export_history(
    database: Path,
    output: Path,
    *,
    runs: Sequence[str] = (),
    configs: Sequence[str] = (),
) -> dict[str, int]:
    """Export selected runs; keep partial and metadata-only measurements visible."""
    output.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        commits = [
            dict(row)
            for row in connection.execute(
                "SELECT *, short_sha AS short_commit FROM commits ORDER BY commit_epoch, commit_sha"
            )
        ]
        metrics = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM metric_definitions ORDER BY category, display_name, metric_id"
            )
        ]
        for metric in metrics:
            metric["source_match"] = json.loads(metric.pop("source_match_json"))
        metric_index = {row["metric_id"]: index for index, row in enumerate(metrics)}
        grouped = _group_runs(connection, runs, configs)
        measured_shas = {run["commit_sha"] for group in grouped.values() for run in group}
        commits = [commit for commit in commits if commit["commit_sha"] in measured_shas]
        for index, commit in enumerate(commits, 1):
            commit["commit_order"] = index

        catalog = []
        for profile, measurements in sorted(grouped.items()):
            entry, payload = _dataset(connection, profile, measurements, metric_index)
            _write_json(output / (entry["id"] + ".json"), payload)
            catalog.append(entry)
        totals = {
            "run_count": sum(entry["run_count"] for entry in catalog),
            "slice_observations": sum(entry["expected_observations"] for entry in catalog),
            "valid_observations": sum(entry["valid_observations"] for entry in catalog),
            "counter_count": sum(entry["counter_count"] for entry in catalog),
            "available_counters": sum(entry["available_counters"] for entry in catalog),
        }
        measurements = sorted(
            (run for group in grouped.values() for run in group),
            key=lambda run: (run["snapshot_at"], run["run_id"]),
        )
        if measurements:
            entry, payload = _dataset(connection, ALL_PROFILE, measurements, metric_index)
            _write_json(output / (entry["id"] + ".json"), payload)
            catalog.insert(0, entry)
        manifest = {
            "schema_version": "mainline-history-dashboard/v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "database": str(database.resolve()),
            "commits": commits,
            "metrics": metrics,
            "datasets": catalog,
            "default_dataset": next((entry["id"] for entry in catalog
                if entry["profile"] == "spec06-gcc16-mainline"), catalog[0]["id"] if catalog else None),
            **totals,
        }
        _write_json(output / "manifest.json", manifest)
    return {key: manifest[key] for key in SUMMARY_FIELDS}

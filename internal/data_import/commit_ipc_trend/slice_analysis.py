"""Database-backed SPEC06 slice IPC trend analysis.

The regular dashboard deliberately admits only complete published runs.  This
module serves the narrower question of whether a particular slice was measured
for a commit, so a run may contribute its valid slice results even when the
rest of SPEC06 is missing.
"""

from __future__ import annotations

from collections import defaultdict
import math
import sqlite3
from typing import Any, Iterable


def _is_spec06(row: sqlite3.Row) -> bool:
    name = str(row["slice_set_name"] or "").lower()
    benchmark_type = str(row["benchmark_type"] or "").lower()
    return "spec06" in name or benchmark_type.startswith("spec06")


def load_spec06_observations(
    connection: sqlite3.Connection,
    *,
    statuses: Iterable[str] | None = None,
    slice_set_ids: Iterable[str] = (),
    comparison_keys: Iterable[str] = (),
    runs: Iterable[str] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load all valid SPEC06 slice IPC observations from an SQLite snapshot.

    ``statuses`` is an optional presentation filter.  By default every run
    status is considered; rows with no valid slice IPC are naturally absent.
    Slice-set and comparison-key filters keep unlike experiments separate.
    """

    run_rows = connection.execute(
        """
        SELECT r.run_id, r.commit_sha, r.config, r.snapshot_at, r.source_uri,
               r.status, r.comparison_key, r.slice_set_id, r.imported_at,
               r.experiment_json, c.short_sha, c.commit_time, c.commit_epoch,
               c.subject, ss.name AS slice_set_name,
               json_extract(r.experiment_json, '$.benchmark_type') AS benchmark_type
          FROM runs r
          JOIN commits c ON c.commit_sha = r.commit_sha
          JOIN slice_sets ss ON ss.slice_set_id = r.slice_set_id
         ORDER BY c.commit_epoch, r.snapshot_at, r.run_id
        """
    ).fetchall()
    status_set = {statuses} if isinstance(statuses, str) else set(statuses or ())
    slice_set_set = {slice_set_ids} if isinstance(slice_set_ids, str) else set(slice_set_ids)
    comparison_set = {comparison_keys} if isinstance(comparison_keys, str) else set(comparison_keys)
    run_set = {runs} if isinstance(runs, str) else set(runs)
    selected_runs: list[dict[str, Any]] = []
    for row in run_rows:
        if not _is_spec06(row):
            continue
        if status_set and row["status"] not in status_set:
            continue
        if slice_set_set and row["slice_set_id"] not in slice_set_set:
            continue
        if comparison_set and row["comparison_key"] not in comparison_set:
            continue
        if run_set and row["run_id"] not in run_set and not any(
            str(row["commit_sha"]).startswith(value) for value in run_set
        ):
            continue
        run = dict(row)
        # The raw experiment JSON can be large; analysis outputs only need the
        # stable run metadata and the explicit status/coverage fields below.
        run.pop("experiment_json", None)
        selected_runs.append(run)

    if not selected_runs:
        return [], []
    placeholders = ",".join("?" for _ in selected_runs)
    observations = [
        dict(row)
        for row in connection.execute(
            f"""
            SELECT r.run_id, r.commit_sha, r.config, r.snapshot_at, r.status,
                   r.comparison_key, r.slice_set_id, c.short_sha,
                   c.commit_time, c.commit_epoch, c.subject,
                   ss.name AS slice_set_name, s.slice_id, s.benchmark,
                   s.workload, s.slice_name AS slice, s.checkpoint,
                   m.weight, m.ordinal, sr.status AS slice_status,
                   sr.instructions, sr.cycles, sr.ipc_reported,
                   sr.ipc_computed, sr.cpi, sr.source_out_uri
              FROM runs r
              JOIN commits c ON c.commit_sha = r.commit_sha
              JOIN slice_sets ss ON ss.slice_set_id = r.slice_set_id
              JOIN slice_results sr ON sr.run_id = r.run_id
              JOIN slices s ON s.slice_id = sr.slice_id
              JOIN slice_set_members m
                ON m.slice_set_id = r.slice_set_id AND m.slice_id = sr.slice_id
             WHERE r.run_id IN ({placeholders})
               AND sr.status = 'valid'
               AND sr.ipc_computed IS NOT NULL
               AND sr.ipc_computed > 0
             ORDER BY c.commit_epoch, r.snapshot_at, r.run_id, m.ordinal
            """,
            [row["run_id"] for row in selected_runs],
        )
    ]
    valid_counts = defaultdict(int)
    for row in observations:
        valid_counts[row["run_id"]] += 1
    for row in selected_runs:
        row["valid_slices"] = valid_counts[row["run_id"]]
        row["expected_slices"] = connection.execute(
            "SELECT count(*) FROM slice_set_members WHERE slice_set_id=?",
            (row["slice_set_id"],),
        ).fetchone()[0]
    # Keep only runs with at least one usable IPC point.
    selected_runs = [row for row in selected_runs if row["valid_slices"]]
    selected_ids = {row["run_id"] for row in selected_runs}
    return selected_runs, [row for row in observations if row["run_id"] in selected_ids]


def deduplicate_runs(
    runs: list[dict[str, Any]], observations: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Choose one observation per commit inside each comparison group.

    Coverage is preferred first, then published status, then the newest
    snapshot.  This keeps a partial retry useful without allowing a less
    complete duplicate to hide a better slice set.
    """

    chosen: dict[tuple[str, str, str], dict[str, Any]] = {}
    for run in runs:
        key = (run["slice_set_id"], run["comparison_key"], run["commit_sha"])
        old = chosen.get(key)
        rank = (
            int(run["valid_slices"]),
            int(run["status"] == "published"),
            str(run.get("snapshot_at") or ""),
            str(run["run_id"]),
        )
        old_rank = (
            int(old["valid_slices"]),
            int(old["status"] == "published"),
            str(old.get("snapshot_at") or ""),
            str(old["run_id"]),
        ) if old else None
        if old is None or rank > old_rank:
            chosen[key] = run
    selected = sorted(
        chosen.values(),
        key=lambda row: (row["slice_set_id"], row["comparison_key"],
                         row["commit_epoch"], row.get("snapshot_at") or "", row["run_id"]),
    )
    selected_ids = {row["run_id"] for row in selected}
    return selected, [row for row in observations if row["run_id"] in selected_ids]


def _pct(first: float | None, last: float | None) -> float | None:
    if first is None or last is None or first == 0:
        return None
    return (last / first - 1.0) * 100.0


def build_relative_ipc_matrix(
    runs: list[dict[str, Any]], observations: list[dict[str, Any]]
) -> dict[tuple[str, str], float | None]:
    """Return IPC changes relative to each slice's first valid observation.

    A missing slice in the first run must remain missing for that run, while a
    later first observation becomes that slice's own baseline.  Using one
    group-wide baseline silently turns valid late observations into blanks.
    Keys use ``slice_id`` and ``run_id`` so labels cannot change the lookup.
    """

    ordered_runs = sorted(
        runs,
        key=lambda row: (int(row.get("commit_epoch", 0)),
                         row.get("snapshot_at") or "", row["run_id"]),
    )
    values: dict[tuple[str, str], float] = {}
    by_slice: dict[str, list[tuple[dict[str, Any], float]]] = defaultdict(list)
    for row in observations:
        value = row.get("ipc_computed")
        if value is None:
            continue
        value = float(value)
        if not math.isfinite(value) or value <= 0:
            continue
        values[(str(row["slice_id"]), str(row["run_id"]))] = value
        by_slice[str(row["slice_id"])].append((row, value))

    baselines = {
        slice_id: next(
            (values[(slice_id, str(run["run_id"]))] for run in ordered_runs
             if (slice_id, str(run["run_id"])) in values),
            None,
        )
        for slice_id in by_slice
    }
    output: dict[tuple[str, str], float | None] = {}
    for slice_id in by_slice:
        baseline = baselines[slice_id]
        for run in ordered_runs:
            key = (slice_id, str(run["run_id"]))
            value = values.get(key)
            output[key] = _pct(baseline, value) if value is not None else None
    return output


def analyze_slice_trends(
    runs: list[dict[str, Any]], observations: list[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    """Return deduplicated points, per-slice summaries, transitions and groups."""

    selected_runs, selected_observations = deduplicate_runs(runs, observations)
    grouped_runs: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in selected_runs:
        grouped_runs[(row["slice_set_id"], row["comparison_key"])].append(row)
    for group in grouped_runs.values():
        group.sort(key=lambda row: (row["commit_epoch"], row.get("snapshot_at") or "", row["run_id"]))

    observations_by_group: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in selected_observations:
        observations_by_group[(row["slice_set_id"], row["comparison_key"])].append(row)

    summaries: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    for group_key, group in grouped_runs.items():
        group_observations = observations_by_group[group_key]
        by_slice: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        for row in group_observations:
            by_slice[row["slice_id"]][row["run_id"]] = row
        members = {(row["slice_id"], row["slice"], row["benchmark"], row["workload"],
                    row["checkpoint"], row["weight"], row["ordinal"])
                   for row in group_observations}
        for slice_id, slice_name, benchmark, workload, checkpoint, weight, ordinal in sorted(
            members, key=lambda item: (item[3], item[6], item[0])
        ):
            series = [
                (index, run, by_slice[slice_id].get(run["run_id"])["ipc_computed"])
                for index, run in enumerate(group)
                if run["run_id"] in by_slice[slice_id]
            ]
            values = [float(value) for _, _, value in series]
            first = values[0] if values else None
            last = values[-1] if values else None
            adjacent = [
                _pct(float(previous[2]), float(current[2]))
                for previous, current in zip(series, series[1:])
                if current[0] == previous[0] + 1
            ]
            adjacent = [value for value in adjacent if value is not None]
            range_pct = _pct(min(values), max(values)) if values else None
            first_last = _pct(first, last)
            max_step = max(adjacent, key=abs) if adjacent else None
            if len(series) < 2:
                category = "insufficient"
            elif (abs(first_last or 0) >= 2 or abs(range_pct or 0) >= 2
                  or abs(max_step or 0) >= 2):
                category = "changed"
            elif abs(range_pct or 0) <= 0.5 and abs(max_step or 0) <= 0.5:
                category = "stable"
            else:
                category = "volatile"
            summaries.append({
                "group": f"{group_key[0][:8]}-{group_key[1][:8]}",
                "slice_set_id": group_key[0], "comparison_key": group_key[1],
                "slice_id": slice_id, "slice": slice_name, "benchmark": benchmark,
                "workload": workload, "checkpoint": checkpoint, "weight": weight,
                "observations": len(series), "expected_runs": len(group),
                "coverage_pct": len(series) / len(group) * 100,
                "first_commit": series[0][1]["short_sha"] if series else None,
                "last_commit": series[-1][1]["short_sha"] if series else None,
                "first_ipc": first, "last_ipc": last,
                "first_to_last_pct": first_last, "range_pct": range_pct,
                "max_adjacent_step_pct": max_step, "category": category,
            })
        for previous, current in zip(group, group[1:]):
            previous_values = by_slice
            changes = []
            for slice_id, points in previous_values.items():
                left = points.get(previous["run_id"])
                right = points.get(current["run_id"])
                if left and right:
                    change = _pct(float(left["ipc_computed"]), float(right["ipc_computed"]))
                    if change is not None:
                        changes.append(change)
            transitions.append({
                "group": f"{group_key[0][:8]}-{group_key[1][:8]}",
                "slice_set_id": group_key[0], "comparison_key": group_key[1],
                "from_run": previous["run_id"], "to_run": current["run_id"],
                "from_commit": previous["short_sha"], "to_commit": current["short_sha"],
                "comparable_slices": len(changes),
                "improved_over_0_5pct": sum(change > 0.5 for change in changes),
                "stable_within_0_5pct": sum(abs(change) <= 0.5 for change in changes),
                "regressed_over_0_5pct": sum(change < -0.5 for change in changes),
                "median_change_pct": sorted(changes)[len(changes) // 2] if changes else None,
                "mean_change_pct": math.fsum(changes) / len(changes) if changes else None,
            })
        groups.append({
            "group": f"{group_key[0][:8]}-{group_key[1][:8]}",
            "slice_set_id": group_key[0], "comparison_key": group_key[1],
            "slice_set_name": group[0]["slice_set_name"], "runs": len(group),
            "slices": max((int(row.get("expected_slices") or 0) for row in group), default=len(members)),
            "observed_slices": len(members),
            "valid_observations": sum(1 for row in group_observations),
            "published_runs": sum(row["status"] == "published" for row in group),
            "first_commit": group[0]["short_sha"], "last_commit": group[-1]["short_sha"],
        })
    return {
        "runs": selected_runs,
        "observations": selected_observations,
        "summaries": summaries,
        "transitions": transitions,
        "groups": groups,
    }

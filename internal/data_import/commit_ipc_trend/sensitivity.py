"""Historical slice sensitivity analysis over the trend SQLite schema.

The module deliberately keeps analysis results outside the fact tables.  It
loads immutable runs and slice observations, constructs parent (or explicitly
diagnostic adjacent) transitions, estimates repeat-run noise, and produces
explainable sensitivity scores and recommendations.
"""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from statistics import median
from typing import Any, Iterable, Mapping, Sequence


ANALYSIS_VERSION = "sensitivity/v1"
DEFAULT_THRESHOLD_CPI = 0.01
DEFAULT_MIN_COVERAGE = 0.8
DEFAULT_MIN_TRANSITIONS = 5
WEIGHT_TOLERANCE = 1e-5


def _finite(value: Any) -> bool:
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _float(value: Any) -> float | None:
    return float(value) if _finite(value) else None


def _quantile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    ratio = position - lower
    return ordered[lower] * (1.0 - ratio) + ordered[upper] * ratio


def _mad(values: Sequence[float]) -> float | None:
    if not values:
        return None
    center = median(values)
    return median([abs(float(value) - center) for value in values])


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _group_key(row: Mapping[str, Any]) -> tuple[str, str]:
    return str(row["comparison_key"]), str(row["slice_set_id"])


def _slice_key(row: Mapping[str, Any]) -> tuple[str, str, str]:
    return (*_group_key(row), str(row["slice_id"]))


def load_inputs(
    connection: sqlite3.Connection,
    *,
    comparison_keys: Iterable[str] = (),
    slice_set_ids: Iterable[str] = (),
    statuses: Iterable[str] = ("published",),
) -> dict[str, list[dict[str, Any]]]:
    """Load runs, valid slice observations, and expected slice members.

    Repeated runs are intentionally retained.  The caller may choose a
    representative run for parent transitions, while ``estimate_noise`` uses
    every retained observation for the same commit and conditions.
    """

    comparison_filter = set(comparison_keys)
    slice_set_filter = set(slice_set_ids)
    status_filter = set(statuses)
    runs: list[dict[str, Any]] = []
    for raw in connection.execute(
        """
        SELECT r.run_id, r.commit_sha, r.config, r.snapshot_at, r.imported_at,
               r.status, r.comparison_key, r.slice_set_id, r.source_uri,
               r.experiment_json, c.short_sha, c.commit_epoch, c.commit_time,
               c.subject, c.parents_json, ss.name AS slice_set_name,
               ss.version AS slice_set_version
          FROM runs r
          JOIN commits c ON c.commit_sha = r.commit_sha
          JOIN slice_sets ss ON ss.slice_set_id = r.slice_set_id
         ORDER BY c.commit_epoch, r.snapshot_at, r.run_id
        """
    ):
        row = dict(raw)
        if comparison_filter and row["comparison_key"] not in comparison_filter:
            continue
        if slice_set_filter and row["slice_set_id"] not in slice_set_filter:
            continue
        if status_filter and row["status"] not in status_filter:
            continue
        row["parents"] = _json(row.pop("parents_json")) or []
        row["experiment"] = _json(row.pop("experiment_json")) or {}
        runs.append(row)

    if not runs:
        return {"runs": [], "observations": [], "members": []}

    run_ids = [str(row["run_id"]) for row in runs]
    placeholders = ",".join("?" for _ in run_ids)
    observations = [
        dict(row)
        for row in connection.execute(
            f"""
            SELECT r.run_id, r.commit_sha, r.comparison_key, r.slice_set_id,
                   r.status AS run_status, c.short_sha, c.commit_epoch,
                   c.commit_time, c.subject, s.slice_id, s.slice_name AS slice,
                   s.benchmark, s.workload, s.checkpoint, m.weight, m.ordinal,
                   sr.status AS slice_status, sr.seed, sr.window_id,
                   sr.instructions, sr.cycles, sr.ipc_computed, sr.cpi,
                   sr.source_out_uri, sr.source_err_uri
              FROM runs r
              JOIN commits c ON c.commit_sha = r.commit_sha
              JOIN slice_results sr ON sr.run_id = r.run_id
              JOIN slices s ON s.slice_id = sr.slice_id
              JOIN slice_set_members m
                ON m.slice_set_id = r.slice_set_id AND m.slice_id = sr.slice_id
             WHERE r.run_id IN ({placeholders})
               AND sr.status = 'valid'
               AND sr.ipc_computed IS NOT NULL
               AND sr.cpi IS NOT NULL
               AND sr.ipc_computed > 0
               AND sr.cpi > 0
             ORDER BY c.commit_epoch, r.snapshot_at, r.run_id, m.ordinal
            """,
            run_ids,
        )
    ]
    set_ids = sorted({str(row["slice_set_id"]) for row in runs})
    set_placeholders = ",".join("?" for _ in set_ids)
    members = [
        dict(row)
        for row in connection.execute(
            f"""
            SELECT m.slice_set_id, m.slice_id, m.weight, m.weight_kind,
                   m.ordinal, s.slice_name AS slice, s.benchmark, s.workload,
                   s.checkpoint
              FROM slice_set_members m
              JOIN slices s ON s.slice_id = m.slice_id
             WHERE m.slice_set_id IN ({set_placeholders})
             ORDER BY m.slice_set_id, s.workload, m.ordinal, m.slice_id
            """,
            set_ids,
        )
    ]
    return {"runs": runs, "observations": observations, "members": members}


def _valid_count_by_run(observations: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for row in observations:
        counts[str(row["run_id"])] += 1
    return counts


def choose_representatives(
    runs: Iterable[Mapping[str, Any]],
    observations: Iterable[Mapping[str, Any]],
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Choose one run per comparison group and commit for parent pairing."""

    valid_counts = _valid_count_by_run(observations)
    selected: dict[tuple[str, str, str], dict[str, Any]] = {}
    for raw in runs:
        row = dict(raw)
        row["valid_slices"] = valid_counts.get(str(row["run_id"]), 0)
        key = (*_group_key(row), str(row["commit_sha"]))
        rank = (
            int(row["status"] == "published"),
            int(row["valid_slices"]),
            str(row.get("snapshot_at") or ""),
            str(row["run_id"]),
        )
        previous = selected.get(key)
        if previous is None or rank > previous["_rank"]:
            row["_rank"] = rank
            selected[key] = row
    for row in selected.values():
        row.pop("_rank", None)
    return selected


def build_parent_transitions(
    runs: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    members: list[dict[str, Any]],
    *,
    min_coverage: float = DEFAULT_MIN_COVERAGE,
    include_adjacent: bool = False,
) -> list[dict[str, Any]]:
    """Build slice-level deltas against tested first parents.

    Adjacent fallback is opt-in and marked ``relation=adjacent_diagnostic`` so
    callers cannot silently treat a time-neighbor as a Commit effect.
    """

    if not 0 <= min_coverage <= 1:
        raise ValueError("min_coverage must be between 0 and 1")
    representatives = choose_representatives(runs, observations)
    observations_by_run: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in observations:
        observations_by_run[str(row["run_id"])][str(row["slice_id"])] = row
    # Members do not carry comparison_key; map them by slice set for all groups.
    members_by_set: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in members:
        members_by_set[str(row["slice_set_id"])].append(row)
    runs_by_group: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in representatives.values():
        runs_by_group[_group_key(row)].append(row)

    transitions: list[dict[str, Any]] = []
    for group, group_runs in runs_by_group.items():
        ordered = sorted(
            group_runs,
            key=lambda row: (
                int(row.get("commit_epoch") or 0),
                str(row.get("snapshot_at") or ""),
                str(row["run_id"]),
            ),
        )
        by_commit = {str(row["commit_sha"]): row for row in ordered}
        for index, current in enumerate(ordered):
            parent = next(
                (by_commit[parent_sha] for parent_sha in current.get("parents", []) if parent_sha in by_commit),
                None,
            )
            relation = "first_parent_tested"
            if parent is None and include_adjacent and index:
                parent = ordered[index - 1]
                relation = "adjacent_diagnostic"
            if parent is None:
                continue

            expected_members = members_by_set.get(str(current["slice_set_id"]), [])
            expected_by_workload: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for item in expected_members:
                expected_by_workload[str(item["workload"])].append(item)
            left = observations_by_run.get(str(parent["run_id"]), {})
            right = observations_by_run.get(str(current["run_id"]), {})
            paired: list[dict[str, Any]] = []
            for slice_id in sorted(set(left) & set(right)):
                a = left[slice_id]
                b = right[slice_id]
                if str(a["slice_set_id"]) != str(current["slice_set_id"]):
                    continue
                if str(b["slice_set_id"]) != str(current["slice_set_id"]):
                    continue
                if str(a["workload"]) != str(b["workload"]):
                    continue
                cpi_a = _float(a.get("cpi"))
                cpi_b = _float(b.get("cpi"))
                ipc_a = _float(a.get("ipc_computed"))
                ipc_b = _float(b.get("ipc_computed"))
                if None in (cpi_a, cpi_b, ipc_a, ipc_b):
                    continue
                paired.append(
                    {
                        "slice_id": slice_id,
                        "slice": b.get("slice"),
                        "benchmark": b.get("benchmark"),
                        "workload": b.get("workload"),
                        "checkpoint": b.get("checkpoint"),
                        "weight": float(b.get("weight") or 0.0),
                        "ordinal": b.get("ordinal"),
                        "ipc_a": ipc_a,
                        "ipc_b": ipc_b,
                        "cpi_a": cpi_a,
                        "cpi_b": cpi_b,
                        "delta_ipc_pct": (ipc_b / ipc_a - 1.0) * 100.0,
                        "delta_cpi": cpi_b - cpi_a,
                    }
                )

            coverage_by_workload: dict[str, dict[str, Any]] = {}
            for workload, workload_members in expected_by_workload.items():
                total_weight = math.fsum(float(item["weight"]) for item in workload_members)
                paired_rows = [row for row in paired if str(row["workload"]) == workload]
                covered_weight = math.fsum(float(row["weight"]) for row in paired_rows)
                coverage = covered_weight / total_weight if total_weight else 0.0
                full = (
                    len(paired_rows) == len(workload_members)
                    and abs(covered_weight - total_weight) <= WEIGHT_TOLERANCE
                )
                coverage_by_workload[workload] = {
                    "workload": workload,
                    "expected_slices": len(workload_members),
                    "paired_slices": len(paired_rows),
                    "expected_weight": total_weight,
                    "coverage_weight": coverage,
                    "status": "full" if full else "partial",
                    "eligible": coverage >= min_coverage,
                }
            for row in paired:
                coverage = coverage_by_workload[str(row["workload"])]
                row["workload_coverage_weight"] = coverage["coverage_weight"]
                row["workload_status"] = coverage["status"]
                row["eligible"] = coverage["eligible"]
            if not paired:
                continue
            transitions.append(
                {
                    "transition_id": f"{parent['run_id']}..{current['run_id']}",
                    "comparison_key": group[0],
                    "slice_set_id": group[1],
                    "relation": relation,
                    "from_run_id": parent["run_id"],
                    "to_run_id": current["run_id"],
                    "from_commit": parent["commit_sha"],
                    "to_commit": current["commit_sha"],
                    "from_short_sha": parent["short_sha"],
                    "to_short_sha": current["short_sha"],
                    "from_epoch": parent["commit_epoch"],
                    "to_epoch": current["commit_epoch"],
                    "from_subject": parent.get("subject"),
                    "to_subject": current.get("subject"),
                    "comparable_slices": len(paired),
                    "eligible_slices": sum(row["eligible"] for row in paired),
                    "workloads": sorted(coverage_by_workload.values(), key=lambda row: row["workload"]),
                    "slices": paired,
                }
            )
    return sorted(transitions, key=lambda row: (row["to_epoch"], row["transition_id"]))


def estimate_noise(
    runs: Iterable[Mapping[str, Any]],
    observations: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Estimate repeat-run noise without collapsing same-Commit runs."""

    del runs  # Kept in the signature to make the input contract explicit.
    grouped: dict[tuple[str, str, str, str], list[float]] = defaultdict(list)
    metadata: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in observations:
        value = _float(row.get("cpi"))
        if value is None:
            continue
        key = (
            str(row["comparison_key"]),
            str(row["slice_set_id"]),
            str(row["commit_sha"]),
            str(row["slice_id"]),
        )
        grouped[key].append(value)
        metadata[key] = {
            "comparison_key": row["comparison_key"],
            "slice_set_id": row["slice_set_id"],
            "commit_sha": row["commit_sha"],
            "short_sha": row.get("short_sha"),
            "slice_id": row["slice_id"],
            "slice": row.get("slice"),
            "workload": row.get("workload"),
            "weight": row.get("weight"),
        }

    by_slice: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for key, values in grouped.items():
        if len(values) < 2:
            continue
        center = median(values)
        mad = _mad(values) or 0.0
        pair_deltas = [
            abs(values[right] - values[left])
            for left in range(len(values))
            for right in range(left + 1, len(values))
        ]
        row = {
            **metadata[key],
            "n_replicates": len(values),
            "median_cpi": center,
            "mad_cpi": mad,
            "noise_sigma": 1.4826 * mad,
            "noise_p95_delta": _quantile(pair_deltas, 0.95),
            "noise_status": "estimated" if len(values) >= 3 else "low_confidence",
        }
        by_slice[(key[0], key[1], key[3])].append(row)

    result: list[dict[str, Any]] = []
    all_slice_keys = {
        (str(row["comparison_key"]), str(row["slice_set_id"]), str(row["slice_id"]))
        for row in observations
    }
    for key in sorted(all_slice_keys):
        entries = by_slice.get(key, [])
        if entries:
            noise_floor = median([
                float(entry["noise_p95_delta"] or 0.0) for entry in entries
            ])
            sigma = median([float(entry["noise_sigma"] or 0.0) for entry in entries])
            status = "estimated" if any(entry["noise_status"] == "estimated" for entry in entries) else "low_confidence"
            n_replicates = max(int(entry["n_replicates"]) for entry in entries)
            sample_groups = len(entries)
            template = entries[0]
        else:
            noise_floor = 0.0
            sigma = None
            status = "unestimated"
            n_replicates = 1
            sample_groups = 0
            template = {
                "comparison_key": key[0],
                "slice_set_id": key[1],
                "slice_id": key[2],
                "slice": None,
                "workload": None,
                "weight": None,
            }
        result.append(
            {
                **template,
                "n_replicates": n_replicates,
                "repeat_groups": sample_groups,
                "noise_sigma": sigma,
                "noise_floor": noise_floor,
                "noise_status": status,
            }
        )
    return result


def load_change_labels(path: Path | None) -> dict[str, dict[str, Any]]:
    """Load versioned commit labels; exact SHA wins over a prefix match."""

    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    commits = payload.get("commits", payload)
    if not isinstance(commits, dict):
        raise ValueError("labels file must contain a commits object")
    result: dict[str, dict[str, Any]] = {}
    for key, value in commits.items():
        if isinstance(value, list):
            value = {"labels": value, "source": "manual", "confidence": 1.0}
        if not isinstance(value, dict):
            raise ValueError(f"invalid label entry for {key!r}")
        labels = value.get("labels", [])
        if isinstance(labels, str):
            labels = [labels]
        if not labels:
            labels = ["unclassified"]
        result[str(key)] = {
            "labels": [str(label) for label in labels],
            "source": value.get("source", "manual"),
            "confidence": float(value.get("confidence", 1.0)),
            "modules": list(value.get("modules", [])),
            "evidence": list(value.get("evidence", [])),
        }
    return result


def labels_for_commit(commit_sha: str, labels: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    if commit_sha in labels:
        return dict(labels[commit_sha])
    matches = [
        (key, value)
        for key, value in labels.items()
        if commit_sha.startswith(str(key)) or str(key).startswith(commit_sha)
    ]
    if matches:
        return dict(max(matches, key=lambda item: len(item[0]))[1])
    return {"labels": ["unclassified"], "source": "default", "confidence": 0.0, "modules": [], "evidence": []}


def calculate_sensitivity_scores(
    transitions: Iterable[Mapping[str, Any]],
    noise_rows: Iterable[Mapping[str, Any]],
    labels: Mapping[str, Mapping[str, Any]] | None = None,
    *,
    threshold_cpi: float = DEFAULT_THRESHOLD_CPI,
    min_transitions: int = DEFAULT_MIN_TRANSITIONS,
) -> list[dict[str, Any]]:
    """Aggregate per-slice responses by architecture change label."""

    if threshold_cpi < 0:
        raise ValueError("threshold_cpi must be non-negative")
    if min_transitions < 1:
        raise ValueError("min_transitions must be positive")
    labels = labels or {}
    noise = {
        (str(row["comparison_key"]), str(row["slice_set_id"]), str(row["slice_id"])): row
        for row in noise_rows
    }
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for transition in transitions:
        label_info = labels_for_commit(str(transition["to_commit"]), labels)
        for label in label_info["labels"]:
            for row in transition.get("slices", []):
                if not row.get("eligible"):
                    continue
                item = {
                    **dict(row),
                    "transition_id": transition["transition_id"],
                    "to_commit": transition["to_commit"],
                    "to_short_sha": transition["to_short_sha"],
                    "relation": transition["relation"],
                    "comparison_key": transition["comparison_key"],
                    "slice_set_id": transition["slice_set_id"],
                    "label": label,
                    "label_source": label_info.get("source"),
                    "label_confidence": label_info.get("confidence"),
                }
                grouped[(str(transition["comparison_key"]), str(transition["slice_set_id"]), str(label), str(row["slice_id"]))].append(item)

    result: list[dict[str, Any]] = []
    for (comparison_key, slice_set_id, label, slice_id), rows in grouped.items():
        deltas = [float(row["delta_cpi"]) for row in rows]
        absolute = [abs(delta) for delta in deltas]
        noise_row = noise.get((comparison_key, slice_set_id, slice_id), {})
        noise_floor = float(noise_row.get("noise_floor") or 0.0)
        effective = [max(value - noise_floor, 0.0) for value in absolute]
        threshold = max(threshold_cpi, noise_floor)
        hits = [value > threshold for value in absolute]
        positive = sum(value > threshold for value in deltas)
        negative = sum(value < -threshold for value in deltas)
        n = len(rows)
        coverage = median([float(row.get("workload_coverage_weight") or 0.0) for row in rows])
        response_rate = sum(hits) / n if n else 0.0
        sign_consistency = max(positive, negative) / n if n else 0.0
        reliability = min(1.0, n / float(min_transitions)) * max(0.0, min(1.0, coverage))
        if noise_row.get("noise_status") == "unestimated":
            reliability *= 0.75
        elif noise_row.get("noise_status") == "low_confidence":
            reliability *= 0.9
        score = median(effective) * response_rate * reliability if effective else 0.0
        if n < min_transitions:
            stability = "insufficient"
        elif noise_floor > threshold_cpi * 2 and response_rate < 0.5:
            stability = "volatile"
        elif response_rate >= 0.4 and median(effective) > 0:
            stability = "sensitive"
        elif response_rate <= 0.2 and noise_floor <= threshold_cpi:
            stability = "stable"
        else:
            stability = "changed"
        weighted_positive = [
            float(row["weight"]) * max(float(row["delta_cpi"]) - noise_floor, 0.0)
            for row in rows
        ]
        weighted_absolute = [
            float(row["weight"]) * max(abs(float(row["delta_cpi"])) - noise_floor, 0.0)
            for row in rows
        ]
        result.append(
            {
                "comparison_key": comparison_key,
                "slice_set_id": slice_set_id,
                "label": label,
                "slice_id": slice_id,
                "slice": rows[0].get("slice"),
                "workload": rows[0].get("workload"),
                "benchmark": rows[0].get("benchmark"),
                "checkpoint": rows[0].get("checkpoint"),
                "weight": float(rows[0].get("weight") or 0.0),
                "n_transitions": n,
                "coverage": coverage,
                "median_abs_delta_cpi": median(absolute),
                "effective_median_abs_delta_cpi": median(effective),
                "p90_abs_delta_cpi": _quantile(absolute, 0.90),
                "median_abs_delta_ipc_pct": median([abs(float(row["delta_ipc_pct"])) for row in rows]),
                "response_rate": response_rate,
                "sign_consistency": sign_consistency,
                "noise_normalized": median([
                    value / noise_floor for value in absolute
                ]) if noise_floor > 0 else None,
                "noise_floor": noise_floor,
                "noise_status": noise_row.get("noise_status", "unestimated"),
                "repeat_groups": noise_row.get("repeat_groups", 0),
                "median_weighted_positive_impact": median(weighted_positive),
                "median_weighted_abs_impact": median(weighted_absolute),
                "reliability": reliability,
                "score": score,
                "stability_class": stability,
                "label_source": rows[0].get("label_source"),
                "label_confidence": rows[0].get("label_confidence"),
                "evidence_transitions": [row["transition_id"] for row in rows],
            }
        )

    by_rank_group: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in result:
        by_rank_group[(row["comparison_key"], row["slice_set_id"], row["label"])].append(row)
    for group_rows in by_rank_group.values():
        group_rows.sort(key=lambda row: (-float(row["score"]), str(row["slice_id"])))
        for rank, row in enumerate(group_rows, 1):
            row["rank"] = rank
    return sorted(
        result,
        key=lambda row: (row["comparison_key"], row["slice_set_id"], row["label"], row["rank"]),
    )


def _pick_pool(
    candidates: list[dict[str, Any]],
    *,
    role: str,
    count: int,
    selected: set[str],
    workload_counts: dict[str, int],
    workload_cap: int,
) -> list[dict[str, Any]]:
    picked: list[dict[str, Any]] = []
    for row in candidates:
        slice_id = str(row["slice_id"])
        workload = str(row.get("workload") or "")
        if slice_id in selected or workload_counts[workload] >= workload_cap:
            continue
        selected.add(slice_id)
        workload_counts[workload] += 1
        picked.append(
            {
                "slice_id": slice_id,
                "slice": row.get("slice"),
                "workload": row.get("workload"),
                "checkpoint": row.get("checkpoint"),
                "weight": row.get("weight"),
                "role": role,
                "rank": len(picked) + 1,
                "score": row.get("score"),
                "stability_class": row.get("stability_class"),
                "noise_status": row.get("noise_status"),
                "n_transitions": row.get("n_transitions"),
                "coverage": row.get("coverage"),
                "rationale": {
                    "label": row.get("label"),
                    "response_rate": row.get("response_rate"),
                    "median_abs_delta_cpi": row.get("median_abs_delta_cpi"),
                    "median_weighted_positive_impact": row.get("median_weighted_positive_impact"),
                    "reliability": row.get("reliability"),
                    "evidence_transitions": row.get("evidence_transitions", []),
                },
            }
        )
        if len(picked) >= count:
            break
    return picked


def recommend_slices(
    scores: Iterable[Mapping[str, Any]],
    *,
    target_label: str | None = None,
    sensitive_count: int = 5,
    impact_count: int = 3,
    control_count: int = 2,
    max_workload_fraction: float = 0.4,
) -> dict[str, Any]:
    """Select sensitive, high-impact, and stable control pools."""

    if min(sensitive_count, impact_count, control_count) < 0:
        raise ValueError("recommendation counts must be non-negative")
    if not 0 < max_workload_fraction <= 1:
        raise ValueError("max_workload_fraction must be in (0, 1]")
    rows = [dict(row) for row in scores]
    if target_label is not None:
        rows = [row for row in rows if row.get("label") == target_label]
    if not rows:
        return {
            "status": "insufficient_candidates",
            "target_label": target_label,
            "items": [],
            "counts_requested": {"sensitive": sensitive_count, "impact": impact_count, "control": control_count},
            "counts_selected": {"sensitive": 0, "impact": 0, "control": 0},
        }
    group_keys = {(row.get("comparison_key"), row.get("slice_set_id")) for row in rows}
    if len(group_keys) > 1:
        raise ValueError("recommend_slices expects one comparison_key/slice_set_id group")
    total_count = sensitive_count + impact_count + control_count
    workloads = {str(row.get("workload") or "") for row in rows}
    workload_cap = max(1, math.ceil(total_count * max_workload_fraction))
    selected: set[str] = set()
    workload_counts: dict[str, int] = defaultdict(int)
    sensitive = sorted(
        [row for row in rows if row.get("stability_class") == "sensitive"],
        key=lambda row: (-float(row.get("score") or 0.0), str(row["slice_id"])),
    )
    impact = sorted(
        rows,
        key=lambda row: (
            -float(row.get("median_weighted_positive_impact") or 0.0),
            -float(row.get("median_weighted_abs_impact") or 0.0),
            str(row["slice_id"]),
        ),
    )
    controls = sorted(
        [row for row in rows if row.get("stability_class") == "stable"],
        key=lambda row: (
            float(row.get("noise_floor") or 0.0),
            -float(row.get("coverage") or 0.0),
            -float(row.get("weight") or 0.0),
            str(row["slice_id"]),
        ),
    )
    items = []
    for role, candidates, count in (
        ("sensitive", sensitive, sensitive_count),
        ("high_weight_impact", impact, impact_count),
        ("stable_control", controls, control_count),
    ):
        items.extend(
            _pick_pool(
                candidates,
                role=role,
                count=count,
                selected=selected,
                workload_counts=workload_counts,
                workload_cap=workload_cap,
            )
        )
    counts_selected = {
        role: sum(item["role"] == role for item in items)
        for role in ("sensitive", "high_weight_impact", "stable_control")
    }
    requested = sensitive_count + impact_count + control_count
    return {
        "status": "ok" if len(items) == requested else "insufficient_candidates",
        "target_label": target_label,
        "comparison_key": next(iter(group_keys))[0],
        "slice_set_id": next(iter(group_keys))[1],
        "workload_cap": workload_cap,
        "workloads": sorted(workloads),
        "counts_requested": {
            "sensitive": sensitive_count,
            "high_weight_impact": impact_count,
            "stable_control": control_count,
        },
        "counts_selected": counts_selected,
        "items": items,
    }


def attach_counter_clues(
    connection: sqlite3.Connection,
    transitions: list[dict[str, Any]],
    *,
    top_slices: int = 20,
    top_counters: int = 5,
) -> list[dict[str, Any]]:
    """Attach same-window A/B counter deltas to the largest slice changes.

    Counter values are evidence rows only.  Missing, ambiguous, parse-error,
    semantic-version-mismatched, or window-mismatched values are excluded from
    the clue list rather than treated as zero.
    """

    if top_slices < 0 or top_counters < 0:
        raise ValueError("counter clue limits must be non-negative")
    run_ids = sorted({
        str(run_id)
        for transition in transitions
        for run_id in (transition["from_run_id"], transition["to_run_id"])
    })
    if not run_ids or top_slices == 0 or top_counters == 0:
        return transitions
    placeholders = ",".join("?" for _ in run_ids)
    values: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for raw in connection.execute(
        f"""
        SELECT cv.run_id, cv.slice_id, cv.metric_id, cv.semantic_version,
               cv.value, cv.availability, cv.window_id, cv.dump_time,
               md.display_name, md.category, md.unit, md.direction,
               md.source_match_json
          FROM counter_values cv
          JOIN metric_definitions md
            ON md.metric_id = cv.metric_id
           AND md.semantic_version = cv.semantic_version
         WHERE cv.run_id IN ({placeholders})
        """,
        run_ids,
    ):
        row = dict(raw)
        row["source_match"] = _json(row.pop("source_match_json"))
        values[(str(row["run_id"]), str(row["slice_id"]))].append(row)

    for transition in transitions:
        transition_clues: dict[str, list[dict[str, Any]]] = defaultdict(list)
        changed = sorted(
            transition.get("slices", []),
            key=lambda row: (-abs(float(row.get("delta_cpi") or 0.0)), str(row["slice_id"])),
        )[:top_slices]
        for slice_row in changed:
            slice_id = str(slice_row["slice_id"])
            left = values.get((str(transition["from_run_id"]), slice_id), [])
            right = values.get((str(transition["to_run_id"]), slice_id), [])
            right_by_key = {
                (str(row["metric_id"]), str(row["semantic_version"])): row
                for row in right
            }
            for left_row in left:
                key = (str(left_row["metric_id"]), str(left_row["semantic_version"]))
                right_row = right_by_key.get(key)
                if right_row is None:
                    continue
                if left_row.get("availability") != "available" or right_row.get("availability") != "available":
                    continue
                if str(left_row.get("window_id")) != str(right_row.get("window_id")):
                    continue
                value_a = _float(left_row.get("value"))
                value_b = _float(right_row.get("value"))
                if value_a is None or value_b is None:
                    continue
                direction = str(left_row.get("direction") or "")
                if abs(value_a) < 1e-12:
                    directional_pct = None
                    raw_pct = None
                else:
                    raw_pct = (value_b / value_a - 1.0) * 100.0
                    if direction == "lower_is_better":
                        directional_pct = raw_pct
                    elif direction == "higher_is_better":
                        directional_pct = -raw_pct
                    else:
                        directional_pct = raw_pct
                clue = {
                    "transition_id": transition["transition_id"],
                    "slice_id": slice_id,
                    "slice": slice_row.get("slice"),
                    "workload": slice_row.get("workload"),
                    "metric_id": left_row["metric_id"],
                    "semantic_version": left_row["semantic_version"],
                    "display_name": left_row.get("display_name"),
                    "category": left_row.get("category"),
                    "unit": left_row.get("unit"),
                    "direction": direction,
                    "window_id": left_row.get("window_id"),
                    "value_a": value_a,
                    "value_b": value_b,
                    "raw_change_pct": raw_pct,
                    "directional_degradation_pct": directional_pct,
                    "source_match": left_row.get("source_match"),
                    "evidence_kind": "counter_correlation_clue",
                }
                transition_clues[slice_id].append(clue)
        transition["counter_clues"] = {}
        for slice_id in {row["slice_id"] for row in changed}:
            candidates = transition_clues.get(slice_id, [])
            candidates.sort(
                key=lambda row: (
                    -abs(float(row["directional_degradation_pct"]))
                    if row["directional_degradation_pct"] is not None else math.inf,
                    str(row["metric_id"]),
                )
            )
            transition["counter_clues"][slice_id] = candidates[:top_counters]
    return transitions


def analysis_id(config: Mapping[str, Any]) -> str:
    payload = json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def flatten_transitions(transitions: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Flatten nested transitions into CSV-friendly slice rows."""

    rows: list[dict[str, Any]] = []
    for transition in transitions:
        for item in transition.get("slices", []):
            rows.append(
                {
                    "transition_id": transition["transition_id"],
                    "comparison_key": transition["comparison_key"],
                    "slice_set_id": transition["slice_set_id"],
                    "relation": transition["relation"],
                    "from_run_id": transition["from_run_id"],
                    "to_run_id": transition["to_run_id"],
                    "from_commit": transition["from_commit"],
                    "to_commit": transition["to_commit"],
                    "from_short_sha": transition["from_short_sha"],
                    "to_short_sha": transition["to_short_sha"],
                    **item,
                }
            )
    return rows

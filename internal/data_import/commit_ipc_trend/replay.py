"""Historical holdout evaluation for slice selection policies."""

from __future__ import annotations

from collections import defaultdict
import math
import random
from statistics import mean, median
from typing import Any, Iterable, Mapping

from .sensitivity import calculate_sensitivity_scores, recommend_slices


def _sign(value: float, epsilon: float = 1e-12) -> int:
    if value > epsilon:
        return 1
    if value < -epsilon:
        return -1
    return 0


def _bootstrap_interval(
    values: list[float], *, seed: int, repeats: int,
) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    if len(values) == 1 or repeats < 1:
        return values[0], values[0]
    rng = random.Random(seed)
    means = [
        mean(rng.choice(values) for _ in values)
        for _ in range(repeats)
    ]
    means.sort()
    low = means[max(0, math.floor(len(means) * 0.025))]
    high = means[min(len(means) - 1, math.ceil(len(means) * 0.975) - 1)]
    return low, high


def _target_label(commit_sha: str, labels: Mapping[str, Mapping[str, Any]]) -> str:
    if commit_sha in labels:
        values = labels[commit_sha].get("labels", ["unclassified"])
        return str(values[0]) if values else "unclassified"
    for key, value in labels.items():
        if commit_sha.startswith(str(key)) or str(key).startswith(commit_sha):
            values = value.get("labels", ["unclassified"])
            return str(values[0]) if values else "unclassified"
    return "unclassified"


def _eligible_rows(transition: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in transition.get("slices", []) if row.get("eligible")]


def _full_rows(transition: Mapping[str, Any]) -> list[dict[str, Any]]:
    full_workloads = {
        str(row["workload"])
        for row in transition.get("workloads", [])
        if row.get("status") == "full"
    }
    return [
        dict(row)
        for row in transition.get("slices", [])
        if row.get("eligible") and str(row["workload"]) in full_workloads
    ]


def _choose_ids(
    transition: Mapping[str, Any],
    strategy: str,
    *,
    top_k: int,
    training_scores: list[dict[str, Any]],
    target_label: str,
    random_seed: int,
) -> list[str]:
    rows = _eligible_rows(transition)
    if strategy == "full":
        return [str(row["slice_id"]) for row in rows]
    if strategy == "random":
        rng = random.Random(random_seed)
        return [
            str(row["slice_id"])
            for row in rng.sample(rows, min(top_k, len(rows)))
        ]
    if strategy == "weight":
        ordered = sorted(rows, key=lambda row: (-float(row.get("weight") or 0.0), str(row["slice_id"])))
        return [str(row["slice_id"]) for row in ordered[:top_k]]

    score_by_slice: dict[str, dict[str, Any]] = {}
    for row in training_scores:
        if row.get("label") == target_label:
            score_by_slice[str(row["slice_id"])] = row
    if not score_by_slice:
        for row in training_scores:
            score_by_slice.setdefault(str(row["slice_id"]), row)
    if strategy == "historical_magnitude":
        ordered = sorted(
            rows,
            key=lambda row: (
                -float(score_by_slice.get(str(row["slice_id"]), {}).get("median_abs_delta_cpi") or 0.0),
                str(row["slice_id"]),
            ),
        )
        return [str(row["slice_id"]) for row in ordered[:top_k]]
    if strategy == "module_sensitive":
        ordered = sorted(
            rows,
            key=lambda row: (
                -float(score_by_slice.get(str(row["slice_id"]), {}).get("score") or 0.0),
                str(row["slice_id"]),
            ),
        )
        return [str(row["slice_id"]) for row in ordered[:top_k]]
    if strategy == "recommended_mix":
        recommendation = recommend_slices(
            [row for row in training_scores if row.get("label") == target_label]
            or training_scores,
            target_label=target_label if any(row.get("label") == target_label for row in training_scores) else None,
            sensitive_count=max(1, top_k // 2),
            impact_count=max(0, top_k // 3),
            control_count=max(0, top_k - max(1, top_k // 2) - max(0, top_k // 3)),
            max_workload_fraction=1.0,
        )
        selected = [str(row["slice_id"]) for row in recommendation.get("items", [])]
        if len(selected) < top_k:
            fallback = _choose_ids(
                transition,
                "historical_magnitude",
                top_k=top_k,
                training_scores=training_scores,
                target_label=target_label,
                random_seed=random_seed,
            )
            selected.extend(slice_id for slice_id in fallback if slice_id not in selected)
        return selected[:top_k]
    raise ValueError(f"unknown selection strategy: {strategy}")


def evaluate_selection(
    transitions: Iterable[Mapping[str, Any]],
    labels: Mapping[str, Mapping[str, Any]] | None = None,
    *,
    train_count: int | None = None,
    train_ratio: float = 0.6,
    top_k: int = 10,
    random_repeats: int = 10,
    threshold_cpi: float = 0.01,
    min_transitions: int = 1,
    seed: int = 0,
    bootstrap_repeats: int = 200,
) -> dict[str, Any]:
    """Evaluate selection strategies on a chronological holdout.

    The training prefix is frozen for every test transition in a group.  A
    future transition's delta never participates in its candidate ranking.
    """

    if not 0 < train_ratio < 1:
        raise ValueError("train_ratio must be between 0 and 1")
    if top_k < 1 or random_repeats < 1:
        raise ValueError("top_k and random_repeats must be positive")
    if bootstrap_repeats < 0:
        raise ValueError("bootstrap_repeats must be non-negative")
    labels = labels or {}
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for transition in transitions:
        grouped[(str(transition["comparison_key"]), str(transition["slice_set_id"]))].append(dict(transition))

    detail: list[dict[str, Any]] = []
    for group, group_transitions in grouped.items():
        group_transitions.sort(key=lambda row: (int(row["to_epoch"]), str(row["transition_id"])))
        commits: list[str] = []
        for transition in group_transitions:
            commit = str(transition["to_commit"])
            if commit not in commits:
                commits.append(commit)
        if len(commits) < 2:
            continue
        count = train_count if train_count is not None else max(1, math.floor(len(commits) * train_ratio))
        count = max(1, min(count, len(commits) - 1))
        train_commits = set(commits[:count])
        train_transitions = [row for row in group_transitions if row["to_commit"] in train_commits]
        test_transitions = [row for row in group_transitions if row["to_commit"] not in train_commits]
        train_scores = calculate_sensitivity_scores(
            train_transitions,
            [],
            labels,
            threshold_cpi=threshold_cpi,
            min_transitions=min_transitions,
        )
        for test_index, transition in enumerate(test_transitions):
            full_rows = _full_rows(transition)
            if not full_rows:
                continue
            target_label = _target_label(str(transition["to_commit"]), labels)
            full_ids = {str(row["slice_id"]) for row in full_rows}
            full_positive = sum(
                max(float(row["weight"]) * float(row["delta_cpi"]), 0.0)
                for row in full_rows
            )
            full_total = sum(float(row["weight"]) * float(row["delta_cpi"]) for row in full_rows)
            major_ids = {
                str(row["slice_id"])
                for row in full_rows
                if float(row["delta_cpi"]) > threshold_cpi
            }
            strategies = ["weight", "historical_magnitude", "module_sensitive", "recommended_mix", "full"]
            strategy_runs = [(strategy, 0) for strategy in strategies]
            strategy_runs.extend(("random", repeat) for repeat in range(random_repeats))
            for strategy, repeat in strategy_runs:
                selected_ids = set(
                    _choose_ids(
                        transition,
                        strategy,
                        top_k=top_k,
                        training_scores=train_scores,
                        target_label=target_label,
                        random_seed=seed + test_index * 1009 + repeat,
                    )
                )
                selected_rows = [row for row in full_rows if str(row["slice_id"]) in selected_ids]
                selected_positive = sum(
                    max(float(row["weight"]) * float(row["delta_cpi"]), 0.0)
                    for row in selected_rows
                )
                selected_total = sum(
                    float(row["weight"]) * float(row["delta_cpi"])
                    for row in selected_rows
                )
                captured = selected_positive / full_positive if full_positive > 0 else None
                recall = (
                    len(major_ids & selected_ids) / len(major_ids)
                    if major_ids else None
                )
                detail.append(
                    {
                        "comparison_key": group[0],
                        "slice_set_id": group[1],
                        "transition_id": transition["transition_id"],
                        "to_commit": transition["to_commit"],
                        "to_short_sha": transition["to_short_sha"],
                        "target_label": target_label,
                        "strategy": strategy,
                        "repeat": repeat,
                        "train_commit_count": count,
                        "test_slice_count": len(full_rows),
                        "selected_slice_count": len(selected_rows),
                        "cost_ratio": len(selected_rows) / len(full_rows),
                        "major_regression_count": len(major_ids),
                        "major_regression_recall": recall,
                        "positive_contribution_capture": captured,
                        "full_weighted_cpi_delta": full_total,
                        "selected_weighted_cpi_delta": selected_total,
                        "direction_correct": bool(_sign(selected_total) == _sign(full_total)) if selected_total else None,
                        "selected_slice_ids": sorted(selected_ids),
                        "full_slice_ids": sorted(full_ids),
                    }
                )

    summary: list[dict[str, Any]] = []
    by_strategy: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in detail:
        by_strategy[(row["comparison_key"], row["slice_set_id"], row["target_label"], row["strategy"])].append(row)
    for (comparison_key, slice_set_id, target_label, strategy), rows in sorted(by_strategy.items()):
        def avg(key: str) -> float | None:
            values = [float(row[key]) for row in rows if row.get(key) is not None]
            return mean(values) if values else None

        directions = [row["direction_correct"] for row in rows if row.get("direction_correct") is not None]
        recall_values = [float(row["major_regression_recall"]) for row in rows if row.get("major_regression_recall") is not None]
        capture_values = [float(row["positive_contribution_capture"]) for row in rows if row.get("positive_contribution_capture") is not None]
        direction_values = [float(value) for value in directions]
        cost_values = [float(row["cost_ratio"]) for row in rows if row.get("cost_ratio") is not None]
        recall_low, recall_high = _bootstrap_interval(recall_values, seed=seed + len(summary) * 7 + 1, repeats=bootstrap_repeats)
        capture_low, capture_high = _bootstrap_interval(capture_values, seed=seed + len(summary) * 7 + 2, repeats=bootstrap_repeats)
        direction_low, direction_high = _bootstrap_interval(direction_values, seed=seed + len(summary) * 7 + 3, repeats=bootstrap_repeats)
        cost_low, cost_high = _bootstrap_interval(cost_values, seed=seed + len(summary) * 7 + 4, repeats=bootstrap_repeats)
        summary.append(
            {
                "comparison_key": comparison_key,
                "slice_set_id": slice_set_id,
                "target_label": target_label,
                "strategy": strategy,
                "test_transitions": len(rows),
                "mean_major_regression_recall": avg("major_regression_recall"),
                "recall_ci_low": recall_low,
                "recall_ci_high": recall_high,
                "median_major_regression_recall": median([row["major_regression_recall"] for row in rows if row.get("major_regression_recall") is not None]) if any(row.get("major_regression_recall") is not None for row in rows) else None,
                "mean_positive_contribution_capture": avg("positive_contribution_capture"),
                "capture_ci_low": capture_low,
                "capture_ci_high": capture_high,
                "mean_direction_accuracy": sum(directions) / len(directions) if directions else None,
                "direction_ci_low": direction_low,
                "direction_ci_high": direction_high,
                "mean_cost_ratio": avg("cost_ratio"),
                "cost_ci_low": cost_low,
                "cost_ci_high": cost_high,
            }
        )
    return {
        "status": "ok" if detail else "insufficient_history",
        "train_ratio": train_ratio,
        "train_count": train_count,
        "top_k": top_k,
        "random_repeats": random_repeats,
        "threshold_cpi": threshold_cpi,
        "detail": detail,
        "summary": summary,
    }

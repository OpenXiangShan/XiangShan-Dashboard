#!/usr/bin/env python3
"""Build historical slice sensitivity, noise, and recommendation artifacts."""

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

from commit_ipc_trend.sensitivity import (
    ANALYSIS_VERSION,
    DEFAULT_MIN_COVERAGE,
    DEFAULT_MIN_TRANSITIONS,
    DEFAULT_THRESHOLD_CPI,
    attach_counter_clues,
    analysis_id,
    build_parent_transitions,
    calculate_sensitivity_scores,
    choose_representatives,
    estimate_noise,
    flatten_transitions,
    load_change_labels,
    load_inputs,
    recommend_slices,
)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_csv(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    rows = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(value, ensure_ascii=False, sort_keys=True)
                    if isinstance(value, (dict, list))
                    else value
                    for key, value in row.items()
                }
            )


def _exclusions(
    runs: list[dict[str, Any]],
    *,
    include_adjacent: bool,
) -> list[dict[str, Any]]:
    """Record representative commits that could not form a transition."""

    runs = list(choose_representatives(runs, ()).values())
    by_group: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in runs:
        by_group.setdefault((str(row["comparison_key"]), str(row["slice_set_id"])), []).append(row)
    result: list[dict[str, Any]] = []
    for group, group_rows in by_group.items():
        commits = {str(row["commit_sha"]) for row in group_rows}
        for row in sorted(group_rows, key=lambda item: (item["commit_epoch"], item["run_id"])):
            parents = row.get("parents") or []
            if any(parent in commits for parent in parents):
                continue
            result.append(
                {
                    "comparison_key": group[0],
                    "slice_set_id": group[1],
                    "run_id": row["run_id"],
                    "commit_sha": row["commit_sha"],
                    "short_sha": row["short_sha"],
                    "reason": "no_tested_first_parent" if not include_adjacent else "no_prior_representative",
                    "relation_mode": "parent_and_adjacent" if include_adjacent else "first_parent",
                }
            )
    return result


def _report(
    path: Path,
    *,
    database: Path,
    config: dict[str, Any],
    data: dict[str, list[dict[str, Any]]],
    transitions: list[dict[str, Any]],
    noise: list[dict[str, Any]],
    scores: list[dict[str, Any]],
    recommendations: list[dict[str, Any]],
    exclusions: list[dict[str, Any]],
) -> None:
    relation_counts: dict[str, int] = {}
    for row in transitions:
        relation_counts[row["relation"]] = relation_counts.get(row["relation"], 0) + 1
    lines = [
        "# 历史微架构敏感切片分析",
        "",
        f"数据库：`{database}`",
        f"分析版本：`{ANALYSIS_VERSION}`；分析 ID：`{config['analysis_id']}`",
        "",
        "本报告区分单次 Commit 变化、跨 Commit 历史敏感性和重复运行噪声。父提交是正式口径；相邻运行只有在显式启用时才标为诊断关系。",
        "",
        "## 输入和覆盖",
        "",
        f"Runs：{len(data['runs'])}；有效切片观测：{len(data['observations'])}；可比 transition：{len(transitions)}；噪声摘要：{len(noise)}；敏感性行：{len(scores)}。",
        f"Transition 关系：{json.dumps(relation_counts, ensure_ascii=False, sort_keys=True)}。",
        f"排除代表提交：{len(exclusions)}。",
        "",
        "## 质量规则",
        "",
        f"最小 workload coverage：{config['min_coverage']}；最小历史 transition 数：{config['min_transitions']}；CPI 实用阈值：{config['threshold_cpi']}。",
        "重复运行不足时，噪声状态为 `unestimated` 或 `low_confidence`；缺失数据没有被填成零。",
        "",
        "## 推荐",
        "",
    ]
    if recommendations:
        for item in recommendations:
            lines.append(
                f"- `{item.get('comparison_key','')[:8]}/{item.get('slice_set_id','')[:8]}` "
                f"标签 `{item.get('target_label')}`：{item.get('status')}，"
                f"选中 {len(item.get('items', []))} 个切片。"
            )
    else:
        lines.append("没有足够的敏感性样本生成推荐。")
    lines += [
        "",
        "## 输出文件",
        "",
        "- `transitions.csv`：每个父子 transition 的切片级 IPC/CPI 变化和 workload coverage。",
        "- `slice_scores.csv`：按改动标签聚合的敏感性、噪声、可靠性和贡献指标。",
        "- `noise_summary.csv`：同 Commit 同条件重复运行的噪声摘要。",
        "- `counter_clues.csv`：同窗口、同语义版本的计数器 A/B 线索（仅启用 `--include-counters` 时有数据）。",
        "- `recommendations.json`：敏感、高权重影响和稳定对照三类候选。",
        "- `exclusions.csv`：没有可比较父提交的代表运行。",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--comparison-key", action="append", default=[])
    parser.add_argument("--slice-set", action="append", default=[], dest="slice_sets")
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--label", help="仅生成指定改动标签的推荐")
    parser.add_argument("--include-partial", action="store_true")
    parser.add_argument(
        "--include-adjacent",
        action="store_true",
        help="没有已测父提交时加入相邻运行，结果标为 adjacent_diagnostic",
    )
    parser.add_argument("--min-coverage", type=float, default=DEFAULT_MIN_COVERAGE)
    parser.add_argument("--min-transitions", type=int, default=DEFAULT_MIN_TRANSITIONS)
    parser.add_argument("--threshold-cpi", type=float, default=DEFAULT_THRESHOLD_CPI)
    parser.add_argument("--sensitive-count", type=int, default=5)
    parser.add_argument("--impact-count", type=int, default=3)
    parser.add_argument("--control-count", type=int, default=2)
    parser.add_argument("--max-workload-fraction", type=float, default=0.4)
    parser.add_argument("--include-counters", action="store_true")
    parser.add_argument("--counter-top-slices", type=int, default=20)
    parser.add_argument("--counter-top", type=int, default=5)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.db.is_file():
        raise SystemExit(f"database does not exist: {args.db}")
    if args.min_transitions < 1 or args.min_coverage < 0 or args.min_coverage > 1:
        raise SystemExit("invalid minimum transition or coverage value")
    statuses = ("published", "partial") if args.include_partial else ("published",)
    labels = load_change_labels(args.labels)
    with sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        data = load_inputs(
            connection,
            comparison_keys=args.comparison_key,
            slice_set_ids=args.slice_sets,
            statuses=statuses,
        )
        transitions = build_parent_transitions(
            data["runs"],
            data["observations"],
            data["members"],
            min_coverage=args.min_coverage,
            include_adjacent=args.include_adjacent,
        )
        if args.include_counters:
            attach_counter_clues(
                connection,
                transitions,
                top_slices=args.counter_top_slices,
                top_counters=args.counter_top,
            )
    noise = estimate_noise(data["runs"], data["observations"])
    scores = calculate_sensitivity_scores(
        transitions,
        noise,
        labels,
        threshold_cpi=args.threshold_cpi,
        min_transitions=args.min_transitions,
    )
    group_labels = sorted({
        (row["comparison_key"], row["slice_set_id"], row["label"])
        for row in scores
        if args.label is None or row["label"] == args.label
    })
    recommendations: list[dict[str, Any]] = []
    for comparison_key, slice_set_id, label in group_labels:
        group_scores = [
            row for row in scores
            if row["comparison_key"] == comparison_key
            and row["slice_set_id"] == slice_set_id
            and row["label"] == label
        ]
        recommendation = recommend_slices(
            group_scores,
            target_label=label,
            sensitive_count=args.sensitive_count,
            impact_count=args.impact_count,
            control_count=args.control_count,
            max_workload_fraction=args.max_workload_fraction,
        )
        recommendations.append(recommendation)

    run_ids = sorted(str(row["run_id"]) for row in data["runs"])
    config = {
        "analysis_version": ANALYSIS_VERSION,
        "comparison_keys": sorted(args.comparison_key),
        "slice_sets": sorted(args.slice_sets),
        "run_ids": run_ids,
        "include_partial": args.include_partial,
        "include_adjacent": args.include_adjacent,
        "min_coverage": args.min_coverage,
        "min_transitions": args.min_transitions,
        "threshold_cpi": args.threshold_cpi,
        "labels_file": str(args.labels) if args.labels else None,
        "include_counters": args.include_counters,
        "counter_top_slices": args.counter_top_slices,
        "counter_top": args.counter_top,
    }
    config["analysis_id"] = analysis_id(config)
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    _write_json(output / "manifest.json", {**config, "database": str(args.db.resolve())})
    _write_csv(output / "transitions.csv", flatten_transitions(transitions))
    _write_csv(output / "slice_scores.csv", scores)
    _write_csv(output / "noise_summary.csv", noise)
    counter_rows = [
        clue
        for transition in transitions
        for clues in transition.get("counter_clues", {}).values()
        for clue in clues
    ]
    _write_csv(output / "counter_clues.csv", counter_rows)
    _write_csv(output / "exclusions.csv", _exclusions(data["runs"], include_adjacent=args.include_adjacent))
    _write_json(output / "recommendations.json", recommendations)
    _report(
        output / "REPORT.zh.md",
        database=args.db,
        config=config,
        data=data,
        transitions=transitions,
        noise=noise,
        scores=scores,
        recommendations=recommendations,
        exclusions=_exclusions(data["runs"], include_adjacent=args.include_adjacent),
    )
    summary = {
        "analysis_id": config["analysis_id"],
        "runs": len(data["runs"]),
        "observations": len(data["observations"]),
        "transitions": len(transitions),
        "noise_rows": len(noise),
        "score_rows": len(scores),
        "recommendation_groups": len(recommendations),
    }
    _write_json(output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

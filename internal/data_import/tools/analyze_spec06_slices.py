#!/usr/bin/env python3
"""Analyze SPEC06 slice IPC for every commit with usable database data."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import sqlite3
import sys
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm
from matplotlib import font_manager

_CJK_FONT = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
if _CJK_FONT.is_file():
    font_manager.fontManager.addfont(str(_CJK_FONT))
    _FONT_FAMILY = font_manager.FontProperties(fname=str(_CJK_FONT)).get_name()
else:
    _FONT_FAMILY = "DejaVu Sans"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from commit_ipc_trend.slice_analysis import (
    analyze_slice_trends,
    build_relative_ipc_matrix,
    load_spec06_observations,
)


plt.rcParams.update({
    "font.family": _FONT_FAMILY,
    "axes.unicode_minus": False,
    "figure.facecolor": "white",
    "axes.facecolor": "#f7f9fc",
})


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _short_slice(row: dict[str, Any]) -> str:
    """Keep graph labels compact while retaining an exact workload/checkpoint key."""
    return f"{row['workload']}:{row['checkpoint']}"


def _fmt_pct(value: float) -> str:
    return "0.00%" if abs(value) < 0.005 else f"{value:+.2f}%"


def _pad_x_axis(axis: Any, values: list[float]) -> None:
    if not values:
        return
    low, high = min(values), max(values)
    span = max(high - low, abs(low), abs(high), 0.5)
    axis.set_xlim(low - span * 0.16, high + span * 0.16)


def _benchmark_summary(
    runs: list[dict[str, Any]], observations: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Calculate one weighted harmonic IPC point per benchmark and run."""
    ordered = sorted(
        runs,
        key=lambda row: (int(row.get("commit_epoch", 0)),
                         row.get("snapshot_at") or "", row["run_id"]),
    )
    points: dict[tuple[str, str], tuple[float, float]] = {}
    for benchmark in sorted({str(row["benchmark"]) for row in observations}):
        for run in ordered:
            rows = [
                row for row in observations
                if str(row["benchmark"]) == benchmark and row["run_id"] == run["run_id"]
                and row.get("ipc_computed") is not None and float(row["ipc_computed"]) > 0
            ]
            weight = math.fsum(float(row["weight"]) for row in rows)
            if not rows or weight <= 0:
                continue
            cpi = math.fsum(
                float(row["weight"]) / float(row["ipc_computed"]) for row in rows
            ) / weight
            if cpi > 0 and math.isfinite(cpi):
                points[(benchmark, run["run_id"])] = (1.0 / cpi, weight)

    output: list[dict[str, Any]] = []
    for benchmark in sorted({key[0] for key in points}):
        series = [
            (run, points[(benchmark, run["run_id"])])
            for run in ordered if (benchmark, run["run_id"]) in points
        ]
        if not series:
            continue
        first_run, (first_ipc, first_weight) = series[0]
        last_run, (last_ipc, last_weight) = series[-1]
        output.append({
            "benchmark": benchmark,
            "first_commit": first_run["short_sha"],
            "last_commit": last_run["short_sha"],
            "first_ipc": first_ipc,
            "last_ipc": last_ipc,
            "first_to_last_pct": (last_ipc / first_ipc - 1.0) * 100.0,
            "first_weight_coverage": first_weight,
            "last_weight_coverage": last_weight,
        })
    return output


def _render_group_dashboard(
    path: Path,
    group: dict[str, Any],
    runs: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    summaries: list[dict[str, Any]],
    transitions: list[dict[str, Any]],
) -> None:
    """Render one compact, data-linked figure for a comparison group."""
    group_id = group["group"]
    run_ids = {row["run_id"] for row in runs}
    group_obs = [row for row in observations if row["run_id"] in run_ids]
    group_summaries = [row for row in summaries if row["group"] == group_id]
    group_transitions = [row for row in transitions if row["group"] == group_id]
    matrix = build_relative_ipc_matrix(runs, group_obs)

    changed = [
        row for row in group_summaries
        if row.get("first_to_last_pct") is not None
    ]
    regressions = sorted(
        [row for row in changed if float(row["first_to_last_pct"]) < 0],
        key=lambda row: float(row["first_to_last_pct"]),
    )[:8]
    improvements = sorted(
        [row for row in changed if float(row["first_to_last_pct"]) > 0],
        key=lambda row: float(row["first_to_last_pct"]), reverse=True,
    )[:8]
    top_slices = regressions + improvements
    benchmarks = _benchmark_summary(runs, group_obs)
    top_benchmarks = sorted(
        benchmarks, key=lambda row: abs(float(row["first_to_last_pct"])), reverse=True
    )[:12]

    heat_candidates = []
    for row in group_summaries:
        values = [
            matrix.get((str(row["slice_id"]), str(run["run_id"])))
            for run in runs
        ]
        finite_values = [abs(float(value)) for value in values if value is not None]
        if finite_values:
            heat_candidates.append((max(finite_values), row, values))
    heat_candidates.sort(key=lambda item: (-item[0], str(item[1]["workload"]), int(item[1]["checkpoint"])))
    heat_candidates = heat_candidates[:40]

    fig = plt.figure(figsize=(18, 12), dpi=150)
    grid = fig.add_gridspec(2, 2, height_ratios=(1.0, 1.25), hspace=0.36, wspace=0.25)
    fig.subplots_adjust(left=0.09, right=0.96, bottom=0.08, top=0.84, hspace=0.42, wspace=0.30)
    fig.suptitle(
        f"SPEC06 切片 IPC 趋势 · {group['slice_set_name']} · {group_id}",
        fontsize=18, fontweight="bold", ha="left", x=0.03, y=0.98,
    )
    fig.text(
        0.03, 0.945,
        f"{len(runs)} 次去重运行 · {len(group_obs)} 条有效切片 IPC · "
        "缺失点保留为空；相对变化按每个切片的首个有效观测计算",
        fontsize=9, color="#5f6b7a",
    )

    ax = fig.add_subplot(grid[0, 0])
    top_slices = list(reversed(top_slices))
    labels = [_short_slice(row) for row in top_slices]
    values = [float(row["first_to_last_pct"]) for row in top_slices]
    colors = ["#c7463a" if value < 0 else "#2d6db3" for value in values]
    positions = list(range(len(top_slices)))
    ax.barh(positions, values, color=colors, height=0.72)
    ax.axvline(0, color="#596575", linewidth=0.8)
    ax.set_yticks(positions, labels, fontsize=7)
    ax.set_xlabel("首尾 IPC 变化 (%)")
    ax.set_title("首尾变化最大的切片（下降 / 提升各 8 个）", loc="left", fontweight="bold")
    ax.grid(axis="x", color="#dfe5ec", linewidth=0.7)
    ax.set_axisbelow(True)
    _pad_x_axis(ax, values)
    for pos, value in zip(positions, values):
        ax.text(value + (0.15 if value >= 0 else -0.15), pos, _fmt_pct(value),
                va="center", ha="left" if value >= 0 else "right", fontsize=7)

    ax = fig.add_subplot(grid[0, 1])
    if group_transitions:
        transition_values = [float(row["median_change_pct"]) for row in group_transitions]
        transition_labels = [
            f"{str(row['from_commit'])[:7]}\n{str(row['to_commit'])[:7]}"
            for row in group_transitions
        ]
        colors = ["#2d6db3" if value >= 0 else "#c7463a" for value in transition_values]
        positions = list(range(len(group_transitions)))
        ax.bar(positions, transition_values, color=colors, width=0.68)
        ax.axhline(0, color="#596575", linewidth=0.8)
        ax.set_xticks(positions, transition_labels, fontsize=7)
        ax.set_ylabel("中位 IPC 变化 (%)")
        ax.set_title("相邻提交：共同有效切片的中位变化", loc="left", fontweight="bold")
        ax.grid(axis="y", color="#dfe5ec", linewidth=0.7)
        ax.set_axisbelow(True)
        for pos, value, row in zip(positions, transition_values, group_transitions):
            offset = max(max(abs(item) for item in transition_values) * 0.04, 0.015)
            ax.text(pos, value + offset,
                    f"{_fmt_pct(value)}\nN={row['comparable_slices']}",
                    ha="center", va="bottom", fontsize=6.5)
    else:
        ax.text(0.5, 0.5, "只有一个有效运行，无法计算相邻趋势", ha="center", va="center")
        ax.set_axis_off()

    ax = fig.add_subplot(grid[1, 0])
    top_benchmarks = list(reversed(top_benchmarks))
    labels = [str(row["benchmark"]) for row in top_benchmarks]
    values = [float(row["first_to_last_pct"]) for row in top_benchmarks]
    colors = ["#c7463a" if value < 0 else "#2d6db3" for value in values]
    positions = list(range(len(top_benchmarks)))
    ax.barh(positions, values, color=colors, height=0.72)
    ax.axvline(0, color="#596575", linewidth=0.8)
    ax.set_yticks(positions, labels, fontsize=8)
    ax.set_xlabel("benchmark 加权 IPC 首尾变化 (%)")
    ax.set_title("变化最大的 benchmark（按有效切片权重调和聚合）", loc="left", fontweight="bold")
    ax.grid(axis="x", color="#dfe5ec", linewidth=0.7)
    ax.set_axisbelow(True)
    _pad_x_axis(ax, values)
    for pos, value in zip(positions, values):
        ax.text(value + (0.12 if value >= 0 else -0.12), pos, _fmt_pct(value),
                va="center", ha="left" if value >= 0 else "right", fontsize=7)

    ax = fig.add_subplot(grid[1, 1])
    if heat_candidates:
        heat_values = [item[2] for item in heat_candidates]
        image_values = [
            [float(value) if value is not None else math.nan for value in values]
            for values in heat_values
        ]
        finite_heat = [abs(value) for row in image_values for value in row if math.isfinite(value)]
        limit = max(0.5, sorted(finite_heat)[max(0, int(len(finite_heat) * 0.95) - 1)])
        norm = TwoSlopeNorm(vmin=-limit, vcenter=0, vmax=limit)
        image = ax.imshow(image_values, aspect="auto", cmap="RdBu", norm=norm, interpolation="nearest")
        labels = [_short_slice(item[1]) for item in heat_candidates]
        ax.set_yticks(range(len(labels)), labels, fontsize=6.5)
        ax.set_xticks(range(len(runs)), [str(run["short_sha"])[:9] for run in runs], rotation=35, ha="right", fontsize=7)
        ax.set_title("重点切片热图（每行按自身首个有效观测归一）", loc="left", fontweight="bold")
        ax.set_xlabel("commit")
        colorbar = fig.colorbar(image, ax=ax, fraction=0.046, pad=0.04)
        colorbar.set_label("相对首个有效 IPC (%)")
    else:
        ax.text(0.5, 0.5, "没有足够的有效切片数据", ha="center", va="center")
        ax.set_axis_off()

    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def _write_report(path: Path, result: dict[str, list[dict[str, Any]]], database: Path) -> None:
    groups = result["groups"]
    lines = [
        "# SPEC06 全数据库切片 IPC 趋势分析", "",
        f"输入数据库：`{database}`", "",
        "只要数据库中某个 commit 存在有效切片 IPC 就纳入分析；不要求该次运行完成全部 SPEC06。",
        "不同 `slice_set_id + comparison_key` 分组独立分析，缺失切片保留覆盖率，不跨组比较。", "",
        f"运行 {len(result['runs'])} 次，切片 IPC 有效观测 {len(result['observations'])} 条，切片 {len(result['summaries'])} 个。", "",
        "## 数据集分组", "",
        "| 分组 | 切片集 | 运行数 | profile 切片 | 有效观测 | 已发布运行 | 首个提交 | 最后提交 |",
        "|---|---|---:|---:|---:|---:|---|---|",
    ]
    for row in groups:
        lines.append(
            f"| `{row['group']}` | {row['slice_set_name']} | {row['runs']} | {row['slices']} | "
            f"{row['valid_observations']} | {row['published_runs']} | `{row['first_commit']}` | `{row['last_commit']}` |"
        )
    lines += [
        "", "## 输出文件", "",
        "- `observations.csv`：按分组去重后的有效切片 IPC 点。",
        "- `observations_all_runs.csv`：所有符合筛选条件的有效点，包含重复提交运行。",
        "- `slice_summary_all.csv`：每个切片的覆盖率、首尾变化、范围和分类。",
        "- `transitions_all.csv`：相邻提交的共同有效切片变化统计。",
        "- `run_inventory.csv`：纳入分析的运行状态和有效切片数。",
        "- `slice_trend_dashboard_<group>.png`：每个分组的一张整合图；完整切片数据仍以 CSV 为准。",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--status", action="append", default=[], help="仅保留指定运行状态，可重复")
    parser.add_argument("--slice-set", action="append", default=[], dest="slice_sets")
    parser.add_argument("--comparison-key", action="append", default=[])
    parser.add_argument("--run", action="append", default=[], dest="runs",
                        help="运行 ID 或 commit SHA 前缀，可重复")
    args = parser.parse_args(argv)
    if not args.db.is_file():
        raise SystemExit(f"database does not exist: {args.db}")
    args.output.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        runs, all_observations = load_spec06_observations(
            connection, statuses=args.status, slice_set_ids=args.slice_sets,
            comparison_keys=args.comparison_key, runs=args.runs,
        )
    result = analyze_slice_trends(runs, all_observations)
    _write_csv(args.output / "observations.csv", result["observations"])
    _write_csv(args.output / "observations_all_runs.csv", all_observations)
    _write_csv(args.output / "slice_summary_all.csv", result["summaries"])
    _write_csv(args.output / "transitions_all.csv", result["transitions"])
    _write_csv(args.output / "run_inventory.csv", result["runs"])
    _write_csv(args.output / "groups.csv", result["groups"])
    for group in result["groups"]:
        group_runs = [row for row in result["runs"] if row["slice_set_id"] == group["slice_set_id"]
                      and row["comparison_key"] == group["comparison_key"]]
        _render_group_dashboard(
            args.output / f"slice_trend_dashboard_{group['group']}.png",
            group,
            group_runs,
            result["observations"],
            result["summaries"],
            result["transitions"],
        )
    _write_report(args.output / "REPORT.zh.md", result, args.db)
    (args.output / "summary.json").write_text(
        json.dumps({key: len(value) for key, value in result.items()}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({key: len(value) for key, value in result.items()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Link RTL TEA/DIP evidence to this repository's read-only slice history."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from commit_ipc_trend.tea_dip import join_history, load_evidence


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--evidence-dir", required=True, type=Path,
                        help="RTL archive containing raw/ and analysis/")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    evidence = load_evidence(args.evidence_dir)
    with sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        result = join_history(connection, evidence)
    result["database"] = str(args.db.resolve())
    result["generated_at"] = datetime.now(timezone.utc).isoformat()
    args.output.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    (args.output / "analysis.json").write_text(payload, encoding="utf-8")
    template = (ROOT / "tools/tea_dip_history.html").read_text()
    (args.output / "index.html").write_text(
        template.replace("__ANALYSIS_JSON__", payload.replace("<", "\\u003c")), encoding="utf-8")
    with (args.output / "slice-links.csv").open("w", newline="", encoding="utf-8") as stream:
        fields = ["group", "slice_id", "workload", "checkpoint", "observations",
                  "max_adjacent_step_pct", "association", "evidence_point"]
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({**row["summary"], "association": row["association"],
                          "evidence_point": row["evidence_point"]} for row in result["slices"])
    coverage = result["coverage"]
    (args.output / "REPORT.zh.md").write_text(f"""# 趋势 → TEA+DIP → PC 联动

打开同目录 `index.html`。可通过 `?slice=mcf:12886` 直接选择切片，页面支持比较组、关键词、缺测和 PC 状态筛选。

历史：{coverage['history_runs']} runs，{coverage['history_slices']} 个不同切片。
TEA+DIP：CI {result['source']['ci']}，提交 `{result['source']['commit']}`，{coverage['evidence_points']} 点；其中 {coverage['matched_points']} 点在历史中有相同 checkpoint。

关联条件是 checkpoint 根、workload 和 checkpoint 编号完全相同。历史分组仍按原 slice_set_id + comparison_key 隔离；不同 profile 不关联。历史中无 TEA+DIP 的点保留为缺测。

归因属于参考 CI；历史各点仍显示自己的提交、run、seed、窗口、周期和指令数。历史 summary IPC 与约20M正式归因窗口分别展示，不生成跨窗口或跨运行的归因增量。相邻变化为描述性响应，不作重复测量或单项因果结论。

TEA 四状态与 DIP 四状态＋Unresolved 各自守恒，不能相加；cause 和 PSV 事件可重叠。绝对归因 CPI = 参考点正式 CPI × 采样占比。PC 表是每状态前 {result['source']['top_pcs_per_state']} 项，符号缺失保留未知。比例来自每 {result['source']['sample_period']} 周期采样；原件记录的端点缺样为 `{json.dumps(result['source']['sampling_endpoint_deficits'])}`，不补样。

`analysis.json` 保存两边来源、输入 SHA256、完整关联与页面数据；`slice-links.csv` 可供后续看板使用。
""", encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), **coverage}, ensure_ascii=False))


if __name__ == "__main__":
    main()

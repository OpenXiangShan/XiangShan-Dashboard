#!/usr/bin/env python3
"""Analyze all raw PERF counters against same-slice CPI changes."""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
import json
import math
from pathlib import Path
import sqlite3
import statistics
import zlib


TRANSFORM = "log1p(value_current) - log1p(value_baseline)"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--min-pairs", type=int, default=200)
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--min-coverage", type=float, default=0.5)
    return parser.parse_args()


def finite(value: object) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def read_selection(path: Path) -> set[str]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return {
            row["run_id"]
            for row in csv.DictReader(stream)
            if row.get("disposition") == "selected" and row.get("run_id")
        }


def decode(blob: bytes, ordinals: set[int] | None = None) -> dict[int, float]:
    values = json.loads(zlib.decompress(blob))["values"]
    if ordinals is None:
        return {int(key): float(value) for key, value in values.items() if finite(value)}
    return {
        int(key): float(value)
        for key, value in values.items()
        if int(key) in ordinals and finite(value)
    }


def delta_value(current: float, baseline: float) -> float | None:
    if current < 0 or baseline < 0:
        return None
    return math.log1p(current) - math.log1p(baseline)


def empty_stats(size: int) -> list[list[float]]:
    return [[0.0] * 7 for _ in range(size + 1)]


def add_stats(stats: list[list[float]], ordinal: int, x: float, y: float) -> None:
    row = stats[ordinal]
    row[0] += 1
    row[1] += x
    row[2] += y
    row[3] += x * x
    row[4] += y * y
    row[5] += x * y
    row[6] += 1 if abs(x) > 1e-12 else 0


def correlation(row: list[float]) -> float | None:
    count, sx, sy, sx2, sy2, sxy, _ = row
    if count < 2:
        return None
    vx = count * sx2 - sx * sx
    vy = count * sy2 - sy * sy
    if vx <= 1e-18 or vy <= 1e-18:
        return None
    return (count * sxy - sx * sy) / math.sqrt(vx * vy)


def counter_row(definition: sqlite3.Row, stats: list[float], total_pairs: int) -> dict[str, object] | None:
    count = int(stats[0])
    r = correlation(stats)
    if r is None or count == 0:
        return None
    return {
        "ordinal": int(definition["ordinal"]),
        "metric_id": definition["metric_id"],
        "source_path": definition["source_path"],
        "source_name": definition["source_name"],
        "label": f'{definition["source_name"]} @ {definition["source_path"]}',
        "pairs": count,
        "coverage": count / total_pairs if total_pairs else 0.0,
        "nonzero_delta_fraction": stats[6] / count,
        "pearson_r": r,
        "abs_pearson_r": abs(r),
    }


def top_rows(
    definitions: dict[int, sqlite3.Row], stats: list[list[float]], total_pairs: int,
    min_pairs: int, min_coverage: float, limit: int,
) -> list[dict[str, object]]:
    rows = []
    for ordinal, row in enumerate(stats):
        if ordinal == 0 or row[0] < min_pairs:
            continue
        item = counter_row(definitions[ordinal], row, total_pairs)
        if item and item["coverage"] >= min_coverage:
            rows.append(item)
    rows.sort(key=lambda item: (item["abs_pearson_r"], item["pairs"]), reverse=True)
    return rows[:limit]


def load_dumps(connection: sqlite3.Connection, run_id: str) -> dict[str, bytes]:
    return {
        row["slice_id"]: row["values_zlib"]
        for row in connection.execute(
            "SELECT slice_id, values_zlib FROM raw_counter_dumps "
            "WHERE run_id=? AND availability != 'parse_error'",
            (run_id,),
        )
    }


def median_or_none(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def analyze_group(
    connection: sqlite3.Connection,
    group: dict[str, object],
    definitions: dict[int, sqlite3.Row],
    valid: dict[str, dict[str, sqlite3.Row]],
    min_pairs: int,
    min_coverage: float,
) -> tuple[list[list[float]], int, dict[str, object]]:
    run_ids = group["run_ids"]
    usable_runs = [
        run_id for run_id in run_ids
        if connection.execute(
            "SELECT 1 FROM raw_counter_dumps WHERE run_id=? AND availability != 'parse_error' "
            "AND available_count > 0 LIMIT 1", (run_id,)
        ).fetchone()
    ]
    if len(usable_runs) < 2:
        return empty_stats(len(definitions)), 0, {
            "label": group["label"], "comparison_key": group["comparison_key"],
            "slice_set_id": group["slice_set_id"], "run_ids": usable_runs,
            "excluded_run_ids": [run_id for run_id in run_ids if run_id not in usable_runs],
            "baseline_run": usable_runs[0] if usable_runs else None,
            "latest_run": usable_runs[-1] if usable_runs else None, "pair_count": 0,
            "top_positive": [], "top_negative": [], "top_absolute": [],
        }
    run_ids = usable_runs
    baseline_run = run_ids[0]
    baseline_dumps = load_dumps(connection, baseline_run)
    stats = empty_stats(len(definitions))
    total_pairs = 0
    for run_id in run_ids[1:]:
        current_dumps = load_dumps(connection, run_id)
        for slice_id, current_blob in current_dumps.items():
            base_row = valid.get(baseline_run, {}).get(slice_id)
            current_row = valid.get(run_id, {}).get(slice_id)
            baseline_blob = baseline_dumps.get(slice_id)
            if not base_row or not current_row or not baseline_blob:
                continue
            cpi_a = base_row["cpi"]
            cpi_b = current_row["cpi"]
            if not finite(cpi_a) or not finite(cpi_b):
                continue
            base_values = decode(baseline_blob)
            current_values = decode(current_blob)
            total_pairs += 1
            y = float(cpi_b) - float(cpi_a)
            for ordinal, current_value in current_values.items():
                baseline_value = base_values.get(ordinal)
                if baseline_value is None:
                    continue
                x = delta_value(current_value, baseline_value)
                if x is not None and finite(x) and finite(y):
                    add_stats(stats, ordinal, x, y)
    top = top_rows(definitions, stats, total_pairs, min_pairs, min_coverage, 50)
    return stats, total_pairs, {
        "label": group["label"],
        "comparison_key": group["comparison_key"],
        "slice_set_id": group["slice_set_id"],
        "run_ids": run_ids,
        "excluded_run_ids": [run_id for run_id in group["run_ids"] if run_id not in run_ids],
        "baseline_run": baseline_run,
        "latest_run": run_ids[-1],
        "pair_count": total_pairs,
        "top_positive": [row for row in top if row["pearson_r"] > 0][:25],
        "top_negative": [row for row in top if row["pearson_r"] < 0][:25],
        "top_absolute": top,
    }


def build_trends(
    connection: sqlite3.Connection,
    group: dict[str, object],
    top_ordinals: list[int],
    valid: dict[str, dict[str, sqlite3.Row]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    run_ids = group.get("analysis_run_ids", group["run_ids"])
    baseline_run = run_ids[0]
    baseline_dumps = load_dumps(connection, baseline_run)
    top_set = set(top_ordinals)
    baseline_values = {
        slice_id: decode(blob, top_set) for slice_id, blob in baseline_dumps.items()
    }
    trend_values: dict[int, dict[str, list[float]]] = {
        ordinal: {run_id: [] for run_id in run_ids} for ordinal in top_ordinals
    }
    hotspot_values: dict[int, dict[str, list[float]]] = {
        ordinal: defaultdict(list) for ordinal in top_ordinals
    }
    for run_id in run_ids:
        current_dumps = load_dumps(connection, run_id)
        for slice_id, current_blob in current_dumps.items():
            base_row = valid.get(baseline_run, {}).get(slice_id)
            current_row = valid.get(run_id, {}).get(slice_id)
            base_values = baseline_values.get(slice_id)
            if not base_row or not current_row or not base_values:
                continue
            current_values = decode(current_blob, top_set)
            workload = current_row["workload"]
            for ordinal in top_ordinals:
                base_value = base_values.get(ordinal)
                current_value = current_values.get(ordinal)
                if base_value is None or current_value is None:
                    continue
                change = delta_value(current_value, base_value)
                if change is None:
                    continue
                trend_values[ordinal][run_id].append(change)
                if run_id == run_ids[-1]:
                    hotspot_values[ordinal][workload].append(change)
    trends = []
    for ordinal in top_ordinals:
        trends.append({
            "ordinal": ordinal,
            "runs": [
                {
                    "run_id": run_id,
                    "short_commit": group["run_meta"][run_id]["short_commit"],
                    "snapshot_at": group["run_meta"][run_id]["snapshot_at"],
                    "median_delta": median_or_none(trend_values[ordinal][run_id]),
                    "samples": len(trend_values[ordinal][run_id]),
                }
                for run_id in run_ids
            ],
        })
    hotspots = []
    for ordinal in top_ordinals[:12]:
        rows = [
            {"workload": workload, "median_delta": median_or_none(values), "samples": len(values)}
            for workload, values in hotspot_values[ordinal].items()
            if values
        ]
        rows.sort(key=lambda row: row["median_delta"], reverse=True)
        hotspots.append({"ordinal": ordinal, "latest_workloads": rows[:12]})
    return trends, hotspots


def render_html(output: Path) -> None:
    data = json.loads((output / "analysis.json").read_text(encoding="utf-8"))
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    html = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>全量性能计数器 Insight</title><style>
:root{font-family:Inter,system-ui,-apple-system,"Segoe UI",sans-serif;color:#182334;background:#f3f5f7;line-height:1.45}*{box-sizing:border-box}body{margin:0}main{max-width:1440px;margin:auto;padding:28px 32px 60px}header{display:flex;justify-content:space-between;align-items:end;gap:24px;border-bottom:1px solid #d9dee5;padding-bottom:20px}h1{font-size:30px;margin:0 0 5px}h2{font-size:18px;margin:0}.muted{color:#687386;font-size:13px}.controls{display:flex;gap:12px;align-items:center}select,button{font:inherit;border:1px solid #c8d0da;border-radius:6px;background:white;padding:8px 10px}button{cursor:pointer}.cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:20px 0}.card,.panel{background:white;border:1px solid #dce1e7;border-radius:8px;padding:16px}.card strong{display:block;font-size:25px;margin-top:4px}.grid{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(380px,.8fr);gap:16px}.panel{margin-bottom:16px}.panel-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}.bars{display:grid;gap:7px}.bar-row{display:grid;grid-template-columns:minmax(200px,2fr) 1fr 56px;gap:9px;align-items:center;font-size:12px}.bar-label{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.bar-track{height:9px;background:#edf0f3;border-radius:3px;overflow:hidden}.bar{height:100%;background:#1d6f8a}.bar.negative{background:#bb4b3b}.bar-value{text-align:right;font-variant-numeric:tabular-nums}.legend{display:flex;gap:14px;font-size:12px;color:#687386;margin-top:10px}.line-chart{width:100%;height:320px}.line-chart svg{width:100%;height:100%;overflow:visible}.axis{stroke:#dfe4e9;stroke-width:1}.line{fill:none;stroke-width:2.2}.dot{stroke:white;stroke-width:1}.table-wrap{overflow:auto}.table{width:100%;border-collapse:collapse;font-size:12px}.table th,.table td{padding:8px;border-bottom:1px solid #edf0f3;text-align:left;vertical-align:top}.table th{color:#687386;font-weight:600;position:sticky;top:0;background:white}.table td.num{text-align:right;font-variant-numeric:tabular-nums}.pill{display:inline-block;padding:2px 6px;border-radius:4px;background:#edf5f7;color:#1d6f8a;font-size:11px}.note{background:#fff8e6;border-left:3px solid #d39b27;padding:11px 13px;color:#604b1d;font-size:13px;margin:16px 0}.full{grid-column:1/-1}@media(max-width:900px){main{padding:20px 14px}.cards{grid-template-columns:repeat(2,1fr)}.grid{grid-template-columns:1fr}.bar-row{grid-template-columns:minmax(140px,2fr) 1fr 50px}}
</style></head><body><main><header><div><h1>全量性能计数器 Insight</h1><div class="muted" id="scope"></div></div><div class="controls"><label class="muted">比较组 <select id="group"></select></label><a id="csv" href="counter-correlations.csv" download><button type="button">下载 CSV</button></a></div></header><div class="note">以下结果使用同一切片的基线差分：x 为计数器 <code>log1p</code> 变化，y 为 CPI 变化。它们是定位线索，不是因果证明；请结合 RTL 递增条件、窗口和反证计数器复核。</div><section class="cards" id="cards"></section><div class="grid"><section class="panel"><div class="panel-head"><h2>计数器变化与 CPI 变化的正相关线索</h2><span class="muted">r 越大越值得检查</span></div><div id="positive" class="bars"></div></section><section class="panel"><div class="panel-head"><h2>负相关线索</h2><span class="muted">可能与吞吐提升同向</span></div><div id="negative" class="bars"></div></section><section class="panel full"><div class="panel-head"><h2>候选计数器跨提交趋势</h2><span class="muted">相对最早观测的中位 log1p 变化</span></div><div id="trend" class="line-chart"></div><div id="trendLegend" class="legend"></div></section><section class="panel"><div class="panel-head"><h2>最新观测的 workload 热点</h2><span class="muted">中位变化最大的 workload</span></div><div class="table-wrap"><table class="table"><thead><tr><th>计数器</th><th>Workload</th><th>中位变化</th><th>样本</th></tr></thead><tbody id="hotspots"></tbody></table></div></section><section class="panel"><div class="panel-head"><h2>数据口径</h2></div><div id="details" class="muted"></div></section></div></main><script>
const DATA=__DATA__;
const $=id=>document.getElementById(id); const colors=['#1d6f8a','#bb4b3b','#7a5a9e','#b27a2c','#378a5b','#a34c78'];
const fmt=(v,d=3)=>Number.isFinite(v)?v.toFixed(d):'—';
function group(){return DATA.groups.find(x=>x.comparison_key===$('group').value)||DATA.groups[0]}
function label(ord){return DATA.counter_labels[String(ord)]||String(ord)}
function bars(target, rows){$(target).innerHTML=rows.slice(0,12).map(row=>{const width=Math.min(100,Math.abs(row.pearson_r)*100);return `<div class="bar-row"><span class="bar-label" title="${esc(label(row.ordinal))}">${esc(label(row.ordinal))}</span><span class="bar-track"><span class="bar ${row.pearson_r<0?'negative':''}" style="display:block;width:${width}%"></span></span><span class="bar-value">${fmt(row.pearson_r,3)}</span></div>`}).join('')||'<span class="muted">没有满足覆盖率/样本数门槛的计数器。</span>'}
function esc(x){return String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function renderTrend(g){const trends=g.trends.slice(0,6);const w=1100,h=300,p=38;let vals=trends.flatMap(t=>t.runs.map(r=>r.median_delta)).filter(Number.isFinite);let lo=Math.min(0,...vals),hi=Math.max(0,...vals);if(hi-lo<1e-9){hi=1;lo=-1}const x=i=>p+i*(w-2*p)/Math.max(1,g.run_ids.length-1),y=v=>h-p-(v-lo)*(h-2*p)/(hi-lo);let svg=`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="计数器趋势图"><line class="axis" x1="${p}" y1="${y(0)}" x2="${w-p}" y2="${y(0)}"/>`;trends.forEach((t,j)=>{const pts=t.runs.map((r,i)=>Number.isFinite(r.median_delta)?`${x(i)},${y(r.median_delta)}`:'').filter(Boolean).join(' ');svg+=`<polyline class="line" stroke="${colors[j]}" points="${pts}"/>`;t.runs.forEach((r,i)=>{if(Number.isFinite(r.median_delta))svg+=`<circle class="dot" fill="${colors[j]}" cx="${x(i)}" cy="${y(r.median_delta)}" r="3"/>`})});svg+='</svg>';$('trend').innerHTML=svg;$('trendLegend').innerHTML=trends.map((t,j)=>`<span><b style="color:${colors[j]}">●</b> ${esc(label(t.ordinal))}</span>`).join('')}
function render(){const g=group();const pos=g.top_positive,neg=g.top_negative;bars('positive',pos);bars('negative',neg);$('scope').textContent=`${g.label} · ${g.run_ids.length} 次运行 · ${g.pair_count.toLocaleString()} 个同切片跨提交配对 · ${DATA.counter_count.toLocaleString()} 项全量定义`;$('cards').innerHTML=[['运行数',g.run_ids.length],['差分配对',g.pair_count.toLocaleString()],['全量计数器',DATA.counter_count.toLocaleString()],['分析变换','log1p 差分']].map(x=>`<div class="card"><span class="muted">${x[0]}</span><strong>${x[1]}</strong></div>`).join('');renderTrend(g);const hot=g.hotspots.flatMap(t=>(t.latest_workloads||[]).slice(0,3).map(x=>({...x,ordinal:t.ordinal}))).sort((a,b)=>b.median_delta-a.median_delta).slice(0,18);$('hotspots').innerHTML=hot.map(x=>`<tr><td title="${esc(label(x.ordinal))}">${esc(label(x.ordinal))}</td><td><span class="pill">${esc(x.workload)}</span></td><td class="num">${fmt(x.median_delta,4)}</td><td class="num">${x.samples}</td></tr>`).join('')||'<tr><td colspan="4">暂无热点。</td></tr>';$('details').innerHTML=`全量定义 ${DATA.counter_count.toLocaleString()} 项；选择清单 ${DATA.selected_run_count} 次运行；原始数据库 ${esc(DATA.database)}。每个 raw dump 的窗口为 ${esc(DATA.window_id)}。`}
DATA.groups.forEach(g=>{const o=document.createElement('option');o.value=g.comparison_key;o.textContent=g.label;$('group').append(o)});$('group').value=DATA.groups[0]?.comparison_key||'';$('group').addEventListener('change',render);render();
</script></body></html>'''.replace('__DATA__', payload)
    (output / "index.html").write_text(html, encoding="utf-8")


def main() -> int:
    args = parse_args()
    if args.min_pairs < 2 or args.top < 1 or not 0 <= args.min_coverage <= 1:
        raise SystemExit("--min-pairs/--top must be positive and --min-coverage must be in [0, 1]")
    selected = read_selection(args.selection)
    connection = sqlite3.connect(args.db)
    connection.row_factory = sqlite3.Row
    try:
        definitions = {
            int(row["ordinal"]): row
            for row in connection.execute("SELECT * FROM raw_counter_definitions ORDER BY ordinal")
        }
        run_meta = {
            row["run_id"]: dict(row)
            for row in connection.execute(
                "SELECT run_id, commit_sha, snapshot_at FROM runs WHERE run_id IN (%s)"
                % ",".join("?" * len(selected)), tuple(selected)
            )
        } if selected else {}
        for run_id, meta in run_meta.items():
            meta["short_commit"] = meta["commit_sha"][:10]
        valid: dict[str, dict[str, sqlite3.Row]] = defaultdict(dict)
        for row in connection.execute(
            """SELECT sr.*, s.workload, s.slice_name
               FROM slice_results sr JOIN slices s USING(slice_id)
              WHERE sr.status='valid'"""
        ):
            if row["run_id"] in selected:
                valid[row["run_id"]][row["slice_id"]] = row
        groups_by_key: dict[tuple[str, str], dict[str, object]] = {}
        for run_id in selected:
            row = connection.execute(
                """SELECT r.run_id, r.comparison_key, r.slice_set_id, ss.name
                   FROM runs r JOIN slice_sets ss USING(slice_set_id) WHERE r.run_id=?""",
                (run_id,),
            ).fetchone()
            if not row:
                continue
            key = (row["comparison_key"], row["slice_set_id"])
            group = groups_by_key.setdefault(key, {
                "label": row["name"], "comparison_key": row["comparison_key"],
                "slice_set_id": row["slice_set_id"], "run_ids": [], "run_meta": run_meta,
            })
            group["run_ids"].append(run_id)
        groups = [group for group in groups_by_key.values() if len(group["run_ids"]) >= 2]
        for group in groups:
            group["run_ids"].sort(key=lambda run_id: run_meta[run_id]["snapshot_at"])
        all_stats = empty_stats(len(definitions))
        total_pairs = 0
        group_results = []
        group_stats = []
        for group in sorted(groups, key=lambda item: item["label"]):
            stats, pair_count, result = analyze_group(
                connection, group, definitions, valid, args.min_pairs,
                args.min_coverage,
            )
            group_results.append(result)
            group_stats.append((group, stats))
            group["analysis_run_ids"] = result["run_ids"]
            total_pairs += pair_count
            for ordinal in range(1, len(definitions) + 1):
                for index in range(7):
                    all_stats[ordinal][index] += stats[ordinal][index]
        top_global = top_rows(definitions, all_stats, total_pairs, args.min_pairs, args.min_coverage, args.top)
        selected_ordinals = []
        for row in top_global + [item for result in group_results for item in result["top_absolute"][:args.top]]:
            if row["ordinal"] not in selected_ordinals:
                selected_ordinals.append(row["ordinal"])
        selected_ordinals = selected_ordinals[:max(args.top, 12)]
        for result, (group, _) in zip(group_results, group_stats):
            trends, hotspots = build_trends(connection, group, selected_ordinals, valid)
            result["trends"] = trends
            result["hotspots"] = hotspots
        output = args.output_dir
        output.mkdir(parents=True, exist_ok=True)
        csv_path = output / "counter-correlations.csv"
        with csv_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=[
                "scope", "group", "ordinal", "metric_id", "source_path", "source_name", "pairs",
                "coverage", "nonzero_delta_fraction", "pearson_r", "abs_pearson_r",
            ])
            writer.writeheader()
            for row in top_global:
                writer.writerow({"scope": "global", "group": "all", **{key: row[key] for key in writer.fieldnames[2:]}})
            for result in group_results:
                for row in result["top_absolute"]:
                    writer.writerow({"scope": "group", "group": result["label"], **{key: row[key] for key in writer.fieldnames[2:]}})
        payload = {
            "schema": "all-counter-analysis/v1",
            "database": str(args.db.resolve()),
            "selection": str(args.selection.resolve()),
            "window_id": "perf_final_dump",
            "transform": TRANSFORM,
            "counter_count": len(definitions),
            "selected_run_count": len(selected),
            "pair_count": total_pairs,
            "min_pairs": args.min_pairs,
            "min_coverage": args.min_coverage,
            "top_global": top_global,
            "counter_labels": {
                str(ordinal): f'{row["source_name"]} @ {row["source_path"]}'
                for ordinal, row in definitions.items()
            },
            "groups": group_results,
        }
        (output / "analysis.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        render_html(output)
        print(json.dumps({
            "output": str(output), "groups": len(groups), "selected_runs": len(selected),
            "counter_definitions": len(definitions), "pairs": total_pairs,
            "top": top_global[:5],
        }, ensure_ascii=False, indent=2))
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

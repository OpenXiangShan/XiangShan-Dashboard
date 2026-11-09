#!/usr/bin/env python3
"""Build normalized bottleneck evidence from selected PERF raw counters."""

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

WINDOW = "perf_final_dump"


def finite(x):
    return isinstance(x, (int, float)) and math.isfinite(float(x))


def pearson(rows):
    if len(rows) < 2:
        return None
    xs, ys = zip(*rows)
    sx, sy = sum(xs), sum(ys)
    sx2, sy2 = sum(x*x for x in xs), sum(y*y for y in ys)
    sxy = sum(x*y for x, y in rows)
    n = len(rows)
    vx, vy = n*sx2-sx*sx, n*sy2-sy*sy
    if vx <= 1e-18 or vy <= 1e-18:
        return None
    return (n*sxy-sx*sy) / math.sqrt(vx*vy)


def decode(blob):
    return {int(k): float(v) for k, v in json.loads(zlib.decompress(blob))["values"].items()
            if finite(v)}


def read_selected(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [r["run_id"] for r in csv.DictReader(f)
                if r.get("disposition") == "selected" and r.get("run_id")]


def ratio(num, den):
    return num / den if den and finite(num) and finite(den) else None


def make_targets(defs):
    by_key = {(r["source_path"], r["source_name"]): int(r["ordinal"]) for r in defs}
    targets = {}

    def exact(name, path_contains):
        return [o for (p, n), o in by_key.items() if n == name and path_contains in p]

    def add(name, nums, dens, description=""):
        nums = [x for x in nums if x]
        dens = [x for x in dens if x]
        if nums and dens:
            targets[name] = {"numerators": nums, "denominators": dens,
                             "description": description}

    rob = "ctrlBlock.rob"
    # The enq_rs/select/enq_rs_execute counters are intentionally excluded:
    # RTL subtracts timestamp fields as UInt, and the database contains signed/
    # wrapped values (for example load_latency_enq_rs=-3.28e13).
    # These fields are exported separately in the raw analysis and are not
    # valid latency evidence until the timestamp instrumentation is fixed.
    for fu in ("load", "store", "brh", "alu", "mul"):
        for stage in ("dispatch", "issue", "execute", "commit"):
            add(f"rob_{fu}_{stage}_per_instr", exact(f"{fu}_latency_{stage}", rob),
                exact(f"{fu}_instr_cnt", rob), f"{fu} average ROB {stage} latency proxy")
    rob_idle = [o for (p, n), o in by_key.items()
                if n == "s_idle_to_idle" and p.endswith("ctrlBlock.rob")]
    add("rob_idle_fraction", rob_idle, exact("clock_cycle", "ctrlBlock.rob"),
        "ROB idle-to-idle cycles / core cycles")
    add("rab_idle_fraction", exact("s_idle_to_idle", "ctrlBlock.rob.rab"),
        exact("clock_cycle", "ctrlBlock.rob"), "RAB idle-to-idle cycles / core cycles")
    add("vtype_idle_fraction", exact("s_idle_to_idle", "ctrlBlock.rob.vtypeBuffer"),
        exact("clock_cycle", "ctrlBlock.rob"), "VTypeBuffer idle-to-idle cycles / core cycles")
    add("ptw_idle_fraction", exact("ptw_fsm_idle_cycle", ".ptw."),
        exact("clock_cycle", "ctrlBlock.rob"), "PTW idle cycles / core cycles")

    icache = "frontend.inner.icache.missUnit"
    add("icache_prefetch_empty_mshr", exact("prefetchMshrEmptyCnt_sum", icache),
        exact("prefetchMshrEmptyCnt_sampled", icache), "average empty prefetch MSHR slots")
    add("icache_fetch_empty_mshr", exact("fetchMshrEmptyCnt_sum", icache),
        exact("fetchMshrEmptyCnt_sampled", icache), "average empty fetch MSHR slots")

    dcache = "memBlock.inner.dcache.dcache.missQueue.entries_"
    for stem, desc in (
        ("miss_penalty", "average DCache MSHR lifetime"),
        ("load_miss_penalty_to_use", "average load miss to refill-to-use lifetime"),
        ("a_to_d_penalty", "average DCache acquire-to-grant lifetime"),
    ):
        add(f"dcache_{stem}_avg", exact(f"{stem}_sum", dcache),
            exact(f"{stem}_sampled", dcache), desc)
    for name, desc in (
        ("miss_req_primary", "primary DCache miss count"),
        ("miss_req_merged", "merged DCache miss count"),
        ("penalty_blocked_by_channel_A", "DCache channel A blocked cycles"),
        ("penalty_waiting_for_channel_D", "DCache channel D waiting cycles"),
        ("penalty_waiting_for_channel_E", "DCache channel E waiting cycles"),
    ):
        add(f"dcache_{name}_per_cycle", exact(name, dcache),
            exact("clock_cycle", "ctrlBlock.rob"), desc)

    replay = "loadQueue.loadQueueReplay"
    causes = ("rar_nack", "raw_nack", "nuke", "mem_amb", "tlb_miss",
              "bank_conflict", "dcache_replay", "forward_fail",
              "dcache_miss", "storeQueue_multi_match")
    for cause in causes:
        add(f"replay_{cause}_per_cycle", exact(f"replay_{cause}", replay),
            exact("clock_cycle", "ctrlBlock.rob"), f"load replay {cause} count / cycle")
        add(f"replay_{cause}_residence_avg",
            exact(f"replay_{cause}_release_latency_cycles", replay),
            exact(f"replay_{cause}_release_count", replay),
            f"load replay {cause} average residence cycles")

    for name, desc in (
        ("exec_stall_cycle", "top-down execute stall"),
        ("mem_stall_anyload", "top-down load memory stall"),
        ("mem_stall_store", "top-down store memory stall"),
        ("mem_stall_l1miss", "top-down L1 miss stall"),
        ("mem_stall_l2miss", "top-down L2 miss stall"),
        ("mem_stall_l3miss", "top-down L3 miss stall"),
    ):
        add(f"topdown_{name}_fraction", exact(name, "topDownMod"),
            exact("clock_cycle", "ctrlBlock.rob"), desc)
    for name, desc in (
        ("stall_cycle_rob", "dispatch blocked by ROB"),
        ("stall_cycle_iq", "dispatch blocked by issue queue"),
        ("stall_cycle_lsqFull", "dispatch blocked by LSQ"),
        ("stall_cycle_allowDispatch", "dispatch resource block"),
    ):
        add(f"dispatch_{name}_fraction", exact(name, "ctrlBlock.dispatch"),
            exact("clock_cycle", "ctrlBlock.rob"), desc)

    for queue in ("issueQueueLDU2_Ldu", "issueQueueSTA0_StaMou", "issueQueueSTA1_StaMou"):
        for fu in ("load", "store"):
            for source in ("reg", "bypass", "forward"):
                add(f"issue_{queue}_{fu}_{source}_per_issue",
                    exact(f"issue_datasource_{source}_futype_{fu}", queue),
                    exact("issue_instr_count", queue),
                    f"{queue} {fu} {source} data-source events per issued instruction")
    return targets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    db_path, selection, output = args.db, args.selection, args.output_dir
    output.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    selected = set(read_selected(selection))
    defs = list(conn.execute("select * from raw_counter_definitions order by ordinal"))
    targets = make_targets(defs)
    def_map = {(r["source_path"], r["source_name"]): int(r["ordinal"]) for r in defs}
    health_defs = []
    for (path, name), ordinal in def_map.items():
        if path.endswith("ctrlBlock.rob") and "_latency_" in name:
            fu = name.split("_latency_", 1)[0]
            instr = def_map.get((path, f"{fu}_instr_cnt"))
            if instr:
                health_defs.append((name, ordinal, instr))

    run_meta = {r["run_id"]: dict(r) for r in conn.execute(
        "select run_id, commit_sha, snapshot_at, comparison_key, slice_set_id from runs"
    ) if r["run_id"] in selected}
    valid = defaultdict(dict)
    for r in conn.execute(
        """select sr.run_id,sr.slice_id,sr.cpi,sr.instructions,sr.cycles,
                  s.workload,s.slice_name
           from slice_results sr join slices s using(slice_id)
           where sr.status='valid'"""
    ):
        if r["run_id"] in selected:
            valid[r["run_id"]][r["slice_id"]] = dict(r)

    groups = defaultdict(list)
    for rid, meta in run_meta.items():
        groups[(meta["comparison_key"], meta["slice_set_id"])].append(rid)
    for key in groups:
        groups[key].sort(key=lambda rid: run_meta[rid]["snapshot_at"])
        groups[key] = [rid for rid in groups[key] if conn.execute(
            "select 1 from raw_counter_dumps where run_id=? "
            "and availability!='parse_error' and available_count>0 limit 1", (rid,)
        ).fetchone()]

    values = defaultdict(lambda: defaultdict(dict))
    health = {name: {"samples": 0, "bad": 0, "negative": 0, "max_abs_per_instr": 0.0}
              for name, _, _ in health_defs}
    rows = conn.execute(
        """select run_id,slice_id,values_zlib from raw_counter_dumps
           where availability!='parse_error' and run_id in (%s)"""
        % ",".join("?" * len(selected)), tuple(selected)
    )
    for r in rows:
        run_id, slice_id = r["run_id"], r["slice_id"]
        if run_id not in valid or slice_id not in valid[run_id]:
            continue
        raw = decode(r["values_zlib"])
        for name, ordinal, instr_ordinal in health_defs:
            value, instr = raw.get(ordinal), raw.get(instr_ordinal)
            if value is not None and instr and instr > 0:
                per_instr = abs(value) / instr
                item = health[name]
                item["samples"] += 1
                item["negative"] += int(value < 0)
                item["bad"] += int(value < 0 or per_instr > 100)
                item["max_abs_per_instr"] = max(item["max_abs_per_instr"], per_instr)
        for name, spec in targets.items():
            num = sum(raw.get(o, 0.0) for o in spec["numerators"])
            den = sum(raw.get(o, 0.0) for o in spec["denominators"])
            v = ratio(num, den)
            if v is not None and math.isfinite(v):
                values[run_id][slice_id][name] = v

    def weighted_cpi(run_id):
        rs = list(valid[run_id].values())
        cycles = sum(float(r["cycles"] or 0) for r in rs)
        inst = sum(float(r["instructions"] or 0) for r in rs)
        return {"cpi": cycles / inst if inst else None, "ipc": inst / cycles if cycles else None,
                "slices": len(rs), "instructions": inst, "cycles": cycles}

    def run_metrics(run_id):
        out = {}
        for name in targets:
            vals = [m[name] for m in values[run_id].values() if name in m]
            out[name] = {"median": statistics.median(vals) if vals else None, "samples": len(vals)}
        return out

    result = {
        "schema": "rtl-bottleneck-analysis/v1", "database": str(db_path),
        "window_id": WINDOW, "selected_runs": len(selected),
        "normalized_metric_count": len(targets), "groups": [],
        "counter_health": health,
        "metrics": {name: {"description": spec["description"]} for name, spec in targets.items()},
    }
    csv_rows = []
    for (comparison_key, slice_set_id), run_ids in sorted(groups.items()):
        if len(run_ids) < 2:
            continue
        run_summary = []
        for rid in run_ids:
            run_summary.append({"run_id": rid, "commit": run_meta[rid]["commit_sha"][:10],
                                 "snapshot_at": run_meta[rid]["snapshot_at"],
                                 **weighted_cpi(rid), "metrics": run_metrics(rid)})
        baseline = run_ids[0]
        pairs = []
        for current in run_ids[1:]:
            for sid in set(valid[baseline]) & set(valid[current]):
                y = valid[current][sid]["cpi"] - valid[baseline][sid]["cpi"]
                if not finite(y):
                    continue
                for name in targets:
                    a, b = values[baseline][sid].get(name), values[current][sid].get(name)
                    if a is not None and b is not None:
                        pairs.append((name, b-a, y))
        correlations = []
        for name in targets:
            xy = [(dx, dy) for n, dx, dy in pairs if n == name]
            r = pearson(xy)
            if r is not None:
                correlations.append({"metric": name, "pairs": len(xy),
                                     "pearson_r": r, "abs_pearson_r": abs(r)})
        correlations.sort(key=lambda x: (x["abs_pearson_r"], x["pairs"]), reverse=True)
        transitions = []
        for a, b in zip(run_summary, run_summary[1:]):
            deltas = []
            for name in targets:
                va, vb = a["metrics"][name]["median"], b["metrics"][name]["median"]
                if va is not None and vb is not None:
                    deltas.append({"metric": name, "delta": vb-va, "baseline": va, "current": vb})
            deltas.sort(key=lambda x: abs(x["delta"]), reverse=True)
            cpi_delta = b["cpi"]-a["cpi"] if a["cpi"] is not None and b["cpi"] is not None else None
            ipc_delta = b["ipc"]-a["ipc"] if a["ipc"] is not None and b["ipc"] is not None else None
            transitions.append({"from_commit": a["commit"], "to_commit": b["commit"],
                                "cpi_delta": cpi_delta, "ipc_delta": ipc_delta,
                                "top_metric_deltas": deltas[:20]})
            for x in deltas[:20]:
                csv_rows.append({"comparison_key": comparison_key, "from_commit": a["commit"],
                                 "to_commit": b["commit"], "cpi_delta": cpi_delta,
                                 "metric": x["metric"], "delta": x["delta"],
                                 "baseline": x["baseline"], "current": x["current"]})
        result["groups"].append({"comparison_key": comparison_key, "slice_set_id": slice_set_id,
                                 "run_ids": run_ids, "pair_count": len(pairs)//len(targets),
                                 "run_summary": run_summary, "correlations": correlations[:30],
                                 "transitions": transitions})

    with (output / "normalized-transition-metrics.csv").open("w", encoding="utf-8", newline="") as f:
        fields = ["comparison_key", "from_commit", "to_commit", "cpi_delta", "metric",
                  "delta", "baseline", "current"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(csv_rows)
    (output / "analysis.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    (output / "metric-definitions.json").write_text(
        json.dumps(result["metrics"], ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"groups": len(result["groups"]), "targets": len(targets),
                      "output": str(output),
                      "group_pairs": [(g["comparison_key"], g["pair_count"]) for g in result["groups"]]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

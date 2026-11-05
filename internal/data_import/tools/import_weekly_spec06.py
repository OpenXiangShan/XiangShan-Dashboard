#!/usr/bin/env python3
"""Import archived weekly SPEC06 GCC16 scores and their verified slice evidence.

The evidence directory contains <Actions run ID>/{run,jobs,artifacts}.json,
job.log and the original score artifact. Logs establish the tested checkout;
the workflow's head SHA can refer to a different commit. Historical failures
and logs written after score publication never become valid observations.
"""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import copy
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commit_ipc_trend.ingest import build_manifest
from commit_ipc_trend.metrics import aggregate_cpi
from commit_ipc_trend.parsers import load_registry, parse_perf_counters, parse_slice_output
from commit_ipc_trend.schema import CPI_FORMULA_VERSION, canonical_hash
from commit_ipc_trend.spec06 import benchmark_for
from commit_ipc_trend.store import Store

SCORE_NAME = "score-spec06-rva23-novec-gcc16-1.0c.txt"
COUNTER_PARSER = "perf-aliases/v3"


def _upsert_definitions(connection, registry):
    for definition in registry["metrics"]:
        connection.execute(
            """INSERT INTO metric_definitions(
                   metric_id, semantic_version, display_name, category, kind, unit,
                   direction, source_match_json, aggregation_rule
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(metric_id, semantic_version) DO UPDATE SET
                   display_name=excluded.display_name, category=excluded.category,
                   kind=excluded.kind, unit=excluded.unit, direction=excluded.direction,
                   source_match_json=excluded.source_match_json,
                   aggregation_rule=excluded.aggregation_rule""",
            (
                definition["metric_id"], definition["semantic_version"],
                definition["display_name"], definition["category"], definition["kind"],
                definition["unit"], definition["direction"],
                json.dumps({
                    "path": definition["source_path"],
                    "name": definition["source_name"],
                    "expected_matches": definition["expected_matches"],
                }, sort_keys=True),
                definition["aggregation_rule"],
            ),
        )


def counter_cache_usable(data, registry):
    """Reuse old caches only when no newly supported name could recover data."""
    values = data["values"]
    alias_metrics = {row["metric_id"] for row in registry["metrics"] if row.get("source_aliases")}
    if data.get("parser") == COUNTER_PARSER:
        return True
    if any(v["metric_id"] in alias_metrics and v["availability"] == "missing" for v in values):
        return False
    return data.get("parser") == "perf-whitespace/v2" or not any(
        v["availability"] == "parse_error" for v in values)


def parse_counter_file(source):
    path = Path(source)
    before = path.stat()
    registry = load_registry()
    clock = copy.deepcopy(registry["metrics"][0])
    clock.update(metric_id="score_roi_cycles", source_name="clock_cycle")
    registry["metrics"].append(clock)
    values = parse_perf_counters(path, registry)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f"source changed while parsing: {source}")
    return source, values


def import_counters(args):
    """Read each actual PERF source once even when multiple CI runs reuse it."""
    connection = sqlite3.connect(args.db)
    connection.row_factory = sqlite3.Row
    jobs = [dict(row) for row in connection.execute(
        """SELECT sr.run_id, sr.slice_id, sr.source_err_uri, s.workload, sm.weight
           FROM slice_results sr JOIN slices s USING(slice_id)
           JOIN runs r USING(run_id)
           JOIN slice_set_members sm ON sm.slice_set_id=r.slice_set_id AND sm.slice_id=s.slice_id
           WHERE sr.status='valid' AND json_extract(r.experiment_json, '$.workflow')='weekly.yml'
             AND json_extract(r.experiment_json, '$.history_repair_version') IS NULL"""
    )]
    missing = [job for job in jobs if not Path(job["source_err_uri"]).is_file()]
    missing_runs = {job["run_id"] for job in missing}
    jobs = [job for job in jobs if Path(job["source_err_uri"]).is_file()]
    sources = sorted({job["source_err_uri"] for job in jobs})
    parsed = {}
    cache = args.output / "counter-cache"
    cache.mkdir(parents=True, exist_ok=True)
    todo = []
    registry = load_registry()
    for source in sources:
        stamp = Path(source).stat()
        name = canonical_hash((source, stamp.st_size, stamp.st_mtime_ns))
        cached = cache / (name + ".json")
        if cached.exists():
            data = json.loads(cached.read_text())
            if counter_cache_usable(data, registry):
                parsed[source] = data["values"]
            else:
                todo.append(source)
        else:
            todo.append(source)
    print(f"Parsing {len(todo)} unique PERF logs ({len(jobs)} valid CI slice observations)", flush=True)
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for index, (source, values) in enumerate(pool.map(parse_counter_file, todo, chunksize=4), 1):
            stamp = Path(source).stat()
            name = canonical_hash((source, stamp.st_size, stamp.st_mtime_ns))
            write_json(cache / (name + ".json"), {"source": source, "values": values, "parser": COUNTER_PARSER})
            parsed[source] = values
            if index % 100 == 0 or index == len(todo):
                print(f"Parsed {index}/{len(todo)}", flush=True)
                write_json(args.output / "counter-progress.json", {"parsed": index, "total": len(todo)})
    rows = []
    for job in jobs:
        for value in parsed[job["source_err_uri"]]:
            if value["metric_id"] == "score_roi_cycles":
                continue
            rows.append((job["run_id"], job["slice_id"], value["metric_id"], value["semantic_version"],
                         value["raw_value"], value["numerator"], value["denominator"], value["value"],
                         value["availability"], value["window_id"], value["dump_time"], value["source_uri"]))
    with connection:
        _upsert_definitions(connection, load_registry())
        connection.executemany("""INSERT OR REPLACE INTO counter_values(
            run_id,slice_id,metric_id,semantic_version,raw_value,numerator,denominator,value,
            availability,window_id,dump_time,source_uri) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    summary = {"unique_logs": len(sources), "valid_slice_observations": len(jobs),
               "counter_observations": len(rows), "availability": dict(Counter(row[8] for row in rows)),
               "missing_logs": len(missing)}
    write_json(args.output / "counter-summary.json", summary)
    print(json.dumps(summary), flush=True)
    # Published SPEC scores use the final PERF window, not the cumulative
    # simulator_out ROI summary used by the workload IPC trend.
    score_audit = []
    for run in connection.execute("""SELECT run_id,experiment_json FROM runs
        WHERE json_extract(experiment_json, '$.workflow')='weekly.yml'
          AND json_extract(experiment_json, '$.history_repair_version') IS NULL"""):
        experiment = json.loads(run["experiment_json"])
        rid = experiment["actions_run_id"]
        if run["run_id"] in missing_runs:
            score_audit.append({"actions_run_id": rid, "status": "missing_source_logs",
                                "checks": [], "all_match": False})
            continue
        receipt = json.loads((args.output / "receipts" / f"{rid}.json").read_text())
        profile = json.loads(Path(receipt["checkpoint_list"]).read_text())
        workloads = {}
        for job in jobs:
            if job["run_id"] != run["run_id"]:
                continue
            vals = {v["metric_id"]: v for v in parsed[job["source_err_uri"]]}
            cycles, instructions = vals["score_roi_cycles"], vals["rob_committed_instructions"]
            if cycles["availability"] != "available" or instructions["availability"] != "available":
                continue
            cpi = cycles["value"] / instructions["value"]
            n, w = workloads.get(job["workload"], (0, 0))
            workloads[job["workload"]] = n + job["weight"] * cpi, w + job["weight"]
        times = {}
        for workload, (n, w) in workloads.items():
            group = benchmark_for(workload)
            times[group] = times.get(group, 0) + n / w * float(profile[workload]["insts"]) / 3e9
        score_text = Path(receipt["score_file"]).read_text()
        checks = []
        for line in score_text.splitlines():
            match = re.match(r"(\d{3}\.\S+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)", line)
            if match:
                name, _, ref, published = match.groups()
                computed = float(ref) / times[name] / 3 if name in times else None
                checks.append({"benchmark": name, "published": float(published), "recomputed": computed,
                               "matches": computed is not None and abs(computed - float(published)) < 0.002})
        score_audit.append({"actions_run_id": rid, "checks": checks,
                            "all_match": len(checks) == 29 and all(c["matches"] for c in checks)})
    write_json(args.output / "score-reproduction.json", score_audit)
    connection.close()


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def failed_points(text):
    match = re.search(r"Failed (?:Workloads|Checkpoints) =+\s*(\[[\s\S]*?\])", text)
    if not match:
        raise ValueError("score artifact has no failure inventory")
    return {name.rsplit("_", 1)[0] for name in json.loads(match[1])}


def import_weekly(args):
    records = []
    with Store(args.db) as store:
        store.initialize()
        for evidence in sorted(args.evidence.glob("*")):
            if not evidence.is_dir():
                continue
            ci = json.loads((evidence / "run.json").read_text())
            if ci["conclusion"] != "success":
                if args.skip_invalid:
                    continue
                raise ValueError(f"CI {ci['id']} was not successful")
            jobs = json.loads((evidence / "jobs.json").read_text())
            job = next(j for j in jobs if "SPEC06 GCC16" in j["name"] or j["name"].startswith("Run GCC16"))
            if job["conclusion"] != "success":
                if args.skip_invalid:
                    continue
                raise ValueError("GCC16 job failed")
            log = (evidence / "job.log").read_text(errors="replace")
            lines = log.splitlines()
            position = next(i for i, line in enumerate(lines) if "[command]/usr/bin/git log -1 --format=%H" in line)
            sha = re.search(r"\b([0-9a-f]{40})\s*$", lines[position + 1])[1]
            original = Path(re.findall(r"SPEC_DIR: (\S+)", log)[-1])
            profile = args.gcc16_profile.resolve()
            checkpoint = Path(re.findall(r"CKPT_HOME: (\S+)", log)[-1])
            if "gcc16_rva23_novec_260820" not in str(checkpoint):
                raise ValueError("unexpected checkpoint profile")
            if not original.name.split("-")[1] == sha[:9]:
                raise ValueError("checkout SHA differs from report directory")
            report = args.report_root / original.name
            if not report.is_dir():
                report = args.archive_root / original.name
            if not report.is_dir():
                if args.skip_invalid:
                    print(json.dumps({"actions_run_id": ci["id"], "status": "missing_report",
                                      "source": str(original)}), flush=True)
                    continue
                raise ValueError(f"missing report: {original}")
            dram_config = next((parsed["dram_config"] for source in sorted(report.glob("*/simulator_out.txt"))
                                if (parsed := parse_slice_output(source)).get("dram_config")), None)
            if not dram_config:
                raise ValueError(f"report does not contain a DRAMsim3 config: {report}")
            score = (evidence / SCORE_NAME).resolve()
            artifacts = json.loads((evidence / "artifacts.json").read_text())
            artifact = next(a for a in artifacts if a["name"] == "score")
            score_time = datetime.fromisoformat(artifact["created_at"].replace("Z", "+00:00"))
            score_text = score.read_text()
            if re.search(r"Data Directory\s*:\s*(.*)", score_text)[1].strip() != str(original):
                raise ValueError("CI score belongs to a different report")
            failures = failed_points(score_text)
            receipt = {
                "schema_version": "perf-trigger-receipt/v1", "status": "completed",
                "report_dir": str(report.resolve()), "checkpoint_list": str(profile),
                "score_file": str(score), "commit_sha": sha,
                "actions_run_id": str(ci["id"]), "branch_label": "kunminghu-v3",
                "started_at": job["started_at"], "completed_at": score_time.isoformat(),
                "experiment": {
                    "config": "DefaultConfig", "emulator": "gsim",
                    "benchmark_type": "spec06-rva23-novec-gcc16-1.0c",
                    "checkpoint_identity": str(checkpoint), "warmup": "20000000",
                    "max_instr": "40000000", "max_cycles": "unlimited",
                    "dram_config": dram_config,
                    "cpu_frequency_mhz": "3000", "dram_frequency_mhz": "1600",
                    "build_options": "CST_COMPILE_OPT=;CST_RUN_OPT=",
                    "score_formula_version": "score-spec06/published-v1",
                },
                "benchmark_map": {w: benchmark_for(w) for w in json.loads(profile.read_text())},
            }
            receipt_path = args.output / "receipts" / f"{ci['id']}.json"
            write_json(receipt_path, receipt)
            manifest = build_manifest(receipt_path, args.git_repo)
            members = manifest["slice_set"]["members"]
            for member, result in zip(members, manifest["slice_results"]):
                source = Path(result["source_out_uri"])
                point = f"{member['workload']}_{member['checkpoint']}"
                image = result.get("checkpoint_image")
                invalid = None
                if point in failures:
                    invalid = "failed_in_ci_score_snapshot"
                elif image and not image.startswith(str(checkpoint) + "/"):
                    invalid = "checkpoint_profile_mismatch"
                elif source.is_file() and source.stat().st_mtime > score_time.timestamp() + 5:
                    invalid = "source_updated_after_ci_score"
                if invalid:
                    result.update(status="missing", error_code=invalid)
                    for field in ("ipc_computed", "ipc_reported", "cpi", "instructions", "cycles"):
                        result[field] = None
            manifest["aggregate_results"] = []
            for workload in sorted({m["workload"] for m in members}):
                rows = [{"weight": m["weight"], "cpi": r.get("cpi"), "status": r["status"]}
                        for m, r in zip(members, manifest["slice_results"]) if m["workload"] == workload]
                # The fixed full profile is authoritative; its decimal weights
                # can sum to 0.999743155 rather than exactly one.
                aggregate = aggregate_cpi(rows, math.fsum(row["weight"] for row in rows))
                for metric in ("weighted_cpi", "equivalent_ipc"):
                    manifest["aggregate_results"].append({
                        "level": "workload", "object_id": workload, "metric_id": metric,
                        "formula_version": CPI_FORMULA_VERSION, "value": aggregate[metric],
                        "numerator": aggregate["numerator"], "denominator": aggregate["denominator"],
                        "coverage_weight": aggregate["coverage_weight"],
                        "valid_member_count": aggregate["valid_member_count"],
                        "total_member_count": len(rows), "status": aggregate["status"],
                        "source_uri": str(profile),
                    })
            run = manifest["run"]
            simulators = sorted({r["simulator_build"].split()[0].removeprefix("emu-")
                                 for r in manifest["slice_results"] if r.get("simulator_build")})
            run["comparison_key"] = canonical_hash({"declared": run["comparison_key"], "observed_emulators": simulators})
            run["status"] = "published" if all(r["status"] == "valid" for r in manifest["slice_results"]) else "partial"
            run["score_generated_at"] = score_time.isoformat()
            run["snapshot_at"] = score_time.isoformat()
            # Byte identity of the immutable CI artifact identifies score publication.
            # ROI file metadata remains in snapshot_identity from build_manifest.
            run["run_id"] = canonical_hash({"ci_run_id": ci["id"], "artifact_id": artifact["id"],
                                            "score_sha256": hashlib.sha256(score.read_bytes()).hexdigest(),
                                            "source_snapshot": run["snapshot_identity"]})
            run["experiment"].update(
                workflow="weekly.yml", actions_run_number=ci["run_number"],
                actions_url=ci["html_url"], workflow_head_sha=ci["head_sha"],
                score_publication_status="published", score_coverage="1.0c",
                checkpoint_profile=str(checkpoint.parent), comparison_conditions="unverified",
                comparison_status="provisional", comparison_policy="historical-diagnostic/v1",
                comparison_dataset="weekly:spec06-gcc16-rva23-novec-260820:1.0c",
                comparison_label="Weekly · SPEC06 GCC16 · 1.0c",
                condition_evidence=str(evidence.resolve()),
                comparison_condition_note="CI defaults recovered; historical build reuse not fully verified",
                ci_failed_points=sorted(failures), original_report_uri=str(original),
                score_artifact_id=artifact["id"],
                observed_emulators=simulators,
            )
            manifest["artifacts"].extend([
                {"slice_id": None, "kind": "actions", "uri": ci["html_url"]},
                {"slice_id": None, "kind": "ci_log", "uri": str((evidence / "job.log").resolve())},
            ])
            write_json(args.output / "manifests" / f"{ci['id']}.json", manifest)
            audit = store.import_manifests([manifest], source_root=str(report))
            record = {"actions_run_id": ci["id"], "run_number": ci["run_number"],
                      "run_id": run["run_id"], "tested_sha": sha,
                      "workflow_head_sha": ci["head_sha"], "created_at": ci["created_at"],
                      "score_published_at": score_time.isoformat(), "status": run["status"],
                      "valid_slices": sum(r["status"] == "valid" for r in manifest["slice_results"]),
                      "expected_slices": len(members), "failed_points": sorted(failures),
                      "invalid_reasons": dict(Counter(r.get("error_code") for r in manifest["slice_results"] if r["status"] != "valid")),
                      "scores": {s["object_id"]: s["value"] for s in manifest["scores"] if s["level"] == "suite"}}
            records.append(record)
            print(json.dumps(record, ensure_ascii=False), flush=True)
    write_json(args.output / "import-summary.json", {"runs": records, "run_count": len(records)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--report-root", type=Path, required=True)
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument("--git-repo", type=Path, required=True)
    parser.add_argument("--gcc16-profile", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--counters-only", action="store_true")
    parser.add_argument("--skip-invalid", action="store_true")
    parser.add_argument("--jobs", type=int, default=8)
    args = parser.parse_args()
    if args.counters_only:
        import_counters(args)
    else:
        import_weekly(args)


if __name__ == "__main__":
    main()

"""Recover a fixed SPEC06 profile without combining distinct run snapshots."""
from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
import gzip
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tarfile

from .metrics import aggregate_cpi
from .parsers import load_registry, parse_perf_counters, parse_slice_output
from .schema import CPI_FORMULA_VERSION, PARSER_VERSION, SCHEMA_VERSION, canonical_hash
from .spec06 import benchmark_for
from .store import Store

VERSION = "gcc16-history/v1"


def profile_members(profile_path: Path) -> tuple[dict, list[dict]]:
    profile = json.loads(profile_path.read_text())
    identity = canonical_hash(profile)
    members = []
    for workload, definition in sorted(profile.items()):
        for checkpoint, weight in definition["points"].items():
            members.append(dict(
                slice_id=canonical_hash([VERSION, identity, workload, int(checkpoint)]),
                benchmark=benchmark_for(workload), workload=workload,
                slice=f"{workload}_{checkpoint}_{float(weight):.12g}", checkpoint=int(checkpoint),
                checkpoint_identity=str(profile_path.parent.parent), restore_mode="checkpoint-image",
                warmup_definition="simulator-warmup-reset", roi_definition="roi_summary",
                weight=float(weight), weight_kind="checkpoint_profile_weight", ordinal=len(members),
            ))
    return profile, members


def restore_files(archive: Path, destination: Path, wanted: set[str]) -> None:
    """Extract only exact database-referenced log names, preserving their timestamps."""
    wanted = {name for name in wanted if not (destination / name).is_file()}
    if not wanted or not archive.is_file():
        return
    print(f"Restoring {len(wanted)} logs from {archive.name}", flush=True)
    with subprocess.Popen(["zstd", "-dc", str(archive)], stdout=subprocess.PIPE) as process:
        with tarfile.open(fileobj=process.stdout, mode="r|") as contents:
            for member in contents:
                relative = Path(member.name)
                if len(relative.parts) != 3 or not member.isfile():
                    continue
                name = "/".join(relative.parts[1:])
                if name not in wanted:
                    continue
                target = destination / name
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(target.name+f".{os.getpid()}.tmp")
                with contents.extractfile(member) as source, temporary.open("wb") as output:
                    shutil.copyfileobj(source, output)
                os.utime(temporary, (member.mtime, member.mtime))
                temporary.replace(target)
        if process.wait():
            raise RuntimeError(f"cannot read {archive}")


def _log_path(source: str, directory: Path) -> Path:
    original = Path(source)
    return original if original.is_file() else directory / original.parent.name / original.name


def inspect_run(connection: sqlite3.Connection, run: dict, members: list[dict],
                profile_path: Path, report_root: Path, cache: Path,
                weekly_source: Path) -> dict:
    experiment = json.loads(run["experiment_json"])
    profile_root = str(profile_path.parent.parent)
    expected = {(m["workload"], m["checkpoint"]): m for m in members}
    candidates = defaultdict(list)
    for row in connection.execute(
        "SELECT sr.*,s.workload,s.checkpoint,s.checkpoint_identity,s.benchmark "
        "FROM slice_results sr JOIN slices s USING(slice_id) WHERE sr.run_id=?", (run["run_id"],)
    ):
        row = dict(row)
        key = row["workload"], row["checkpoint"]
        identity = row["checkpoint_identity"] or experiment.get("checkpoint_profile", "")
        if key in expected and identity.startswith(profile_root):
            candidates[key].append(row)
    if not candidates:
        return {}
    directory = cache / "source" / Path(run["source_uri"]).name
    cached_weekly_source = weekly_source / Path(run["source_uri"]).name
    if not directory.is_dir() and cached_weekly_source.is_dir():
        directory = cached_weekly_source
    archive = report_root / (Path(run["source_uri"]).name + ".tar.zst")
    wanted = {f"{Path(r['source_out_uri']).parent.name}/simulator_out.txt"
              for rows in candidates.values() for r in rows
              if not Path(r["source_out_uri"]).is_file()}
    restore_files(archive, directory, wanted)
    selected, corrections = {}, []
    protected_errors = {"failed_in_ci_score_snapshot", "source_updated_after_ci_score",
                        "checkpoint_profile_mismatch"}
    for key, rows in candidates.items():
        unique = {row["source_out_uri"]: row for row in rows}
        if len(unique) != 1:
            selected[key] = dict(status="missing", error_code="ambiguous_source_points")
            continue
        row = next(iter(unique.values()))
        path = _log_path(row["source_out_uri"], directory)
        if not path.is_file():
            selected[key] = dict(status="missing", error_code="missing_simulator_out")
            continue
        parsed = parse_slice_output(path)
        if parsed.get("checkpoint_image") and not parsed["checkpoint_image"].startswith(profile_root+"/checkpoint/"):
            parsed.update(status="parse_error", error_code="checkpoint_profile_mismatch")
        if row["error_code"] in protected_errors:
            parsed.update(status=row["status"], error_code=row["error_code"])
        unchanged = all(parsed.get(field) == row[field] for field in ("instructions", "cycles"))
        if parsed.get("seed") is not None and row["seed"] is not None and parsed["seed"] != row["seed"]:
            unchanged = False
        if row["status"]=="valid" and not unchanged:
            parsed.update(status="missing", error_code="source_snapshot_mismatch")
        if parsed["status"]=="parse_error" and row["status"]=="valid":
            corrections.append(dict(run_id=run["run_id"], slice_id=row["slice_id"],
                                    workload=row["workload"], error_code=parsed["error_code"]))
        parsed["seed"] = parsed.get("seed") if parsed.get("seed") is not None else row["seed"]
        selected[key] = {**parsed, "source": row, "out_path": str(path), "unchanged": unchanged}
    counter_count = dict(connection.execute(
        "SELECT slice_id,count(*) FROM counter_values WHERE run_id=? AND availability='available' "
        "GROUP BY slice_id", (run["run_id"],)))
    valid = [point for point in selected.values() if point["status"] == "valid"]
    return dict(run=run, experiment=experiment, selected=selected, directory=directory, archive=archive, cache=cache,
                corrections=corrections, rank=(len(valid), sum(counter_count.get(p["source"]["slice_id"], 0)
                for p in valid), run["snapshot_at"]))


def _parse_counters(job: tuple[str, dict]) -> tuple[str, list[dict]]:
    source, registry = job
    path = Path(source)
    before = path.stat()
    values = parse_perf_counters(path, registry)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f"source changed while parsing: {source}")
    return source, values


def build_manifest(connection: sqlite3.Connection, inspected: dict, profile: dict,
                   members: list[dict], profile_path: Path, registry: dict, jobs: int) -> tuple[dict, dict]:
    source_run = inspected["run"]
    source_id = source_run["run_id"]
    selected = inspected["selected"]
    existing = defaultdict(list)
    for row in connection.execute("SELECT * FROM counter_values WHERE run_id=?", (source_id,)):
        row = dict(row)
        row.pop("run_id")
        existing[row["slice_id"]].append(row)
    needed, reused = {}, {}
    metric_ids = {metric["metric_id"] for metric in registry["metrics"]}
    wanted = set()
    for point in selected.values():
        if point["status"] != "valid":
            continue
        source = point["source"]
        counters = existing[source["slice_id"]]
        available = {row["metric_id"] for row in counters if row["availability"] == "available"}
        if point["unchanged"] and metric_ids <= available:
            reused[source["slice_id"]] = counters
        else:
            path = Path(source["source_err_uri"])
            if not path.is_file():
                wanted.add(f"{path.parent.name}/{path.name}")
            needed[source["slice_id"]] = path
    restore_files(inspected["archive"], inspected["directory"], wanted)
    paths = {str(_log_path(str(path), inspected["directory"])) for path in needed.values()}
    paths = {path for path in paths if Path(path).is_file()}
    counter_cache = inspected["cache"] / "counter-cache"
    counter_cache.mkdir(exist_ok=True)
    parsed, pending, cache_files = {}, [], {}
    for path in sorted(paths):
        stamp = Path(path).stat()
        cache_file = counter_cache / (canonical_hash([VERSION, registry, path, stamp.st_size, stamp.st_mtime_ns])+".json")
        cache_files[path] = cache_file
        if cache_file.is_file():
            parsed[path] = json.loads(cache_file.read_text())
        else:
            pending.append((path, registry))
    if pending:
        print(f"Parsing {len(pending)} counter logs", flush=True)
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            for path, values in pool.map(_parse_counters, pending, chunksize=4):
                parsed[path] = values
                temporary = cache_files[path].with_suffix(f".{os.getpid()}.tmp")
                temporary.write_text(json.dumps(values, separators=(",", ":")))
                temporary.replace(cache_files[path])
    results, counters, mapping = [], [], []
    for member in members:
        point = selected.get((member["workload"], member["checkpoint"]),
                             dict(status="missing", error_code="not_measured_in_source_run"))
        source = point.get("source")
        result = {key: point.get(key) for key in ("status", "error_code", "seed", "window_id",
                  "instructions", "cycles", "ipc_reported", "ipc_computed", "cpi")}
        result.update(slice_id=member["slice_id"], source_out_uri=point.get("out_path", ""),
                      source_err_uri=str(_log_path(source["source_err_uri"], inspected["directory"])) if source else "")
        results.append(result)
        values = []
        if source:
            mapping.append([source["slice_id"], member["slice_id"]])
        if point["status"] == "valid":
            values = reused.get(source["slice_id"], parsed.get(result["source_err_uri"], []))
        by_metric = {row["metric_id"]: row for row in values}
        for definition in registry["metrics"]:
            row = by_metric.get(definition["metric_id"])
            if row:
                counters.append({**row, "slice_id": member["slice_id"]})
            else:
                counters.append(dict(slice_id=member["slice_id"], metric_id=definition["metric_id"],
                    semantic_version=definition["semantic_version"], raw_value=None, numerator=None,
                    denominator=None, value=None, availability="parse_error" if point["status"]=="parse_error" else "missing",
                    window_id="perf_final_dump", dump_time=None, source_uri=result["source_err_uri"]))
    aggregates = []
    for workload in profile:
        rows = [{"weight": member["weight"], "cpi": result["cpi"], "status": result["status"]}
                for member, result in zip(members, results) if member["workload"] == workload]
        aggregate = aggregate_cpi(rows, sum(row["weight"] for row in rows))
        for metric in ("weighted_cpi", "equivalent_ipc"):
            aggregates.append(dict(level="workload", object_id=workload, metric_id=metric,
                formula_version=CPI_FORMULA_VERSION, value=aggregate[metric], numerator=aggregate["numerator"],
                denominator=aggregate["denominator"], coverage_weight=aggregate["coverage_weight"],
                valid_member_count=aggregate["valid_member_count"], total_member_count=len(rows),
                status=aggregate["status"], source_uri=str(profile_path)))
    scores = [dict(row) for row in connection.execute("SELECT * FROM aggregate_results WHERE run_id=? "
              "AND level IN ('suite','benchmark')", (source_id,))]
    for row in scores:
        row.pop("run_id")
    commit = dict(connection.execute("SELECT * FROM commits WHERE commit_sha=?", (source_run["commit_sha"],)).fetchone())
    commit["parents"] = json.loads(commit.pop("parents_json"))
    snapshot = canonical_hash([source_id, [(r["slice_id"], r["status"], r["instructions"], r["cycles"], r["seed"]) for r in results],
                               [(c["slice_id"], c["metric_id"], c["availability"], c["value"]) for c in counters]])
    run = dict(source_run)
    experiment = dict(inspected["experiment"])
    experiment.update(source_run_id=source_id, history_repair_version=VERSION,
        checkpoint_profile=str(profile_path.parent.parent), comparison_dataset="spec06-gcc16-mainline",
        comparison_label="SPEC2006 GCC16 · 完整 profile 主线", comparison_status="provisional",
        comparison_policy="historical-diagnostic/v1")
    for field in ("dram_config", "cpu_frequency_mhz", "dram_frequency_mhz"):
        observed = {point.get(field) for point in selected.values()
                    if point["status"]=="valid" and point.get(field)}
        if len(observed)==1:
            experiment[field] = observed.pop()
    emulators = sorted({point["simulator_build"].split()[0].removeprefix("emu-")
                       for point in selected.values() if point.get("simulator_build")})
    if len(emulators)==1:
        experiment["emulator"] = emulators[0]
    # Recover lost runner defaults only when every valid point agrees with
    # the observed 40M summary and 20M final PERF instruction windows.
    retired = {c["slice_id"]: c["value"] for c in counters
               if c["metric_id"] == "rob_committed_instructions" and c["availability"] == "available"}
    valid_results = [r for r in results if r["status"] == "valid"]
    if valid_results and all(abs(r["instructions"] - 40_000_000) <= 128
            and r["slice_id"] in retired and abs(retired[r["slice_id"]] - 20_000_000) <= 128
            for r in valid_results):
        recovered = {}
        for field, default in (("warmup", "20000000"), ("max_instr", "40000000"), ("max_cycles", "unlimited")):
            if experiment.get(field) in (None, "unknown", "simulator-warmup-reset"):
                recovered[field] = default
        if recovered:
            experiment.update(recovered, comparison_conditions="unverified")
            experiment["comparison_condition_note"] = (
                "GCC16 runner defaults recovered; 40M summary / 20M final PERF windows verified; "
                "historical build options remain unverified")
    conditions = {key: str(experiment[key]).removeprefix("emu-") if experiment.get(key) is not None else None
                  for key in ("emulator", "warmup", "max_instr", "max_cycles", "dram_config",
                              "cpu_frequency_mhz", "dram_frequency_mhz", "build_options")}
    run["comparison_key"] = canonical_hash([VERSION, canonical_hash(profile), run["config"], conditions, emulators])
    run.update(run_id=canonical_hash([VERSION, canonical_hash(profile), snapshot]), experiment=experiment,
        parser_version=PARSER_VERSION+"+"+VERSION, snapshot_identity=snapshot,
        score_formula_version=scores[0]["formula_version"] if scores else source_run["score_formula_version"],
        status="published" if all(r["status"]=="valid" for r in results) else "partial")
    run.pop("experiment_json")
    slice_set = dict(slice_set_id=canonical_hash([(m["slice_id"], m["weight"]) for m in members]),
        name="SPEC2006 GCC16 full profile", version=canonical_hash(profile), source_uri=str(profile_path), members=members)
    artifacts = [dict(slice_id=r["slice_id"], kind=kind, uri=r[field]) for r in results
                 for kind,field in [("simulator_out","source_out_uri"),("simulator_err","source_err_uri")] if r[field]]
    manifest = dict(schema_version=SCHEMA_VERSION, parser_version=run["parser_version"], commit=commit,
        run=run, slice_set=slice_set, scores=scores, slice_results=results, metric_definitions=[],
        counter_values=counters, aggregate_results=aggregates, artifacts=artifacts)
    missing = [dict(workload=m["workload"], checkpoint=m["checkpoint"], reason=r["error_code"])
               for m,r in zip(members,results) if r["status"] != "valid"]
    coverage = [score["coverage_weight"] for score in scores
                if score["level"]=="benchmark" and score["coverage_weight"] is not None]
    audit = dict(commit=commit["short_sha"], source_run_id=source_id, run_id=run["run_id"],
        valid_slices=len(results)-len(missing), expected_slices=len(results),
        valid_workloads=sum(a["status"]=="valid" for a in aggregates if a["metric_id"]=="equivalent_ipc"),
        available_counters=sum(c["availability"]=="available" for c in counters),
        expected_counters=len(counters), parsed_counter_logs=len(pending), raw_mapping=mapping,
        benchmark_scores=sum(score["level"]=="benchmark" for score in scores),
        suite_scores=sum(score["level"]=="suite" for score in scores),
        score_coverage=min(coverage) if coverage else None,
        counter_repairs=[[point["source"]["slice_id"], member["slice_id"]]
            for member in members
            if (point := selected.get((member["workload"], member["checkpoint"])))
            and point["status"]=="valid" and point["unchanged"]
            and point["source"]["slice_id"] in needed], missing=missing)
    return manifest, audit


def prepare(database: Path, profile_path: Path, mainline: set[str], report_root: Path,
            output: Path, weekly_source: Path, jobs: int = 8,
            registry: dict | None = None) -> dict:
    database, profile_path, report_root, output = (path.resolve() for path in (database, profile_path, report_root, output))
    weekly_source = weekly_source.resolve()
    output.mkdir(parents=True, exist_ok=True)
    registry = registry or load_registry()
    profile, members = profile_members(profile_path)
    winners, corrections = {}, []
    with Store(database, read_only=True) as store:
        connection = store.connection
        runs = list(connection.execute("SELECT * FROM runs WHERE config='DefaultConfig' ORDER BY snapshot_at"))
        for index, row in enumerate(runs, 1):
            run = dict(row)
            if run["commit_sha"] not in mainline or json.loads(run["experiment_json"]).get("history_repair_version"):
                continue
            print(f"Inspecting {index}/{len(runs)}: {Path(run['source_uri']).name}", flush=True)
            inspected = inspect_run(connection, run, members, profile_path, report_root,
                                    output, weekly_source)
            if not inspected:
                continue
            corrections.extend(inspected["corrections"])
            old = winners.get(run["commit_sha"])
            if old is None or inspected["rank"] > old["rank"]:
                winners[run["commit_sha"]] = inspected
        audits = []
        for index, inspected in enumerate(winners.values(), 1):
            print(f"Recovering {index}/{len(winners)}: {Path(inspected['run']['source_uri']).name}", flush=True)
            manifest, audit = build_manifest(connection, inspected, profile, members, profile_path, registry, jobs)
            temporary = output / (audit["run_id"]+f".{os.getpid()}.tmp")
            with gzip.open(temporary, "wt") as stream:
                json.dump(manifest, stream, separators=(",", ":"))
            temporary.replace(output / (audit["run_id"]+".json.gz"))
            audits.append(audit)
    plan = dict(version=VERSION, database=str(database), profile=str(profile_path), corrections=corrections, runs=audits)
    temporary = output / f"plan.{os.getpid()}.tmp"
    temporary.write_text(json.dumps(plan, ensure_ascii=False, indent=2)+"\n")
    temporary.replace(output/"plan.json")
    return plan


def apply(database: Path, output: Path) -> dict:
    plan = json.loads((output/"plan.json").read_text())
    with Store(database) as store:
        def manifests():
            for run in plan["runs"]:
                with gzip.open(output/(run["run_id"]+".json.gz"), "rt") as stream:
                    yield json.load(stream)
        audit = store.import_manifests(manifests(), source_root=str(output))
        with store.connection:
            for correction in plan["corrections"]:
                key = correction["run_id"], correction["slice_id"]
                store.connection.execute("UPDATE slice_results SET status='parse_error',error_code=? "
                    "WHERE run_id=? AND slice_id=?", (correction["error_code"], *key))
                store.connection.execute("UPDATE counter_values SET availability='parse_error',value=NULL "
                    "WHERE run_id=? AND slice_id=?", key)
                store.connection.execute("UPDATE raw_counter_dumps SET availability='parse_error' "
                    "WHERE run_id=? AND slice_id=?", key)
                store.connection.execute("UPDATE runs SET status='partial' WHERE run_id=?", (key[0],))
                store.connection.execute("UPDATE aggregate_results SET status='partial',value=NULL,"
                    "coverage_weight=NULL,valid_member_count=NULL "
                    "WHERE run_id=? AND level='workload' AND object_id=?", (key[0], correction["workload"]))
            for run in plan["runs"]:
                store.connection.executemany(
                    "INSERT OR REPLACE INTO counter_values "
                    "SELECT ?,?,metric_id,semantic_version,raw_value,numerator,denominator,value,availability,"
                    "window_id,dump_time,source_uri FROM counter_values "
                    "WHERE run_id=? AND slice_id=? AND availability='available'",
                    [(run["source_run_id"], source, run["run_id"], target)
                     for source,target in run["counter_repairs"]])
                store.connection.executemany(
                    "INSERT OR REPLACE INTO raw_counter_dumps "
                    "SELECT ?,?,window_id,dump_time,availability,available_count,metric_count,compression,values_zlib,source_uri "
                    "FROM raw_counter_dumps WHERE run_id=? AND slice_id=?",
                    [(run["run_id"], target, run["source_run_id"], source) for source,target in run["raw_mapping"]])
    result = dict(normalized_runs=len(plan["runs"]), complete_runs=sum(r["valid_slices"]==r["expected_slices"]
            and r["available_counters"]==r["expected_counters"] and r["suite_scores"]==3
            and r["score_coverage"]==1.0 for r in plan["runs"]),
        valid_workloads=sum(r["valid_workloads"] for r in plan["runs"]),
        corrected_source_slices=len(plan["corrections"]), import_audit=audit)
    (output/"applied.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n")
    return result

"""Import a completed perf-trigger directory using an explicit run contract.

The receipt is created only after the workflow completes. The checkpoint list
is authoritative: missing results remain missing rather than disappearing from
the weighted aggregate. No large PERF log is read unless counters are requested.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

from .metrics import aggregate_cpi
from .parsers import commit_metadata, load_registry, parse_perf_counters, parse_score, parse_slice_output
from .schema import CPI_FORMULA_VERSION, PARSER_VERSION, SCHEMA_VERSION, SCORE_FORMULA_VERSION, ManifestError, canonical_hash, validate_manifest


SLICE_RE = re.compile(r"^(?P<workload>.+)_(?P<checkpoint>\d+)_(?P<weight>\d+(?:\.\d+)?)$")
REQUIRED_CONDITIONS = (
    "config", "emulator", "benchmark_type", "checkpoint_identity", "warmup",
    "max_instr", "max_cycles", "dram_config", "cpu_frequency_mhz",
    "dram_frequency_mhz", "build_options", "score_formula_version",
)


def read_checkpoint_list(path: Path, run_dir: Path | None = None) -> list[dict[str, Any]]:
    members = []
    seen = set()
    profile_weights = {}
    if path.suffix.lower() == ".json":
        profile = json.loads(path.read_text(encoding="utf-8"))
        lines = []
        for workload, data in profile.items():
            if not isinstance(data, dict) or not isinstance(data.get("points"), dict):
                raise ManifestError(f"invalid checkpoint profile for {workload}")
            for checkpoint, weight in data["points"].items():
                prefix = f"{workload}_{checkpoint}_"
                candidates = [p.name for p in run_dir.glob(prefix + "*") if p.is_dir()] if run_dir else []
                # perf-trigger formats the weight in slice directory names to
                # six decimal places, while the authoritative profile JSON may
                # retain more precision.  Match the checkpoint first and allow
                # that formatting quantization when resolving the source path.
                matching = [name for name in candidates if SLICE_RE.fullmatch(name)
                            and math.isclose(float(name.rsplit("_", 1)[1]), float(weight), abs_tol=5.00001e-7)]
                if len(matching) > 1:
                    distances = {
                        name: abs(float(name.rsplit("_", 1)[1]) - float(weight))
                        for name in matching
                    }
                    nearest = min(distances.values())
                    matching = [name for name, distance in distances.items()
                                if math.isclose(distance, nearest, abs_tol=1e-12)]
                if len(matching) > 1:
                    raise ManifestError(f"ambiguous checkpoint directory: {prefix}")
                name = matching[0] if matching else prefix + str(weight)
                lines.append(name)
                profile_weights[name] = float(weight)
    else:
        lines = path.read_text(encoding="utf-8").splitlines()
    for line in lines:
        name = line.strip().rstrip("/")
        if not name or name.startswith("#"):
            continue
        match = SLICE_RE.fullmatch(name)
        if not match:
            raise ManifestError(f"invalid checkpoint list entry: {line!r}")
        workload = match.group("workload")
        checkpoint = int(match.group("checkpoint"))
        if (workload, checkpoint) in seen:
            raise ManifestError(f"duplicate checkpoint: {name}")
        seen.add((workload, checkpoint))
        weight = profile_weights.get(name, float(match.group("weight")))
        if not math.isfinite(weight) or weight <= 0:
            raise ManifestError(f"invalid checkpoint weight: {name}")
        members.append({"name": name, "workload": workload, "checkpoint": checkpoint, "weight": weight})
    if not members:
        raise ManifestError(f"empty checkpoint list: {path}")
    for workload in {m["workload"] for m in members}:
        total = math.fsum(m["weight"] for m in members if m["workload"] == workload)
        if total > 1.0 + 1e-5:
            raise ManifestError(f"checkpoint weights for {workload} sum to {total}, expected <= 1")
    return members


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def build_manifest(receipt_path: Path, git_repo: Path, include_counters: bool = False) -> dict[str, Any]:
    """Build one manifest from a finalization receipt; never infer conditions from directory names."""
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("schema_version") != "perf-trigger-receipt/v1" or receipt.get("status") != "completed":
        raise ManifestError("receipt must be a completed perf-trigger-receipt/v1")
    experiment = receipt.get("experiment", {})
    missing = [key for key in REQUIRED_CONDITIONS if key not in experiment or experiment[key] is None]
    if missing:
        raise ManifestError(f"receipt is missing comparison conditions: {missing}")
    run_dir = Path(receipt["report_dir"]).resolve(strict=True)
    checkpoint_list = Path(receipt["checkpoint_list"]).resolve(strict=True)
    score_path = Path(receipt["score_file"]).resolve(strict=True) if receipt.get("score_file") else None
    if experiment["benchmark_type"].startswith("spec06") and score_path is None:
        raise ManifestError("SPEC06 run requires its published score file")
    if not run_dir.is_dir():
        raise ManifestError(f"report directory is not a directory: {run_dir}")
    full_sha = receipt["commit_sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", full_sha):
        raise ManifestError("receipt must contain the actual 40-digit checkout SHA")
    commit = commit_metadata(full_sha, [git_repo])
    if commit["commit_sha"] != full_sha:
        raise ManifestError("commit does not match checkout SHA")
    commit["short_sha"] = full_sha[:9]
    checkpoint_hash = _sha256(checkpoint_list)
    entries = read_checkpoint_list(checkpoint_list, run_dir)
    benchmark_map = receipt.get("benchmark_map", {})
    if not isinstance(benchmark_map, dict):
        raise ManifestError("benchmark_map must be an object")
    members, results, counters, artifacts = [], [], [], []
    observed_files = [checkpoint_list]
    if score_path:
        observed_files.append(score_path)
        artifacts.append({"slice_id": None, "kind": "score", "uri": str(score_path)})
    registry = load_registry() if include_counters else {"metrics": []}
    for ordinal, entry in enumerate(entries):
        name, workload = entry["name"], entry["workload"]
        slice_dir = run_dir / name
        out_path, err_path = slice_dir / "simulator_out.txt", slice_dir / "simulator_err.txt"
        canonical_name = f"{workload}_{entry['checkpoint']}_{entry['weight']:.12g}"
        slice_id = canonical_hash({
            "checkpoint_identity": experiment["checkpoint_identity"],
            "checkpoint_list_sha256": checkpoint_hash, "workload": workload,
            "checkpoint": entry["checkpoint"], "weight": entry["weight"],
            "warmup": experiment["warmup"], "max_instr": experiment["max_instr"],
            "max_cycles": experiment["max_cycles"],
        })
        member = {
            "slice_id": slice_id, "benchmark": benchmark_map.get(workload, workload),
            "workload": workload, "slice": canonical_name, "checkpoint": entry["checkpoint"],
            "checkpoint_identity": str(experiment["checkpoint_identity"]),
            "restore_mode": "checkpoint-image", "warmup_definition": str(experiment["warmup"]),
            "roi_definition": f"max_instr={experiment['max_instr']};max_cycles={experiment['max_cycles']}",
            "weight": entry["weight"], "weight_kind": "checkpoint_list_weight",
            "ordinal": ordinal,
        }
        members.append(member)
        if out_path.is_file():
            observed_files.append(out_path)
            parsed = parse_slice_output(out_path)
            if parsed["status"] == "valid":
                for field in ("dram_config", "cpu_frequency_mhz", "dram_frequency_mhz"):
                    observed = parsed.get(field)
                    if observed and str(observed) != str(experiment[field]):
                        raise ManifestError(f"{name}: {field} differs from receipt ({observed} != {experiment[field]})")
            artifacts.append({"slice_id": slice_id, "kind": "simulator_out", "uri": str(out_path)})
        else:
            parsed = {"status": "missing", "error_code": "missing_simulator_out"}
        if err_path.is_file():
            observed_files.append(err_path)
            artifacts.append({"slice_id": slice_id, "kind": "simulator_err", "uri": str(err_path)})
        if include_counters and err_path.is_file():
            counters.extend({**row, "slice_id": slice_id} for row in parse_perf_counters(err_path, registry))
        if include_counters and not err_path.is_file():
            parsed = {**parsed, "status": "missing", "error_code": "missing_simulator_err"}
        results.append({**parsed, "slice_id": slice_id, "source_out_uri": str(out_path), "source_err_uri": str(err_path)})
    # The original receipt may carry a timestamp, but it is not proof that the
    # score covers every slice. Require score freshness and complete ROI data.
    score_stale = bool(score_path and any(
        out_path.is_file() and out_path.stat().st_mtime_ns > score_path.stat().st_mtime_ns
        for out_path in (Path(r["source_out_uri"]) for r in results)
    ))
    status = "stale" if score_stale else "published" if all(r["status"] == "valid" for r in results) else "partial"
    if include_counters and any(c["availability"] == "parse_error" for c in counters):
        status = "partial"
    slice_set_id = canonical_hash([(m["slice_id"], m["weight"]) for m in members])
    # Include exact workload selection and window settings; exclude run ID,
    # branch label and commit SHA so different commits can be compared.
    comparison = {"policy": "perf-trigger/v1", "slice_set_id": slice_set_id, **experiment}
    comparison["checkpoint_list_sha256"] = checkpoint_hash
    snapshot = canonical_hash([
        (str(path), path.stat().st_size, path.stat().st_mtime_ns) for path in sorted(observed_files)
    ])
    run_id = canonical_hash({"source_uri": str(run_dir), "snapshot": snapshot, "actions_run_id": receipt.get("actions_run_id")})
    scores = []
    if score_path and experiment["benchmark_type"].startswith("spec06"):
        if experiment["score_formula_version"] != SCORE_FORMULA_VERSION:
            raise ManifestError("SPEC06 score formula version must match the parser")
        scores, _ = parse_score(score_path)
    aggregate_results = []
    for workload in sorted({m["workload"] for m in members}):
        workload_rows = [
            {"weight": m["weight"], "cpi": r.get("cpi"), "status": r["status"]}
            for m, r in zip(members, results) if m["workload"] == workload
        ]
        # A subset such as 0.3c is not a complete workload.
        full_profile = experiment["benchmark_type"].endswith("-1.0c")
        total_weight = math.fsum(row["weight"] for row in workload_rows) if full_profile else 1.0
        agg = aggregate_cpi(workload_rows, total_weight)
        for metric in ("weighted_cpi", "equivalent_ipc"):
            aggregate_results.append({
                "level": "workload", "object_id": workload, "metric_id": metric,
                "formula_version": CPI_FORMULA_VERSION, "value": agg[metric],
                "numerator": agg["numerator"], "denominator": agg["denominator"],
                "coverage_weight": agg["coverage_weight"], "valid_member_count": agg["valid_member_count"],
                "total_member_count": len(workload_rows), "status": agg["status"],
                "source_uri": str(checkpoint_list),
            })
    latest = max(p.stat().st_mtime for p in observed_files)
    manifest = {
        "schema_version": SCHEMA_VERSION, "parser_version": PARSER_VERSION,
        "commit": commit,
        "run": {
            "run_id": run_id, "commit_sha": full_sha, "branch_label": receipt.get("branch_label"),
            "config": experiment["config"], "started_at": receipt.get("started_at"),
            "completed_at": receipt.get("completed_at"), "score_generated_at": _iso(score_path.stat().st_mtime) if score_path else None,
            "snapshot_at": _iso(latest), "source_uri": str(run_dir), "status": status,
            "parser_version": PARSER_VERSION, "score_formula_version": experiment["score_formula_version"],
            "comparison_key": canonical_hash(comparison), "snapshot_identity": snapshot,
            "experiment": {**experiment, "checkpoint_list_sha256": checkpoint_hash,
                           "actions_run_id": receipt.get("actions_run_id"), "comparison_policy_version": "perf-trigger/v1"},
        },
        "slice_set": {"slice_set_id": slice_set_id, "name": experiment["benchmark_type"],
                      "version": checkpoint_hash, "source_uri": "sha256:" + checkpoint_hash, "members": members},
        "scores": scores, "slice_results": results, "counter_values": counters,
        "counter_import": "complete" if include_counters else "skipped",
        "metric_definitions": registry["metrics"], "aggregate_results": aggregate_results,
        "artifacts": artifacts + [{"slice_id": None, "kind": "receipt", "uri": str(receipt_path)}],
    }
    validate_manifest(manifest)
    return manifest

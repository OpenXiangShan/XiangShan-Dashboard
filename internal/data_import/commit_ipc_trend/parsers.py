"""Parsers for the constrained seven-run mcf demo dataset."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import subprocess
from typing import Any, Iterable

from .metrics import aggregate_cpi
from .schema import (
    COMPARISON_POLICY_VERSION,
    CPI_FORMULA_VERSION,
    PARSER_VERSION,
    SCHEMA_VERSION,
    SCORE_FORMULA_VERSION,
    ManifestError,
    canonical_hash,
    validate_manifest,
)


RUN_DIR_RE = re.compile(
    r"^cr(?P<date>\d{6})-(?P<sha>[0-9a-f]{7,40})-(?P<label>.+)$"
)
CONFIG_RE = re.compile(r"^(?P<config>[A-Za-z0-9_]+Config)(?:-|$)")
SUMMARY_RE = re.compile(
    r"instrCnt\s*=\s*([0-9,]+),\s*cycleCnt\s*=\s*([0-9,]+),\s*IPC\s*=\s*([0-9.]+)"
)
SCORE_ROW_RE = re.compile(
    r"^(?P<name>\S+)\s+(?P<time>\d+(?:\.\d+)?|nan)\s+"
    r"(?P<ref>\d+(?:\.\d+)?|nan)\s+(?P<score>\d+(?:\.\d+)?|nan)\s+"
    r"(?P<coverage>\d+(?:\.\d+)?|nan)$"
)
PERF_RE = re.compile(
    r"^\[PERF \]\[time=\s*(?P<time>\d+)\] (?P<path>.*): "
    r"(?P<name>[^,]+),\s+(?P<value>[+-]?\d+(?:\.\d+)?)\s*$"
)


def _number(value: str) -> float | None:
    return None if value == "nan" else float(value)


def load_registry(path: Path | None = None) -> dict[str, Any]:
    registry_path = path or Path(__file__).with_name("metric_registry.json")
    return json.loads(registry_path.read_text(encoding="utf-8"))


def commit_metadata(short_sha: str, repos: Iterable[Path]) -> dict[str, Any]:
    for repo in repos:
        result = subprocess.run(
            (
                "git",
                "-c",
                f"safe.directory={repo}",
                "-C",
                str(repo),
                "show",
                "-s",
                "--format=%H%x00%P%x00%ct%x00%cI%x00%s",
                short_sha,
            ),
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            continue
        parts = result.stdout.rstrip("\n").split("\0", 4)
        if len(parts) == 5:
            return {
                "commit_sha": parts[0],
                "short_sha": short_sha,
                "parents": parts[1].split(),
                "commit_epoch": int(parts[2]),
                "commit_time": parts[3],
                "subject": parts[4],
            }
    raise ManifestError(f"cannot resolve Git commit: {short_sha}")


def parse_score(path: Path, *, require_suite: bool = True) -> tuple[list[dict[str, Any]], dict[str, str]]:
    scores: list[dict[str, Any]] = []
    info: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        match = SCORE_ROW_RE.fullmatch(line)
        if match:
            name = match.group("name")
            score = _number(match.group("score"))
            if score is None:
                continue
            suite = name in {"SPECint2006/GHz", "SPECfp2006/GHz", "SPEC2006"}
            scores.append(
                {
                    "level": "suite" if suite else "benchmark",
                    "object_id": name.removesuffix("/GHz"),
                    "metric_id": "score_per_ghz" if suite else "score",
                    "formula_version": SCORE_FORMULA_VERSION,
                    "value": score,
                    "numerator": None,
                    "denominator": None,
                    "coverage_weight": _number(match.group("coverage")),
                    "valid_member_count": None,
                    "total_member_count": None,
                    "status": "valid",
                    "source_uri": str(path),
                }
            )
            continue
        if ":" in line:
            key, value = (part.strip() for part in line.split(":", 1))
            if key in {
                "Checkpoint Version",
                "DRAMSIM3 Config",
                "Data Directory",
                "Minimal Coverage",
                "Checkpoints Number",
            }:
                info[key] = value
    if require_suite and not any(row["object_id"] == "SPEC2006" for row in scores):
        raise ManifestError(f"SPEC2006 score is missing in {path}")
    return scores, info


def parse_profile(path: Path, workload: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    try:
        profile = payload[workload]
        points = {int(key): float(value) for key, value in profile["points"].items()}
    except (KeyError, TypeError, ValueError) as error:
        raise ManifestError(f"invalid {workload} profile: {path}") from error
    if not points or not math.isclose(sum(points.values()), 1.0, abs_tol=1e-5):
        raise ManifestError(f"{workload} profile weights do not sum to one: {path}")
    return {"instructions": int(profile["insts"]), "points": points}


def parse_slice_output(path: Path) -> dict[str, Any]:
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "",
                  path.read_text(encoding="utf-8", errors="replace"))
    summaries = SUMMARY_RE.findall(text)
    if not summaries:
        return {"status": "parse_error", "error_code": "missing_roi_summary"}
    instructions_raw, cycles_raw, ipc_raw = summaries[-1]
    instructions = int(instructions_raw.replace(",", ""))
    cycles = int(cycles_raw.replace(",", ""))
    ipc_reported = float(ipc_raw)
    ipc_computed = instructions / cycles if cycles else None
    cpi = cycles / instructions if instructions else None
    mismatch = ipc_computed is None or abs(ipc_computed - ipc_reported) > 1e-6
    aborted = bool(re.search(r"\bABORT\b|HIT BAD TRAP|Assertion failed", text))

    def find(pattern: str) -> str | None:
        match = re.search(pattern, text, re.MULTILINE)
        return match.group(1).strip() if match else None

    seed = find(r"^Using seed = (\d+)$")
    return {
        "status": "parse_error" if mismatch or aborted else "valid",
        "error_code": "simulator_aborted" if aborted else "ipc_mismatch" if mismatch else None,
        "seed": int(seed) if seed is not None else None,
        "window_id": "roi_summary",
        "instructions": instructions,
        "cycles": cycles,
        "ipc_reported": ipc_reported,
        "ipc_computed": ipc_computed,
        "cpi": cpi,
        "checkpoint_image": find(r"^The image is (.+)$"),
        "dram_config": find(r"^DRAMSIM3 config: (.+)$"),
        "cpu_frequency_mhz": int(find(r"^CPU_FREQ: (\d+) DRAM_FREQ:") or 0),
        "dram_frequency_mhz": int(find(r"^CPU_FREQ: \d+ DRAM_FREQ: (\d+)$") or 0),
        "simulator_build": find(r"^(emu-(?:gsim|verilator) compiled at .+)$"),
    }


def parse_perf_counters(
    path: Path, registry: dict[str, Any]
) -> list[dict[str, Any]]:
    definitions = registry["metrics"]
    by_name: dict[str, list[dict[str, Any]]] = {}
    for definition in definitions:
        for name in (definition["source_name"], *definition.get("source_aliases", [])):
            by_name.setdefault(name, []).append(definition)

    dumps: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", errors="replace") as stream:
        for raw_line in stream:
            match = PERF_RE.fullmatch(raw_line.rstrip("\n"))
            if not match:
                continue
            dump_time = int(match.group("time"))
            key = (match.group("path"), match.group("name"))
            if not dumps or dumps[-1]["time"] != dump_time:
                dumps.append(
                    {
                        "time": dump_time,
                        "count": 0,
                        "first_key": key,
                        "last_key": key,
                        "found": {},
                    }
                )
            dump = dumps[-1]
            dump["count"] += 1
            dump["last_key"] = key
            for definition in by_name.get(match.group("name"), []):
                if definition["source_path"] == match.group("path"):
                    names = dump["found"].setdefault(definition["metric_id"], {})
                    names.setdefault(match.group("name"), []).append(float(match.group("value")))

    final_dump = dumps[-1] if dumps else None
    reference = max(dumps[:-1], key=lambda dump: dump["count"], default=None)
    dump_complete = bool(
        final_dump
        and reference
        and final_dump["count"] == reference["count"]
        and final_dump["first_key"] == reference["first_key"]
        and final_dump["last_key"] == reference["last_key"]
    )
    found = final_dump["found"] if final_dump else {}
    values: list[dict[str, Any]] = []
    for definition in definitions:
        names = found.get(definition["metric_id"], {})
        # A verified historical name has the same meaning and source path.
        # Prefer the current name; never sum a counter with its old alias.
        if definition["source_name"] in names:
            matches = names[definition["source_name"]]
        elif len(names) == 1:
            matches = next(iter(names.values()))
        else:
            matches = []
        expected = int(definition["expected_matches"])
        if not dump_complete:
            availability, raw_value = "parse_error", None
        elif len(names) > 1 and definition["source_name"] not in names:
            availability, raw_value = "ambiguous", None
        elif not matches:
            availability, raw_value = "missing", None
        elif expected and len(matches) != expected:
            availability, raw_value = "ambiguous", None
        elif definition["aggregation_rule"] == "sum":
            availability, raw_value = "available", math.fsum(matches)
        else:
            availability, raw_value = "available", matches[0]
        values.append(
            {
                "metric_id": definition["metric_id"],
                "semantic_version": definition["semantic_version"],
                "raw_value": raw_value,
                "numerator": None,
                "denominator": None,
                "value": raw_value,
                "availability": availability,
                "window_id": "perf_final_dump",
                "dump_time": final_dump["time"] if final_dump else None,
                "source_uri": str(path),
            }
        )
    return values


def _iso_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


def _snapshot_identity(paths: Iterable[Path]) -> str:
    rows = []
    for path in sorted(paths):
        stat = path.stat()
        rows.append((str(path), stat.st_size, stat.st_mtime_ns))
    return canonical_hash(rows)


def build_run_manifest(
    run_dir: Path,
    git_repos: Iterable[Path],
    profile_path: Path | None = None,
    registry_path: Path | None = None,
    include_counters: bool = True,
) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    match = RUN_DIR_RE.fullmatch(run_dir.name)
    if not match:
        raise ManifestError(f"unsupported run directory name: {run_dir.name}")
    config_match = CONFIG_RE.match(match.group("label"))
    if not config_match:
        raise ManifestError(f"cannot identify config from: {run_dir.name}")
    config = config_match.group("config")
    commit = commit_metadata(match.group("sha"), tuple(git_repos))

    score_paths = sorted(run_dir.glob("score-spec06-*-1.0c.txt"))
    if len(score_paths) != 1:
        raise ManifestError(f"expected one 1.0c score file in {run_dir}, found {len(score_paths)}")
    score_path = score_paths[0]
    scores, score_info = parse_score(score_path)
    checkpoint_version = score_info.get("Checkpoint Version")
    if not checkpoint_version:
        raise ManifestError(f"checkpoint version is missing in {score_path}")
    profile_path = profile_path or Path(checkpoint_version).parent / "json" / "mcf.json"
    profile = parse_profile(profile_path, "mcf")
    registry = load_registry(registry_path)

    members: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    counters: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = [
        {"slice_id": None, "kind": "score", "uri": str(score_path)}
    ]
    snapshot_paths = [score_path, profile_path]
    run_conditions: dict[str, Any] | None = None
    for ordinal, (checkpoint, weight) in enumerate(
        sorted(profile["points"].items(), key=lambda item: item[0])
    ):
        candidates = sorted(run_dir.glob(f"mcf_{checkpoint}_*"))
        if len(candidates) != 1:
            raise ManifestError(
                f"expected one mcf checkpoint {checkpoint} in {run_dir}, found {len(candidates)}"
            )
        slice_dir = candidates[0]
        try:
            directory_weight = float(slice_dir.name.rsplit("_", 1)[1])
        except (IndexError, ValueError) as error:
            raise ManifestError(f"invalid slice directory: {slice_dir}") from error
        if not math.isclose(directory_weight, weight, abs_tol=1e-9):
            raise ManifestError(f"profile/directory weight mismatch: {slice_dir}")

        output_path = slice_dir / "simulator_out.txt"
        error_path = slice_dir / "simulator_err.txt"
        if not output_path.is_file() or not error_path.is_file():
            raise ManifestError(f"missing simulator output in {slice_dir}")
        parsed = parse_slice_output(output_path)
        conditions = {
            key: parsed.get(key)
            for key in (
                "dram_config",
                "cpu_frequency_mhz",
                "dram_frequency_mhz",
            )
        }
        if run_conditions is None:
            run_conditions = conditions
        elif conditions != run_conditions:
            raise ManifestError(f"mixed run conditions in {run_dir}")

        slice_id = canonical_hash(
            {
                "checkpoint_version": checkpoint_version,
                "workload": "mcf",
                "checkpoint": checkpoint,
                "restore_mode": "checkpoint-image",
                "warmup_definition": "simulator-warmup-reset",
                "roi_definition": "simulator-final-summary",
            }
        )
        member = {
            "slice_id": slice_id,
            "benchmark": "429.mcf",
            "workload": "mcf",
            "slice": slice_dir.name,
            "checkpoint": checkpoint,
            "checkpoint_identity": parsed.get("checkpoint_image"),
            "restore_mode": "checkpoint-image",
            "warmup_definition": "simulator-warmup-reset",
            "roi_definition": "simulator-final-summary",
            "weight": weight,
            "weight_kind": "simpoint_weight_unverified",
            "ordinal": ordinal,
        }
        members.append(member)
        result = {
            **parsed,
            "slice_id": slice_id,
            "slice": slice_dir.name,
            "checkpoint": checkpoint,
            "weight": weight,
            "source_out_uri": str(output_path),
            "source_err_uri": str(error_path),
        }
        results.append(result)
        artifacts.extend(
            [
                {"slice_id": slice_id, "kind": "simulator_out", "uri": str(output_path)},
                {"slice_id": slice_id, "kind": "simulator_err", "uri": str(error_path)},
            ]
        )
        snapshot_paths.append(output_path)
        snapshot_paths.append(error_path)
        if include_counters:
            for counter in parse_perf_counters(error_path, registry):
                counters.append({**counter, "slice_id": slice_id})

    slice_set_id = canonical_hash(
        [(row["slice_id"], row["weight"]) for row in members]
    )
    assert run_conditions is not None
    experiment = {
        **run_conditions,
        "checkpoint_version": checkpoint_version,
        "slice_set_id": slice_set_id,
        "warmup_definition": "simulator-warmup-reset",
        "roi_definition": "simulator-final-summary",
        "simulator_semantic_version": "emu-gsim/unversioned",
        "comparison_policy_version": COMPARISON_POLICY_VERSION,
        "comparison_status": "provisional",
    }
    comparison_key = canonical_hash({"config": config, **experiment})
    snapshot_identity = _snapshot_identity(snapshot_paths)
    run_id = canonical_hash(
        {"source_uri": str(run_dir), "snapshot_identity": snapshot_identity}
    )
    status = "published"
    if any(row["status"] != "valid" for row in results) or any(
        row["availability"] == "parse_error" for row in counters
    ):
        status = "partial"
    elif score_path.stat().st_mtime_ns < max(
        Path(row["source_out_uri"]).stat().st_mtime_ns for row in results
    ):
        status = "stale"

    total_weight = math.fsum(row["weight"] for row in members)
    aggregate = aggregate_cpi(results, total_weight)
    aggregate_results = []
    for metric_id in ("weighted_cpi", "equivalent_ipc"):
        aggregate_results.append(
            {
                "level": "workload",
                "object_id": "mcf",
                "metric_id": metric_id,
                "formula_version": CPI_FORMULA_VERSION,
                "value": aggregate[metric_id],
                "numerator": aggregate["numerator"],
                "denominator": aggregate["denominator"],
                "coverage_weight": aggregate["coverage_weight"],
                "valid_member_count": aggregate["valid_member_count"],
                "total_member_count": len(members),
                "status": aggregate["status"],
                "source_uri": str(profile_path),
            }
        )

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "parser_version": PARSER_VERSION,
        "commit": commit,
        "run": {
            "run_id": run_id,
            "commit_sha": commit["commit_sha"],
            "branch_label": match.group("label"),
            "config": config,
            "started_at": None,
            "completed_at": _iso_mtime(max(snapshot_paths, key=lambda path: path.stat().st_mtime_ns)),
            "score_generated_at": _iso_mtime(score_path),
            "snapshot_at": _iso_mtime(max(snapshot_paths, key=lambda path: path.stat().st_mtime_ns)),
            "source_uri": str(run_dir),
            "status": status,
            "parser_version": PARSER_VERSION,
            "score_formula_version": SCORE_FORMULA_VERSION,
            "comparison_key": comparison_key,
            "experiment": experiment,
            "snapshot_identity": snapshot_identity,
        },
        "slice_set": {
            "slice_set_id": slice_set_id,
            "name": "mcf",
            "version": str(profile_path.parent.parent.name),
            "source_uri": str(profile_path),
            "members": members,
        },
        "scores": scores,
        "slice_results": results,
        "counter_values": counters,
        "counter_import": "complete" if include_counters else "skipped",
        "metric_definitions": registry["metrics"],
        "aggregate_results": aggregate_results,
        "artifacts": artifacts,
    }
    validate_manifest(manifest)
    return manifest

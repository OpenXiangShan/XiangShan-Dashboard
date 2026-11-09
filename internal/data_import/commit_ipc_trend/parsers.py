"""Parse CI scores, simulator summaries, PERF counters, and Git metadata."""

from __future__ import annotations

import json
import math
from pathlib import Path
import re
import subprocess
from typing import Any, Iterable

from .schema import SCORE_FORMULA_VERSION, ManifestError


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

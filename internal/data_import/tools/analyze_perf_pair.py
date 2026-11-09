#!/usr/bin/env python3
"""Run XiangShan top-down or rolling tools on a comparable A/B run pair."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from commit_ipc_trend.schema import canonical_hash  # noqa: E402
from commit_ipc_trend.service import TrendService  # noqa: E402
from commit_ipc_trend.store import Store  # noqa: E402
from commit_ipc_trend.rolling import inspect_database  # noqa: E402


def prepare_topdown_inputs(compared: dict, profile_path: Path, output: Path) -> tuple[Path, Path, Path]:
    """Restrict the upstream directory scanner to the compared workload and slices."""
    workload = compared["object_id"]
    profile = json.loads(profile_path.read_text())
    if workload not in profile:
        raise ValueError(f"checkpoint profile is missing workload: {workload}")
    points = profile[workload]["points"]
    expected = {str(row["checkpoint"]): row["weight"] for row in compared["slices"]}
    if set(points) != set(expected) or any(
        abs(float(points[key]) - weight) > 1e-9 for key, weight in expected.items()
    ):
        raise ValueError("checkpoint profile does not match compared workload slices")
    selected_profile = output / "checkpoints.json"
    selected_profile.write_text(json.dumps({workload: profile[workload]}, indent=2) + "\n")
    directories = []
    for side in ("a", "b"):
        directory = output / f"input-{side}"
        directory.mkdir()
        for row in compared["slices"]:
            source = Path(row[f"source_out_uri_{side}"]).resolve(strict=True).parent
            (directory / row["slice"]).mkdir()
            for name in ("simulator_out.txt", "simulator_err.txt"):
                (directory / row["slice"] / name).symlink_to((source / name).resolve(strict=True))
        directories.append(directory)
    return directories[0], directories[1], selected_profile


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", type=Path, required=True)
    p.add_argument("--a", required=True, help="base run ID")
    p.add_argument("--b", required=True, help="target run ID")
    p.add_argument("--workload", required=True)
    p.add_argument("--xiangshan", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--python", default=sys.executable, help="Python with upstream analysis dependencies")
    sub = p.add_subparsers(dest="kind", required=True)
    td = sub.add_parser("topdown")
    td.add_argument("--checkpoint-json", type=Path, required=True)
    td.add_argument("--base-issue", type=float, required=True)
    td.add_argument("--target-issue", type=float, required=True)
    roll = sub.add_parser("rolling")
    roll.add_argument("--base-db", type=Path, required=True)
    roll.add_argument("--target-db", type=Path, required=True)
    roll.add_argument("--perf-name", required=True)
    roll.add_argument("--hart", type=int, default=0)
    args = p.parse_args()
    if not args.db.is_file():
        p.error(f"database is missing: {args.db}")
    interpreter = shutil.which(args.python)
    if interpreter is None:
        p.error(f"Python executable is missing: {args.python}")
    interpreter = os.path.abspath(interpreter)
    args.xiangshan = args.xiangshan.resolve()
    if args.kind == "topdown":
        args.checkpoint_json = args.checkpoint_json.resolve()
    else:
        args.base_db = args.base_db.resolve()
        args.target_db = args.target_db.resolve()
    with Store(args.db) as store:
        store.initialize()
        compared = TrendService(store).compare_points(args.a, args.b, object_id=args.workload)
        if compared["status"] != "comparable":
            p.error(f"comparable A/B runs required: {compared.get('reasons')}")
        base, target = compared["run_a"], compared["run_b"]
        if args.kind == "topdown":
            script = args.xiangshan / "scripts/top-down/top_down.py"
            config = args.xiangshan / "scripts/top-down/configs.py"
            for required in (args.checkpoint_json, script, config):
                if not required.is_file():
                    p.error(f"file is missing: {required}")
            inputs = {"checkpoint_json": str(args.checkpoint_json), "checkpoint_json_sha256": digest(args.checkpoint_json),
                      "base_issue": args.base_issue, "target_issue": args.target_issue,
                      "config_sha256": digest(config), "selection": "compared_workload_slices"}
        else:
            script = args.xiangshan / "scripts/rolling.py"
            if not script.is_file():
                p.error(f"file is missing: {script}")
            for required in (args.base_db, args.target_db):
                if not required.is_file():
                    p.error(f"rolling DB is missing: {required}")
            if args.base_db.samefile(args.target_db):
                p.error("A/B rolling requires two distinct source databases")
            inspect_database(args.base_db, args.hart)
            inspect_database(args.target_db, args.hart)
            inputs = {"base_db": str(args.base_db), "target_db": str(args.target_db),
                      "perf_name": args.perf_name, "hart": args.hart}
        analyzer_hash = digest(script)
        analysis_id = canonical_hash({"kind": args.kind, "a": args.a, "b": args.b,
                                      "workload": args.workload, "inputs": inputs,
                                      "analyzer_sha256": analyzer_hash})
        output = args.output_dir.resolve() / analysis_id
        output.mkdir(parents=True, exist_ok=False)
        if args.kind == "topdown":
            selected_base, selected_target, selected_profile = prepare_topdown_inputs(
                compared, args.checkpoint_json, output
            )
            command = [interpreter, str(script), "-b", str(selected_base),
                       "-r", str(selected_target), "-j", str(selected_profile),
                       "--base-issue", str(args.base_issue), "--ref-issue", str(args.target_issue),
                       "--base-label", "Base", "--ref-label", "Target"]
        if args.kind == "rolling":
            command = [interpreter, str(script), "diff", str(args.base_db), str(args.target_db),
                       "--perf-name", args.perf_name, "--hart", str(args.hart),
                       "--output", str(output / "rolling.png")]
        env = dict(os.environ, MPLBACKEND="Agg", MPLCONFIGDIR=str(output / "mpl-cache"))
        try:
            completed = subprocess.run(command, cwd=output, env=env, capture_output=True, text=True, timeout=3600)
        except (OSError, subprocess.TimeoutExpired) as error:
            completed = subprocess.CompletedProcess(command, -1, "", str(error))
        (output / "stdout.txt").write_text(completed.stdout)
        (output / "stderr.txt").write_text(completed.stderr)
        expected_outputs = [output / "rolling.png"] if args.kind == "rolling" else [
            output / "results" / name for name in (
                "results_base.csv", "results_ref.csv", "results-weighted_base.csv", "results-weighted_ref.csv"
            )
        ]
        success = completed.returncode == 0 and all(path.is_file() and path.stat().st_size for path in expected_outputs)
        record = {"analysis_id": analysis_id, "run_a": base["run_id"], "run_b": target["run_id"],
                  "workload": args.workload, "kind": args.kind, "status": "completed" if success else "failed",
                  "inputs": inputs, "command": command, "analyzer_sha256": analyzer_hash,
                  "returncode": completed.returncode,
                  "outputs": [str(path) for path in output.rglob("*") if path.is_file()]}
        result = output / "analysis.json"
        result.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
        with store.connection:
            store.connection.execute(
                """INSERT INTO analysis_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(analysis_id) DO UPDATE SET status=excluded.status,
                     result_uri=excluded.result_uri""",
                (analysis_id, base["run_id"], target["run_id"], args.kind, record["status"], json.dumps(inputs),
                 str(result), analyzer_hash, datetime.now(timezone.utc).isoformat()),
            )
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 0 if success else 1


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Collect XiangShan CI data and refresh the local internal dashboard."""

from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from dataclasses import dataclass
import fcntl
import io
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
from typing import Any
from urllib.parse import urlencode
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPO = "OpenXiangShan/XiangShan"
DEFAULT_WORKFLOW = "weekly.yml"
DEFAULT_BRANCH = "kunminghu-v3"
LOCAL_ROOT = ROOT / "local"
SCORE_NAME = "score-spec06-rva23-novec-gcc16-1.0c.txt"
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class GitHubApi:
    """Small adapter around the authenticated ``gh api`` command."""

    def __init__(self, repository: str):
        self.repository = repository

    def json(self, endpoint: str) -> dict[str, Any]:
        result = subprocess.run(
            ["gh", "api", endpoint], check=True, capture_output=True, text=True
        )
        return json.loads(result.stdout)

    def bytes(self, endpoint: str) -> bytes:
        result = subprocess.run(
            ["gh", "api", "--allow-escape-sequences", endpoint], check=True, capture_output=True
        )
        return result.stdout

    def workflow_runs(self, workflow: str, branch: str, page_limit: int) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for page in range(1, page_limit + 1):
            query = urlencode({"branch": branch, "per_page": 100, "page": page})
            payload = self.json(
                f"repos/{self.repository}/actions/workflows/{workflow}/runs?{query}"
            )
            runs = payload.get("workflow_runs", [])
            result.extend(runs)
            if len(runs) < 100:
                break
        return result

    def jobs(self, run_id: int, page_limit: int) -> list[dict[str, Any]]:
        return self._paged(
            f"repos/{self.repository}/actions/runs/{run_id}/jobs", "jobs", page_limit
        )

    def artifacts(self, run_id: int, page_limit: int) -> list[dict[str, Any]]:
        return self._paged(
            f"repos/{self.repository}/actions/runs/{run_id}/artifacts",
            "artifacts",
            page_limit,
        )

    def _paged(self, base: str, key: str, page_limit: int) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for page in range(1, page_limit + 1):
            query = urlencode({"per_page": 100, "page": page})
            payload = self.json(f"{base}?{query}")
            rows = payload.get(key, [])
            result.extend(rows)
            if len(rows) < 100:
                break
        return result

    def job_logs(self, job_id: int) -> str:
        return self.bytes(
            f"repos/{self.repository}/actions/jobs/{job_id}/logs"
        ).decode("utf-8", errors="replace")

    def artifact_zip(self, artifact_id: int) -> bytes:
        return self.bytes(
            f"repos/{self.repository}/actions/artifacts/{artifact_id}/zip"
        )


@dataclass(frozen=True)
class JobMetadata:
    job: dict[str, Any]
    log: str
    commit_sha: str
    report_dir: str
    checkpoint_list: str
    checkpoint_home: str


def select_spec06_job(jobs: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [
        job
        for job in jobs
        if "SPEC06 GCC16" in job.get("name", "")
        or job.get("name", "").startswith("Run GCC16")
    ]
    successful = [job for job in candidates if job.get("conclusion") == "success"]
    return successful[0] if successful else (candidates[0] if candidates else None)


def parse_job_log(job: dict[str, Any], log: str) -> JobMetadata:
    clean = ANSI_RE.sub("", log)
    lines = clean.splitlines()
    commit_sha = ""
    for index, line in enumerate(lines):
        if "git log -1 --format=%H" not in line:
            continue
        for candidate in lines[index + 1 : index + 8]:
            match = re.search(r"\b([0-9a-f]{40})\b", candidate, re.IGNORECASE)
            if match:
                commit_sha = match.group(1)
                break
        if commit_sha:
            break

    def last_value(label: str) -> str:
        values = re.findall(rf"\b{re.escape(label)}:\s*(\S+)", clean)
        if not values:
            raise ValueError(f"job log does not contain {label}")
        return values[-1]

    if not commit_sha:
        raise ValueError("job log does not contain the checkout SHA")
    return JobMetadata(
        job=job,
        log=clean,
        commit_sha=commit_sha,
        report_dir=last_value("SPEC_DIR"),
        checkpoint_list=last_value("CKPT_JSON_PATH"),
        checkpoint_home=last_value("CKPT_HOME"),
    )


def score_from_artifact(payload: bytes) -> bytes:
    with ZipFile(io.BytesIO(payload)) as archive:
        names = archive.namelist()
        exact = [name for name in names if Path(name).name == SCORE_NAME]
        candidates = exact or [
            name
            for name in names
            if Path(name).name.startswith("score") and name.endswith(".txt")
        ]
        if not candidates:
            raise ValueError("score artifact does not contain a score text file")
        return archive.read(candidates[0])


def evidence_complete(path: Path) -> bool:
    required = ("run.json", "jobs.json", "artifacts.json", "job.log", SCORE_NAME)
    return path.is_dir() and all((path / name).is_file() for name in required)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def collect_evidence(
    api: GitHubApi,
    *,
    workflow: str,
    branch: str,
    evidence_root: Path,
    report_root: Path,
    page_limit: int,
    max_runs: int,
) -> dict[str, Any]:
    evidence_root.mkdir(parents=True, exist_ok=True)
    runs = api.workflow_runs(workflow, branch, page_limit)[:max_runs]
    summary: dict[str, Any] = {
        "workflow": workflow,
        "branch": branch,
        "seen": len(runs),
        "existing": [],
        "collected": [],
        "skipped": [],
    }
    for run in runs:
        run_id = int(run["id"])
        destination = evidence_root / str(run_id)
        if evidence_complete(destination):
            summary["existing"].append(run_id)
            continue
        if run.get("conclusion") != "success":
            summary["skipped"].append({"run_id": run_id, "reason": "workflow_not_successful"})
            continue

        jobs = api.jobs(run_id, page_limit)
        job = select_spec06_job(jobs)
        if job is None or job.get("conclusion") != "success":
            summary["skipped"].append({"run_id": run_id, "reason": "spec06_job_not_successful"})
            continue

        try:
            metadata = parse_job_log(job, api.job_logs(int(job["id"])))
        except (OSError, ValueError) as error:
            summary["skipped"].append({"run_id": run_id, "reason": str(error)})
            continue

        report_dir = report_root / Path(metadata.report_dir).name
        if not report_dir.is_dir():
            summary["skipped"].append({"run_id": run_id, "reason": f"missing_report:{report_dir}"})
            continue

        artifacts = api.artifacts(run_id, page_limit)
        score_artifacts = [artifact for artifact in artifacts if artifact.get("name") == "score"]
        if not score_artifacts:
            summary["skipped"].append({"run_id": run_id, "reason": "missing_score_artifact"})
            continue

        try:
            score = score_from_artifact(api.artifact_zip(int(score_artifacts[0]["id"])))
        except (OSError, ValueError, KeyError) as error:
            summary["skipped"].append({"run_id": run_id, "reason": str(error)})
            continue

        temporary = Path(tempfile.mkdtemp(prefix=f".{run_id}-", dir=evidence_root))
        try:
            temporary.chmod(0o2770)
            write_json(temporary / "run.json", run)
            write_json(temporary / "jobs.json", jobs)
            write_json(temporary / "artifacts.json", artifacts)
            (temporary / "job.log").write_text(metadata.log)
            (temporary / SCORE_NAME).write_bytes(score)
            if destination.exists():
                shutil.rmtree(destination)
            os.replace(temporary, destination)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        summary["collected"].append(
            {
                "run_id": run_id,
                "run_number": run.get("run_number"),
                "commit_sha": metadata.commit_sha,
                "report_dir": str(report_dir),
            }
        )
    return summary


def run_stage(command: list[str]) -> None:
    print("$ " + " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="shared local JSON configuration")
    parser.add_argument("--data-dir", type=Path, help="root for SQLite, evidence, caches and dashboard JSON")
    parser.add_argument("--update-host", help="host allowed to run updates")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--workflow", default=DEFAULT_WORKFLOW)
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument("--report-root", type=Path,
                        help="local perf-report directory")
    parser.add_argument("--git-repo", type=Path,
                        help="local XiangShan Git checkout")
    parser.add_argument("--db", type=Path)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dashboard-output", type=Path)
    parser.add_argument("--gcc16-profile", type=Path,
                        help="local GCC16 checkpoints_all.json profile")
    parser.add_argument("--page-limit", type=int, default=3)
    parser.add_argument("--max-runs", type=int, default=20)
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--import-only", action="store_true")
    parser.add_argument("--collect-only", action="store_true")
    parser.add_argument("--skip-receipts", action="store_true")
    parser.add_argument("--skip-counters", action="store_true")
    parser.add_argument("--skip-export", action="store_true")
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    paths = ("data_dir", "report_root", "git_repo", "gcc16_profile", "db", "evidence",
             "output", "dashboard_output")
    if args.config:
        config_path = args.config.expanduser().resolve()
        config = json.loads(config_path.read_text())
        unknown = set(config) - {*paths, "update_host"}
        if unknown:
            parser.error(f"unknown configuration fields: {', '.join(sorted(unknown))}")
        for name, value in config.items():
            if getattr(args, name) is None:
                if name in paths:
                    value = Path(value).expanduser()
                    if not value.is_absolute():
                        value = config_path.parent / value
                setattr(args, name, value)
    args.data_dir = (args.data_dir or LOCAL_ROOT).expanduser().resolve()
    for name, relative in (("db", "performance.sqlite"), ("evidence", "evidence"),
                           ("output", "weekly-spec06-gcc16"),
                           ("dashboard_output", "mainline-history")):
        if getattr(args, name) is None:
            setattr(args, name, args.data_dir / relative)
    for name in ("report_root", "git_repo", "gcc16_profile"):
        if getattr(args, name) is None:
            parser.error(f"--{name.replace('_', '-')} is required, directly or in --config")
    for name in paths:
        setattr(args, name, getattr(args, name).expanduser().resolve())
    if args.import_only and args.collect_only:
        parser.error("--import-only and --collect-only are mutually exclusive")
    if args.update_host and socket.gethostname().split(".")[0] != args.update_host.split(".")[0]:
        parser.error(f"run this update on {args.update_host}; current host is {socket.gethostname()}")
    return args


@contextmanager
def update_lock(database: Path):
    database.parent.mkdir(parents=True, exist_ok=True)
    with database.with_name(database.name + ".update.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit(f"another dashboard update is running for {database}") from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.report_root.is_dir():
        raise SystemExit(f"report root is not a directory: {args.report_root}")
    if not args.git_repo.is_dir():
        raise SystemExit(f"XiangShan checkout is not a directory: {args.git_repo}")
    if not args.gcc16_profile.is_file():
        raise SystemExit(f"GCC16 profile does not exist: {args.gcc16_profile}")

    previous_umask = os.umask(0o007)
    try:
        with update_lock(args.db):
            temporary = args.data_dir / "tmp"
            temporary.mkdir(parents=True, exist_ok=True)
            os.environ["SQLITE_TMPDIR"] = str(temporary)
            return refresh(args)
    finally:
        os.umask(previous_umask)


def refresh(args: argparse.Namespace) -> int:
    summary: dict[str, Any] = {"status": "ok"}
    if not args.import_only:
        summary["collection"] = collect_evidence(
            GitHubApi(args.repo),
            workflow=args.workflow,
            branch=args.branch,
            evidence_root=args.evidence,
            report_root=args.report_root,
            page_limit=args.page_limit,
            max_runs=args.max_runs,
        )
    if args.collect_only:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    if not args.import_only:
        run_stage(["git", "-c", f"safe.directory={args.git_repo}", "-C", str(args.git_repo), "fetch", "--no-tags",
                   "--recurse-submodules=no", "origin", args.branch])

    args.output.mkdir(parents=True, exist_ok=True)
    run_stage([
        sys.executable,
        str(ROOT / "tools/import_weekly_spec06.py"),
        "--evidence", str(args.evidence),
        "--report-root", str(args.report_root),
        "--archive-root", str(args.output / "source"),
        "--git-repo", str(args.git_repo),
        "--gcc16-profile", str(args.gcc16_profile),
        "--db", str(args.db),
        "--output", str(args.output),
        "--skip-invalid",
    ])

    if not args.skip_receipts:
        receipt_command = [
            sys.executable,
            str(ROOT / "tools/ingest_completed_runs.py"),
            "--report-root", str(args.report_root),
            "--git-repo", str(args.git_repo),
            "--db", str(args.db),
        ]
        if not args.skip_counters:
            receipt_command.append("--counters")
        run_stage(receipt_command)

    if not args.skip_counters:
        run_stage([
            sys.executable,
            str(ROOT / "tools/import_weekly_spec06.py"),
            "--evidence", str(args.evidence),
            "--report-root", str(args.report_root),
            "--archive-root", str(args.output / "source"),
            "--git-repo", str(args.git_repo),
            "--gcc16-profile", str(args.gcc16_profile),
            "--db", str(args.db),
            "--output", str(args.output),
            "--counters-only",
            "--skip-invalid",
            "--jobs", str(args.jobs),
        ])

    if not args.skip_export:
        selected = []
        if not args.skip_counters:
            recovery = args.dashboard_output.parent / "spec06-gcc-recovery"
            run_stage([sys.executable, str(ROOT / "tools/repair_spec06_history.py"), "prepare",
                "--db", str(args.db), "--profile", str(args.gcc16_profile),
                "--git-repo", str(args.git_repo), "--branch", "origin/"+args.branch,
                "--report-root", str(args.report_root), "--output", str(recovery),
                "--weekly-source", str(args.output / "source"),
                "--jobs", str(args.jobs)])
            run_stage([sys.executable, str(ROOT / "tools/repair_spec06_history.py"), "apply",
                "--db", str(args.db), "--output", str(recovery)])
            plan = json.loads((recovery / "plan.json").read_text())
            selected = [value for run in plan["runs"] for value in ("--run", run["run_id"])]
            with closing(sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
                weekly = connection.execute("""
                    SELECT run_id FROM runs
                    WHERE json_extract(experiment_json, '$.comparison_dataset')
                        = 'weekly:spec06-gcc16-rva23-novec-260820:1.0c'
                    ORDER BY snapshot_at, run_id
                """)
                selected.extend(value for run in weekly for value in ("--run", run[0]))
        run_stage([
            sys.executable,
            str(ROOT / "tools/export_dashboard_history.py"),
            "--db", str(args.db),
            "--output", str(args.dashboard_output),
            *selected,
        ])

    write_json(args.output / "update-summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Generate reviewable architecture labels from a local Git diff."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from commit_ipc_trend.change_labels import classify_paths


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", f"safe.directory={repo}", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def classify_commit(repo: Path, commit: str) -> tuple[str, dict[str, object]]:
    full_sha = _git(repo, "rev-parse", f"{commit}^{{commit}}").strip()
    paths = _git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", full_sha).splitlines()
    entry = classify_paths(paths)
    entry["git_commit"] = full_sha
    entry["commit"] = commit
    return full_sha, entry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--git-repo", type=Path, required=True)
    parser.add_argument("--commit", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    commits: dict[str, dict[str, object]] = {}
    for commit in args.commit:
        full_sha, entry = classify_commit(args.git_repo, commit)
        commits[full_sha] = entry
    payload = {
        "schema_version": "architecture-labels/v1",
        "generator": "commit-ipc-trend/path-heuristic/v1",
        "commits": commits,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"commits": len(commits), "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

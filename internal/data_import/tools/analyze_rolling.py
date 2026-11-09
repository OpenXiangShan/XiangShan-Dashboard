#!/usr/bin/env python3
"""Inspect and analyze one XiangShan rolling DB, or extract one from a tar archive."""

from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from commit_ipc_trend.rolling import (  # noqa: E402
    analyze,
    extract_database,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    extract = sub.add_parser(
        "extract", help="stream one SQLite member from a tar archive (including .tar.zst)"
    )
    extract.add_argument("--archive", type=Path, required=True)
    extract.add_argument("--member", required=True)
    extract.add_argument("--output", type=Path, required=True)
    run = sub.add_parser("run", help="inspect, correlate and plot one rolling DB")
    run.add_argument("--db", type=Path, required=True)
    run.add_argument("--xiangshan", type=Path, required=True)
    run.add_argument(
        "--python", default=sys.executable, help="Python with XiangShan analysis dependencies"
    )
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--source-run", help="Actions URL or source run identifier; provenance only")
    run.add_argument("--hart", type=int, default=0)
    run.add_argument("--aggregate", type=int, default=50)
    run.add_argument("--perf-name", action="append")
    run.add_argument(
        "--prefetch-progress", action="store_true",
        help="optional phase correlation via interpolation",
    )
    run.add_argument("--timeout", type=int, default=3600, help="seconds per analyzer command")
    args = parser.parse_args()
    try:
        if args.mode == "extract":
            extract_database(args.archive, args.member, args.output)
            print(args.output)
            return 0
        if args.hart < 0 or args.aggregate < 1 or args.timeout < 1:
            parser.error("hart must be nonnegative; aggregate and timeout must be positive")
        return analyze(args)
    except (ValueError, OSError, sqlite3.Error, subprocess.SubprocessError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    raise SystemExit(main())

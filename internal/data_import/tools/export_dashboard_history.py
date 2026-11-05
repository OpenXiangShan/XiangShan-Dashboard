#!/usr/bin/env python3
"""Export all selected SQLite measurements, grouped by checkpoint profile."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from commit_ipc_trend.dashboard_export import export_history  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--run",
        action="append",
        default=[],
        help="run ID, report directory name, or commit SHA prefix (repeatable)",
    )
    parser.add_argument(
        "--xs-config", action="append", default=[], help="core configuration (repeatable)"
    )
    args = parser.parse_args(argv)
    summary = export_history(args.db, args.output, runs=args.run, configs=args.xs_config)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

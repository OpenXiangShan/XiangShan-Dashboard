#!/usr/bin/env python3
"""Poll completed perf-trigger receipts and import new immutable snapshots."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from commit_ipc_trend.ingest import build_manifest  # noqa: E402
from commit_ipc_trend.store import Store  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--report-root", type=Path, required=True)
    p.add_argument("--git-repo", type=Path, required=True)
    p.add_argument("--db", type=Path, required=True)
    p.add_argument("--counters", action="store_true")
    args = p.parse_args()
    receipts = sorted(args.report_root.glob("*/.perf-platform-receipt.json"))
    imported, skipped, errors = [], [], []
    with Store(args.db) as store:
        store.initialize()
        for path in receipts:
            try:
                manifest = build_manifest(path, args.git_repo, include_counters=args.counters)
                run_id = manifest["run"]["run_id"]
                prior = store.connection.execute("SELECT run_id FROM runs WHERE run_id = ?", (run_id,)).fetchone()
                existing_counters = store.connection.execute(
                    "SELECT count(*) FROM counter_values WHERE run_id = ?", (run_id,)
                ).fetchone()[0]
                if prior and (not args.counters or existing_counters):
                    skipped.append(run_id)
                    continue
                store.import_manifests([manifest], source_root=str(path.parent))
                imported.append(run_id)
            except (OSError, KeyError, ValueError, RuntimeError) as exc:
                errors.append({"receipt": str(path), "error": str(exc)})
    print(json.dumps({"imported": imported, "skipped": skipped, "errors": errors}, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

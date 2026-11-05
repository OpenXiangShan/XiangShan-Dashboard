"""Small manifest contract shared by the parser and SQLite importer."""

from __future__ import annotations

import hashlib
import json
from typing import Any


SCHEMA_VERSION = "1.0"
PARSER_VERSION = "commit-ipc-trend/0.1"
SCORE_FORMULA_VERSION = "score-spec06/published-v1"
CPI_FORMULA_VERSION = "weighted-cpi/v1"
COMPARISON_POLICY_VERSION = "demo-v1"


class ManifestError(ValueError):
    """Raised when a parsed run does not satisfy the demo contract."""


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ManifestError(f"unsupported schema_version: {manifest.get('schema_version')!r}")
    for key in ("commit", "run", "slice_set", "scores", "slice_results", "counter_values"):
        if key not in manifest:
            raise ManifestError(f"missing manifest field: {key}")

    run = manifest["run"]
    for key in ("run_id", "commit_sha", "config", "source_uri", "comparison_key"):
        if not run.get(key):
            raise ManifestError(f"missing run field: {key}")

    members = manifest["slice_set"].get("members", [])
    results = manifest["slice_results"]
    if not members or len(members) != len(results):
        raise ManifestError("slice set members and results must be non-empty and aligned")
    member_ids = {row["slice_id"] for row in members}
    if member_ids != {row["slice_id"] for row in results}:
        raise ManifestError("slice set membership differs from parsed results")
    counter_import = manifest.get("counter_import", "complete")
    if counter_import not in {"complete", "skipped"}:
        raise ManifestError(f"invalid counter_import mode: {counter_import!r}")
    if counter_import == "skipped" and manifest["counter_values"]:
        raise ManifestError("skipped counter import cannot contain counter values")

"""SQLite schema and transactional manifest import."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable
import uuid

from .schema import validate_manifest


DDL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS commits (
  commit_sha TEXT PRIMARY KEY,
  short_sha TEXT NOT NULL,
  commit_time TEXT NOT NULL,
  commit_epoch INTEGER NOT NULL,
  subject TEXT NOT NULL,
  parents_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS slice_sets (
  slice_set_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  version TEXT NOT NULL,
  source_uri TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS slices (
  slice_id TEXT PRIMARY KEY,
  benchmark TEXT NOT NULL,
  workload TEXT NOT NULL,
  slice_name TEXT NOT NULL,
  checkpoint INTEGER NOT NULL,
  checkpoint_identity TEXT,
  restore_mode TEXT NOT NULL,
  warmup_definition TEXT NOT NULL,
  roi_definition TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS slice_set_members (
  slice_set_id TEXT NOT NULL REFERENCES slice_sets(slice_set_id),
  slice_id TEXT NOT NULL REFERENCES slices(slice_id),
  weight REAL NOT NULL,
  weight_kind TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  PRIMARY KEY (slice_set_id, slice_id)
);

CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  commit_sha TEXT NOT NULL REFERENCES commits(commit_sha),
  branch_label TEXT,
  config TEXT NOT NULL,
  started_at TEXT,
  completed_at TEXT,
  score_generated_at TEXT,
  snapshot_at TEXT NOT NULL,
  imported_at TEXT NOT NULL,
  source_uri TEXT NOT NULL,
  status TEXT NOT NULL,
  parser_version TEXT NOT NULL,
  score_formula_version TEXT NOT NULL,
  comparison_key TEXT NOT NULL,
  slice_set_id TEXT NOT NULL REFERENCES slice_sets(slice_set_id),
  snapshot_identity TEXT NOT NULL,
  experiment_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS slice_results (
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  slice_id TEXT NOT NULL REFERENCES slices(slice_id),
  status TEXT NOT NULL,
  seed INTEGER,
  window_id TEXT,
  instructions INTEGER,
  cycles INTEGER,
  ipc_reported REAL,
  ipc_computed REAL,
  cpi REAL,
  source_out_uri TEXT NOT NULL,
  source_err_uri TEXT NOT NULL,
  error_code TEXT,
  PRIMARY KEY (run_id, slice_id)
);

CREATE TABLE IF NOT EXISTS metric_definitions (
  metric_id TEXT NOT NULL,
  semantic_version TEXT NOT NULL,
  display_name TEXT NOT NULL,
  category TEXT NOT NULL,
  kind TEXT NOT NULL,
  unit TEXT NOT NULL,
  direction TEXT NOT NULL,
  source_match_json TEXT NOT NULL,
  aggregation_rule TEXT NOT NULL,
  PRIMARY KEY (metric_id, semantic_version)
);

CREATE TABLE IF NOT EXISTS counter_values (
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  slice_id TEXT NOT NULL REFERENCES slices(slice_id),
  metric_id TEXT NOT NULL,
  semantic_version TEXT NOT NULL,
  raw_value REAL,
  numerator REAL,
  denominator REAL,
  value REAL,
  availability TEXT NOT NULL,
  window_id TEXT NOT NULL,
  dump_time INTEGER,
  source_uri TEXT NOT NULL,
  PRIMARY KEY (run_id, slice_id, metric_id, semantic_version),
  FOREIGN KEY (metric_id, semantic_version)
    REFERENCES metric_definitions(metric_id, semantic_version)
);

CREATE TABLE IF NOT EXISTS raw_counter_definitions (
  metric_id TEXT PRIMARY KEY,
  semantic_version TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  source_path TEXT NOT NULL,
  source_name TEXT NOT NULL,
  aggregation_rule TEXT NOT NULL,
  expected_matches INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_counter_dumps (
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  slice_id TEXT NOT NULL REFERENCES slices(slice_id),
  window_id TEXT NOT NULL,
  dump_time INTEGER,
  availability TEXT NOT NULL,
  available_count INTEGER NOT NULL,
  metric_count INTEGER NOT NULL,
  compression TEXT NOT NULL,
  values_zlib BLOB NOT NULL,
  source_uri TEXT NOT NULL,
  PRIMARY KEY (run_id, slice_id)
);

CREATE TABLE IF NOT EXISTS aggregate_results (
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  level TEXT NOT NULL,
  object_id TEXT NOT NULL,
  metric_id TEXT NOT NULL,
  formula_version TEXT NOT NULL,
  value REAL,
  numerator REAL,
  denominator REAL,
  coverage_weight REAL,
  valid_member_count INTEGER,
  total_member_count INTEGER,
  status TEXT NOT NULL,
  source_uri TEXT NOT NULL,
  PRIMARY KEY (run_id, level, object_id, metric_id, formula_version)
);

CREATE TABLE IF NOT EXISTS artifacts (
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  slice_id TEXT,
  kind TEXT NOT NULL,
  uri TEXT NOT NULL,
  content_hash TEXT,
  PRIMARY KEY (run_id, kind, uri)
);

CREATE TABLE IF NOT EXISTS import_batches (
  batch_id TEXT PRIMARY KEY,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  parser_version TEXT,
  source_root TEXT,
  status TEXT NOT NULL,
  stats_json TEXT NOT NULL,
  errors_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analysis_runs (
  analysis_id TEXT PRIMARY KEY,
  run_a TEXT NOT NULL REFERENCES runs(run_id),
  run_b TEXT NOT NULL REFERENCES runs(run_id),
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  input_json TEXT NOT NULL,
  result_uri TEXT NOT NULL,
  analyzer_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_commit ON runs(commit_sha);
CREATE INDEX IF NOT EXISTS idx_runs_comparison ON runs(comparison_key, status);
CREATE INDEX IF NOT EXISTS idx_aggregates_lookup
  ON aggregate_results(level, object_id, metric_id, run_id);
CREATE INDEX IF NOT EXISTS idx_slices_name ON slices(slice_name);
CREATE INDEX IF NOT EXISTS idx_counters_lookup
  ON counter_values(metric_id, slice_id, run_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: Path | str, read_only: bool = False):
        self.path = Path(path)
        self.read_only = read_only
        if read_only:
            if not self.path.is_file():
                raise FileNotFoundError(self.path)
            self.connection = sqlite3.connect(
                f"{self.path.resolve().as_uri()}?mode=ro", uri=True
            )
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def initialize(self) -> None:
        if self.read_only:
            raise RuntimeError("cannot initialize a read-only store")
        self.connection.executescript(DDL)

    def validate_schema(self) -> None:
        required = {
            "commits",
            "runs",
            "slices",
            "slice_results",
            "aggregate_results",
            "counter_values",
        }
        present = {
            row[0]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        missing = required - present
        if missing:
            raise RuntimeError(f"invalid trend database; missing tables: {sorted(missing)}")

    def _upsert(self, table: str, row: dict[str, Any], keys: tuple[str, ...]) -> None:
        columns = tuple(row)
        assignments = [column for column in columns if column not in keys]
        conflict = ", ".join(keys)
        update = ", ".join(f"{column}=excluded.{column}" for column in assignments)
        action = f"DO UPDATE SET {update}" if update else "DO NOTHING"
        sql = (
            f"INSERT INTO {table} ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' for _ in columns)}) "
            f"ON CONFLICT ({conflict}) {action}"
        )
        self.connection.execute(sql, tuple(row[column] for column in columns))

    def _insert_immutable(
        self,
        table: str,
        row: dict[str, Any],
        keys: tuple[str, ...],
        ignored: tuple[str, ...] = (),
    ) -> None:
        columns = tuple(row)
        where = " AND ".join(f"{key} = ?" for key in keys)
        existing = self.connection.execute(
            f"SELECT {', '.join(columns)} FROM {table} WHERE {where}",
            tuple(row[key] for key in keys),
        ).fetchone()
        if existing is not None:
            changed = [
                column
                for column in columns
                if column not in ignored and existing[column] != row[column]
            ]
            if changed:
                identity = ", ".join(f"{key}={row[key]!r}" for key in keys)
                raise ValueError(
                    f"immutable {table} definition changed ({identity}): {changed}"
                )
            return
        self.connection.execute(
            f"INSERT INTO {table} ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' for _ in columns)})",
            tuple(row[column] for column in columns),
        )

    def import_manifests(
        self, manifests: Iterable[dict[str, Any]], source_root: str = ""
    ) -> dict[str, Any]:
        manifests = list(manifests)
        batch_id = str(uuid.uuid4())
        started_at = _now()
        parser_version = manifests[0].get("parser_version") if manifests else None
        try:
            with self.connection:
                for manifest in manifests:
                    validate_manifest(manifest)
                    self._import_manifest(manifest)
                stats = self.counts()
                audit = {
                    "batch_id": batch_id,
                    "status": "completed",
                    "source_root": source_root,
                    "manifest_count": len(manifests),
                    "run_ids": [manifest["run"]["run_id"] for manifest in manifests],
                    "stats": stats,
                    "errors": [],
                }
                self._upsert(
                    "import_batches",
                    {
                        "batch_id": batch_id,
                        "started_at": started_at,
                        "finished_at": _now(),
                        "parser_version": parser_version,
                        "source_root": source_root,
                        "status": "completed",
                        "stats_json": json.dumps(stats, sort_keys=True),
                        "errors_json": "[]",
                    },
                    ("batch_id",),
                )
            return audit
        except Exception as error:
            with self.connection:
                self._upsert(
                    "import_batches",
                    {
                        "batch_id": batch_id,
                        "started_at": started_at,
                        "finished_at": _now(),
                        "parser_version": parser_version,
                        "source_root": source_root,
                        "status": "failed",
                        "stats_json": "{}",
                        "errors_json": json.dumps([str(error)]),
                    },
                    ("batch_id",),
                )
            raise

    def _import_manifest(self, manifest: dict[str, Any]) -> None:
        now = _now()
        commit = manifest["commit"]
        self._insert_immutable(
            "commits",
            {
                "commit_sha": commit["commit_sha"],
                "short_sha": commit["short_sha"],
                "commit_time": commit["commit_time"],
                "commit_epoch": commit["commit_epoch"],
                "subject": commit["subject"],
                "parents_json": json.dumps(commit["parents"]),
            },
            ("commit_sha",),
        )
        slice_set = manifest["slice_set"]
        self._insert_immutable(
            "slice_sets",
            {
                "slice_set_id": slice_set["slice_set_id"],
                "name": slice_set["name"],
                "version": slice_set["version"],
                "source_uri": slice_set["source_uri"],
                "created_at": now,
            },
            ("slice_set_id",),
            ignored=("created_at",),
        )
        for member in slice_set["members"]:
            self._insert_immutable(
                "slices",
                {
                    "slice_id": member["slice_id"],
                    "benchmark": member["benchmark"],
                    "workload": member["workload"],
                    "slice_name": member["slice"],
                    "checkpoint": member["checkpoint"],
                    "checkpoint_identity": member["checkpoint_identity"],
                    "restore_mode": member["restore_mode"],
                    "warmup_definition": member["warmup_definition"],
                    "roi_definition": member["roi_definition"],
                },
                ("slice_id",),
            )
            self._insert_immutable(
                "slice_set_members",
                {
                    "slice_set_id": slice_set["slice_set_id"],
                    "slice_id": member["slice_id"],
                    "weight": member["weight"],
                    "weight_kind": member["weight_kind"],
                    "ordinal": member["ordinal"],
                },
                ("slice_set_id", "slice_id"),
            )

        run = manifest["run"]
        for table in ("artifacts", "aggregate_results", "slice_results"):
            self.connection.execute(
                f"DELETE FROM {table} WHERE run_id = ?", (run["run_id"],)
            )
        if manifest.get("counter_import", "complete") == "complete":
            self.connection.execute(
                "DELETE FROM counter_values WHERE run_id = ?", (run["run_id"],)
            )
        self._upsert(
            "runs",
            {
                "run_id": run["run_id"],
                "commit_sha": run["commit_sha"],
                "branch_label": run["branch_label"],
                "config": run["config"],
                "started_at": run["started_at"],
                "completed_at": run["completed_at"],
                "score_generated_at": run["score_generated_at"],
                "snapshot_at": run["snapshot_at"],
                "imported_at": now,
                "source_uri": run["source_uri"],
                "status": run["status"],
                "parser_version": run["parser_version"],
                "score_formula_version": run["score_formula_version"],
                "comparison_key": run["comparison_key"],
                "slice_set_id": slice_set["slice_set_id"],
                "snapshot_identity": run["snapshot_identity"],
                "experiment_json": json.dumps(run["experiment"], sort_keys=True),
            },
            ("run_id",),
        )
        for definition in manifest["metric_definitions"]:
            self._insert_immutable(
                "metric_definitions",
                {
                    "metric_id": definition["metric_id"],
                    "semantic_version": definition["semantic_version"],
                    "display_name": definition["display_name"],
                    "category": definition["category"],
                    "kind": definition["kind"],
                    "unit": definition["unit"],
                    "direction": definition["direction"],
                    "source_match_json": json.dumps(
                        {
                            "path": definition["source_path"],
                            "name": definition["source_name"],
                            "expected_matches": definition["expected_matches"],
                        },
                        sort_keys=True,
                    ),
                    "aggregation_rule": definition["aggregation_rule"],
                },
                ("metric_id", "semantic_version"),
            )
        for result in manifest["slice_results"]:
            self._upsert(
                "slice_results",
                {
                    "run_id": run["run_id"],
                    "slice_id": result["slice_id"],
                    "status": result["status"],
                    "seed": result.get("seed"),
                    "window_id": result.get("window_id"),
                    "instructions": result.get("instructions"),
                    "cycles": result.get("cycles"),
                    "ipc_reported": result.get("ipc_reported"),
                    "ipc_computed": result.get("ipc_computed"),
                    "cpi": result.get("cpi"),
                    "source_out_uri": result["source_out_uri"],
                    "source_err_uri": result["source_err_uri"],
                    "error_code": result.get("error_code"),
                },
                ("run_id", "slice_id"),
            )
        for counter in manifest["counter_values"]:
            self._upsert(
                "counter_values",
                {"run_id": run["run_id"], **counter},
                ("run_id", "slice_id", "metric_id", "semantic_version"),
            )
        for aggregate in [*manifest["scores"], *manifest["aggregate_results"]]:
            self._upsert(
                "aggregate_results",
                {"run_id": run["run_id"], **aggregate},
                ("run_id", "level", "object_id", "metric_id", "formula_version"),
            )
        for artifact in manifest["artifacts"]:
            self._upsert(
                "artifacts",
                {
                    "run_id": run["run_id"],
                    "slice_id": artifact.get("slice_id"),
                    "kind": artifact["kind"],
                    "uri": artifact["uri"],
                    "content_hash": artifact.get("content_hash"),
                },
                ("run_id", "kind", "uri"),
            )

    def counts(self) -> dict[str, int]:
        tables = (
            "commits",
            "runs",
            "slices",
            "slice_set_members",
            "slice_results",
            "metric_definitions",
            "counter_values",
            "aggregate_results",
            "artifacts",
        )
        return {
            table: int(self.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in tables
        }

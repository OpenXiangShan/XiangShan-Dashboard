"""Page-independent trend and A/B query service."""

from __future__ import annotations

import json
import math
import sqlite3
import subprocess
from pathlib import Path
from typing import Any, Iterable

from .anomalies import counter_clues, select_slice_candidates
from .metrics import cpi_contributions, degradation_percent
from .store import Store


SLICE_METRICS = {
    "ipc": "sr.ipc_computed",
    "ipc_reported": "sr.ipc_reported",
    "cpi": "sr.cpi",
    "cycles": "sr.cycles",
    "instructions": "sr.instructions",
}
PUBLISHED_STATUSES = ("published",)


def _rows(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(row) for row in cursor.fetchall()]


class TrendService:
    def __init__(self, store: Store):
        self.store = store
        self.connection = store.connection

    def list_filters(self) -> dict[str, list[str]]:
        return {
            "configs": [
                row[0]
                for row in self.connection.execute(
                    "SELECT DISTINCT config FROM runs ORDER BY config"
                )
            ],
            "comparison_keys": [
                row[0]
                for row in self.connection.execute(
                    "SELECT DISTINCT comparison_key FROM runs ORDER BY comparison_key"
                )
            ],
            "slice_sets": [
                row[0]
                for row in self.connection.execute(
                    "SELECT slice_set_id FROM slice_sets ORDER BY name, version"
                )
            ],
            "slices": [
                row[0]
                for row in self.connection.execute(
                    "SELECT slice_name FROM slices ORDER BY checkpoint"
                )
            ],
            "counters": [
                row[0]
                for row in self.connection.execute(
                    "SELECT DISTINCT metric_id FROM metric_definitions ORDER BY metric_id"
                )
            ],
        }

    def select_baseline(self, target_run_id: str, git_repo: Path) -> dict[str, Any]:
        """Nearest tested, published first-parent ancestor in the same comparison group."""
        target = self._resolve_run(target_run_id)
        result = subprocess.run(
            ["git", "-c", f"safe.directory={git_repo}", "-C", str(git_repo), "rev-list", "--first-parent", target["commit_sha"]],
            text=True, capture_output=True, check=True,
        )
        for sha in result.stdout.splitlines()[1:]:
            candidates = _rows(self.connection.execute(
                """SELECT r.*, c.short_sha, c.commit_time, c.subject FROM runs r
                     JOIN commits c ON c.commit_sha = r.commit_sha
                    WHERE r.commit_sha = ? AND r.comparison_key = ? AND r.slice_set_id = ?
                      AND r.status = 'published'
                    ORDER BY r.snapshot_at DESC""",
                (sha, target["comparison_key"], target["slice_set_id"]),
            ))
            if candidates:
                return {"status": "found", "baseline": self._public_run(candidates[0]),
                        "target": self._public_run(target), "relation": "first_parent_tested_ancestor"}
        return {"status": "not_found", "baseline": None, "target": self._public_run(target)}

    def get_trend(
        self,
        level: str,
        object_ids: str | Iterable[str],
        metric_id: str,
        comparison_key: str | None = None,
        include_non_published: bool = False,
    ) -> list[dict[str, Any]]:
        objects = [object_ids] if isinstance(object_ids, str) else list(object_ids)
        if not objects:
            return []
        placeholders = ",".join("?" for _ in objects)
        object_params: list[Any] = list(objects)
        run_filter = ""
        if not include_non_published:
            run_filter += " AND r.status = 'published'"
        if comparison_key:
            run_filter += " AND r.comparison_key = ?"

        if level in {"suite", "benchmark", "workload"}:
            params: list[Any] = [level, *object_params, metric_id]
            if comparison_key:
                params.append(comparison_key)
            cursor = self.connection.execute(
                f"""
                SELECT a.object_id, a.value, a.status, a.coverage_weight,
                       a.formula_version AS metric_version, a.source_uri,
                       r.run_id, r.status AS run_status, r.comparison_key,
                       r.parser_version, r.source_uri AS run_source_uri,
                       r.experiment_json, c.commit_sha, c.short_sha,
                       c.commit_time, c.commit_epoch, c.subject
                  FROM aggregate_results a
                  JOIN runs r ON r.run_id = a.run_id
                  JOIN commits c ON c.commit_sha = r.commit_sha
                 WHERE a.level = ? AND a.object_id IN ({placeholders})
                   AND a.metric_id = ? {run_filter}
                 ORDER BY a.object_id, c.commit_epoch, r.snapshot_at
                """,
                params,
            )
        elif level == "slice" and metric_id in SLICE_METRICS:
            params = list(object_params)
            if comparison_key:
                params.append(comparison_key)
            cursor = self.connection.execute(
                f"""
                SELECT s.slice_name AS object_id, {SLICE_METRICS[metric_id]} AS value,
                       sr.status, NULL AS coverage_weight,
                       'slice-core/v1' AS metric_version, sr.source_out_uri AS source_uri,
                       r.run_id, r.status AS run_status, r.comparison_key,
                       r.parser_version, r.source_uri AS run_source_uri,
                       r.experiment_json, c.commit_sha, c.short_sha,
                       c.commit_time, c.commit_epoch, c.subject
                  FROM slice_results sr
                  JOIN slices s ON s.slice_id = sr.slice_id
                  JOIN runs r ON r.run_id = sr.run_id
                  JOIN commits c ON c.commit_sha = r.commit_sha
                 WHERE s.slice_name IN ({placeholders}) {run_filter}
                 ORDER BY s.slice_name, c.commit_epoch, r.snapshot_at
                """,
                params,
            )
        elif level == "counter":
            params = [*object_params, metric_id]
            if comparison_key:
                params.append(comparison_key)
            cursor = self.connection.execute(
                f"""
                SELECT s.slice_name AS object_id, cv.value, cv.availability AS status,
                       NULL AS coverage_weight, cv.semantic_version AS metric_version,
                       cv.source_uri, cv.window_id, cv.dump_time,
                       r.run_id, r.status AS run_status, r.comparison_key,
                       r.parser_version, r.source_uri AS run_source_uri,
                       r.experiment_json, c.commit_sha, c.short_sha,
                       c.commit_time, c.commit_epoch, c.subject
                  FROM counter_values cv
                  JOIN slices s ON s.slice_id = cv.slice_id
                  JOIN runs r ON r.run_id = cv.run_id
                  JOIN commits c ON c.commit_sha = r.commit_sha
                 WHERE s.slice_name IN ({placeholders}) AND cv.metric_id = ? {run_filter}
                 ORDER BY s.slice_name, c.commit_epoch, r.snapshot_at
                """,
                params,
            )
        else:
            raise ValueError(f"unsupported trend query: level={level}, metric={metric_id}")

        result = _rows(cursor)
        previous: dict[str, dict[str, Any]] = {}
        for row in result:
            row["experiment"] = json.loads(row.pop("experiment_json"))
            prior = previous.get(row["object_id"])
            reasons = []
            if prior:
                if prior["comparison_key"] != row["comparison_key"]:
                    reasons.append("comparison_key_changed")
                if prior["metric_version"] != row["metric_version"]:
                    reasons.append("metric_version_changed")
                if prior["status"] not in {"valid", "available"}:
                    reasons.append("previous_point_invalid")
                if row["status"] not in {"valid", "available"}:
                    reasons.append("current_point_invalid")
            row["break_before"] = bool(prior and reasons)
            row["break_reasons"] = reasons
            previous[row["object_id"]] = row
        return result

    def compare_points(
        self,
        run_a: str,
        run_b: str,
        level: str = "workload",
        object_id: str = "mcf",
        top_n: int | None = None,
    ) -> dict[str, Any]:
        if level != "workload":
            raise ValueError("comparison currently supports level=workload")
        a = self._resolve_run(run_a)
        b = self._resolve_run(run_b)
        reasons = []
        if a["comparison_key"] != b["comparison_key"]:
            reasons.append("comparison_key_mismatch")
        if a["slice_set_id"] != b["slice_set_id"]:
            reasons.append("slice_set_mismatch")
        if a["status"] != "published" or b["status"] != "published":
            reasons.append("run_not_published")
        if reasons:
            return {
                "status": "incomparable",
                "reasons": reasons,
                "run_a": self._public_run(a),
                "run_b": self._public_run(b),
            }

        expected = self._workload_members(a["slice_set_id"], object_id)
        if not expected:
            raise ValueError(f"workload not found in slice set: {object_id}")
        rows_a = self._slice_rows(a["run_id"], object_id)
        rows_b = self._slice_rows(b["run_id"], object_id)
        expected_ids = {row["slice_id"] for row in expected}
        actual_a = {row["slice_id"] for row in rows_a}
        actual_b = {row["slice_id"] for row in rows_b}
        missing_a = sorted(expected_ids - actual_a)
        missing_b = sorted(expected_ids - actual_b)
        extra_a = sorted(actual_a - expected_ids)
        extra_b = sorted(actual_b - expected_ids)
        if missing_a or missing_b or extra_a or extra_b:
            return {
                "status": "incomparable",
                "reasons": ["slice_membership_incomplete"],
                "run_a": self._public_run(a),
                "run_b": self._public_run(b),
                "object_id": object_id,
                "membership": {
                    "expected_slice_count": len(expected),
                    "missing_in_a": missing_a,
                    "missing_in_b": missing_b,
                    "extra_in_a": extra_a,
                    "extra_in_b": extra_b,
                },
            }
        if top_n is not None:
            if top_n < 1:
                raise ValueError("top_n must be positive")
            selected = {
                row["slice_id"]
                for row in sorted(rows_a, key=lambda row: (-row["weight"], row["slice"]))[:top_n]
            }
            rows_a = [row for row in rows_a if row["slice_id"] in selected]
            rows_b = [row for row in rows_b if row["slice_id"] in selected]
        if any(
            row["status"] != "valid" or row["cpi"] is None
            for row in [*rows_a, *rows_b]
        ):
            return {
                "status": "incomparable",
                "reasons": ["invalid_slice_result"],
                "run_a": self._public_run(a),
                "run_b": self._public_run(b),
            }

        total_weight = math.fsum(float(row["weight"]) for row in expected)
        if math.isclose(total_weight, 1.0, abs_tol=1e-5):
            total_weight = 1.0
        selected_weight = math.fsum(row["weight"] for row in rows_a)
        weighted_sum_a = math.fsum(row["weight"] * row["cpi"] for row in rows_a)
        weighted_sum_b = math.fsum(row["weight"] * row["cpi"] for row in rows_b)
        contributions = cpi_contributions(rows_a, rows_b)
        contributions.sort(
            key=lambda row: row["weighted_cpi_contribution"], reverse=True
        )
        contribution_sum = math.fsum(
            row["weighted_cpi_contribution"] for row in contributions
        )
        weighted_sum_delta = weighted_sum_b - weighted_sum_a
        coverage = selected_weight / total_weight
        if math.isclose(coverage, 1.0, abs_tol=1e-5):
            coverage = 1.0
        full_profile = all(
            (experiment := json.loads(run["experiment_json"])).get("score_coverage") == "1.0c"
            or str(experiment.get("benchmark_type", "")).endswith("-1.0c") for run in (a, b)
        )
        diagnostic = (top_n is not None or not math.isclose(coverage, 1.0, abs_tol=1e-5)
                      or (not full_profile and not math.isclose(total_weight, 1.0, abs_tol=1e-5)))
        diagnostic_cpi_a = weighted_sum_a / selected_weight if selected_weight else None
        diagnostic_cpi_b = weighted_sum_b / selected_weight if selected_weight else None
        comparison_cpi_a = diagnostic_cpi_a if diagnostic else weighted_sum_a
        comparison_cpi_b = diagnostic_cpi_b if diagnostic else weighted_sum_b
        warnings = []
        if json.loads(a["experiment_json"]).get("comparison_status") == "provisional":
            warnings.append("comparison_key_is_provisional")
        return {
            "status": "comparable",
            "mode": "diagnostic_partial_subset" if diagnostic else "full_slice_set",
            "run_a": self._public_run(a),
            "run_b": self._public_run(b),
            "object_id": object_id,
            "coverage_weight": selected_weight,
            "selected_weight": selected_weight,
            "selected_slice_count": len(rows_a),
            "total_slice_count": len(expected),
            "weighted_cpi_a": None if diagnostic else weighted_sum_a,
            "weighted_cpi_b": None if diagnostic else weighted_sum_b,
            "weighted_cpi_delta": None if diagnostic else weighted_sum_delta,
            "weighted_cpi_numerator_a": weighted_sum_a,
            "weighted_cpi_numerator_b": weighted_sum_b,
            "weighted_cpi_numerator_delta": weighted_sum_delta,
            "equivalent_ipc_a": None if diagnostic else 1.0 / weighted_sum_a,
            "equivalent_ipc_b": None if diagnostic else 1.0 / weighted_sum_b,
            "diagnostic_subset_cpi_a": diagnostic_cpi_a if diagnostic else None,
            "diagnostic_subset_cpi_b": diagnostic_cpi_b if diagnostic else None,
            "diagnostic_subset_cpi_delta": (
                diagnostic_cpi_b - diagnostic_cpi_a if diagnostic else None
            ),
            "cpi_degradation_percent": degradation_percent(
                comparison_cpi_a, comparison_cpi_b, "lower_is_better"
            ),
            "contribution_sum": contribution_sum,
            "contribution_identity_error": contribution_sum - weighted_sum_delta,
            "slices": contributions,
            "warnings": warnings,
        }

    def detect_anomalies(
        self,
        current: str,
        baseline: str | None = None,
        fixed_baseline: str | None = None,
        workloads: Iterable[str] | None = None,
        workload_threshold_pct: float = 0.5,
        slice_threshold_pct: float = 0.5,
        contribution_top_n: int = 10,
        counter_top_n: int = 5,
        counter_change_threshold_pct: float = 5.0,
        git_repo: Path | None = None,
    ) -> dict[str, Any]:
        """Build a deterministic anomaly report from existing A/B observations.

        This deliberately does not claim statistical significance or schedule reruns.
        A workload is anomalous when weighted CPI exceeds the configured practical
        threshold. Slice candidates are selected by CPI degradation and, for a
        regressed workload, positive weighted-CPI contribution.
        """
        if workload_threshold_pct < 0 or slice_threshold_pct < 0:
            raise ValueError("degradation thresholds must be non-negative")
        if counter_change_threshold_pct < 0:
            raise ValueError("counter change threshold must be non-negative")
        if contribution_top_n < 1 or counter_top_n < 0:
            raise ValueError("top_n values must be positive (counter_top_n may be zero)")

        current_run = self._resolve_run(current)
        pairs: list[tuple[str, dict[str, Any], str]] = []
        if baseline is None and git_repo is not None:
            selected_baseline = self.select_baseline(current, git_repo)
            if selected_baseline["status"] == "found":
                previous = self._resolve_run(selected_baseline["baseline"]["run_id"])
                pairs.append(("previous", previous, selected_baseline["relation"]))
        elif baseline is None:
            selected = self._select_previous_run(current_run)
            if selected is not None:
                previous, basis = selected
                pairs.append(("previous", previous, basis))
        else:
            pairs.append(("previous", self._resolve_run(baseline), "explicit"))
        if fixed_baseline is not None:
            fixed = self._resolve_run(fixed_baseline)
            if all(row[1]["run_id"] != fixed["run_id"] for row in pairs):
                pairs.append(("fixed", fixed, "explicit"))

        available_workloads = self._run_workloads(current_run["run_id"])
        selected_workloads = list(dict.fromkeys(workloads or available_workloads))
        if not selected_workloads:
            raise ValueError("no workloads selected")
        unknown = sorted(set(selected_workloads) - set(available_workloads))
        if unknown:
            raise ValueError(f"workloads not found for current run: {unknown}")

        policy = {
            "workload_cpi_degradation_pct": workload_threshold_pct,
            "slice_cpi_degradation_pct": slice_threshold_pct,
            "weighted_contribution_top_n": contribution_top_n,
            "counter_change_pct": counter_change_threshold_pct,
            "counter_top_n": counter_top_n,
            "statistical_significance": "not_assessed_single_observation",
            "rerun_required": False,
        }
        comparisons = [
            self._detect_pair(
                current_run,
                base,
                label,
                basis,
                selected_workloads,
                policy,
            )
            for label, base, basis in pairs
        ]
        return {
            "status": "ok" if comparisons else "no_baseline",
            "current": self._public_run(current_run),
            "policy": policy,
            "comparison_count": len(comparisons),
            "comparisons": comparisons,
            "warnings": [
                "single_observation_effect_size_only",
                "counter_changes_are_diagnostic_clues_not_causal_proof",
            ],
        }

    def _detect_pair(
        self,
        current: dict[str, Any],
        baseline: dict[str, Any],
        label: str,
        baseline_basis: str,
        workloads: list[str],
        policy: dict[str, Any],
    ) -> dict[str, Any]:
        workload_rows: list[dict[str, Any]] = []
        incomparable: list[dict[str, Any]] = []
        anomalous_slice_count = 0
        for workload in workloads:
            comparison = self.compare_points(
                baseline["run_id"], current["run_id"], object_id=workload
            )
            if comparison["status"] != "comparable":
                incomparable.append(
                    {"workload": workload, "reasons": comparison.get("reasons", [])}
                )
                continue
            degradation = comparison["cpi_degradation_percent"]
            workload_anomaly = bool(
                degradation is not None
                and degradation > policy["workload_cpi_degradation_pct"]
            )
            candidates = select_slice_candidates(
                comparison["slices"],
                workload_anomaly,
                policy["slice_cpi_degradation_pct"],
                policy["weighted_contribution_top_n"],
            )
            for candidate in candidates:
                candidate["counter_clues"] = self._counter_clues(
                    baseline["run_id"],
                    current["run_id"],
                    candidate["slice_id"],
                    policy["counter_change_pct"],
                    policy["counter_top_n"],
                )
            anomalous_slice_count += len(candidates)
            workload_rows.append(
                {
                    "workload": workload,
                    "comparison_mode": comparison["mode"],
                    "warnings": comparison["warnings"],
                    "is_anomaly": workload_anomaly,
                    "cpi_degradation_percent": degradation,
                    "weighted_cpi_a": comparison["weighted_cpi_a"],
                    "weighted_cpi_b": comparison["weighted_cpi_b"],
                    "weighted_cpi_delta": comparison["weighted_cpi_delta"],
                    "equivalent_ipc_a": comparison["equivalent_ipc_a"],
                    "equivalent_ipc_b": comparison["equivalent_ipc_b"],
                    "coverage_weight": comparison["coverage_weight"],
                    "slice_count": comparison["selected_slice_count"],
                    "anomalous_slice_count": len(candidates),
                    "anomalous_slices": candidates,
                }
            )
        workload_rows.sort(
            key=lambda row: row["cpi_degradation_percent"]
            if row["cpi_degradation_percent"] is not None
            else -math.inf,
            reverse=True,
        )
        return {
            "label": label,
            "baseline_basis": baseline_basis,
            "status": "comparable" if not incomparable else "partially_comparable",
            "baseline": self._public_run(baseline),
            "current": self._public_run(current),
            "summary": {
                "workload_count": len(workload_rows),
                "anomalous_workload_count": sum(row["is_anomaly"] for row in workload_rows),
                "anomalous_slice_count": anomalous_slice_count,
                "incomparable_workload_count": len(incomparable),
            },
            "workloads": workload_rows,
            "incomparable_workloads": incomparable,
        }

    def _counter_clues(
        self,
        run_a: str,
        run_b: str,
        slice_id: str,
        threshold_pct: float,
        top_n: int,
    ) -> list[dict[str, Any]]:
        if top_n == 0:
            return []
        rows = _rows(
            self.connection.execute(
                """
                SELECT cv.run_id, cv.metric_id, cv.semantic_version, cv.value,
                       cv.availability, cv.window_id, md.display_name, md.category,
                       md.unit, md.direction
                  FROM counter_values cv
                  JOIN metric_definitions md
                    ON md.metric_id = cv.metric_id
                   AND md.semantic_version = cv.semantic_version
                 WHERE cv.run_id IN (?, ?) AND cv.slice_id = ?
                 ORDER BY cv.metric_id, cv.semantic_version, cv.run_id
                """,
                (run_a, run_b, slice_id),
            )
        )
        return counter_clues(rows, run_a, run_b, threshold_pct, top_n)

    def _select_previous_run(
        self, current: dict[str, Any]
    ) -> tuple[dict[str, Any], str] | None:
        parents = json.loads(current["parents_json"])
        for parent in parents:
            candidates = self._compatible_prior_runs(current, commit_sha=parent)
            if candidates:
                return candidates[0], "tested_parent"
        candidates = self._compatible_prior_runs(current, before_epoch=current["commit_epoch"])
        if candidates:
            return candidates[0], "nearest_prior_compatible"
        return None

    def _compatible_prior_runs(
        self,
        current: dict[str, Any],
        commit_sha: str | None = None,
        before_epoch: int | None = None,
    ) -> list[dict[str, Any]]:
        condition = "AND r.commit_sha = ?" if commit_sha is not None else "AND c.commit_epoch < ?"
        value: Any = commit_sha if commit_sha is not None else before_epoch
        return _rows(
            self.connection.execute(
                f"""
                SELECT r.*, c.short_sha, c.commit_time, c.commit_epoch, c.subject,
                       c.parents_json
                  FROM runs r JOIN commits c ON c.commit_sha = r.commit_sha
                 WHERE r.run_id != ? AND r.status = 'published'
                   AND r.comparison_key = ? AND r.slice_set_id = ?
                   {condition}
                 ORDER BY c.commit_epoch DESC, r.snapshot_at DESC
                """,
                (
                    current["run_id"],
                    current["comparison_key"],
                    current["slice_set_id"],
                    value,
                ),
            )
        )

    def get_children(
        self, object_id: str, run_a: str, run_b: str, top_n: int | None = None
    ) -> list[dict[str, Any]]:
        return self.compare_points(run_a, run_b, "workload", object_id, top_n).get(
            "slices", []
        )

    def get_artifacts(self, run_id: str, slice_id: str | None = None) -> list[dict[str, Any]]:
        if slice_id is None:
            return _rows(
                self.connection.execute(
                    "SELECT * FROM artifacts WHERE run_id = ? ORDER BY kind, uri", (run_id,)
                )
            )
        return _rows(
            self.connection.execute(
                "SELECT * FROM artifacts WHERE run_id = ? AND slice_id = ? ORDER BY kind, uri",
                (run_id, slice_id),
            )
        )

    def _resolve_run(self, identifier: str) -> dict[str, Any]:
        rows = _rows(
            self.connection.execute(
                """
                SELECT r.*, c.short_sha, c.commit_time, c.commit_epoch, c.subject,
                       c.parents_json
                  FROM runs r JOIN commits c ON c.commit_sha = r.commit_sha
                 WHERE r.run_id = ? OR r.commit_sha = ? OR c.short_sha = ?
                 ORDER BY r.snapshot_at DESC
                """,
                (identifier, identifier, identifier),
            )
        )
        if not rows:
            raise ValueError(f"run not found: {identifier}")
        if len(rows) > 1:
            raise ValueError(f"run identifier is ambiguous; use run_id: {identifier}")
        return rows[0]

    def _run_workloads(self, run_id: str) -> list[str]:
        return [
            row[0]
            for row in self.connection.execute(
                """
                SELECT DISTINCT s.workload
                  FROM runs r
                  JOIN slice_set_members m ON m.slice_set_id = r.slice_set_id
                  JOIN slices s ON s.slice_id = m.slice_id
                 WHERE r.run_id = ?
                 ORDER BY s.workload
                """,
                (run_id,),
            )
        ]

    def _workload_members(
        self, slice_set_id: str, workload: str
    ) -> list[dict[str, Any]]:
        return _rows(
            self.connection.execute(
                """
                SELECT m.slice_id, m.weight, m.ordinal
                  FROM slice_set_members m
                  JOIN slices s ON s.slice_id = m.slice_id
                 WHERE m.slice_set_id = ? AND s.workload = ?
                 ORDER BY m.ordinal
                """,
                (slice_set_id, workload),
            )
        )

    def _slice_rows(self, run_id: str, workload: str) -> list[dict[str, Any]]:
        return _rows(
            self.connection.execute(
                """
                SELECT sr.*, s.slice_name AS slice, s.checkpoint, m.weight
                  FROM slice_results sr
                  JOIN slices s ON s.slice_id = sr.slice_id
                  JOIN runs r ON r.run_id = sr.run_id
                  JOIN slice_set_members m
                    ON m.slice_set_id = r.slice_set_id AND m.slice_id = sr.slice_id
                 WHERE sr.run_id = ? AND s.workload = ?
                 ORDER BY m.ordinal
                """,
                (run_id, workload),
            )
        )

    @staticmethod
    def _public_run(run: dict[str, Any]) -> dict[str, Any]:
        return {
            key: run[key]
            for key in (
                "run_id",
                "commit_sha",
                "short_sha",
                "commit_time",
                "subject",
                "status",
                "source_uri",
                "parser_version",
                "comparison_key",
                "slice_set_id",
            )
        }

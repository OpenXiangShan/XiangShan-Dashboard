"""Join archived RTL TEA/DIP attribution to checkpoint-matched IPC history."""
from __future__ import annotations

from collections import defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3

from .slice_analysis import analyze_slice_trends

TEA_STATES = ("Computing", "Stalled", "Flushed", "Drained")
DIP_STATES = ("Base", "Stall", "FrontEnd", "Misspeculation", "Unresolved")
EVENTS = ("DR_L1", "DR_TLB", "DR_SQ", "FL_MB", "FL_EX", "FL_MO", "ST_L1", "ST_TLB")


def _csv(path: Path):
    with path.open(newline="", encoding="utf-8") as stream:
        yield from csv.DictReader(stream)


def _budget(values: dict, label: str) -> None:
    if (any(not math.isfinite(v) or v < 0 or v > 1 for v in values.values())
            or not math.isclose(math.fsum(values.values()), 1, abs_tol=1e-9)):
        raise ValueError(f"{label}: state fractions do not conserve the budget")


def load_evidence(directory: Path, *, top_pcs: int = 8) -> dict:
    """Read the archived RTL analysis contract, without importing its runners."""
    directory = directory.resolve()
    manifest = json.loads((directory / "analysis/manifest.json").read_text())
    hashes = {}

    def verified(relative: str) -> Path:
        path = directory / relative
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != manifest["files"][relative]["sha256"]:
            raise ValueError(f"archive SHA256 mismatch: {relative}")
        hashes[relative] = digest
        return path

    run = json.loads(verified("raw/run.json").read_text())
    if run["head_sha"] != manifest["source_sha"] or run["conclusion"] != "success":
        raise ValueError("archive run identity or successful conclusion mismatch")
    profile = json.loads(verified("raw/profile.json").read_text())
    audit = json.loads(verified("analysis/summary.json").read_text())
    identities = {
        f"{workload}_{checkpoint}_{float(weight):.6f}": (workload, int(checkpoint))
        for workload, definition in profile.items()
        for checkpoint, weight in definition["points"].items()
    }
    rows = list(_csv(verified("analysis/points.csv")))
    if len(rows) != len(identities) or {r["point"] for r in rows} != set(identities):
        raise ValueError("archive point membership differs from its profile")
    first = rows[0]["point"]
    stdout = verified(f"raw/results/{first}/simulator_out.txt").read_text()
    checkpoint_root = re.search(r"^The image is (.+)/checkpoint/", stdout, re.M).group(1)
    periods = {name: int(re.search(rf"^{name}SamplePeriod0 = (\d+)$", stdout, re.M)[1])
               for name in ("tea", "dip")}
    if periods["tea"] != periods["dip"]:
        raise ValueError("TEA and DIP sample periods differ")
    points = {}
    for row in rows:
        point = row["point"]
        workload, checkpoint = identities[point]
        if row["workload"] != workload:
            raise ValueError(f"{point}: workload/profile mismatch")
        tea = {state: float(row[state]) for state in TEA_STATES}
        dip = {state: float(row[f"DIP_{state}"]) for state in DIP_STATES}
        _budget(tea, point + " TEA")
        _budget(dip, point + " DIP")
        cycles, instructions = int(row["cycles"]), int(row["instructions"])
        if cycles <= 0 or instructions <= 0 or not math.isclose(
                cycles / instructions, float(row["cpi"]), rel_tol=1e-10):
            raise ValueError(f"{point}: CPI differs from cycles/instructions")
        points[point] = dict(
            point=point, workload=workload, checkpoint=checkpoint,
            benchmark=row["benchmark"], group=row["group"], seed=int(row["seed"]),
            cycles=cycles, instructions=instructions, cpi=cycles / instructions,
            roi_start=int(row["roi_start"]), roi_end=int(row["roi_end"]),
            warmup_instructions=int(row["warmup_instructions"]),
            samples=int(row["roi_samples"]), benchmark_weight=float(row["weight"]),
            coverage=float(row["coverage"]), tea=tea, dip=dip,
            tea_events={key: float(row[key]) for key in (
                "Stalled_memory_union", "Stalled_no_event", "cancelled", "unresolved", "unknown")},
            dip_causes={key.removeprefix("DIP_"): float(value)
                        for key, value in row.items()
                        if any(key.startswith(f"DIP_{state}_") for state in DIP_STATES)},
        )
    # Symbols are keyed by input as identical virtual PCs can mean different code.
    symbols = {}
    for name in ("hotspots.csv", "dip_hotspots.csv"):
        for row in _csv(verified("analysis/" + name)):
            symbols[row["workload"], row["pc"]] = {
                key: row[key] for key in ("function", "instruction")}
    for view, filename in (("tea", "pc_state_signatures.csv"),
                           ("dip", "dip_pc_signatures.csv")):
        pcs = defaultdict(lambda: defaultdict(float))
        signatures = defaultdict(lambda: defaultdict(float))
        for row in _csv(verified("analysis/" + filename)):
            key = row["point"], row["state"], row["pc"]
            if key[0] not in points:
                raise ValueError(f"PC attribution refers to unknown point: {key[0]}")
            fraction = float(row["fraction"])
            pcs[key[0], key[1]][key[2]] += fraction
            if view == "tea":
                psv = int(row["psv"])
                signature = ("Cancelled" if psv == -1 else "Unresolved" if psv == -2
                             else "Unknown" if psv == -3 else
                             "+".join(e for i, e in enumerate(EVENTS) if psv & (1 << i))
                             or "No recorded event")
            else:
                signature = row["causes"] or "None"
            signatures[key][signature] += fraction
        for point, definition in points.items():
            definition[view + "_pcs"] = []
            for state, expected in definition[view].items():
                values = pcs[point, state]
                # DIP pending slots have no final PC and remain a separate budget item.
                if view == "dip" and state == "Unresolved":
                    continue
                if not math.isclose(math.fsum(values.values()), expected, abs_tol=1e-8):
                    raise ValueError(f"{point} {view} {state}: PC budget mismatch")
                for pc, fraction in sorted(values.items(), key=lambda item: -item[1])[:top_pcs]:
                    definition[view + "_pcs"].append(dict(
                        pc=pc, state=state, fraction=fraction,
                        cpi=definition["cpi"] * fraction,
                        signatures=dict(sorted(signatures[point, state, pc].items(),
                                               key=lambda item: -item[1])),
                        **symbols.get((definition["workload"], pc), {}),
                    ))
    return dict(
        source=dict(directory=str(directory), ci=run["id"], commit=run["head_sha"],
                    branch=run["head_branch"], completed_at=run["updated_at"],
                    archive=manifest["remote_archive"], checkpoint_root=checkpoint_root,
                    emulator_sha256=manifest["emulator_sha256"],
                    reference_sha256=manifest["reference_so_sha256"],
                    sample_period=periods["tea"], top_pcs_per_state=top_pcs, hashes=hashes,
                    sampling_endpoint_deficits=audit["sampling_endpoint_deficits"]),
        points=list(points.values()),
    )


def join_history(connection: sqlite3.Connection, evidence: dict) -> dict:
    """Link only identical checkpoint roots, preserving history comparison groups.

    Attribution is always a reference measurement. Even a matching source commit
    does not establish identical binary, seed, counters or measurement window.
    """
    # The explicit checkpoint root identifies the dataset even in historical
    # runs whose experiment JSON has no benchmark_type.
    observations = [dict(row) for row in connection.execute("""
        SELECT r.run_id,r.commit_sha,r.config,r.snapshot_at,r.status,
               r.comparison_key,r.slice_set_id,r.source_uri AS source_archive,
               c.short_sha,c.commit_time,c.commit_epoch,c.subject,
               ss.name AS slice_set_name,s.slice_id,s.benchmark,s.workload,
               s.slice_name AS slice,s.checkpoint,m.weight,m.ordinal,
               sr.seed,sr.window_id,sr.instructions,sr.cycles,
               sr.ipc_computed,sr.source_out_uri
          FROM runs r JOIN commits c USING(commit_sha)
          JOIN slice_sets ss USING(slice_set_id)
          JOIN slice_results sr USING(run_id)
          JOIN slices s USING(slice_id)
          JOIN slice_set_members m
            ON m.slice_set_id=r.slice_set_id AND m.slice_id=sr.slice_id
         WHERE s.checkpoint_identity=? AND sr.status='valid' AND sr.ipc_computed>0
         ORDER BY c.commit_epoch,r.snapshot_at,r.run_id,m.ordinal
        """, (evidence["source"]["checkpoint_root"],))]
    runs = {}
    for row in observations:
        run = runs.setdefault(row["run_id"], {key: row[key] for key in (
            "run_id", "commit_sha", "config", "snapshot_at", "status", "comparison_key",
            "slice_set_id", "short_sha", "commit_time", "commit_epoch", "subject",
            "slice_set_name")})
        run["valid_slices"] = run.get("valid_slices", 0) + 1
    expected = dict(connection.execute(
        "SELECT slice_set_id,count(*) FROM slice_set_members GROUP BY slice_set_id"))
    for run in runs.values():
        run["expected_slices"] = expected[run["slice_set_id"]]
    result = analyze_slice_trends(list(runs.values()), observations)
    history = defaultdict(list)
    for row in result["observations"]:
        history[row["slice_set_id"], row["comparison_key"], row["slice_id"]].append(dict(
            run_id=row["run_id"], commit=row["commit_sha"], short_sha=row["short_sha"],
            commit_time=row["commit_time"], subject=row["subject"],
            instructions=row["instructions"], cycles=row["cycles"],
            ipc=row["ipc_computed"], source=row["source_out_uri"],
            seed=row["seed"], window=row["window_id"], source_archive=row["source_archive"],
        ))
    by_key = {(p["workload"], p["checkpoint"]): p for p in evidence["points"]}
    linked = []
    matched = set()
    for summary in result["summaries"]:
        key = summary["workload"], int(summary["checkpoint"])
        point = by_key.get(key)
        if point:
            matched.add(key)
        linked.append(dict(summary=summary, history=history[
            summary["slice_set_id"], summary["comparison_key"], summary["slice_id"]],
                           evidence_point=point["point"] if point else None,
                           association="checkpoint_reference" if point else "missing"))
    linked.sort(key=lambda row: -abs(row["summary"]["max_adjacent_step_pct"] or 0))
    return dict(
        schema_version="tea-dip-history/v1", source=evidence["source"],
        groups=result["groups"], slices=linked, evidence=evidence["points"],
        coverage=dict(history_slices=len({(r["summary"]["workload"], r["summary"]["checkpoint"])
                                          for r in linked}),
                      evidence_points=len(by_key), matched_points=len(matched),
                      evidence_without_history=[p["point"] for key, p in by_key.items()
                                                if key not in matched],
                      history_runs=len(result["runs"])),
    )

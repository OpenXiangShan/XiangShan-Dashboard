"""On-demand GEM5 queries shared by HTTP and the local CLI.

No web framework or history database is required. Heavy work, including arbitrary
counter weighting, uses one bounded executor and gem5_data_proc subprocesses.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
import math
import os
import signal
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import tempfile
import time
from urllib.parse import urlsplit
import uuid

VERSION = 1
WORKER = Path(__file__).with_name("analysis_worker.py")


class QueryError(ValueError):
    pass


class QueryCancelled(Exception):
    pass


def atomic_json(path: Path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def run_repository(value):
    parsed = urlsplit(str(value).strip())
    if parsed.scheme:
        return "xs" if "/xiangshan/" in parsed.path.lower() else "gem5"
    return None


def parse_run(value: str) -> str:
    value = str(value).strip()
    if re.fullmatch(r"[1-9]\d{0,19}", value):
        return value
    parsed = urlsplit(value)
    match = re.fullmatch(r"/OpenXiangShan/(?:GEM5|XiangShan)/actions/runs/([1-9]\d{0,19})(?:/attempts/\d+)?/?", parsed.path, re.I)
    if parsed.scheme == "https" and parsed.netloc.lower() == "github.com" and match:
        if "/attempts/" in parsed.path:
            raise QueryError("暂不支持指定历史 attempt；请使用 Run 链接并选择对应归档")
        return match[1]
    raise QueryError("请输入 GEM5/XiangShan Actions Run ID 或对应 GitHub Actions 链接")


def read_metadata(path: Path):
    result = {}
    for line in path.read_text(errors="replace").splitlines():
        key, separator, value = line.partition(":")
        if separator:
            result[key.strip()] = value.strip()
    return result


def finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def change(a, b):
    if not finite(a) or not finite(b):
        return None, None
    delta = b - a
    return delta, (100 * delta / abs(a) if a else 0 if b == 0 else None)


def make_rows(left, right, metrics, level, manifests):
    rows = []
    indexes = [{row["name"]: row for row in run[level]} for run in (left, right)]
    point_maps = [{p["name"]: p for p in m["points"]} for m in manifests]
    same_profile = manifests[0]["profile_hash"] == manifests[1]["profile_hash"]
    for name in sorted(set(indexes[0]) | set(indexes[1])):
        a, b = indexes[0].get(name, {}), indexes[1].get(name, {})
        if level == "simpoint":
            benchmark = a.get("bmk") or b.get("bmk")
            point = point_maps[0].get(name) or point_maps[1].get(name) or {}
        else:
            benchmark, point = name, {}
        for metric in metrics:
            av, bv = a.get(metric), b.get(metric)
            status = "available" if finite(av) and finite(bv) else "missing"
            # Point numbers from different checkpoint profiles are not identities.
            if level == "simpoint" and not same_profile:
                status = "profile_mismatch"
            delta, pct = change(av, bv) if status == "available" else (None, None)
            rows.append({"name": name, "benchmark": benchmark, "workload": point.get("workload"),
                         "point": point.get("point"), "metric": metric,
                         "a": av, "b": bv, "delta": delta, "delta_pct": pct,
                         "weight_a": point_maps[0].get(name, {}).get("weight"),
                         "weight_b": point_maps[1].get(name, {}).get("weight"),
                         "coverage_a": a.get("coverage"), "coverage_b": b.get("coverage"),
                         "status": status})
    return rows


def filter_rows(rows, query):
    benchmarks = set(query.get("benchmarks") or [])
    simpoints = set(str(x) for x in query.get("simpoints") or [])
    threshold = float(query.get("min_change") or 0)
    if not math.isfinite(threshold) or threshold < 0:
        raise QueryError("变化阈值必须为非负有限数")
    result = [row for row in rows
              if (not benchmarks or row["benchmark"] in benchmarks)
              and (not simpoints or row["name"] in simpoints or str(row["point"]) in simpoints)
              and (not threshold or row["delta_pct"] is not None and abs(row["delta_pct"]) >= threshold)]
    sort = query.get("sort", "abs_delta")
    if sort not in {"abs_delta", "abs_pct", "pct", "delta", "name"}:
        raise QueryError("不支持的排序方式")
    if sort != "name":
        key = "delta" if sort in {"delta", "abs_delta"} else "delta_pct"
        result.sort(key=lambda row: (row[key] is not None,
                    abs(row[key]) if sort in {"abs_delta", "abs_pct"} and row[key] is not None else row[key] or 0), reverse=True)
    return result


class AnalysisService:
    def __init__(self, archive_root: Path, processor: Path, cache: Path,
                 workers=1, extract_jobs=4, max_pending=8, timeout=1800,
                 rtl_report_roots=None, profile_roots=None):
        self.archive_root = archive_root.resolve()
        self.processor = processor.resolve()
        self.cache = cache.resolve()
        if not self.archive_root.is_dir() or not (self.processor / "batch.py").is_file():
            raise QueryError("请配置有效的 NFS archive-root 和 gem5_data_proc 目录")
        for name in ("analysis_pipeline.py", "analysis_inputs.py"):
            if not (self.processor / name).is_file():
                raise QueryError(f"gem5_data_proc 缺少 {name}，请使用包含分析入口的处理器 checkout")
        spec = importlib.util.spec_from_file_location("gem5_analysis_inputs", self.processor / "analysis_inputs.py")
        self.inputs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.inputs)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="gem5-analysis")
        self.workers, self.extract_jobs = workers, extract_jobs
        self.max_pending, self.timeout = max_pending, timeout
        self.lock = threading.RLock()
        self.snapshot_locks = {}
        self.jobs, self.pending = {}, {}
        self.cancel_events, self.processes, self.futures, self.progress_paths = {}, {}, {}, {}
        self.archives, self.index_time = {}, 0
        self.rtl_report_roots = [Path(p) for p in (rtl_report_roots or ["/nfs/home/ci-runner/perf-report", "/nfs/home/ci-runner/perf-report-custom", "/nfs/home/cirunner/perf-report"])]
        self.profile_roots = [Path(p) for p in (profile_roots or ["/nfs/home/share/checkpoints_profiles", "/nfs/share/zyy"])]
        self.rtl_archives = {}
        self.processor_hash = self._processor_hash()

    def _processor_hash(self):
        hasher = hashlib.sha256(WORKER.read_bytes())
        hasher.update(Path(__file__).with_name("rtl_sources.py").read_bytes())
        for folder in ("utils", "targets", "simpoint_cpt"):
            for path in sorted((self.processor / folder).rglob("*")):
                if path.suffix in {".py", ".yaml", ".json"} and path.is_file():
                    hasher.update(str(path.relative_to(self.processor)).encode())
                    hasher.update(path.read_bytes())
        for name in ("analysis_pipeline.py", "analysis_inputs.py", "batch.py", "paths.py", "local_configs.py"):
            hasher.update((self.processor / name).read_bytes())
        return hasher.hexdigest()

    def refresh_index(self, force=False):
        with self.lock:
            if not force and time.monotonic() - self.index_time < 60:
                return
            found = {}
            # Do not recurse through spec_all/ or read stats while resolving IDs.
            for suite in sorted(self.archive_root.iterdir()):
                if not suite.is_dir():
                    continue
                for archive in sorted(suite.iterdir()):
                    metadata = archive / "metadata.txt"
                    if not archive.is_dir() or not metadata.is_file():
                        continue
                    try:
                        meta = read_metadata(metadata)
                    except OSError:
                        continue
                    run = meta.get("workflow_run_id", "")
                    if not re.fullmatch(r"[1-9]\d*", run):
                        continue
                    resolved = archive.resolve()
                    if not resolved.is_relative_to(self.archive_root):
                        continue
                    identity = digest(str(resolved))[:24]
                    found[identity] = {"id": identity, "run_id": run, "backend": "gem5", "repository": "GEM5",
                        "label": f'{meta.get("benchmark_type", suite.name)} · {meta.get("config_name", "unknown")} · {archive.name}',
                        "benchmark_type": meta.get("benchmark_type", suite.name),
                        "config": meta.get("config_name", "unknown"),
                        "commit": meta.get("commit", "unknown"), "path": str(resolved),
                        "metadata": meta,
                        "available": (resolved / "cluster.json").is_file() and (resolved / "spec_all").is_dir()}
            self.archives, self.index_time = found, time.monotonic()

    def resolve_run(self, value, backend=None):
        run = parse_run(value)
        repository = run_repository(value)
        if backend not in (None, "xs", "gem5"):
            raise QueryError("backend 必须为 xs 或 gem5")
        backend = repository or backend
        candidates = []
        if backend != "xs":
            self.refresh_index()
            candidates = [v for v in self.archives.values() if v["run_id"] == run]
            if not candidates:
                self.refresh_index(force=True)
                candidates = [v for v in self.archives.values() if v["run_id"] == run]
        if candidates:
            return {"run_id": run, "candidates": candidates}
        if backend == "gem5":
            raise QueryError(f"GEM5 Run {run} 没有可访问的 NFS 归档；旧数据可能已被清理，或尚未生成归档")
        from rtl_sources import resolve_rtl
        try:
            receipts = resolve_rtl(run, self.cache / "rtl-evidence", self.rtl_report_roots, self.profile_roots)
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            raise QueryError(f"RTL Run {run} 定位失败：{error}") from error
        for receipt in receipts:
            identity = digest({"backend": "xs", "run": run, "job": receipt["job_id"], "path": receipt["path"], "profile": receipt["profile_path"]})[:24]
            meta = {"workflow_run_id": run, "commit": receipt["commit"],
                    "benchmark_type": receipt["benchmark_type"], "checkpoint_root": receipt.get("CKPT_HOME", ""),
                    "config_path": "RTL", "config_name": Path(receipt["path"]).name,
                    "actions_job_id": receipt["job_id"], "run_attempt": receipt.get("run_attempt")}
            archive = {"id": identity, "run_id": run, "backend": "xs", "repository": "XiangShan",
                       "label": f'RTL · {receipt["benchmark_type"]} · {Path(receipt["path"]).name}',
                       "benchmark_type": receipt["benchmark_type"], "config": "RTL", "commit": receipt["commit"],
                       "path": receipt["path"], "profile_path": receipt["profile_path"],
                       "score_path": receipt["score_path"], "metadata": meta,
                       "available": receipt["available"], "actions_url": receipt["actions_url"]}
            self.rtl_archives[identity] = archive
            candidates.append(archive)
        return {"run_id": run, "candidates": candidates}

    def archive(self, identity):
        self.refresh_index()
        archive = self.archives.get(str(identity)) or self.rtl_archives.get(str(identity))
        if not archive or not archive["available"]:
            raise QueryError("归档不存在或输入不完整，请重新解析 Run")
        return archive

    def _signature(self, archive, processor_hash=None):
        root = Path(archive["path"])
        entries = []
        if archive.get("backend") == "xs":
            profile = Path(archive["profile_path"])
            for identifier, path in self.inputs.selected_logs(root, profile):
                record = []
                for source in (path, path.with_name("simulator_out.txt")):
                    stat = source.stat() if source.exists() else None
                    record.append((str(source.relative_to(root)), stat.st_size if stat else None, stat.st_mtime_ns if stat else None))
                entries.append((identifier, record))
            metadata = archive["metadata"]
        else:
            profile = root / "cluster.json"
            for path in sorted((root / "spec_all").rglob("stats.txt")):
                point = path.parent.parent if path.parent.name == "m5out" else path.parent
                stat = path.stat()
                entries.append((str(path.relative_to(root)), stat.st_size, stat.st_mtime_ns,
                                (point / "completed").exists(), (point / "abort").exists()))
            metadata = (root / "metadata.txt").read_text()
        if not entries:
            raise QueryError("归档中没有与本次 profile 匹配的计数器日志")
        return digest({"schema": VERSION, "archive": archive["id"], "stats": entries,
                       "metadata": metadata, "profile": profile.read_text(),
                       "processor": processor_hash or self.processor_hash})

    def _check_cancelled(self, job):
        if self.cancel_events[job["id"]].is_set():
            raise QueryCancelled()

    @staticmethod
    def _stop_process(process):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            except ProcessLookupError:
                pass

    def cancel_job(self, identity):
        with self.lock:
            if identity not in self.jobs:
                raise QueryError("分析任务不存在，或服务已重启")
            job = self.jobs[identity]
            if job["status"] in {"complete", "failed", "cancelled"}:
                return self.get_job(identity)
            self.cancel_events[identity].set()
            future = self.futures.get(identity)
            if future and future.cancel():
                job.update(status="cancelled", stage="分析已停止", finished_at=time.time())
                self.pending.pop(digest(job["query"]), None)
            else:
                job.update(stage="正在停止分析…")
            process = self.processes.get(identity)
        if process:
            self._stop_process(process)
        return self.get_job(identity)

    def _run_worker(self, action, payload, directory, job):
        self._check_cancelled(job)
        request = directory / f"{action}-request.json"
        atomic_json(request, {"processor": str(self.processor), **payload})
        log_path = self.cache / "jobs" / f'{job["id"]}.log'
        with log_path.open("ab") as log:
            # Register process and cancellation atomically, closing the launch race.
            with self.lock:
                self._check_cancelled(job)
                process = subprocess.Popen([sys.executable, str(WORKER), action, str(request)],
                    stdout=log, stderr=log, start_new_session=True,
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "OPENBLAS_NUM_THREADS": "1",
                         "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"})
                self.processes[job["id"]] = process
                self.progress_paths[job["id"]] = directory / "progress.json" if action == "prepare" else None
            deadline = time.monotonic() + self.timeout
            try:
                while process.poll() is None:
                    if self.cancel_events[job["id"]].wait(.25):
                        self._stop_process(process)
                        raise QueryCancelled()
                    if time.monotonic() >= deadline:
                        self._stop_process(process)
                        raise QueryError("分析超时，已停止本次处理进程")
                self._check_cancelled(job)
                if process.returncode:
                    raise subprocess.CalledProcessError(process.returncode, process.args)
            finally:
                with self.lock:
                    self.processes.pop(job["id"], None)
                    self.progress_paths.pop(job["id"], None)

    def _prepare(self, archive, job):
        self._check_cancelled(job)
        # Refresh code/YAML identity so newly added groups invalidate old snapshots.
        processor_hash = self._processor_hash()
        signature = self._signature(archive, processor_hash)
        dest = self.cache / "runs" / signature
        with self.lock:
            snapshot_lock = self.snapshot_locks.setdefault(signature, threading.Lock())
        while not snapshot_lock.acquire(timeout=.25):
            self._check_cancelled(job)
        try:
            self._check_cancelled(job)
            if (dest / "manifest.json").is_file():
                return dest
            if dest.exists():
                shutil.rmtree(dest)
            dest.mkdir(parents=True)
            root = Path(archive["path"])
            profile_path = Path(archive.get("profile_path", root / "cluster.json"))
            profile_hash = hashlib.sha256(profile_path.read_bytes()).hexdigest()
            benchmark_type = archive["benchmark_type"]
            match = re.search(r"spec(06|17|26)", benchmark_type, re.I)
            if not match:
                raise QueryError("第一版支持 SPEC06/17/26；Trace 与 SMT 成对 workload 尚未接入算分")
            if "smt" in benchmark_type.lower():
                raise QueryError("SMT 成对 workload 尚未接入当前单线程 SPEC 算分链路")
            manifest = {**archive, "snapshot_id": signature, "profile_hash": profile_hash,
                        "processor_hash": processor_hash}
            self._update(job, stage=f'处理 Run {archive["run_id"]}：提取、加权、算分')
            try:
                with tempfile.TemporaryDirectory(prefix="gem5-analysis-stats-") as stage:
                    self._run_worker("prepare", {"source": str(root), "output": str(dest),
                        "extract_jobs": self.extract_jobs, "spec": match[1], "manifest": manifest,
                        "stage_dir": stage, "backend": archive.get("backend", "gem5"),
                        "profile_path": str(profile_path)}, dest, job)
                if self._signature(archive, self._processor_hash()) != signature:
                    raise QueryError("归档或处理器在处理过程中发生变化，请等待 CI 完成或代码更新结束后重试")
            except BaseException:
                shutil.rmtree(dest, ignore_errors=True)
                raise
            return dest
        finally:
            snapshot_lock.release()

    def submit(self, query):
        if not isinstance(query, dict):
            raise QueryError("请求必须为 JSON 对象")
        a, b = self.archive(query.get("a")), self.archive(query.get("b"))
        metrics = query.get("metrics") or ["cpi"]
        if not isinstance(metrics, list) or not 1 <= len(metrics) <= 512 or any(not isinstance(m, str) or len(m) > 512 for m in metrics):
            raise QueryError("每次请选择 1–512 个计数器")
        if query.get("level", "benchmark") not in {"benchmark", "simpoint"}:
            raise QueryError("不支持的聚合层级")
        for key in ("benchmarks", "simpoints", "groups"):
            if not isinstance(query.get(key, []), list):
                raise QueryError(f"{key} 必须为数组")
        # Validate numeric/sort inputs before doing expensive work.
        filter_rows([], query)
        query = {**query, "metrics": list(dict.fromkeys(metrics))}
        identity = digest(query)
        with self.lock:
            existing = self.pending.get(identity)
            if existing:
                return self.get_job(existing)
            if sum(j["status"] in {"queued", "running"} for j in self.jobs.values()) >= self.max_pending:
                raise QueryError("分析队列已满，请稍后重试")
            job = {"id": uuid.uuid4().hex, "status": "queued", "stage": "等待分析队列",
                   "created_at": time.time(), "query": query}
            self.jobs[job["id"]] = job
            self.pending[identity] = job["id"]
            (self.cache / "jobs").mkdir(exist_ok=True)
            self.cancel_events[job["id"]] = threading.Event()
            self.futures[job["id"]] = self.executor.submit(self._execute, job, identity, [a, b])
            return self.get_job(job["id"])

    def _update(self, job, **changes):
        with self.lock:
            job.update(changes)

    def _execute(self, job, identity, archives):
        directory = self.cache / "jobs" / job["id"]
        try:
            directory.mkdir()
            self._check_cancelled(job)
            self._update(job, status="running", stage="检查归档与缓存")
            snapshots = [self._prepare(archive, job) for archive in archives]
            manifests = [json.loads((s / "manifest.json").read_text()) for s in snapshots]
            catalogs = [json.loads((s / "catalog.json").read_text()) for s in snapshots]
            query = job["query"]
            known = set().union(*(set(c["yaml_metrics"]) | {"raw:" + m for m in c["raw_metrics"]} for c in catalogs))
            unknown = set(query["metrics"]) - known
            if unknown:
                raise QueryError(f'未找到计数器：{", ".join(sorted(unknown))}')
            self._update(job, stage="比较计数器与排序")
            self._run_worker("compare", {"snapshots": [str(s) for s in snapshots],
                              "metrics": query["metrics"], "level": query.get("level", "benchmark"),
                              "benchmarks": query.get("benchmarks", []),
                              "simpoints": query.get("simpoints", []),
                              "output": str(directory)}, directory, job)
            left, right = json.loads((directory / "values.json").read_text())
            rows = make_rows(left, right, query["metrics"], query.get("level", "benchmark"), manifests)
            rows = filter_rows(rows, query)
            warnings = []
            if manifests[0]["profile_hash"] != manifests[1]["profile_hash"]:
                warnings.append("两次运行的 profile 不同；逐切片差值不可用，加权结果各自使用原 profile")
            for name in ("benchmark_type", "config_path", "resolved_extra_args", "checkpoint_root"):
                values = [m["metadata"].get(name, m["metadata"].get("extra_args", "") if name == "resolved_extra_args" else "") for m in manifests]
                if values[0] != values[1]:
                    warnings.append(f"运行参数不同：{name}：{values[0] or '未记录'} → {values[1] or '未记录'}")
            if {p["name"] for p in manifests[0]["points"]} != {p["name"] for p in manifests[1]["points"]}:
                warnings.append("有效切片集合不同；两侧加权结果分别使用各自已有切片，请结合覆盖率判断")
            if any(m.get("backend") == "xs" for m in manifests):
                warnings.append("RTL 报告目录可能被同 commit 的多次 CI 复用；当前查询读取现存报告，不能还原历史 Run 的不可变快照")
            if manifests[0].get("backend", "gem5") != manifests[1].get("backend", "gem5"):
                warnings.append("跨 RTL/GEM5 比较：同名 YAML 指标仍需核对语义；单侧缺失为 null。Topdown 的 width=8、槽位和 fewops 等口径并非全部等价")
            for label, manifest in zip(("A", "B"), manifests):
                if manifest["completed_points"] < manifest["expected_points"]:
                    warnings.append(f'{label} 为部分运行：{manifest["completed_points"]}/{manifest["expected_points"]} 个 profile 切片完成')
            if query.get("simpoints"):
                warnings.append("SimPoint 筛选仅改变明细；顶部原始运行分数不重新计算")
            result = {"runs": manifests, "warnings": warnings, "rows": rows,
                      "benchmarks": sorted({r["benchmark"] for r in make_rows(left, right, ["cpi"], "benchmark", manifests)}),
                      "simpoints": sorted({p["name"] for m in manifests for p in m["points"]}),
                      "row_count": len(rows), "query": query}
            with self.lock:
                self._check_cancelled(job)
                atomic_json(directory / "result.json", result)
                self._update(job, status="complete", stage="分析完成", result=result, finished_at=time.time())
        except QueryCancelled:
            self._update(job, status="cancelled", stage="分析已停止", finished_at=time.time())
        except BaseException as error:
            if isinstance(error, subprocess.CalledProcessError):
                log = self.cache / "jobs" / f'{job["id"]}.log'
                message = "gem5_data_proc 处理失败：" + "\n".join(log.read_text(errors="replace").splitlines()[-8:])
            else:
                message = str(error)
            self._update(job, status="failed", stage="分析失败", error=message, finished_at=time.time())
        finally:
            with self.lock:
                self.pending.pop(identity, None)
                # Keep a bounded in-memory history; completed results remain on disk.
                completed = [k for k, j in self.jobs.items() if j["status"] in {"complete", "failed", "cancelled"}]
                for key in completed[:-64]:
                    self.jobs.pop(key, None)
                    self.cancel_events.pop(key, None)
                    self.futures.pop(key, None)

    def get_job(self, identity):
        with self.lock:
            if identity not in self.jobs:
                raise QueryError("分析任务不存在，或服务已重启")
            job = dict(self.jobs[identity])
            job["elapsed_seconds"] = round((job.get("finished_at") or time.time()) - job["created_at"])
            progress = self.progress_paths.get(identity)
            if progress and not self.cancel_events[identity].is_set():
                try:
                    detail = json.loads(progress.read_text())
                    job["progress"] = detail
                    job["stage"] = f'{job["stage"].split("：")[0]}：{detail["stage"]} {detail["completed"]}/{detail["total"]} 切片'
                except (OSError, ValueError, KeyError):
                    pass
            job["queue_position"] = (1 + [k for k, j in self.jobs.items() if j["status"] == "queued"].index(identity)
                                     if job["status"] == "queued" else 0)
            return job

    def metrics(self, snapshots, search="", groups=(), offset=0, limit=100):
        if not snapshots or len(snapshots) > 2:
            raise QueryError("请先完成 A/B 分析")
        catalogs = []
        for snapshot in snapshots:
            if not re.fullmatch(r"[a-f0-9]{64}", snapshot):
                raise QueryError("无效的 snapshot ID")
            path = self.cache / "runs" / snapshot / "catalog.json"
            if not path.is_file():
                raise QueryError("计数器目录不存在")
            catalogs.append(json.loads(path.read_text()))
        group_map = {}
        for catalog in catalogs:
            for name, columns in catalog["groups"].items():
                group_map.setdefault(name, set()).update(columns)
        yaml_names = set().union(*(set(c["yaml_metrics"]) for c in catalogs))
        group_metrics = {group: sorted(columns & yaml_names) for group, columns in group_map.items()}
        if groups:
            yaml_names &= set().union(*(group_map.get(g, set()) for g in groups))
        raw_names = set().union(*(set(c["raw_metrics"]) for c in catalogs)) if not groups else set()
        items = [{"id": n, "name": n, "kind": "yaml", "groups": [g for g, cols in group_map.items() if n in cols]} for n in sorted(yaml_names)]
        items.extend({"id": "raw:" + n, "name": n, "kind": "raw", "groups": []} for n in sorted(raw_names))
        items = [item for item in items if search.lower() in item["name"].lower()]
        return {"items": items[offset:offset + limit], "total": len(items),
                "groups": sorted(group_map), "group_metrics": group_metrics, "offset": offset, "limit": limit}

    def close(self):
        self.executor.shutdown(wait=True)

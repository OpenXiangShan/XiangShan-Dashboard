"""Resolve RTL Actions receipts from executed job logs, never guessed dates."""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess

ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
STAMP = re.compile(r"^\d{4}-\d\d-\d\dT\S+\s")


def github(endpoint, raw=False):
    result = subprocess.run(["gh", "api", endpoint], capture_output=True, timeout=60)
    if result.returncode:
        raise ValueError("无法读取 RTL CI 元数据/日志；请检查 gh 登录、网络或日志是否过期：" + result.stderr.decode(errors="replace")[-350:])
    return result.stdout.decode(errors="replace") if raw else json.loads(result.stdout)


def executed_metadata(log):
    lines = [STAMP.sub("", ANSI.sub("", line)) for line in log.splitlines()]
    # Only actual environment dumps count, not shell examples/conditional echoes.
    metadata = {}
    for key in ("SPEC_DIR", "CKPT_JSON_PATH", "CKPT_HOME", "SCORE_FILE"):
        values = [m[1] for line in lines if (m := re.fullmatch(r"\s+" + key + r":\s+(/\S+)", line))]
        if values:
            if len(set(values)) != 1:
                raise ValueError(f"RTL job 中存在多个 {key}，无法唯一绑定")
            metadata[key] = values[0]
    if not {"SPEC_DIR", "CKPT_JSON_PATH", "SCORE_FILE"}.issubset(metadata):
        return None
    for index, line in enumerate(lines):
        if "[command]" in line and "git log -1 --format=%H" in line:
            for candidate in lines[index + 1:index + 5]:
                if re.fullmatch(r"[a-f0-9]{40}", candidate):
                    metadata["commit"] = candidate
                    break
            if "commit" in metadata:
                break
    if "commit" not in metadata:
        raise ValueError("RTL job 缺少实际 checkout SHA，不能使用 workflow head SHA 代替")
    score = Path(metadata["SCORE_FILE"]).name
    match = re.fullmatch(r"score-(spec(?:06|17|26)[A-Za-z0-9_.-]*)\.txt", score)
    if not match:
        return None
    metadata["benchmark_type"] = match[1]
    return metadata


def resolve_rtl(run_id, evidence_root, report_roots, profile_roots):
    cache = evidence_root / str(run_id)
    cache.mkdir(parents=True, exist_ok=True)
    receipt = cache / "receipts.json"
    if receipt.is_file():
        receipts = json.loads(receipt.read_text())
    else:
        run = github(f"repos/OpenXiangShan/XiangShan/actions/runs/{run_id}")
        if run.get("status") != "completed":
            raise ValueError("RTL CI 尚未结束；请等待报告完成")
        receipts, page = [], 1
        while True:
            jobs = github(f"repos/OpenXiangShan/XiangShan/actions/runs/{run_id}/jobs?per_page=100&page={page}")["jobs"]
            for job in jobs:
                name = job.get("name", "")
                if job.get("conclusion") != "success" or "Checkpoints" not in name or "Fuzz" in name:
                    continue
                log_path = cache / f'{job["id"]}.log'
                if log_path.is_file():
                    log = log_path.read_text()
                else:
                    log = github(f'repos/OpenXiangShan/XiangShan/actions/jobs/{job["id"]}/logs', raw=True)
                    log_path.write_text(log)
                metadata = executed_metadata(log)
                if metadata:
                    receipts.append({**metadata, "job_id": job["id"], "job_name": name,
                                     "run_attempt": run.get("run_attempt"), "actions_url": run["html_url"]})
            if len(jobs) < 100:
                break
            page += 1
        if not receipts:
            raise ValueError("该 RTL Run 没有包含可核对 SPEC_DIR / CKPT_JSON_PATH 的成功性能 job")
        temporary = receipt.with_suffix(".tmp")
        temporary.write_text(json.dumps(receipts, ensure_ascii=False))
        temporary.replace(receipt)
    checked = []
    for metadata in receipts:
        report = Path(metadata["SPEC_DIR"]).resolve()
        profile = Path(metadata["CKPT_JSON_PATH"]).resolve()
        score = Path(metadata["SCORE_FILE"]).resolve()
        if not any(report.is_relative_to(root.resolve()) for root in report_roots):
            raise ValueError(f"RTL 报告路径不在配置的 --rtl-report-root 中：{report}")
        if not any(profile.is_relative_to(root.resolve()) for root in profile_roots):
            raise ValueError(f"RTL 权重路径不在配置的 --profile-root 中：{profile}")
        if not score.is_relative_to(report):
            raise ValueError("RTL score 路径不属于该报告目录")
        checked.append({**metadata, "path": str(report), "profile_path": str(profile),
                        "score_path": str(score), "available": report.is_dir() and profile.is_file()})
    return checked

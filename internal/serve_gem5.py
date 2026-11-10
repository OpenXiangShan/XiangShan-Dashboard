#!/usr/bin/env python3
"""Serve the opt-in developer Dashboard and its local GEM5 analysis API."""
from __future__ import annotations

import argparse
import csv
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from gem5_analysis import AnalysisService, QueryError

ROOT = Path(__file__).resolve().parents[1]


class Handler(SimpleHTTPRequestHandler):
    service: AnalysisService

    def json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlsplit(self.path)
        if not parsed.path.startswith("/api/analysis/"):
            return super().do_GET()
        try:
            query = parse_qs(parsed.query)
            route = parsed.path.removeprefix("/api/analysis/")
            if route == "health":
                return self.json({"status": "ok", "workers": self.service.workers,
                                  "extract_jobs": self.service.extract_jobs, "max_pending": self.service.max_pending})
            if route == "resolve":
                return self.json(self.service.resolve_run(query.get("run", [""])[0], query.get("backend", [None])[0]))
            if route == "metrics":
                offset, limit = int(query.get("offset", [0])[0]), int(query.get("limit", [100])[0])
                if offset < 0 or not 1 <= limit <= 200:
                    raise QueryError("无效的计数器分页参数")
                return self.json(self.service.metrics(query.get("snapshot", []), query.get("q", [""])[0],
                                 query.get("group", []), offset, limit))
            if route.startswith("jobs/"):
                identity = route[5:]
                if identity.endswith("/rows.csv"):
                    job = self.service.get_job(identity.removesuffix("/rows.csv"))
                    rows = job.get("result", {}).get("rows", [])
                    if job["status"] != "complete" or not rows:
                        raise QueryError("暂无可导出的分析结果")
                    stream = io.StringIO()
                    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                    writer.writeheader()
                    writer.writerows(rows)
                    body = ("\ufeff" + stream.getvalue()).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/csv; charset=utf-8")
                    self.send_header("Content-Disposition", 'attachment; filename="gem5-comparison.csv"')
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    return self.wfile.write(body)
                return self.json(self.service.get_job(identity))
            self.json({"error": "API 不存在"}, 404)
        except (QueryError, ValueError) as error:
            self.json({"error": str(error)}, 400)
        except OSError as error:
            self.json({"error": f"无法读取本地数据：{error}"}, 500)

    def do_POST(self):
        cancel_match = re.fullmatch(r"/api/analysis/jobs/([a-f0-9]{32})/cancel", self.path)
        if self.path != "/api/analysis/compare" and not cancel_match:
            return self.json({"error": "API 不存在"}, 404)
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc != self.headers.get("Host"):
            return self.json({"error": "仅接受同源分析请求"}, 403)
        if self.headers.get_content_type() != "application/json":
            return self.json({"error": "需要 application/json"}, 415)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65536:
                raise QueryError("请求大小无效")
            request = json.loads(self.rfile.read(length))
            if cancel_match:
                return self.json(self.service.cancel_job(cancel_match[1]))
            self.json(self.service.submit(request), 202)
        except (QueryError, ValueError, TypeError) as error:
            self.json({"error": str(error)}, 400)

    def translate_path(self, path):
        decoded = unquote(urlsplit(path).path).lstrip("/")
        if decoded.startswith("data/"):
            root, relative = ROOT / "data", decoded.removeprefix("data/")
        else:
            root, relative = Path(self.directory), decoded
        candidate = (root / relative).resolve()
        if not candidate.is_relative_to(root.resolve()):
            return str(Path(self.directory) / "__not_found__")
        return str(candidate)

    def list_directory(self, path):
        self.send_error(404, "Directory listing disabled")
        return None

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--archive-root", type=Path, default=Path("/nfs/home/share/gem5_ci/performance_data"))
    result.add_argument("--processor", type=Path, required=True)
    result.add_argument("--rtl-report-root", type=Path, action="append", help="allowed RTL report root; repeatable")
    result.add_argument("--profile-root", type=Path, action="append", help="allowed RTL checkpoint profile root; repeatable")
    result.add_argument("--cache", type=Path, default=ROOT / "internal/local/gem5")
    result.add_argument("--bind", default="127.0.0.1")
    result.add_argument("--port", type=int, default=8002)
    result.add_argument("--workers", type=int, default=1)
    result.add_argument("--extract-jobs", type=int, default=4)
    result.add_argument("--max-pending", type=int, default=8)
    return result


def main():
    args = parser().parse_args()
    if not 1 <= args.workers <= 4 or not 1 <= args.extract_jobs <= 16 or not 1 <= args.max_pending <= 64:
        raise SystemExit("workers=1..4, extract-jobs=1..16, max-pending=1..64")
    build = ROOT / "dist-internal"
    if not (build / "index.html").is_file():
        raise SystemExit("请先运行 npm run build:internal")
    service = AnalysisService(args.archive_root, args.processor, args.cache,
                              args.workers, args.extract_jobs, args.max_pending,
                              rtl_report_roots=args.rtl_report_root, profile_roots=args.profile_root)
    Handler.service = service
    server = ThreadingHTTPServer((args.bind, args.port), partial(Handler, directory=str(build)))
    print(f"GEM5 developer Dashboard: http://{args.bind}:{server.server_port}/?analysis=1", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        service.close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Serve the internal dashboard, history analysis, and shared generated reports."""

from __future__ import annotations

import argparse
from functools import partial
from io import BytesIO
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
LINKED_PAGES = {
    "/xiangshan-performance-dashboard/reports/20261103/": (
        "report",
        "reports/20261103/index.html",
    ),
    "/xiangshan-performance-dashboard/reports/20261103/index.html": (
        "report",
        "reports/20261103/index.html",
    ),
    "/xiangshan-performance-dashboard/reports/20261103/report.html": (
        "report-text",
        "reports/20261103/report.html",
    ),
    "/xiangshan-performance-dashboard/reports/tea-dip/": (
        "tea-dip",
        "reports/tea-dip/index.html",
    ),
    "/xiangshan-performance-dashboard/reports/tea-dip/index.html": (
        "tea-dip",
        "reports/tea-dip/index.html",
    ),
}
PAGE_LINKS = (
    ("/xiangshan-performance-dashboard/", "性能看板", "dashboard"),
    ("/xiangshan-performance-dashboard/reports/20261103/", "分析报告", "report"),
    ("/xiangshan-performance-dashboard/reports/tea-dip/", "TEA+DIP", "tea-dip"),
    ("/xiangshan-performance-dashboard/legacy.html", "完整相关性与历史分析", "legacy"),
)


def linked_page_bar(page: str) -> bytes:
    current = "report" if page == "report-text" else page
    links = "".join(
        f'<a href="{href}"'
        f'{" aria-current=\"page\"" if key == current else ""}>{label}</a>'
        for href, label, key in PAGE_LINKS
    )
    return (
        '<link rel="stylesheet" href="/internal-linked-pages.css">'
        f'<body class="internal-linked-page internal-{page}">'
        '<div class="internal-pagebar" aria-label="内部页面导航">'
        '<a class="internal-pagebar-brand" href="/xiangshan-performance-dashboard/">'
        '<span class="internal-pagebar-mark">X</span>'
        '<span>香山 <small>内部性能分析</small></span></a>'
        f'<div class="internal-pagebar-links">{links}</div></div>'
    ).encode("utf-8")


class Handler(SimpleHTTPRequestHandler):
    data_root: Path
    reports_root: Path

    def translate_path(self, path: str) -> str:
        translated = Path(super().translate_path(path))
        parts = translated.relative_to(self.directory).parts
        if parts[:2] == ("outputs", "mainline-history"):
            return str(self.data_root.joinpath(*parts[2:]))
        if parts[:1] == ("xiangshan-performance-dashboard",):
            relative = parts[1:]
            if not relative or relative == ("index.html",):
                return str(ROOT / "dist-internal/index.html")
            if relative[:1] == ("reports",):
                return str(self.reports_root.joinpath(*relative[1:]))
            if relative[:1] == ("assets",):
                return str((ROOT / "dist-internal").joinpath(*relative))
            if relative == ("comparison.js",):
                return str(ROOT / "internal/comparison.js")
            if relative == ("legacy.html",):
                return str(ROOT / "internal/legacy/index.html")
            return str((ROOT / "internal/legacy").joinpath(*relative))
        return str(translated)

    def send_head(self):
        path = urlsplit(self.path).path
        if path == "/internal-linked-pages.css":
            css = (ROOT / "internal/linked_pages.css").read_bytes()
            return self.send_bytes(css, "text/css; charset=utf-8")
        page = LINKED_PAGES.get(path)
        if page:
            kind, relative = page
            source = self.reports_root / Path(relative).relative_to("reports")
            if not source.is_file():
                self.send_error(404, "Linked analysis page not found")
                return None
            html = source.read_bytes()
            if kind == "tea-dip":
                html = html.replace(b"</style>", b"</style>" + linked_page_bar(kind), 1)
            else:
                html = html.replace(b"<body>", linked_page_bar(kind), 1)
            return self.send_bytes(html, "text/html; charset=utf-8")
        return super().send_head()

    def send_bytes(self, content: bytes, content_type: str) -> BytesIO:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        return BytesIO(content)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    data = parser.add_mutually_exclusive_group(required=True)
    data.add_argument("--data-root", type=Path,
                      help="directory containing the private manifest.json export")
    data.add_argument("--data-dir", type=Path,
                      help="shared root containing mainline-history/")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", default=8001, type=int)
    args = parser.parse_args()
    Handler.data_root = (args.data_root or args.data_dir / "mainline-history").resolve()
    Handler.reports_root = Handler.data_root.parent / "reports"
    server = ThreadingHTTPServer(
        (args.bind, args.port), partial(Handler, directory=str(ROOT / "dist-internal"))
    )
    print(f"Internal dashboard: http://{args.bind}:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

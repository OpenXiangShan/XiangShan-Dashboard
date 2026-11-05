#!/usr/bin/env python3
"""Serve the optional internal build with local performance data."""

from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Handler(SimpleHTTPRequestHandler):
    data_root: Path
    legacy_root: Path | None

    def translate_path(self, path: str) -> str:
        translated = Path(super().translate_path(path))
        relative = translated.relative_to(self.directory)
        if relative.parts[:2] == ("outputs", "mainline-history"):
            return str(self.data_root.joinpath(*relative.parts[2:]))
        if relative.parts[:1] == ("xiangshan-performance-dashboard",) and self.legacy_root:
            return str(self.legacy_root.joinpath(*relative.parts[1:]))
        return str(translated)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True, type=Path,
                        help="directory containing the private manifest.json export")
    parser.add_argument("--legacy-root", type=Path,
                        help="optional legacy analysis page and generated reports")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", default=8001, type=int)
    args = parser.parse_args()
    Handler.data_root = args.data_root.resolve()
    Handler.legacy_root = args.legacy_root.resolve() if args.legacy_root else None
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

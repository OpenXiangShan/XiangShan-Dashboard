#!/usr/bin/env python3
"""CLI over the same JSON API used by the developer Dashboard."""
import argparse
import json
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default="http://127.0.0.1:8002")
    sub = parser.add_subparsers(dest="command", required=True)
    resolve = sub.add_parser("resolve")
    resolve.add_argument("run")
    compare = sub.add_parser("compare")
    compare.add_argument("a", help="resolved archive ID")
    compare.add_argument("b", help="resolved archive ID")
    compare.add_argument("--metric", action="append", default=[])
    compare.add_argument("--level", choices=["benchmark", "simpoint"], default="benchmark")
    args = parser.parse_args()

    def api(path, payload=None):
        request = Request(args.server.rstrip("/") + "/api/analysis/" + path,
                          data=json.dumps(payload).encode() if payload is not None else None,
                          headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as error:
            raise SystemExit(error.read().decode()) from None

    if args.command == "resolve":
        result = api("resolve?" + urlencode({"run": args.run}))
    else:
        result = api("compare", {"a": args.a, "b": args.b, "metrics": args.metric or ["cpi"], "level": args.level})
        while result["status"] in {"queued", "running"}:
            time.sleep(1)
            result = api("jobs/" + result["id"])
        if result["status"] == "failed":
            raise SystemExit(result["error"])
        result = result["result"]
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

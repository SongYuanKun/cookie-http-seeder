#!/usr/bin/env python3
"""Minimal example: read a seeded cookie_header for HTTP requests.

Usage (after a successful push):

  python examples/consume_cookies.py fang
  python examples/consume_cookies.py fang --data-dir ./data
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="source name, e.g. fang")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="directory containing {source}-cookies.json",
    )
    parser.add_argument(
        "--url",
        default="",
        help="optional URL to GET with the Cookie header (demo only)",
    )
    args = parser.parse_args(argv)

    path = args.data_dir / f"{args.source}-cookies.json"
    if not path.is_file():
        print(f"missing {path}; push cookies from the extension first", file=sys.stderr)
        return 1

    payload = json.loads(path.read_text(encoding="utf-8"))
    header = payload.get("cookie_header")
    if not isinstance(header, str) or not header.strip():
        print(f"{path} has no cookie_header", file=sys.stderr)
        return 1

    print(f"source={args.source}")
    print(f"updatedAt={payload.get('updatedAt', '')}")
    print(f"cookie_header_bytes={len(header.encode('utf-8'))}")

    if args.url:
        request = urllib.request.Request(
            args.url,
            headers={"Cookie": header, "User-Agent": "cookie-http-seeder-example/0.1"},
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            print(f"GET {args.url} -> HTTP {response.status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

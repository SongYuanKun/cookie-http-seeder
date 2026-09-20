#!/usr/bin/env python3
"""Inspect a snapshot, or GET one explicitly scoped URL without following redirects."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from cookie_http_seeder.store import load_cookie_header, read_snapshot


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A different URL needs a new allow-list check and Cookie selection.
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="configured source name")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--url", default="", help="optional URL to GET; never follows redirects")
    args = parser.parse_args(argv)
    try:
        doc = read_snapshot(args.source, data_dir=args.data_dir)
        if doc is None:
            print("missing snapshot; push cookies from the extension first", file=sys.stderr)
            return 1
        print(f"source={args.source}")
        print(f"updatedAt={doc.get('updatedAt', '') if isinstance(doc, dict) else ''}")
        if not args.url:
            print("no request sent; pass --url to perform one URL-scoped GET")
            return 0
        header = load_cookie_header(source=args.source, data_dir=args.data_dir, url=args.url)
        if not header:
            print("no applicable cookies; check scope/expiry or re-seed", file=sys.stderr)
            return 1
        request = Request(args.url, headers={
            "Cookie": header, "User-Agent": "cookie-http-seeder-example/0.2",
        })
        with build_opener(NoRedirect()).open(request, timeout=15) as response:
            print(f"HTTP {response.status}")
        return 0
    except HTTPError as error:
        print(f"HTTP {error.code}; redirects are not followed", file=sys.stderr)
    except (OSError, ValueError, TypeError, KeyError, URLError):
        # Do not print cookie values, response bodies or signed URLs in diagnostics.
        print("unable to read scoped cookies or send request; check configuration", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

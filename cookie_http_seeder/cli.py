from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .notify import notify_needed
from .receiver import (
    configure,
    default_token_file,
    ensure_token_file,
    resolve_token,
    serve,
    status_document,
)
from .store import default_data_dir, load_sources


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cookie-http-seeder",
        description="Session seeder for Cookie-HTTP crawlers",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="cookie + token directory (default: ./data)",
    )
    parser.add_argument(
        "--sources",
        type=Path,
        default=None,
        help="sources JSON (default: examples/sources.json or packaged defaults)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-token", help="create data/cookie-receiver.token if missing")

    serve_p = sub.add_parser("serve", help="run the loopback receiver")
    serve_p.add_argument("--host", default="127.0.0.1")
    serve_p.add_argument("--port", type=int, default=18765)
    serve_p.add_argument("--token", default="")
    serve_p.add_argument("--token-file", type=Path, default=None)
    serve_p.add_argument(
        "--no-notify",
        action="store_true",
        help="disable webhook notify on successful push",
    )
    serve_p.add_argument("--webhook-file", type=Path, default=None)

    need = sub.add_parser("notify-needed", help="send a refresh-needed webhook message")
    need.add_argument("--reason", default="cookies need refresh")
    need.add_argument("--webhook-file", type=Path, default=None)

    status = sub.add_parser("status", help="print local cookie file status as JSON")
    status.add_argument("--token", default="")
    status.add_argument("--token-file", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    data_dir = args.data_dir or default_data_dir()
    sources = load_sources(args.sources)
    configure(sources=sources, data_dir=data_dir)

    if args.command == "init-token":
        path = default_token_file(data_dir)
        token = ensure_token_file(path)
        print(f"token_file={path}")
        print(f"token={token}")
        return 0

    if args.command == "notify-needed":
        if args.webhook_file is not None:
            os.environ["COOKIE_HTTP_SEEDER_WEBHOOK_FILE"] = str(args.webhook_file)
        status = notify_needed(reason=args.reason, webhook_path=args.webhook_file)
        print(f"notify={status}")
        return 0 if status == "sent" else 1

    if args.command == "status":
        print(json.dumps(status_document(), ensure_ascii=False, indent=2))
        return 0

    if args.command == "serve":
        token_file = args.token_file or default_token_file(data_dir)
        if args.token:
            token = resolve_token(token=args.token, token_file=None)
        elif os.environ.get("COOKIE_HTTP_SEEDER_TOKEN"):
            token = resolve_token(token=None, token_file=None)
        else:
            token = ensure_token_file(token_file)
            print(f"token_file={token_file}")
        if args.webhook_file is not None:
            os.environ["COOKIE_HTTP_SEEDER_WEBHOOK_FILE"] = str(args.webhook_file)
        serve(
            host=args.host,
            port=args.port,
            token=token,
            notify=not bool(args.no_notify),
        )
        return 0

    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())

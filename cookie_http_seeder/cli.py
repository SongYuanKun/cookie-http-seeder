from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .notify import notify_needed
from .paths import (
    ENV_NO_NOTIFY,
    ENV_TOKEN,
    ENV_WEBHOOK_FILE,
    ensure_data_dir,
    env_flag,
    env_host,
    env_port,
    sources_override_file,
    token_file,
    webhook_file,
)
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
        help="cookie + token directory (env COOKIE_HTTP_SEEDER_DATA, else ./data or XDG)",
    )
    parser.add_argument(
        "--sources",
        type=Path,
        default=None,
        help="sources JSON (else $data-dir/sources.json, env, examples, or packaged)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-token", help="create cookie-receiver.token if missing")
    sub.add_parser("paths", help="print resolved data paths as JSON")

    serve_p = sub.add_parser("serve", help="run the loopback receiver")
    serve_p.add_argument(
        "--host",
        default=None,
        help="bind address (default: env COOKIE_HTTP_SEEDER_HOST or 127.0.0.1)",
    )
    serve_p.add_argument(
        "--port",
        type=int,
        default=None,
        help="bind port (default: env COOKIE_HTTP_SEEDER_PORT or 18765)",
    )
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


def _paths_document(data_dir: Path) -> dict[str, object]:
    hook = webhook_file(data_dir)
    override = sources_override_file(data_dir)
    return {
        "dataDir": str(data_dir),
        "tokenFile": str(token_file(data_dir)),
        "webhookFile": str(hook),
        "webhookPresent": hook.is_file(),
        "sourcesOverride": str(override),
        "sourcesOverridePresent": override.is_file(),
    }


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    data_dir = ensure_data_dir(args.data_dir or default_data_dir())
    sources = load_sources(args.sources, data_dir=data_dir)
    configure(sources=sources, data_dir=data_dir)

    if args.command == "init-token":
        path = default_token_file(data_dir)
        token = ensure_token_file(path)
        print(f"token_file={path}")
        print(f"token={token}")
        return 0

    if args.command == "paths":
        print(json.dumps(_paths_document(data_dir), ensure_ascii=False, indent=2))
        return 0

    if args.command == "notify-needed":
        if args.webhook_file is not None:
            os.environ[ENV_WEBHOOK_FILE] = str(args.webhook_file)
        status = notify_needed(reason=args.reason, webhook_path=args.webhook_file)
        print(f"notify={status}")
        return 0 if status == "sent" else 1

    if args.command == "status":
        doc = status_document()
        doc.update(_paths_document(data_dir))
        print(json.dumps(doc, ensure_ascii=False, indent=2))
        return 0

    if args.command == "serve":
        token_path = args.token_file or default_token_file(data_dir)
        if args.token:
            token = resolve_token(token=args.token, token_file=None)
        elif os.environ.get(ENV_TOKEN):
            token = resolve_token(token=None, token_file=None)
        else:
            token = ensure_token_file(token_path)
            print(f"token_file={token_path}")
        if args.webhook_file is not None:
            os.environ[ENV_WEBHOOK_FILE] = str(args.webhook_file)
        no_notify_env = env_flag(ENV_NO_NOTIFY)
        notify = not bool(args.no_notify) and not bool(no_notify_env)
        host = args.host or env_host()
        port = args.port if args.port is not None else env_port()
        print(json.dumps(_paths_document(data_dir), ensure_ascii=False), flush=True)
        serve(host=host, port=port, token=token, notify=notify)
        return 0

    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())

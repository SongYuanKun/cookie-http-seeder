from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .notify import notify_needed
from .paths import (
    ENV_NO_NOTIFY,
    ENV_SOURCES,
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
    ReceiverState,
    configure,
    default_token_file,
    ensure_token_file,
    resolve_token,
    serve,
    status_document,
)
from .senders import list_sender_tags, sender_directory, sender_tag
from .store import default_data_dir, load_cookie_header, load_sources
from .time_display import display_times


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cookie-http-seeder", description="Cookie snapshot seeder"
    )
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--sources", type=Path, default=None,
                        help="managed sources JSON; UI edits persist to this file")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-token", help="create cookie-receiver.token if missing")
    sub.add_parser("paths", help="print resolved data paths")
    sub.add_parser("senders", help="list initialized sender labels (no credentials)")
    serve_p = sub.add_parser("serve", help="run the loopback receiver")
    serve_p.add_argument("--host", default=None)
    serve_p.add_argument("--port", type=int, default=None)
    serve_p.add_argument("--token", default="")
    serve_p.add_argument("--token-file", type=Path, default=None)
    serve_p.add_argument("--no-notify", action="store_true")
    serve_p.add_argument("--webhook-file", type=Path, default=None)
    need = sub.add_parser("notify-needed", help="send a refresh-needed notification")
    need.add_argument("--reason", default="cookies need refresh")
    need.add_argument("--webhook-file", type=Path, default=None)
    status = sub.add_parser("status", help="local cookie file status, not login validity")
    status.add_argument("--token", default="")  # legacy CLI compatibility; local read only
    status.add_argument("--token-file", type=Path, default=None)
    header = sub.add_parser("header", help="print a URL-scoped Cookie header (sensitive)")
    header.add_argument("source")
    header.add_argument("--url", required=True)
    doctor = sub.add_parser("doctor", help="credential-free connection and storage diagnostics")
    doctor.add_argument("--endpoint", default="http://127.0.0.1:18765")
    doctor.add_argument("--token-file", type=Path, default=None)
    doctor.add_argument("--local-only", action="store_true")
    report = sub.add_parser("report", help="report a crawler observation tied to an exact snapshot")
    report.add_argument("source")
    report.add_argument("--snapshot-version", required=True)
    report.add_argument("--result", choices=["valid", "invalid", "error", "unverified"],
                        required=True)
    report.add_argument("--reason-code", required=True)
    report.add_argument("--endpoint", default="http://127.0.0.1:18765")
    report.add_argument("--token-file", type=Path, default=None)
    for command in (status, doctor, report, header):
        command.add_argument("--sender-tag", type=sender_tag, default="default",
                             help="select one sender label; default keeps legacy storage")
    for command in (status, doctor, report):
        command.add_argument("--json", dest="raw_json", action="store_true",
                             help="machine-readable JSON with original UTC timestamps; "
                                  "default display uses local YYYY-MM-DD HH:mm:ss")
    return parser


def _paths_document(data_dir: Path) -> dict[str, object]:
    hook, override = webhook_file(data_dir), sources_override_file(data_dir)
    return {"dataDir": str(data_dir), "tokenFile": str(token_file(data_dir)),
            "webhookFile": str(hook), "webhookPresent": hook.is_file(),
            "sourcesOverride": str(override), "sourcesOverridePresent": override.is_file()}


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    data_dir = args.data_dir or default_data_dir()
    if args.command == "senders":
        print(json.dumps({"senders": list_sender_tags(data_dir)}, indent=2))
        return 0
    if args.command == "status" and args.sender_tag != "default":
        selected = sender_directory(data_dir, args.sender_tag)
        config = selected / "sources.json"
        if not config.is_file() or config.is_symlink():
            print(json.dumps({"ok": False, "error": "sender_not_initialized"}))
            return 1
        state = ReceiverState(load_sources(config), selected, config)
        state.sender_tag = args.sender_tag
        doc = {**state.status(), "dataDir": str(selected)}
        print(json.dumps(doc if args.raw_json else display_times(doc),
                         ensure_ascii=False, indent=2))
        return 0
    if args.command == "doctor":
        from .diagnostics import diagnose
        doc = diagnose(data_dir, endpoint=args.endpoint, sources_path=args.sources,
                       token_path=args.token_file, local_only=args.local_only,
                       sender_tag=args.sender_tag)
        print(json.dumps(doc if args.raw_json else display_times(doc),
                         ensure_ascii=False, indent=2))
        return 0 if doc["ok"] else 1
    if args.command == "report":
        from .client import ClientError, ReceiverClient
        try:
            token = resolve_token(token=os.environ.get(ENV_TOKEN),
                                  token_file=args.token_file or token_file(data_dir))
            result = ReceiverClient(args.endpoint, token, sender_tag=args.sender_tag).report(
                args.source, args.snapshot_version, args.result, args.reason_code)
        except (OSError, ValueError, SystemExit, ClientError):
            print(json.dumps({"ok": False,
                              "error": "report_failed_check_connection_version_and_reason"}))
            return 1
        print(json.dumps(result if args.raw_json else display_times(result),
                         ensure_ascii=False, indent=2))
        return 0
    data_dir = ensure_data_dir(data_dir)
    sources = load_sources(args.sources, data_dir=data_dir)
    env = os.environ.get(ENV_SOURCES, "").strip()
    sources_path = args.sources or (Path(env).expanduser() if env else None)
    configure(sources=sources, data_dir=data_dir, sources_path=sources_path)
    if args.command == "init-token":
        path = default_token_file(data_dir)
        token = ensure_token_file(path)
        print(f"token_file={path}\ntoken={token}")
        return 0
    if args.command == "paths":
        print(json.dumps(_paths_document(data_dir), ensure_ascii=False, indent=2))
        return 0
    if args.command == "notify-needed":
        status = notify_needed(reason=args.reason,
                               webhook_path=args.webhook_file or webhook_file(data_dir))
        print(f"notify={status}")
        return 0 if status == "sent" else 1
    if args.command == "status":
        doc = {**status_document(), **_paths_document(data_dir)}
        print(json.dumps(doc if args.raw_json else display_times(doc),
                         ensure_ascii=False, indent=2))
        return 0
    if args.command == "header":
        header = load_cookie_header(source=args.source, data_dir=data_dir, url=args.url,
                                    sender_tag=args.sender_tag)
        if header:
            print(header)
        return 0 if header else 1
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
        notify = not args.no_notify and not bool(env_flag(ENV_NO_NOTIFY))
        port = args.port if args.port is not None else env_port()
        if not 1 <= port <= 65535:
            raise SystemExit("invalid port")
        serve(host=args.host or env_host(), port=port, token=token, notify=notify)
        return 0
    raise SystemExit("unknown command")


if __name__ == "__main__":
    raise SystemExit(main())

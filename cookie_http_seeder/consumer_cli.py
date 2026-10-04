"""Credential-free CLI results and bounded standard-library site transport."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .client import ClientError, ReceiverClient, receiver_origin
from .consumer import ConsumerBusy, CookieConsumer, CredentialsUnavailable, LoginRequired
from .consumer_state import ConsumerState, consumer_identity, default_state_path
from .paths import ENV_TOKEN, token_file
from .receiver import resolve_token
from .response_rules import Response, ResponseRules
from .senders import sender_tag
from .store import cookie_path_for_source


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send_request(url, headers) -> Response:
    opener = build_opener(ProxyHandler({}), _NoRedirects())
    try:
        response = opener.open(Request(url, headers=headers), timeout=8)
    except HTTPError as error:
        response = error
    with response:
        raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            return Response(599)
        return Response(response.code, raw.decode("utf-8", errors="replace"),
                        dict(response.headers))


def add_commands(sub):
    for name in ("consume", "consumer-status", "consumer-flush"):
        command = sub.add_parser(name)
        command.add_argument("source")
        command.add_argument("--sender-tag", type=sender_tag, default="default")
        command.add_argument("--endpoint", default="http://127.0.0.1:18765")
        command.add_argument("--state-path", type=Path)
        if name != "consumer-status":
            command.add_argument("--token-file", type=Path)
        if name == "consume":
            command.add_argument("--url", required=True)
            command.add_argument("--rules", type=Path, required=True)
            command.add_argument("--wait-seconds", type=float, default=0)
            command.add_argument("--poll-seconds", type=float, default=5)


def run(args, data_dir) -> int:
    try:
        endpoint = receiver_origin(args.endpoint)
        cookie_path_for_source(args.source, data_dir=data_dir)  # name validation only
        identity = consumer_identity(data_dir, endpoint, args.sender_tag, args.source)
        path = args.state_path or default_state_path(identity)
        if args.command == "consumer-status":
            result = ConsumerState(path, identity, args.source).status()
            print(json.dumps(result))
            return 0 if result["ok"] else 1
        token = resolve_token(token=os.environ.get(ENV_TOKEN),
                              token_file=args.token_file or token_file(data_dir))
        client = ReceiverClient(endpoint, token, sender_tag=args.sender_tag)
        if args.command == "consumer-flush":
            consumer = CookieConsumer(source=args.source, data_dir=data_dir, client=client,
                                      classify=lambda _: None, state_path=path)
            status = consumer.flush_feedback()
            print(json.dumps({"ok": status not in {"blocked", "exhausted"},
                              "feedback_status": status, "state": consumer.status()}))
            return 1 if status in {"blocked", "exhausted"} else 0
        if (not math.isfinite(args.wait_seconds) or not 0 <= args.wait_seconds <= 86400
                or not math.isfinite(args.poll_seconds) or not 0.1 <= args.poll_seconds <= 3600):
            raise ValueError("invalid wait bounds")
        if args.rules.stat().st_size > 131072:
            raise ValueError("rule document too large")
        rules = ResponseRules.from_document(json.loads(args.rules.read_text(encoding="utf-8")))
        consumer = CookieConsumer(source=args.source, data_dir=data_dir, client=client,
                                  classify=rules, state_path=path)
        try:
            outcome = consumer.request(args.url, send_request)
        except (LoginRequired, CredentialsUnavailable) as error:
            if args.wait_seconds > 0:
                if not consumer.wait_for_update(args.url, timeout=args.wait_seconds,
                                                poll_interval=args.poll_seconds):
                    print(json.dumps({"ok": False, "error": "wait_timeout"}))
                    return 3
                outcome = consumer.request(args.url, send_request)
            else:
                print(json.dumps({"ok": False, "error": "login_required" if
                                  isinstance(error, LoginRequired) else "credentials_unavailable"}))
                return 3 if isinstance(error, LoginRequired) else 2
        print(json.dumps({"ok": outcome.observation.result == "valid",
                          "result": outcome.observation.result,
                          "reason_code": outcome.observation.reason_code,
                          "snapshot_version": outcome.snapshot_version,
                          "feedback_status": outcome.feedback_status, "paused": outcome.paused}))
        return {"valid": 0, "invalid": 3, "error": 4, "unverified": 4}[outcome.observation.result]
    except (OSError, ValueError, ClientError, ConsumerBusy, SystemExit):
        print(json.dumps({"ok": False, "error": "consumer_configuration_or_runtime_error"}))
        return 1

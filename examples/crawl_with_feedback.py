#!/usr/bin/env python3
"""One scoped GET with explicit site rules, durable pause and version-bound feedback."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from cookie_http_seeder.client import ReceiverClient
from cookie_http_seeder.consumer import (
    ConsumerBusy,
    CookieConsumer,
    CredentialsUnavailable,
    LoginRequired,
    Response,
    ResponseRules,
)
from cookie_http_seeder.paths import token_file
from cookie_http_seeder.receiver import resolve_token
from cookie_http_seeder.senders import sender_tag


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("--url", required=True)
    parser.add_argument("--rules", type=Path, required=True,
                        help="explicit site response rules; never infer validity from HTTP 200")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--sender-tag", type=sender_tag, default="default")
    parser.add_argument("--endpoint", default="http://127.0.0.1:18765")
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--state-file", type=Path,
                        help="private writable consumer state, outside read-only snapshot volumes")
    parser.add_argument("--wait-seconds", type=float, default=0,
                        help="bounded wait for replacement cookies after a previous invalid result")
    args = parser.parse_args(argv)
    try:
        rules = ResponseRules.from_document(json.loads(args.rules.read_text(encoding="utf-8")))
        token = resolve_token(token=None, token_file=args.token_file or token_file(args.data_dir))
        client = ReceiverClient(args.endpoint, token, sender_tag=args.sender_tag)
        consumer = CookieConsumer(source=args.source, data_dir=args.data_dir, client=client,
                                  classify=rules, state_path=args.state_file)
        if args.wait_seconds and not consumer.wait_for_update(args.url, timeout=args.wait_seconds):
            print("paused: replacement snapshot was not received before the deadline")
            return 2
        opener = build_opener(ProxyHandler({}), NoRedirect())

        def send(url, headers):
            request = Request(url, headers={
                **headers, "User-Agent": "cookie-http-seeder-example/0.4"})
            try:
                response = opener.open(request, timeout=15)
            except HTTPError as error:
                response = error  # Includes redirects: inspect without sending to another URL.
            with response:
                raw = response.read(1024 * 1024 + 1)
                body = raw.decode("utf-8", errors="replace") if len(raw) <= 1024 * 1024 else ""
                return Response(response.status, body, dict(response.headers))

        outcome = consumer.request(args.url, send)
        print(f"source={args.source} sender_tag={args.sender_tag} "
              f"result={outcome.observation.result} reason={outcome.observation.reason_code} "
              f"feedback={outcome.feedback_status} paused={outcome.paused}")
        if outcome.paused:
            print("login in the matching browser, push a replacement, then verify again")
            return 2
        return 0 if outcome.observation.result == "valid" else 1
    except LoginRequired:
        print("paused: this snapshot was confirmed invalid; login and push a replacement")
        return 2
    except (OSError, ValueError, TypeError, ConsumerBusy, CredentialsUnavailable):
        print("request unavailable: check scoped snapshot, rules, connection and private state")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

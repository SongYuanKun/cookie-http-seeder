"""Authenticated receiver transport, bound to an explicit application state."""
from __future__ import annotations

import json
import re
import secrets
from http.server import BaseHTTPRequestHandler
from typing import Any

from . import __version__
from .receiver_state import ConflictError, ReceiverState
from .senders import sender_tag
from .sync_state import PreconditionRequired, SnapshotConflict

_MAX_BODY_BYTES = 512 * 1024
_EXTENSION_ORIGIN = re.compile(r"^chrome-extension://[a-p]{32}$")

def _json_object(pairs: list[tuple[str, Any]]) -> dict:
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate JSON field")
        obj[key] = value
    return obj


def _invalid_constant(_value: str) -> None:
    raise ValueError("non-finite JSON numbers are not allowed")


def build_handler(expected_token: str, *, state: ReceiverState, notify: bool = False) -> type[BaseHTTPRequestHandler]:
    root_state = state

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            pass  # Never log URLs, request bodies, credentials or reflected input.

        def _send(self, code: int, payload: dict) -> None:
            if payload.get("ok") is True:
                payload = {**payload, "sender_tag": getattr(self, "sender_tag", "default")}
            body = (json.dumps(payload, ensure_ascii=False) + "\n").encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _authorize(self) -> bool:
            origins = self.headers.get_all("Origin", [])
            if len(origins) > 1 or (origins and not _EXTENSION_ORIGIN.fullmatch(origins[0])):
                self._send(403, {"ok": False, "error": "origin_not_allowed"})
                return False
            auths = self.headers.get_all("Authorization", [])
            value = auths[0].removeprefix("Bearer ") if len(auths) == 1 else ""
            valid = (len(auths) == 1 and auths[0].startswith("Bearer ")
                     and secrets.compare_digest(value.encode(), expected_token.encode()))
            if not valid:
                self._send(401, {"ok": False, "error": "unauthorized"})
                return False
            tags = self.headers.get_all("X-Sender-Tag", [])
            try:
                if len(tags) > 1:
                    raise ValueError("duplicate sender tag")
                self.sender_tag = sender_tag(tags[0] if tags else "default")
            except ValueError:
                self._send(400, {"ok": False, "error": "invalid_sender_tag"})
                return False
            return True

        def handle_expect_100(self) -> bool:
            if not self._authorize():
                return False
            return super().handle_expect_100()

        def _read_json(self) -> object:
            lengths = self.headers.get_all("Content-Length", [])
            if self.headers.get("Transfer-Encoding") or len(lengths) != 1:
                raise ValueError("one Content-Length and no Transfer-Encoding required")
            if not re.fullmatch(r"[0-9]{1,9}", lengths[0]):
                raise ValueError("invalid content length")
            length = int(lengths[0])
            if not 0 < length <= _MAX_BODY_BYTES:
                raise ValueError("invalid content length")
            if self.headers.get_content_type() != "application/json":
                raise ValueError("Content-Type must be application/json")
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("truncated body")
            return json.loads(raw.decode("utf-8"), object_pairs_hook=_json_object,
                              parse_constant=_invalid_constant)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/healthz":
                self._send(200, {"status": "ok", "receiver_version": __version__})
            elif self._authorize():
                if self.path == "/v1/senders":
                    try:
                        self._send(200, root_state.sender_statuses())
                    except (OSError, ValueError, TypeError):
                        self._send(500, {"ok": False, "error": "unreadable_sender_state"})
                    return
                try:
                    state = root_state.for_sender(self.sender_tag)
                except (ValueError, OSError, TypeError):
                    self._send(400, {"ok": False, "error": "invalid_sender_or_storage"})
                    return
                if self.path == "/v1/sources":
                    self._send(200, state.document())
                elif self.path == "/v1/status":
                    self._send(200, state.status())
                elif self.path.startswith("/v2/sync/"):
                    try:
                        self._send(200, state.source_version(self.path.removeprefix("/v2/sync/")))
                    except (ValueError, OSError, TypeError):
                        self._send(400, {"ok": False, "error": "invalid_source_or_state"})
                else:
                    self._send(404, {"ok": False, "error": "not_found"})

        def _mutate(self) -> None:
            if not self._authorize():
                return
            try:
                state = root_state.for_sender(self.sender_tag)
                if self.command == "POST" and self.path == "/v1/cookies":
                    self._send(410, {"ok": False, "error": "upgrade_to_v2_structured_cookies"})
                    return
                if self.command == "POST" and self.path == "/v2/cookies":
                    result = state.ingest(self._read_json(), notify=notify)
                elif self.command == "POST" and self.path == "/v1/feedback":
                    result = state.report(self._read_json(), notify=notify)
                elif self.command == "PUT" and self.path == "/v1/sources":
                    body = self._read_json()
                    if not isinstance(body, dict) or set(body) != {"sources", "revision"}:
                        raise ValueError("sources and revision are required")
                    result = state.replace_sources(body["sources"], body["revision"])
                elif self.command == "DELETE" and self.path.startswith("/v2/cookies/"):
                    result = state.clear_and_pause(self.path.removeprefix("/v2/cookies/"),
                                                   self.headers.get("If-Match"))
                else:
                    self._send(404, {"ok": False, "error": "not_found"})
                    return
            except PreconditionRequired:
                self._send(428, {"ok": False, "error": "snapshot_precondition_required"})
                return
            except SnapshotConflict:
                self._send(409, {"ok": False, "error": "snapshot_conflict"})
                return
            except ConflictError:
                self._send(409, {"ok": False, "error": "configuration_changed"})
                return
            except TimeoutError:
                self._send(408, {"ok": False, "error": "request_timeout"})
                return
            except (ValueError, TypeError, UnicodeError, RecursionError):
                self._send(400, {"ok": False, "error": "invalid_payload_or_source"})
                return
            except OSError:
                self._send(500, {"ok": False, "error": "storage_failed"})
                return
            self._send(200, result)

        do_POST = _mutate
        do_PUT = _mutate
        do_DELETE = _mutate

    return Handler


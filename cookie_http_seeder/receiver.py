"""Authenticated loopback receiver, managed sources and complete v2 snapshots."""
from __future__ import annotations

import json
import os
import re
import secrets
import socket
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .cookies import normalize_sources
from .notify import notify_needed, notify_pushed
from .paths import atomic_write_text, ensure_data_dir, token_file, webhook_file
from .store import read_snapshot, save_snapshot
from .sync_state import PreconditionRequired, SnapshotConflict, SyncState

_MAX_BODY_BYTES = 512 * 1024
_TOKEN_RE = re.compile(r"^[A-Za-z0-9._-]{16,256}$")
_EXTENSION_ORIGIN = re.compile(r"^chrome-extension://[a-p]{32}$")


class ConflictError(ValueError):
    """Reload configuration before retrying."""


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class ReceiverState:
    def __init__(self, sources: dict, data_dir: Path, sources_path: Path | None = None):
        self.sources = normalize_sources(sources)
        self.data_dir = ensure_data_dir(data_dir)
        self.sources_path = sources_path or self.data_dir / "sources.json"
        self.lock = threading.RLock()
        self.sync = SyncState(self.data_dir)
        # Fresh on every boot and config write; avoids the content-hash ABA problem.
        self.revision = secrets.token_hex(16)
        self.notify_timer: threading.Timer | None = None
        self.pending_notify: set[str] = set()

    def document(self) -> dict[str, Any]:
        with self.lock:
            return {"ok": True, "schema_version": 1, "protocol_version": 2,
                    "sources": json.loads(json.dumps(self.sources)), "revision": self.revision,
                    "capabilities": ["conditional_snapshots", "validation_feedback"]}

    def check_revision(self, revision: object) -> None:
        if not isinstance(revision, str) or revision != self.revision:
            raise ConflictError("configuration changed; reload and approve sources")

    def replace_sources(self, raw: object, revision: object) -> dict[str, Any]:
        sources = normalize_sources(raw)
        with self.lock:
            self.check_revision(revision)
            # Invalidate old credentials before publishing a narrower/new policy.
            # Each file write is atomic; this is not a multi-file database transaction.
            for name, old in self.sources.items():
                if sources.get(name) != old:
                    save_snapshot([], source=name, spec=old, updated_at=_utc_now(),
                                  data_dir=self.data_dir)
            atomic_write_text(self.sources_path,
                              json.dumps({"schema_version": 1, "sources": sources}, indent=2)
                              + "\n", mode=0o600)
            self.sources = sources
            self.revision = secrets.token_hex(16)
            return self.document()

    def clear_and_pause(self, source: str, revision: object) -> dict[str, Any]:
        with self.lock:
            self.check_revision(revision)
            if source not in self.sources:
                raise ValueError("unknown source")
            save_snapshot([], source=source, spec=self.sources[source], updated_at=_utc_now(),
                          data_dir=self.data_dir)
            sources = self.document()["sources"]
            sources[source]["enabled"] = False
            result = self.replace_sources(sources, revision)
            return {**result, "source": source, "cleared": True, "paused": True}

    def ingest(self, payload: object, *, notify: bool = False) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("body must be a JSON object")
        if payload.get("schema_version") != 2 or payload.get("complete") is not True:
            raise ValueError("schema_version=2 and complete=true are required")
        if "cookie_header" in payload:
            raise ValueError("raw cookie_header is unsupported; send structured cookies")
        if set(payload) - {"schema_version", "complete", "source", "cookies",
                           "config_revision", "expected_version", "request_id"}:
            raise ValueError("unknown snapshot fields")
        source = payload.get("source")
        if not isinstance(source, str):
            raise ValueError("invalid source")
        with self.lock:
            self.check_revision(payload.get("config_revision"))
            spec = self.sources.get(source)
            if spec is None or not spec["enabled"]:
                raise ValueError("unknown or paused source")
            result = self.sync.accept(payload, spec)
            if notify and not result["unchanged"]:
                self.pending_notify.add(source)
                if self.notify_timer is not None:
                    self.notify_timer.cancel()
                self.notify_timer = threading.Timer(2.0, self.flush_notify)
                self.notify_timer.daemon = True
                self.notify_timer.start()
            return result

    def source_version(self, source: str) -> dict[str, Any]:
        with self.lock:
            if source not in self.sources:
                raise ValueError("unknown source")
            return {"ok": True, "source": source, "snapshot_version": self.sync.version(source),
                    "config_revision": self.revision, "enabled": self.sources[source]["enabled"]}

    def report(self, payload: object, *, notify: bool = False) -> dict[str, Any]:
        with self.lock:
            source = payload.get("source") if isinstance(payload, dict) else None
            if (not isinstance(source, str) or source not in self.sources
                    or not self.sources[source]["enabled"]):
                raise ValueError("unknown or paused source")
            result, should_notify = self.sync.feedback(payload, notify=notify)
            if should_notify:
                # Fixed structured reason only: never send crawler response bodies or tokens.
                reason = f"{source}: {payload['reason_code']}"

                def send() -> None:
                    try:
                        notify_needed(reason=reason,
                                      webhook_path=webhook_file(self.data_dir))
                    except Exception:  # noqa: BLE001
                        pass  # Notification cannot break the persisted report.
                worker = threading.Thread(target=send, daemon=True)
                worker.start()
            return result

    def flush_notify(self) -> None:
        with self.lock:
            sources = sorted(self.pending_notify)
            self.pending_notify.clear()
        if sources:
            status = notify_pushed(sources=sources, webhook_path=webhook_file(self.data_dir))
            print(f"[cookie-http-seeder] notify: {status.split(':', 1)[0]}", flush=True)

    def status(self) -> dict[str, Any]:
        with self.lock:
            sources = {}
            for name, spec in self.sources.items():
                try:
                    doc = read_snapshot(name, data_dir=self.data_dir)
                    if isinstance(doc, dict) and doc.get("schema_version") == 2:
                        entry = {"present": bool(doc.get("cookies")),
                                 "cookieCount": len(doc.get("cookies", [])),
                                 "updatedAt": doc.get("updatedAt"), "cleared": doc.get("cleared"),
                                 "format": "snapshot-v2"}
                    else:
                        entry = {"present": bool(doc), "format": "legacy" if doc else "missing"}
                except (OSError, ValueError, TypeError):
                    entry = {"present": False, "error": "unreadable_snapshot"}
                try:
                    sync = self.sync.status(name)
                except (OSError, ValueError, TypeError, OverflowError):
                    sync = {"validation": "unverified", "freshness": "unknown",
                            "syncError": "unreadable_sync_state"}
                sources[name] = {**entry, "enabled": spec["enabled"], **sync}
            return {"ok": True, "sources": sources, "config_revision": self.revision,
                    "observedAt": _utc_now()}


_STATE: ReceiverState | None = None


def configure(*, sources: dict, data_dir: Path, sources_path: Path | None = None) -> None:
    global _STATE
    _STATE = ReceiverState(sources, data_dir, sources_path)


def _state() -> ReceiverState:
    if _STATE is None:
        raise RuntimeError("configure the receiver first")
    return _STATE


def status_document() -> dict[str, Any]:
    return _state().status()


def ingest_payload(payload: object, *, token_ok: bool, notify: bool = False) -> dict[str, Any]:
    if not token_ok:
        raise PermissionError("unauthorized")
    return _state().ingest(payload, notify=notify)


def resolve_token(*, token: str | None, token_file: Path | None) -> str:
    if token:
        value = token.strip()
    elif token_file is not None and token_file.exists():
        value = token_file.read_text(encoding="utf-8").strip()
    else:
        value = os.environ.get("COOKIE_HTTP_SEEDER_TOKEN", "").strip()
    if not _TOKEN_RE.fullmatch(value):
        raise SystemExit("missing/invalid token (need 16-256 characters of [A-Za-z0-9._-])")
    return value


def default_token_file(data_dir: Path | None = None) -> Path:
    return token_file(data_dir)


def ensure_token_file(path: Path) -> str:
    ensure_data_dir(path.parent)
    if path.exists():
        return resolve_token(token=None, token_file=path)
    value = secrets.token_urlsafe(24)
    atomic_write_text(path, value + "\n", mode=0o600)
    return value


def _json_object(pairs: list[tuple[str, Any]]) -> dict:
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate JSON field")
        obj[key] = value
    return obj


def _invalid_constant(_value: str) -> None:
    raise ValueError("non-finite JSON numbers are not allowed")


def build_handler(expected_token: str, *, notify: bool = False) -> type[BaseHTTPRequestHandler]:
    state = _state()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            pass  # Never log URLs, request bodies, credentials or reflected input.

        def _send(self, code: int, payload: dict) -> None:
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
            return valid

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
                self._send(200, {"status": "ok"})
            elif self._authorize():
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


def serve(*, host: str, port: int, token: str, notify: bool = False) -> None:
    if host not in {"127.0.0.1", "::1", "localhost"}:
        print("[cookie-http-seeder] non-loopback bind: restrict access or use a TLS proxy.",
              flush=True)
    server_class = ThreadingHTTPServer
    if ":" in host:
        class IPv6Server(ThreadingHTTPServer):
            address_family = socket.AF_INET6
        server_class = IPv6Server
    server = server_class((host, port), build_handler(token, notify=notify))
    print(f"[cookie-http-seeder] listening on port {port}; protocol v2", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

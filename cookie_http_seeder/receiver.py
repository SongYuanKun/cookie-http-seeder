"""Cookie receiver for the Chrome extension (loopback or private Tailnet)."""

from __future__ import annotations

import errno
import json
import os
import re
import secrets
import socket
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .notify import notify_pushed
from .paths import atomic_write_text, ensure_data_dir, token_file, webhook_file
from .store import (
    cookie_path_for_source,
    default_data_dir,
    load_cookie_header,
    save_cookie_header,
)

_MAX_BODY_BYTES = 64 * 1024
_MAX_HEADER_CHARS = 48 * 1024
_TOKEN_RE = re.compile(r"^[A-Za-z0-9._-]{16,256}$")
_STATUS_LOCK = threading.Lock()
_LAST_PUSH: dict[str, dict[str, Any]] = {}
_NOTIFY_LOCK = threading.Lock()
_PENDING_NOTIFY_SOURCES: set[str] = set()
_NOTIFY_TIMER: threading.Timer | None = None
_ALLOWED_SOURCES: frozenset[str] = frozenset()
_DATA_DIR: Path = default_data_dir()
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def normalize_bind_host(host: str) -> str:
    """Strip URL brackets so socket.bind / inet_pton see a bare address."""
    value = host.strip()
    if value.startswith("[") and value.endswith("]"):
        return value[1:-1]
    return value


def is_ipv6_literal(host: str) -> bool:
    candidate = normalize_bind_host(host)
    if candidate in {"::", "::1"}:
        return True
    try:
        socket.inet_pton(socket.AF_INET6, candidate)
    except OSError:
        return False
    return True


def format_listen_url(host: str, port: int) -> str:
    display = normalize_bind_host(host)
    if is_ipv6_literal(display):
        return f"http://[{display}]:{port}"
    return f"http://{display}:{port}"


def http_server_class_for_host(host: str) -> type[ThreadingHTTPServer]:
    if not is_ipv6_literal(host):
        return ThreadingHTTPServer

    class IPv6ThreadingHTTPServer(ThreadingHTTPServer):
        address_family = socket.AF_INET6

    return IPv6ThreadingHTTPServer


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def configure(*, sources: dict[str, dict[str, object]], data_dir: Path) -> None:
    global _ALLOWED_SOURCES, _DATA_DIR
    _ALLOWED_SOURCES = frozenset(sources)
    _DATA_DIR = ensure_data_dir(data_dir)


def resolve_token(*, token: str | None, token_file: Path | None) -> str:
    if token:
        value = token.strip()
    elif token_file is not None and token_file.exists():
        value = token_file.read_text(encoding="utf-8").strip()
    else:
        env = os.environ.get("COOKIE_HTTP_SEEDER_TOKEN", "").strip()
        if env:
            value = env
        else:
            raise SystemExit(
                "missing token: set COOKIE_HTTP_SEEDER_TOKEN or pass --token / --token-file"
            )
    if not _TOKEN_RE.fullmatch(value):
        raise SystemExit("invalid token (need 16–256 chars of [A-Za-z0-9._-])")
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


def cookies_to_header(cookies: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for item in cookies:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        value = item.get("value")
        if not isinstance(name, str) or not name or not isinstance(value, str):
            continue
        if name in seen:
            continue
        seen.add(name)
        parts.append(f"{name}={value}")
    return "; ".join(parts)


def ingest_payload(
    payload: object,
    *,
    token_ok: bool,
    notify: bool = False,
) -> dict[str, Any]:
    if not token_ok:
        raise PermissionError("unauthorized")
    if not isinstance(payload, dict):
        raise ValueError("body must be a JSON object")
    source = payload.get("source")
    if source not in _ALLOWED_SOURCES:
        raise ValueError(f"source must be one of: {', '.join(sorted(_ALLOWED_SOURCES))}")

    header = payload.get("cookie_header")
    if header is None and isinstance(payload.get("cookies"), list):
        header = cookies_to_header(payload["cookies"])
    if not isinstance(header, str) or not header.strip():
        raise ValueError("cookie_header is required")
    header = header.strip()
    if len(header) > _MAX_HEADER_CHARS:
        raise ValueError("cookie_header too large")

    updated_at = _utc_now()
    path = save_cookie_header(
        header, source=str(source), updated_at=updated_at, data_dir=_DATA_DIR
    )
    with _STATUS_LOCK:
        _LAST_PUSH[str(source)] = {
            "updatedAt": updated_at,
            "bytes": len(header.encode("utf-8")),
            "path": str(path),
        }
    if notify:
        _schedule_notify(str(source))
    return {"ok": True, "source": source, "updatedAt": updated_at}


def _schedule_notify(source: str) -> None:
    global _NOTIFY_TIMER
    with _NOTIFY_LOCK:
        _PENDING_NOTIFY_SOURCES.add(source)
        if _NOTIFY_TIMER is not None:
            _NOTIFY_TIMER.cancel()
        timer = threading.Timer(2.0, _flush_notify)
        timer.daemon = True
        _NOTIFY_TIMER = timer
        timer.start()


def _flush_notify() -> None:
    with _NOTIFY_LOCK:
        sources = sorted(_PENDING_NOTIFY_SOURCES)
        _PENDING_NOTIFY_SOURCES.clear()
    if not sources:
        return
    try:
        status = notify_pushed(sources=sources)
    except Exception as error:  # noqa: BLE001
        status = f"failed: {type(error).__name__}"
    print(f"[cookie-http-seeder] notify: {status}", flush=True)


def status_document() -> dict[str, Any]:
    with _STATUS_LOCK:
        pushes = {key: dict(value) for key, value in _LAST_PUSH.items()}
    sources: dict[str, Any] = {}
    for source in sorted(_ALLOWED_SOURCES):
        path = cookie_path_for_source(source, data_dir=_DATA_DIR)
        header = load_cookie_header(source=source, data_dir=_DATA_DIR)
        entry = {
            "path": str(path),
            "present": bool(header),
            "bytes": len(header.encode("utf-8")) if header else 0,
        }
        if source in pushes:
            entry["lastPush"] = pushes[source]
        sources[source] = entry
    hook = webhook_file(_DATA_DIR)
    return {
        "ok": True,
        "dataDir": str(_DATA_DIR),
        "tokenFile": str(token_file(_DATA_DIR)),
        "webhookFile": str(hook),
        "webhookPresent": hook.is_file(),
        "sources": sources,
        "observedAt": _utc_now(),
    }


def build_handler(expected_token: str, *, notify: bool = False) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        notify_on_push = notify

        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            import sys

            print(
                f"[cookie-http-seeder] {self.address_string()} {args[0] if args else format}",
                file=sys.stderr,
            )

        def _auth_ok(self) -> bool:
            auth = self.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                return False
            provided = auth.removeprefix("Bearer ").strip()
            return bool(provided) and secrets.compare_digest(provided, expected_token)

        def _read_json(self) -> object:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0 or length > _MAX_BODY_BYTES:
                raise ValueError("invalid content length")
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("truncated body")
            return json.loads(raw.decode("utf-8"))

        def _send(self, code: int, payload: dict[str, Any]) -> None:
            body = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode(
                "utf-8"
            )
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/healthz":
                self._send(200, {"status": "ok"})
                return
            if path == "/v1/status":
                if not self._auth_ok():
                    self._send(401, {"ok": False, "error": "unauthorized"})
                    return
                self._send(200, status_document())
                return
            self._send(404, {"ok": False, "error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path != "/v1/cookies":
                self._send(404, {"ok": False, "error": "not_found"})
                return
            try:
                payload = self._read_json()
                result = ingest_payload(
                    payload,
                    token_ok=self._auth_ok(),
                    notify=self.notify_on_push,
                )
            except PermissionError:
                self._send(401, {"ok": False, "error": "unauthorized"})
                return
            except (TypeError, ValueError, UnicodeError, json.JSONDecodeError) as error:
                self._send(400, {"ok": False, "error": str(error)})
                return
            except OSError as error:
                self._send(500, {"ok": False, "error": f"write_failed: {error}"})
                return
            self._send(200, result)

    return Handler


def serve(*, host: str, port: int, token: str, notify: bool = False) -> None:
    bind_host = normalize_bind_host(host)
    listen_url = format_listen_url(bind_host, port)
    if bind_host not in _LOOPBACK_HOSTS:
        print(
            f"[cookie-http-seeder] warning: binding {bind_host}; keep it private.",
            flush=True,
        )
    server_cls = http_server_class_for_host(bind_host)
    # Tailscale / interface addresses may appear after the service starts.
    delay = 1.0
    while True:
        try:
            server = server_cls((bind_host, port), build_handler(token, notify=notify))
            break
        except OSError as error:
            if error.errno not in {errno.EADDRNOTAVAIL, errno.EADDRINUSE}:
                raise
            print(
                f"[cookie-http-seeder] bind {listen_url} failed ({error}); "
                f"retry in {delay:.0f}s",
                flush=True,
            )
            time.sleep(delay)
            delay = min(delay * 2, 30.0)
    server.timeout = 30
    try:
        server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    except OSError:
        pass
    print(
        f"[cookie-http-seeder] listening {listen_url} "
        f"(POST /v1/cookies, GET /v1/status, GET /healthz, notify={notify})",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[cookie-http-seeder] stopped", flush=True)
    finally:
        server.server_close()

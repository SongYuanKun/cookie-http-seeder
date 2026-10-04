"""Compatibility entry points; explicit application state is preferred."""
from __future__ import annotations

import os
import re
import secrets
from http.server import ThreadingHTTPServer as ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import receiver_http, receiver_service
from .paths import atomic_write_text, ensure_data_dir, token_file
from .receiver_service import (
    format_listen_url as format_listen_url,
)
from .receiver_service import (
    http_server_class_for_host as http_server_class_for_host,
)
from .receiver_service import (
    is_ipv6_literal as is_ipv6_literal,
)
from .receiver_service import (
    normalize_bind_host as normalize_bind_host,
)
from .receiver_state import ConflictError as ConflictError
from .receiver_state import ReceiverState as ReceiverState

_TOKEN_RE = re.compile(r"^[A-Za-z0-9._-]{16,256}$")

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


def build_handler(expected_token: str, *, notify: bool = False, state=None):
    return receiver_http.build_handler(expected_token, notify=notify, state=state or _state())


def serve(*, host: str, port: int, token: str, notify: bool = False, state=None) -> None:
    receiver_service.serve(host=host, port=port, token=token, notify=notify,
                           state=state or _state(), runner=_serve)


def _serve(*, host: str, port: int, token: str, notify: bool = False, state=None) -> None:
    receiver_service._serve(host=host, port=port, token=token, notify=notify,
                            state=state or _state(), server_factory=http_server_class_for_host)

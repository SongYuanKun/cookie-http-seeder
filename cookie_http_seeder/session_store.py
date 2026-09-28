"""Opaque, versioned session envelopes; caller holds the receiver state lock."""
from __future__ import annotations

import json
import re
import secrets
from pathlib import Path

from .paths import atomic_write_text
from .session_bundle import validate_envelope

_SOURCE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_VERSION = re.compile(r"^[a-f0-9]{32}$")
_REQUEST = re.compile(r"^[0-9a-fA-F-]{36}$")


class SessionConflict(ValueError):
    """A newer upload has already replaced the expected version."""


def bundle_path(directory: Path, source: str) -> Path:
    if not isinstance(source, str) or not _SOURCE.fullmatch(source):
        raise ValueError("invalid source")
    return directory / f"{source}-session.json"


def read_bundle(directory: Path, source: str) -> dict | None:
    path = bundle_path(directory, source)
    if path.is_symlink():
        raise ValueError("session bundle cannot be a symlink")
    if not path.exists():
        return None
    if path.stat().st_size > 768 * 1024:
        raise ValueError("stored session bundle is too large")
    doc = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(doc, dict) or doc.get("source") != source
            or not isinstance(doc.get("bundle_version"), str)
            or not _VERSION.fullmatch(doc["bundle_version"])
            or not isinstance(doc.get("updated_at"), str)
            or not isinstance(doc.get("peer"), dict)):
        raise ValueError("invalid stored session bundle")
    validate_envelope(doc.get("envelope"))
    return doc


def metadata(doc: dict | None, source: str, revision: str, enabled: bool) -> dict:
    return {"ok": True, "source": source, "bundle_version": doc["bundle_version"] if doc else None,
            "config_revision": revision, "enabled": enabled,
            "updated_at": doc["updated_at"] if doc else None,
            "peer": doc["peer"] if doc else None}


def write_bundle(directory: Path, *, source: str, envelope: dict, revision: str,
                 expected_version: str | None, request_id: str, peer_ip: str,
                 updated_at: str) -> dict:
    validate_envelope(envelope)
    if (envelope["source"] != source or not isinstance(request_id, str)
            or not _REQUEST.fullmatch(request_id)):
        raise ValueError("invalid bundle source or request ID")
    if expected_version is not None and (not isinstance(expected_version, str)
                                         or not _VERSION.fullmatch(expected_version)):
        raise ValueError("invalid expected bundle version")
    old = read_bundle(directory, source)
    if old and old.get("request_id") == request_id:
        if old["envelope"] == envelope:
            return metadata(old, source, revision, True)
        raise SessionConflict("request ID reused with different contents")
    if (old["bundle_version"] if old else None) != expected_version:
        raise SessionConflict("bundle changed")
    doc = {"source": source, "envelope": envelope, "bundle_version": secrets.token_hex(16),
           "request_id": request_id, "updated_at": updated_at,
           "peer": {"source": "receiver_peer", "ip": peer_ip}}
    atomic_write_text(bundle_path(directory, source),
                      json.dumps(doc, separators=(",", ":")), mode=0o600)
    return metadata(doc, source, revision, True)


def remove_bundle(directory: Path, source: str) -> None:
    path = bundle_path(directory, source)
    if path.is_symlink():
        raise ValueError("session bundle cannot be a symlink")
    path.unlink(missing_ok=True)

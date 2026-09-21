"""Structured snapshots; raw-header files remain read-only legacy compatible."""
from __future__ import annotations

import json
import os
import secrets
from importlib import resources
from pathlib import Path
from typing import Any

from .cookies import SOURCE_NAME, cookies_to_header, normalize_cookies, normalize_sources
from .paths import (
    ENV_SOURCES,
    atomic_write_text,
    cookie_file,
    default_data_dir,
    sources_override_file,
)
from .senders import sender_directory

_PACKAGE_ROOT = Path(__file__).resolve().parent
_REPO_ROOT = _PACKAGE_ROOT.parent
__all__ = [
    "cookie_path_for_source", "default_data_dir", "load_cookie_header", "load_sources",
    "save_cookie_header", "save_snapshot", "read_snapshot",
]


def _read_default_sources_text(*, data_dir: Path | None = None) -> str:
    env = os.environ.get(ENV_SOURCES, "").strip()
    if env:
        return Path(env).expanduser().read_text(encoding="utf-8")
    override = sources_override_file(data_dir)
    if override.is_file():
        return override.read_text(encoding="utf-8")
    for candidate in (_REPO_ROOT / "examples/sources.json", Path.cwd() / "examples/sources.json"):
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    return resources.files("cookie_http_seeder.resources").joinpath("sources.json").read_text(
        encoding="utf-8"
    )


def load_sources(
    path: Path | None = None, *, data_dir: Path | None = None
) -> dict[str, dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8") if path is not None
                     else _read_default_sources_text(data_dir=data_dir))
    if (not isinstance(raw, dict) or "sources" not in raw
            or set(raw) - {"schema_version", "sources"} or raw.get("schema_version", 1) != 1):
        raise ValueError("invalid source configuration schema")
    return normalize_sources(raw["sources"])


def cookie_path_for_source(source: str, *, data_dir: Path | None = None) -> Path:
    if not isinstance(source, str) or not SOURCE_NAME.fullmatch(source):
        raise ValueError("invalid source name")
    return cookie_file(source, data_dir=data_dir)


def read_snapshot(source: str, *, data_dir: Path | None = None,
                  sender_tag: str = "default") -> Any:
    data_dir = sender_directory(data_dir or default_data_dir(), sender_tag)
    path = cookie_path_for_source(source, data_dir=data_dir)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def save_snapshot(
    cookies: object, *, source: str, spec: dict[str, Any], updated_at: str, data_dir: Path,
    request_id: str | None = None,
) -> dict[str, Any]:
    jar = normalize_cookies(cookies, spec["domains"])
    payload = {
        "schema_version": 2, "source": source, "domains": spec["domains"],
        "target_url": spec.get("target_url", ""), "cookies": jar,
        "updatedAt": updated_at, "cleared": not jar,
        "snapshot_version": secrets.token_hex(16), "request_id": request_id,
    }
    if payload["target_url"]:
        payload["cookie_header"] = cookies_to_header(
            jar, payload["target_url"], domains=spec["domains"]
        )
    elif not jar:
        payload["cookie_header"] = ""
    atomic_write_text(
        cookie_path_for_source(source, data_dir=data_dir),
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", mode=0o600,
    )
    return payload


def load_cookie_header(
    path: Path | None = None, *, source: str | None = None, data_dir: Path | None = None,
    url: str | None = None, sender_tag: str = "default",
) -> str | None:
    data_dir = sender_directory(data_dir or default_data_dir(), sender_tag)
    if sender_tag != "default" and (path is not None or source is None):
        raise ValueError("select a sender using source and data_dir, not an explicit file path")
    if source is not None:
        path = path or cookie_path_for_source(source, data_dir=data_dir)
    payload = json.loads(path.read_text(encoding="utf-8")) if path and path.is_file() else None
    if isinstance(payload, dict) and "schema_version" in payload:
        if payload["schema_version"] != 2:
            raise ValueError("unsupported snapshot schema_version")
        # A durable empty snapshot overrides legacy environment variables.
        if payload.get("cleared") is True:
            return None
        target = url or payload.get("target_url")
        if not target:
            raise ValueError("a target URL is required for this snapshot")
        return cookies_to_header(payload["cookies"], target, domains=payload["domains"]) or None
    if source is not None and sender_tag == "default":
        key = f"COOKIE_HTTP_SEEDER_{source.upper().replace('-', '_')}_HEADER"
        if os.environ.get(key, "").strip():
            payload = os.environ[key].strip()
    if payload is None:
        return None
    if url is not None:
        raise ValueError("legacy header has no URL scope; re-seed using extension 0.2+")
    if isinstance(payload, str):
        return payload.strip() or None
    if isinstance(payload, dict):
        header = payload.get("cookie_header") or payload.get("Cookie") or payload.get("cookie")
        return (header.strip() or None) if isinstance(header, str) else None
    raise ValueError("invalid legacy cookie file")


def save_cookie_header(
    cookie_header: str, path: Path | None = None, *, source: str | None = None,
    updated_at: str | None = None, data_dir: Path | None = None,
) -> Path:
    """Legacy local writer. Not accepted by the network ingestion endpoint."""
    if source is not None:
        path = path or cookie_path_for_source(source, data_dir=data_dir)
    if path is None:
        raise ValueError("path or source is required")
    payload = {"cookie_header": cookie_header.strip()}
    if updated_at:
        payload["updatedAt"] = updated_at
    atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n", mode=0o600)
    return path


def load_request_credentials(*, source: str, url: str, data_dir: Path | None = None,
                             sender_tag: str = "default") -> dict:
    """Read header and version from ONE atomic snapshot, for accurate feedback.

    The returned cookie_header is a credential. Do not log this dictionary.
    """
    doc = read_snapshot(source, data_dir=data_dir, sender_tag=sender_tag)
    if (not isinstance(doc, dict) or doc.get("schema_version") != 2
            or not doc.get("snapshot_version")):
        raise ValueError("re-seed with extension 0.3+ before reporting feedback")
    return {"cookie_header": cookies_to_header(doc["cookies"], url, domains=doc["domains"]),
            "snapshot_version": doc["snapshot_version"]}

from __future__ import annotations

import json
import os
import re
from importlib import resources
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent
_REPO_ROOT = _PACKAGE_ROOT.parent
_SOURCE_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def default_data_dir() -> Path:
    """Cookie/token directory: env override, else ``./data`` under the process CWD."""
    env = os.environ.get("COOKIE_HTTP_SEEDER_DATA", "").strip()
    return Path(env) if env else Path.cwd() / "data"


def _read_default_sources_text() -> str:
    env = os.environ.get("COOKIE_HTTP_SEEDER_SOURCES", "").strip()
    if env:
        return Path(env).read_text(encoding="utf-8")

    # Prefer editable/git checkout examples so local edits take effect
    for candidate in (
        _REPO_ROOT / "examples" / "sources.json",
        Path.cwd() / "examples" / "sources.json",
    ):
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")

    return (
        resources.files("cookie_http_seeder.resources")
        .joinpath("sources.json")
        .read_text(encoding="utf-8")
    )


def load_sources(path: Path | None = None) -> dict[str, dict[str, object]]:
    if path is not None:
        raw_text = path.read_text(encoding="utf-8")
    else:
        raw_text = _read_default_sources_text()
    raw = json.loads(raw_text)
    if not isinstance(raw, dict) or not isinstance(raw.get("sources"), dict):
        raise ValueError("sources file must contain a sources object")
    sources: dict[str, dict[str, object]] = {}
    for name, spec in raw["sources"].items():
        if not _SOURCE_NAME.fullmatch(name):
            raise ValueError(f"invalid source name: {name}")
        if not isinstance(spec, dict):
            raise ValueError(f"invalid source spec: {name}")
        domains = spec.get("domains")
        if not isinstance(domains, list) or not domains:
            raise ValueError(f"source {name} needs domains")
        if any(not isinstance(item, str) or not item for item in domains):
            raise ValueError(f"source {name} has invalid domains")
        sources[name] = {"domains": list(domains)}
    if not sources:
        raise ValueError("sources is empty")
    return sources


def cookie_path_for_source(source: str, *, data_dir: Path | None = None) -> Path:
    if not _SOURCE_NAME.fullmatch(source):
        raise ValueError(f"unsupported cookie source: {source}")
    root = data_dir or default_data_dir()
    return root / f"{source}-cookies.json"


def load_cookie_header(
    path: Path | None = None, *, source: str | None = None, data_dir: Path | None = None
) -> str | None:
    if source is not None:
        env_key = f"COOKIE_HTTP_SEEDER_{source.upper().replace('-', '_')}_HEADER"
        env_value = os.environ.get(env_key, "").strip()
        if env_value:
            return env_value
        path = path or cookie_path_for_source(source, data_dir=data_dir)
    if path is None:
        return None
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, str):
        return payload.strip() or None
    if isinstance(payload, dict):
        header = payload.get("cookie_header") or payload.get("Cookie") or payload.get("cookie")
        if isinstance(header, str) and header.strip():
            return header.strip()
    return None


def save_cookie_header(
    cookie_header: str,
    path: Path | None = None,
    *,
    source: str | None = None,
    updated_at: str | None = None,
    data_dir: Path | None = None,
) -> Path:
    if source is not None:
        path = path or cookie_path_for_source(source, data_dir=data_dir)
    if path is None:
        raise ValueError("path or source is required")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, str] = {"cookie_header": cookie_header.strip()}
    if updated_at:
        payload["updatedAt"] = updated_at
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path

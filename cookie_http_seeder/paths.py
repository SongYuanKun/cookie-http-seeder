"""Resolve durable data paths for local, systemd, and container deploys."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

APP_NAME = "cookie-http-seeder"
ENV_DATA = "COOKIE_HTTP_SEEDER_DATA"
ENV_SOURCES = "COOKIE_HTTP_SEEDER_SOURCES"
ENV_TOKEN = "COOKIE_HTTP_SEEDER_TOKEN"
ENV_TOKEN_FILE = "COOKIE_HTTP_SEEDER_TOKEN_FILE"
ENV_WEBHOOK_FILE = "COOKIE_HTTP_SEEDER_WEBHOOK_FILE"
ENV_HOST = "COOKIE_HTTP_SEEDER_HOST"
ENV_PORT = "COOKIE_HTTP_SEEDER_PORT"
ENV_NO_NOTIFY = "COOKIE_HTTP_SEEDER_NO_NOTIFY"


def xdg_data_home() -> Path:
    raw = os.environ.get("XDG_DATA_HOME", "").strip()
    if raw:
        return Path(raw)
    return Path.home() / ".local" / "share"


def default_data_dir() -> Path:
    """Pick the cookie/token directory.

    Precedence:
    1. ``COOKIE_HTTP_SEEDER_DATA``
    2. ``./data`` when it already exists (project checkout / compose mount)
    3. ``$XDG_DATA_HOME/cookie-http-seeder`` (or ``~/.local/share/...``)
    """
    env = os.environ.get(ENV_DATA, "").strip()
    if env:
        return Path(env).expanduser()

    cwd_data = Path.cwd() / "data"
    if cwd_data.is_dir():
        return cwd_data

    return xdg_data_home() / APP_NAME


def ensure_data_dir(data_dir: Path | None = None) -> Path:
    root = data_dir or default_data_dir()
    root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    return root


def token_file(data_dir: Path | None = None) -> Path:
    env = os.environ.get(ENV_TOKEN_FILE, "").strip()
    if env:
        return Path(env).expanduser()
    return (data_dir or default_data_dir()) / "cookie-receiver.token"


def webhook_file(data_dir: Path | None = None) -> Path:
    env = os.environ.get(ENV_WEBHOOK_FILE, "").strip()
    if env:
        return Path(env).expanduser()
    return (data_dir or default_data_dir()) / "feishu-webhook"


def sources_override_file(data_dir: Path | None = None) -> Path:
    """Optional per-deploy sources file living next to cookie files."""
    return (data_dir or default_data_dir()) / "sources.json"


def cookie_file(source: str, *, data_dir: Path | None = None) -> Path:
    return (data_dir or default_data_dir()) / f"{source}-cookies.json"


def atomic_write_text(path: Path, text: str, *, mode: int = 0o600) -> None:
    """Write via temp file + replace so crawlers never read a partial JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(tmp_path, mode)
        except OSError:
            pass
        os.replace(tmp_path, path)
        try:
            os.chmod(path, mode)
        except OSError:
            pass
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def env_flag(name: str) -> bool | None:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return None
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise SystemExit(f"invalid boolean for {name}: {raw!r}")


def env_host(default: str = "127.0.0.1") -> str:
    return os.environ.get(ENV_HOST, "").strip() or default


def env_port(default: int = 18765) -> int:
    raw = os.environ.get(ENV_PORT, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise SystemExit(f"invalid {ENV_PORT}: {raw!r}") from error
    if not (1 <= value <= 65535):
        raise SystemExit(f"invalid {ENV_PORT}: {value}")
    return value

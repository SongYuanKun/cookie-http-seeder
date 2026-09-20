"""Credential-free checks. Never create tokens or modify source configuration."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .client import ClientError, ReceiverClient
from .paths import token_file
from .receiver import resolve_token
from .store import load_sources
from .sync_state import SyncState


def diagnose(data_dir: Path, *, endpoint: str = "http://127.0.0.1:18765",
             sources_path: Path | None = None, token_path: Path | None = None,
             local_only: bool = False) -> dict:
    checks: list[dict] = []

    def add(name: str, status: str, code: str, action: str = "") -> None:
        checks.append({"check": name, "status": status, "code": code, "action": action})

    if not data_dir.is_dir():
        add("data_dir", "error", "missing_directory",
            "Create the data directory and check --data-dir.")
    else:
        try:
            fd, name = tempfile.mkstemp(prefix=".doctor-", dir=data_dir)
            try:
                os.close(fd)
            finally:
                Path(name).unlink()
            add("data_dir", "ok", "writable")
        except OSError:
            add("data_dir", "error", "not_writable",
                "Check the process user and directory permissions.")
    sources = None
    try:
        sources = load_sources(sources_path, data_dir=data_dir)
        add("sources", "ok", "valid_configuration")
    except (OSError, ValueError, TypeError):
        add("sources", "error", "invalid_configuration",
            "Fix the sources JSON or configured file path.")
    token = None
    try:
        path = token_path or token_file(data_dir)
        token = resolve_token(token=os.environ.get("COOKIE_HTTP_SEEDER_TOKEN"), token_file=path)
        add("token", "ok", "configured")
        if os.name == "posix" and path.is_file() and path.stat().st_mode & 0o077:
            add("token_permissions", "warning", "broad_permissions",
                "Use chmod 600 on the token file.")
    except (OSError, SystemExit, ValueError):
        add("token", "error", "missing_or_invalid_token",
            "Run init-token locally or check the token file.")
    snapshots = {}
    if sources is not None and data_dir.is_dir():
        sync = SyncState(data_dir)
        for source in sources:
            try:
                snapshots[source] = sync.status(source)
                issue = snapshots[source]["freshness"] in {"missing", "stale", "unknown"}
                add(f"snapshot:{source}", "warning" if issue else "ok",
                    snapshots[source]["freshness"],
                    "Authorize and push this source in the extension." if issue else "")
            except (OSError, ValueError, TypeError, OverflowError):
                add(f"snapshot:{source}", "error", "unreadable_state",
                    "Inspect the local snapshot/metadata files privately.")
    if not local_only and token is not None:
        try:
            client = ReceiverClient(endpoint, token)
            remote = client.request("/v1/sources")
            client.request("/v1/status")
            add("receiver", "ok", "authenticated")
            if "conditional_snapshots" not in remote.get("capabilities", []):
                add("protocol", "error", "upgrade_required",
                    "Upgrade the receiver and extension together.")
            if sources is not None:
                matched = remote.get("sources") == sources
                add("configuration_match", "ok" if matched else "warning",
                    "match" if matched else "mismatch",
                    "Use the same data directory for CLI and receiver." if not matched else "")
        except ClientError as error:
            add("receiver", "error", error.code,
                "Check receiver, tunnel, TLS and token; do not paste credentials into logs.")
        except ValueError:
            add("receiver", "error", "invalid_endpoint",
                "Use an HTTPS origin or http://127.0.0.1:18765.")
    return {"ok": all(c["status"] != "error" for c in checks), "checks": checks,
            "snapshots": snapshots, "localOnly": local_only}

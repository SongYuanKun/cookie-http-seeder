"""Credential-free checks. Never create tokens or modify source configuration."""
from __future__ import annotations

import os
import re
import sys
import tempfile
import time
from datetime import datetime
from importlib import metadata
from pathlib import Path

from . import __version__
from .client import ClientError, ReceiverClient
from .client_health import ERRORS, PHASES, ClientHealthStore
from .incidents import KINDS, REASONS, STATES, IncidentStore
from .paths import token_file
from .receiver import resolve_token
from .senders import sender_directory
from .store import load_sources
from .sync_state import SyncState, utc_at

CAPABILITIES = {"conditional_snapshots", "validation_feedback", "sender_tags", "source_thresholds",
                "sender_status_summary", "stale_notifications", "client_health", "source_incidents"}
NEXT_STEPS = {"configure_data_directory", "fix_permissions", "configure_sources", "configure_token",
              "upgrade_receiver", "align_configuration", "check_connection", "install_package",
              "use_installed_package", "check_browser", "push_snapshot", "relogin",
              "validate_snapshot", "inspect_metadata"}


def _known(value, choices):
    return isinstance(value, str) and value in choices


def _safe_version(value):
    return value if (isinstance(value, str) and len(value) <= 15
                     and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value)) else None


def _installed_version():
    # Enumerate actual dist-info installations: metadata.version() can pick up
    # checkout egg-info before the environment's installed wheel or editable metadata.
    versions = set()
    try:
        for distribution in metadata.distributions():
            name = re.sub(r"[-_.]+", "-", distribution.metadata.get("Name", "").lower())
            if name != "cookie-http-seeder":
                continue
            installed = any(Path(str(file)).name == "METADATA" and
                            Path(str(file)).parent.name.endswith(".dist-info")
                            for file in distribution.files or ())
            version = _safe_version(distribution.version)
            if installed and version:
                versions.add(version)
    except (OSError, ValueError, TypeError):
        return None
    return versions.pop() if len(versions) == 1 else None


def _safe_time(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value,
    ):
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value
    except ValueError:
        return None


def _safe_health(raw, sources):
    raw = raw if isinstance(raw, dict) else {}
    state = raw.get("state")
    state = state if _known(state, {"not_reported", "recent", "overdue", "unknown"}) else "unknown"
    result = {"ok": state != "unknown" and raw.get("ok", True) is True,
              "state": state, "sources": {}}
    if state == "unknown":
        result["error"] = "unreadable_client_health"
    result["receivedAt"] = _safe_time(raw.get("receivedAt"))
    age = raw.get("ageSeconds")
    result["ageSeconds"] = age if type(age) in {int, float} and 0 <= age < 1e12 else None
    result["client_version"] = _safe_version(raw.get("client_version"))
    interval = raw.get("interval_minutes")
    result["interval_minutes"] = (interval if type(interval) is int and
                                  15 <= interval <= 10080 else None)
    entries = raw.get("sources")
    for name, entry in (entries.items() if isinstance(entries, dict) else ()):
        if (name not in sources or not isinstance(entry, dict)
                or type(entry.get("approved")) is not bool
                or type(entry.get("permission_granted")) is not bool
                or not _known(entry.get("sync_phase"), PHASES)
                or not _known(entry.get("error_code"), ERRORS)):
            continue
        result["sources"][name] = {key: entry[key] for key in (
            "approved", "permission_granted", "sync_phase", "error_code")}
        result["sources"][name]["last_success_at"] = _safe_time(entry.get("last_success_at"))
    return result


def _safe_incidents(raw, sources):
    result = {"ok": isinstance(raw, dict) and raw.get("ok") is True, "active": [], "history": []}
    if not result["ok"]:
        return {**result, "error": "unreadable_incidents"}
    for key, limit in (("active", 300), ("history", 100)):
        entries = raw.get(key)
        for entry in (entries[:limit] if isinstance(entries, list) else ()):
            if (not isinstance(entry, dict) or not _known(entry.get("source"), sources)
                    or not isinstance(entry.get("incident_id"), str)
                    or not re.fullmatch(r"[a-f0-9]{32}", entry["incident_id"])
                    or not _known(entry.get("kind"), KINDS)
                    or not _known(entry.get("status"), STATES)):
                continue
            version = entry.get("snapshot_version")
            result[key].append({**{field: entry[field] for field in (
                "source", "incident_id", "kind", "status")},
                "snapshot_version": version if isinstance(version, str) and
                re.fullmatch(r"[a-f0-9]{32}", version) else None,
                "reason_code": entry.get("reason_code") if _known(entry.get("reason_code"), REASONS)
                else None, "acknowledged": entry.get("acknowledged") is True,
                **{field: _safe_time(entry.get(field)) for field in (
                    "opened_at", "updated_at", "snoozed_until")}})
    return result


def diagnose(data_dir: Path, *, endpoint: str = "http://127.0.0.1:18765",
             sources_path: Path | None = None, token_path: Path | None = None,
             local_only: bool = False, sender_tag: str = "default") -> dict:
    root_dir = data_dir
    data_dir = sender_directory(root_dir, sender_tag)
    if sender_tag != "default":
        # No fallback to global --sources/environment/example configuration.
        sources_path = data_dir / "sources.json"
    checks: list[dict] = []

    def add(name: str, status: str, code: str, action: str = "", next_step: str = "") -> None:
        checks.append({"check": name, "status": status, "code": code, "action": action,
                       "next_step": next_step if next_step in NEXT_STEPS else ""})

    installed_version = _installed_version()
    add("runtime_version", "ok", "running")
    if installed_version is None:
        add("installed_version", "warning", "not_installed",
            "Install the built wheel in the intended environment.", "install_package")
    elif installed_version != __version__:
        add("installed_version", "warning", "runtime_mismatch",
            "Run the intended installed environment outside the checkout.", "use_installed_package")
    else:
        add("installed_version", "ok", "match")

    if not data_dir.is_dir():
        add("data_dir", "error", "missing_directory",
            "Create the data directory and check --data-dir.", "configure_data_directory")
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
                "Check the process user and directory permissions.", "fix_permissions")
    receiver_version = None
    sources = None
    try:
        sources = load_sources(sources_path, data_dir=data_dir)
        add("sources", "ok", "valid_configuration")
    except (OSError, ValueError, TypeError):
        add("sources", "error", "invalid_configuration",
            "Fix the sources JSON or configured file path.", "configure_sources")
    token = None
    try:
        path = token_path or token_file(root_dir)
        token = resolve_token(token=os.environ.get("COOKIE_HTTP_SEEDER_TOKEN"), token_file=path)
        add("token", "ok", "configured")
        if os.name == "posix" and path.is_file() and path.stat().st_mode & 0o077:
            add("token_permissions", "warning", "broad_permissions",
                "Use chmod 600 on the token file.", "fix_permissions")
    except (OSError, SystemExit, ValueError):
        add("token", "error", "missing_or_invalid_token",
            "Run init-token locally or check the token file.", "configure_token")
    snapshots = {}
    if sources is not None and data_dir.is_dir():
        sync = SyncState(data_dir)
        for source, spec in sources.items():
            try:
                snapshots[source] = sync.status(
                    source, stale_after=spec.get("stale_after_seconds", 86400),
                    validation_ttl=spec.get("validation_ttl_seconds", 86400))
                issue = snapshots[source]["freshness"] in {"missing", "stale", "unknown"}
                add(f"snapshot:{source}", "warning" if issue else "ok",
                    snapshots[source]["freshness"],
                    "Authorize and push this source in the extension." if issue else "",
                    "push_snapshot" if issue else "")
                validation = snapshots[source]["validation"]
                if validation in {"invalid", "unverified", "expired"}:
                    add(f"validation:{source}", "warning", validation,
                        "Log in and push a new snapshot, then validate with the crawler."
                        if validation == "invalid" else "Validate this snapshot with the crawler.",
                        "relogin" if validation == "invalid" else "validate_snapshot")
            except (OSError, ValueError, TypeError, OverflowError):
                add(f"snapshot:{source}", "error", "unreadable_state",
                    "Inspect the local snapshot/metadata files privately.", "inspect_metadata")
    configured = sources or {}
    metadata_origin = "local"
    capabilities = []
    health = _safe_health(
        ClientHealthStore(data_dir, sources=lambda: configured).status(), configured)
    try:
        incidents = _safe_incidents(
            IncidentStore(data_dir, sources=lambda: configured).list(), configured)
    except (OSError, ValueError, TypeError, OverflowError):
        incidents = _safe_incidents(None, configured)
    if not local_only and token is not None:
        try:
            client = ReceiverClient(endpoint, token, sender_tag=sender_tag)
            remote = client.request("/v1/sources")
            status = client.request("/v1/status")
            metadata_origin = "receiver"
            raw_capabilities = remote.get("capabilities")
            capabilities = sorted({c for c in (raw_capabilities if
                isinstance(raw_capabilities, list) else ()) if isinstance(c, str) and
                c in CAPABILITIES})
            for capability in ("client_health", "source_incidents"):
                if capability not in capabilities:
                    add(capability, "warning", "upgrade_required", "Upgrade receiver to 0.5.0.",
                        "upgrade_receiver")
            health = _safe_health(status.get("clientHealth"), configured)
            incidents = _safe_incidents(status.get("incidents"), configured)
            receiver_version = _safe_version(remote.get("receiver_version"))
            add("receiver_version", "ok" if receiver_version else "warning",
                "reported" if receiver_version else "unknown")
            add("receiver", "ok", "authenticated")
            if "conditional_snapshots" not in capabilities:
                add("protocol", "error", "upgrade_required",
                    "Upgrade the receiver and extension together.", "upgrade_receiver")
            if sources is not None:
                matched = remote.get("sources") == sources
                add("configuration_match", "ok" if matched else "warning",
                    "match" if matched else "mismatch",
                    "Use the same data directory for CLI and receiver." if not matched else "",
                    "align_configuration" if not matched else "")
        except ClientError as error:
            add("receiver", "error", error.code,
                "Check receiver, tunnel, TLS and token; do not paste credentials into logs.",
                "check_connection")
        except ValueError:
            add("receiver", "error", "invalid_endpoint",
                "Use an HTTPS origin or http://127.0.0.1:18765.", "check_connection")
    if health["state"] in {"overdue", "unknown"}:
        add("client_health", "warning", health["state"],
            "Check the browser and scheduled health reporting.",
            "inspect_metadata" if health["state"] == "unknown" else "check_browser")
    if not incidents["ok"]:
        add("incidents", "warning", "unreadable_incidents",
            "Inspect incident metadata privately.", "inspect_metadata")
    for entry in incidents["active"]:
        action = {"login_invalid": "relogin", "validation_expired": "validate_snapshot",
                  "snapshot_stale": "push_snapshot"}[entry["kind"]]
        add(f"incident:{entry['source']}:{entry['kind']}", "warning", entry["status"],
            "Handle the source and validate the new snapshot with the crawler.", action)
    return {"ok": all(c["status"] != "error" for c in checks), "checks": checks,
            "snapshots": snapshots, "localOnly": local_only, "client_version": __version__,
            "runtime_version": __version__, "installed_version": installed_version,
            "python_version": ".".join(map(str, sys.version_info[:3])),
            "receiver_version": receiver_version, "capabilities": capabilities,
            "metadata_origin": metadata_origin, "clientHealth": health, "incidents": incidents,
            "sourceThresholds": {source: {field: spec.get(field, 86400) for field in (
                "stale_after_seconds", "validation_ttl_seconds")}
                for source, spec in configured.items()}, "observedAt": utc_at(time.time()),
            "next_steps": sorted({check["next_step"] for check in checks if check["next_step"]}),
            "sender_tag": sender_tag}

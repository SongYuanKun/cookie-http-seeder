"""Receiver domain state; one independent application and namespace per instance."""
from __future__ import annotations

import json
import secrets
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .cookies import normalize_sources
from .notify import notify_needed, notify_pushed
from .paths import atomic_write_text, ensure_data_dir, webhook_file
from .senders import MAX_SENDERS, list_sender_tags, sender_directory, sender_tag
from .store import load_sources, read_snapshot, save_snapshot
from .sync_state import SyncState


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
        self.sender_tag = "default"
        self.notification_dir = self.data_dir
        self._senders: dict[str, ReceiverState] = {}

    def for_sender(self, tag: str, *, create: bool = True) -> ReceiverState:
        """One independently locked state/configuration per storage label."""
        tag = sender_tag(tag)
        if tag == "default":
            return self
        with self.lock:
            # Recheck the directory even for a cached state (no symlink aliases).
            directory = sender_directory(self.data_dir, tag)
            if tag in self._senders:
                return self._senders[tag]
            config = directory / "sources.json"
            if config.is_symlink():
                raise ValueError("sender configuration must not be a symlink")
            if config.is_file():
                sources = load_sources(config)
            else:
                if not create:
                    raise ValueError("sender is not initialized")
                existing = set(list_sender_tags(self.data_dir)) | set(self._senders)
                if len(existing - {"default"}) >= MAX_SENDERS:
                    raise ValueError("sender namespace limit reached")
                # Seed configuration only. NEVER copy default cookies or sync metadata.
                sources = json.loads(json.dumps(self.sources))
                ensure_data_dir(directory.parent)
                ensure_data_dir(directory)
                atomic_write_text(config, json.dumps({"schema_version": 1,
                                                     "sources": sources}) + "\n")
            child = ReceiverState(sources, directory, config)
            child.sender_tag = tag
            child.notification_dir = self.data_dir
            self._senders[tag] = child
            return child

    def document(self) -> dict[str, Any]:
        with self.lock:
            return {"ok": True, "schema_version": 1, "protocol_version": 2,
                    "receiver_version": __version__,
                    "sources": json.loads(json.dumps(self.sources)), "revision": self.revision,
                    "sender_tag": self.sender_tag,
                    "capabilities": ["conditional_snapshots", "validation_feedback", "sender_tags",
                                     "source_thresholds", "sender_status_summary",
                                     "stale_notifications"]}

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
                thresholds = {"stale_after_seconds", "validation_ttl_seconds"}
                previous = {k: v for k, v in old.items() if k not in thresholds}
                replacement = sources.get(name)
                updated = ({k: v for k, v in replacement.items() if k not in thresholds}
                           if replacement is not None else None)
                if updated != previous:
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
                reason = f"{self.sender_tag}/{source}: {payload['reason_code']}"

                def send() -> None:
                    try:
                        notify_needed(reason=reason,
                                      webhook_path=webhook_file(self.notification_dir))
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
            status = notify_pushed(
                sources=[f"{self.sender_tag}/{name}" for name in sources],
                webhook_path=webhook_file(self.notification_dir))
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
                    sync = self.sync.status(
                        name, stale_after=spec.get("stale_after_seconds", 86400),
                        validation_ttl=spec.get("validation_ttl_seconds", 86400))
                except (OSError, ValueError, TypeError, OverflowError):
                    sync = {"validation": "unverified", "freshness": "unknown",
                            "syncError": "unreadable_sync_state"}
                sources[name] = {**entry, "enabled": spec["enabled"], **sync}
            return {"ok": True, "sender_tag": self.sender_tag,
                    "receiver_version": __version__,
                    "sources": sources, "config_revision": self.revision,
                    "observedAt": _utc_now()}

    def initialized_states(self):
        """Enumerate existing labels only; one corrupt label does not hide the others."""
        for tag in list_sender_tags(self.data_dir):
            try:
                yield tag, self.for_sender(tag, create=False)
            except (OSError, ValueError, TypeError):
                yield tag, None

    def sender_statuses(self) -> dict[str, Any]:
        summaries = {}
        for tag, state in self.initialized_states():
            summaries[tag] = (state.status() if state is not None else
                              {"ok": False, "error": "unreadable_sender_state"})
        return {"ok": True, "senders": summaries, "observedAt": _utc_now()}


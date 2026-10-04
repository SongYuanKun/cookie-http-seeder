"""Strict credential-free client reports, aged by receiver receipt time."""
from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path

from .paths import atomic_write_text
from .sync_state import utc_at

PHASES = frozenset({"idle", "pending", "syncing", "probing", "retrying", "succeeded",
                    "exhausted", "blocked", "paused"})
ERRORS = frozenset({"none", "network_error", "unauthorized", "forbidden", "configuration_changed",
                    "permission_denied", "snapshot_conflict", "rate_limited", "retry_exhausted",
                    "worker_interrupted", "unknown_error"})


def _validate(payload, sources, now):
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version", "client_version", "interval_minutes", "sources",
    }:
        raise ValueError("invalid health fields")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise ValueError("invalid health schema")
    version = payload["client_version"]
    if not isinstance(version, str) or len(version) > 15 or not re.fullmatch(r"\d+\.\d+\.\d+",
                                                                          version):
        raise ValueError("invalid client version")
    interval = payload["interval_minutes"]
    if type(interval) is not int or not 15 <= interval <= 10080:
        raise ValueError("invalid report interval")
    entries = payload["sources"]
    if not isinstance(entries, dict) or len(entries) > 100 or set(entries) - set(sources):
        raise ValueError("invalid health sources")
    for entry in entries.values():
        if not isinstance(entry, dict) or set(entry) != {
            "approved", "permission_granted", "sync_phase", "error_code", "last_success_at",
        }:
            raise ValueError("invalid source health fields")
        if type(entry["approved"]) is not bool or type(entry["permission_granted"]) is not bool:
            raise ValueError("invalid permission status")
        if (not isinstance(entry["sync_phase"], str) or entry["sync_phase"] not in PHASES
                or not isinstance(entry["error_code"], str) or entry["error_code"] not in ERRORS):
            raise ValueError("invalid health category")
        value = entry["last_success_at"]
        if value is not None:
            if not isinstance(value, str) or not re.fullmatch(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value,
            ):
                raise ValueError("invalid success timestamp")
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo != UTC or not 0 <= parsed.timestamp() <= now:
                raise ValueError("invalid success timestamp")
    return json.loads(json.dumps(payload))


class ClientHealthStore:
    def __init__(self, data_dir: Path, clock=time.time):
        self.path = data_dir / ".client-health.json"
        self.clock = clock

    def accept(self, payload, sources) -> dict:
        now = self.clock()
        doc = _validate(payload, sources, now)
        self._write({**doc, "received_at": now})
        return self.status()

    def _write(self, doc):
        atomic_write_text(self.path, json.dumps(doc, sort_keys=True) + "\n", mode=0o600)

    def _read(self):
        if self.path.is_symlink() or self.path.stat().st_size > 65536:
            raise ValueError("invalid health file")
        doc = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or "received_at" not in doc:
            raise ValueError("invalid health state")
        received = doc.pop("received_at")
        if type(received) not in {int, float} or not 0 <= received <= self.clock():
            raise ValueError("invalid receipt time")
        _validate(doc, doc.get("sources", {}), received)
        return doc, received

    def forget(self, names) -> None:
        if not self.path.exists():
            return
        doc, received = self._read()
        doc["sources"] = {k: v for k, v in doc["sources"].items() if k not in names}
        self._write({**doc, "received_at": received})

    def status(self) -> dict:
        if not self.path.exists():
            return {"ok": True, "state": "not_reported", "sources": {}}
        try:
            doc, received = self._read()
            age = max(0, int(self.clock() - received))
            return {"ok": True, "state": ("overdue" if age >
                    2 * doc["interval_minutes"] * 60 + 300 else "recent"),
                    "receivedAt": utc_at(received), "ageSeconds": age,
                    "client_version": doc["client_version"],
                    "interval_minutes": doc["interval_minutes"], "sources": doc["sources"]}
        except (OSError, ValueError, TypeError, OverflowError):
            return {"ok": False, "state": "unknown", "sources": {},
                    "error": "unreadable_client_health"}

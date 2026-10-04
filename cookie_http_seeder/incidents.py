"""Per-namespace, bounded incident history; no credentials or response bodies."""
from __future__ import annotations

import json
import re
import secrets
import time
from pathlib import Path

from .paths import atomic_write_text
from .sync_state import VERSION, utc_at

KINDS = {"login_invalid", "validation_expired", "snapshot_stale"}
STATES = {"open", "acknowledged", "snoozed", "awaiting_validation", "resolved"}
REASONS = {"login_required", "session_expired", "account_mismatch", "validation_expired",
           "snapshot_stale"}
FIELDS = {"incident_id", "source", "kind", "snapshot_version", "status", "reason_code",
          "opened_at", "updated_at", "snoozed_until", "acknowledged"}


class IncidentStore:
    def __init__(self, data_dir: Path, clock=time.time):
        self.path = data_dir / ".incidents.json"
        self.clock = clock

    def _load(self):
        if not self.path.exists():
            return {"schema_version": 1, "active": [], "history": []}
        if self.path.is_symlink() or self.path.stat().st_size > 1024 * 1024:
            raise ValueError("invalid incident file")
        doc = json.loads(self.path.read_text(encoding="utf-8"))
        if (not isinstance(doc, dict) or set(doc) != {"schema_version", "active", "history"}
                or type(doc["schema_version"]) is not int or doc["schema_version"] != 1
                or not isinstance(doc["active"], list) or len(doc["active"]) > 300
                or not isinstance(doc["history"], list) or len(doc["history"]) > 100):
            raise ValueError("invalid incident state")
        keys = set()
        for entry in doc["active"] + doc["history"]:
            if (not isinstance(entry, dict) or set(entry) != FIELDS
                    or not isinstance(entry["incident_id"], str)
                    or not re.fullmatch(r"[a-f0-9]{32}", entry["incident_id"])
                    or not isinstance(entry["source"], str)
                    or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", entry["source"])
                    or entry["kind"] not in KINDS or entry["status"] not in STATES
                    or entry["reason_code"] not in REASONS
                    or type(entry["acknowledged"]) is not bool):
                raise ValueError("invalid incident record")
            version = entry["snapshot_version"]
            if version is not None and (
                not isinstance(version, str) or not VERSION.fullmatch(version)):
                raise ValueError("invalid incident version")
            for field in ("opened_at", "updated_at", "snoozed_until"):
                value = entry[field]
                if value is not None and (type(value) not in {int, float} or not 0 <= value < 1e12):
                    raise ValueError("invalid incident timestamp")
        for entry in doc["active"]:
            key = entry["source"], entry["kind"]
            if key in keys or entry["status"] == "resolved":
                raise ValueError("duplicate active incident")
            keys.add(key)
        return doc

    def _write(self, doc):
        doc["history"] = doc["history"][-100:]
        atomic_write_text(self.path, json.dumps(doc, sort_keys=True) + "\n", mode=0o600)

    def list(self) -> dict:
        doc = self._load()
        def render(entry):
            return {key: (utc_at(value) if value is not None and key in {
                "opened_at", "updated_at", "snoozed_until"} else value)
                    for key, value in entry.items()}
        return {"ok": True, "active": [render(e) for e in doc["active"]],
                "history": [render(e) for e in doc["history"]]}

    def observe(self, source: str, status: dict) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", source):
            raise ValueError("invalid source")
        version = status.get("snapshot_version")
        if version is not None and (not isinstance(version, str) or not VERSION.fullmatch(version)):
            raise ValueError("invalid version")
        doc = self._load()
        original = json.dumps(doc, sort_keys=True)
        now = self.clock()
        validation = status.get("validation")
        for kind in sorted(KINDS):
            entry = next((e for e in doc["active"] if e["source"] == source and e["kind"] == kind),
                         None)
            problem = (bool(version) and validation == "invalid" if kind == "login_invalid" else
                       validation == "expired" if kind == "validation_expired" else
                       status.get("freshness") == "stale")
            recovered = (bool(version) and validation == "valid" and
                         version != entry["snapshot_version"] if kind == "login_invalid" and entry
                         else validation == "valid" if kind == "validation_expired" else
                         status.get("freshness") == "fresh" if kind == "snapshot_stale" else False)
            if entry and recovered:
                entry.update(status="resolved", updated_at=now)
                doc["active"].remove(entry)
                doc["history"].append(entry)
            elif not entry and problem:
                detail = status.get("validationDetail", {})
                reason = detail.get("reasonCode") if kind == "login_invalid" else kind
                if reason not in REASONS:
                    reason = "login_required"
                doc["active"].append({"incident_id": secrets.token_hex(16), "source": source,
                    "kind": kind, "snapshot_version": version, "status": "open",
                    "reason_code": reason, "opened_at": now, "updated_at": now,
                    "snoozed_until": None, "acknowledged": False})
            elif entry:
                if kind == "login_invalid" and version and version != entry["snapshot_version"]:
                    if problem:
                        entry.update(snapshot_version=version, status="open", acknowledged=False,
                                     snoozed_until=None, updated_at=now)
                    elif entry["status"] != "awaiting_validation":
                        entry.update(status="awaiting_validation", updated_at=now)
                elif (entry["status"] == "snoozed" and entry["snoozed_until"] <= now):
                    entry.update(status="open", snoozed_until=None, updated_at=now)
        if json.dumps(doc, sort_keys=True) != original:
            self._write(doc)

    def may_notify(self, source, kind) -> bool:
        entry = next((e for e in self._load()["active"]
                      if e["source"] == source and e["kind"] == kind), None)
        return not entry or (not entry["acknowledged"] and
                             (entry["snoozed_until"] is None or
                              self.clock() >= entry["snoozed_until"]))

    def act(self, source, incident_id, action, duration_seconds=None) -> dict:
        if action not in {"acknowledge", "snooze", "reopen"}:
            raise ValueError("invalid action")
        if action == "snooze":
            if type(duration_seconds) is not int or not 60 <= duration_seconds <= 86400:
                raise ValueError("invalid snooze duration")
        elif duration_seconds is not None:
            raise ValueError("unexpected duration")
        doc = self._load()
        entry = next((e for e in doc["active"] if e["source"] == source and
                      e["incident_id"] == incident_id), None)
        if entry is None:
            raise ValueError("unknown current incident")
        entry.update(status={"acknowledge": "acknowledged", "snooze": "snoozed",
                             "reopen": "open"}[action], updated_at=self.clock(),
                     acknowledged=action == "acknowledge",
                     snoozed_until=self.clock() + duration_seconds if action == "snooze" else None)
        self._write(doc)
        return self.list()

    def forget(self, names) -> None:
        if not self.path.exists():
            return
        doc = self._load()
        for key in ("active", "history"):
            doc[key] = [e for e in doc[key] if e["source"] not in names]
        self._write(doc)

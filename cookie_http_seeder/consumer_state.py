"""Durable consumer pause and a bounded, version-coalesced feedback outbox."""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path

from .paths import atomic_write_text, ensure_data_dir, xdg_data_home
from .process_lock import DataDirectoryInUse, DataDirectoryLock
from .response_rules import Observation
from .sync_state import VERSION

MAX_FEEDBACK_ATTEMPTS = 3
MAX_OUTBOX = 16


class ConsumerBusy(RuntimeError):
    """Another request owns this consumer state file."""


def consumer_identity(data_dir, endpoint, tag, source):
    raw = f"{Path(data_dir).resolve()}\n{endpoint}\n{tag}\n{source}"
    return hashlib.sha256(raw.encode()).hexdigest()


def default_state_path(identity):
    return xdg_data_home() / "cookie-http-seeder" / "consumer-state" / identity / "state.json"


class ConsumerState:
    def __init__(self, path: Path, identity: str, source: str):
        self.path, self.identity, self.source = Path(path), identity, source

    @contextmanager
    def locked(self):
        directory = ensure_data_dir(self.path.parent / f".{self.path.name}.lock")
        try:
            with DataDirectoryLock(directory):
                yield
        except DataDirectoryInUse:
            raise ConsumerBusy("another request owns this consumer state") from None

    def load(self):
        if self.path.is_symlink():
            raise ValueError("invalid consumer state")
        if not self.path.exists():
            return {"schema_version": 2, "identity": self.identity,
                    "blocked_version": None, "outbox": []}
        try:
            if self.path.stat().st_size > 65536:
                raise ValueError("invalid consumer state")
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ValueError("invalid consumer state") from None
        if not isinstance(state, dict) or type(state.get("schema_version")) is not int:
            raise ValueError("invalid consumer state")
        if state["schema_version"] == 1:
            if (set(state) != {"schema_version", "identity", "blocked_version",
                               "pending_feedback", "feedback_attempts"}
                    or type(state["feedback_attempts"]) is not int
                    or not 0 <= state["feedback_attempts"] <= MAX_FEEDBACK_ATTEMPTS):
                raise ValueError("invalid consumer state")
            pending = state["pending_feedback"]
            if pending is not None and (not isinstance(pending, dict) or set(pending) != {
                "source", "snapshot_version", "result", "reason_code",
            }):
                raise ValueError("invalid consumer state")
            attempts = state["feedback_attempts"]
            state = {"schema_version": 2, "identity": state["identity"],
                     "blocked_version": state["blocked_version"],
                     "outbox": [{**pending, "attempts": attempts,
                                 "status": "exhausted" if attempts == 3 else "pending"}]
                     if pending is not None else []}
        if (set(state) != {"schema_version", "identity", "blocked_version", "outbox"}
                or state["schema_version"] != 2 or state["identity"] != self.identity
                or not isinstance(state["outbox"], list) or len(state["outbox"]) > MAX_OUTBOX):
            raise ValueError("invalid consumer state")
        blocked = state["blocked_version"]
        if blocked is not None and (not isinstance(blocked, str) or not VERSION.fullmatch(blocked)):
            raise ValueError("invalid consumer state")
        versions = set()
        for record in state["outbox"]:
            self._validate_record(record)
            if record["snapshot_version"] in versions:
                raise ValueError("invalid consumer state")
            versions.add(record["snapshot_version"])
        return state

    def _validate_record(self, record):
        if (not isinstance(record, dict) or set(record) != {
            "source", "snapshot_version", "result", "reason_code", "attempts", "status",
        } or record["source"] != self.source or not isinstance(record["snapshot_version"], str)
                or not VERSION.fullmatch(record["snapshot_version"])
                or type(record["attempts"]) is not int or not 0 <= record["attempts"] <= 3
                or record["status"] not in {"pending", "blocked", "exhausted"}
                or (record["attempts"] == 3 and record["status"] == "pending")):
            raise ValueError("invalid consumer state")
        Observation(record["result"], record["reason_code"])

    def save(self, state):
        ensure_data_dir(self.path.parent)
        atomic_write_text(self.path, json.dumps(state, sort_keys=True) + "\n", mode=0o600)

    def enqueue(self, state, report):
        record = next((r for r in state["outbox"] if r["snapshot_version"] ==
                       report["snapshot_version"]), None)
        if record:
            # Same version shares one retry budget. Invalid evidence takes precedence.
            if record["result"] != "invalid" or report["result"] == "invalid":
                record.update(report)
        else:
            if len(state["outbox"]) >= MAX_OUTBOX:
                raise ValueError("feedback outbox full")
            record = {**report, "attempts": 0, "status": "pending"}
            self._validate_record(record)
            state["outbox"].append(record)

    def status(self):
        try:
            state = self.load()
        except (OSError, ValueError, TypeError):
            return {"ok": False, "error": "unreadable_consumer_state"}
        return {"ok": True, "schema_version": 2, "paused": bool(state["blocked_version"]),
                "blocked_version": state["blocked_version"], "outbox": state["outbox"]}

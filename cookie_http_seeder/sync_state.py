"""Conditional snapshots and credential-free operational state.

The receiver owns serialization (one process per data directory). Cookie data and
operational metadata are separate atomic files, not a multi-file transaction.
A metadata write failure may leave an accepted snapshot; retrying is safe via CAS.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .cookies import normalize_cookies
from .paths import atomic_write_text
from .store import cookie_path_for_source, read_snapshot, save_snapshot

VERSION = re.compile(r"^[a-f0-9]{32}$")
REQUEST_ID = re.compile(r"^[a-zA-Z0-9_-][a-zA-Z0-9._-]{15,79}$")
RESULT_REASONS = {
    "valid": {"logged_in"},
    "invalid": {"login_required", "session_expired", "account_mismatch"},
    "error": {"network_error", "rate_limited", "unexpected_response"},
    "unverified": {"manual_reset"},
}


class SnapshotConflict(ValueError):
    """Fetch the current version, then recollect browser cookies before retrying."""


class PreconditionRequired(ValueError):
    """The client must support conditional snapshot writes."""


def utc_at(timestamp: float) -> str:
    rendered = datetime.fromtimestamp(timestamp, UTC).isoformat(timespec="seconds")
    return rendered.replace("+00:00", "Z")


def _timestamp(value: object) -> float | None:
    if isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return result.timestamp() if result.tzinfo else None
        except (ValueError, OverflowError):
            pass
    return None


def _age(value: object, now: float) -> int | None:
    if type(value) not in {int, float} or not 0 <= value <= now + 300:
        return None
    return max(0, int(now - value))


def _fingerprint(cookies: list[dict], spec: dict) -> str:
    # Keep array order: equal-length cookie paths retain browser creation order.
    value = {"cookies": cookies, "domains": spec["domains"],
             "target_url": spec.get("target_url", "")}
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class SyncState:
    """All mutating methods must run under the owning ReceiverState lock."""

    def __init__(self, data_dir: Path, *, clock=time.time):
        self.data_dir = data_dir
        self.clock = clock

    def _meta_path(self, source: str) -> Path:
        cookie_path_for_source(source, data_dir=self.data_dir)  # validate source
        return self.data_dir / f".{source}-sync.json"

    def _meta(self, source: str) -> dict[str, Any]:
        path = self._meta_path(source)
        if not path.is_file():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            raise ValueError("invalid sync state")
        return raw

    def _write_meta(self, source: str, meta: dict[str, Any]) -> None:
        atomic_write_text(self._meta_path(source),
                          json.dumps({**meta, "schema_version": 1}, sort_keys=True) + "\n",
                          mode=0o600)

    def version(self, source: str) -> str | None:
        doc = read_snapshot(source, data_dir=self.data_dir)
        if not isinstance(doc, dict) or doc.get("schema_version") != 2:
            return None
        version = doc.get("snapshot_version")
        if version is not None and (not isinstance(version, str) or not VERSION.fullmatch(version)):
            raise ValueError("invalid snapshot version")
        return version

    def accept(self, payload: dict, spec: dict) -> dict[str, Any]:
        if "expected_version" not in payload or "request_id" not in payload:
            raise PreconditionRequired("upgrade client; expected_version and request_id required")
        expected, request_id = payload["expected_version"], payload["request_id"]
        if expected is not None and (
            not isinstance(expected, str) or not VERSION.fullmatch(expected)
        ):
            raise ValueError("invalid expected_version")
        if not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id):
            raise ValueError("invalid request_id")
        source = payload["source"]
        jar = normalize_cookies(payload.get("cookies"), spec["domains"])
        digest = _fingerprint(jar, spec)
        previous = read_snapshot(source, data_dir=self.data_dir)
        if not isinstance(previous, dict) or previous.get("schema_version") != 2:
            previous = {}
        version = self.version(source)
        meta = self._meta(source)
        old_digest = _fingerprint(previous.get("cookies", []), {
            "domains": previous.get("domains", []), "target_url": previous.get("target_url", ""),
        }) if previous else None
        replay = bool(version and request_id in {
            previous.get("request_id"),
            meta.get("lastRequestId") if meta.get("snapshotVersion") == version else None,
        })
        if replay and digest != old_digest:
            raise ValueError("request_id reused for different content")
        if not replay and expected != version:
            raise SnapshotConflict("snapshot_conflict")
        unchanged = bool(version and old_digest == digest)
        now = self.clock()
        if unchanged:
            doc = previous
        else:
            doc = save_snapshot(jar, source=source, spec=spec, updated_at=utc_at(now),
                                data_dir=self.data_dir, request_id=request_id)
        if meta.get("snapshotVersion") != doc["snapshot_version"]:
            meta = {"lastNotifyAt": meta.get("lastNotifyAt")}
        meta.update({"snapshotVersion": doc["snapshot_version"], "lastSeen": now,
                     "lastRequestId": request_id})
        self._write_meta(source, meta)
        return {"ok": True, "source": source, "updatedAt": doc["updatedAt"],
                "lastSeenAt": utc_at(now), "snapshot_version": doc["snapshot_version"],
                "cookieCount": len(doc["cookies"]), "cleared": not doc["cookies"],
                "unchanged": unchanged, "replayed": replay}

    def feedback(self, payload: object, *, notify: bool = False) -> tuple[dict, bool]:
        if not isinstance(payload, dict) or set(payload) != {
            "source", "snapshot_version", "result", "reason_code",
        }:
            raise ValueError("invalid feedback fields")
        source, version = payload["source"], payload["snapshot_version"]
        cookie_path_for_source(source, data_dir=self.data_dir)
        if not isinstance(version, str) or not VERSION.fullmatch(version):
            raise ValueError("snapshot_version required")
        if version != self.version(source):
            raise SnapshotConflict("snapshot_conflict")
        result, reason = payload["result"], payload["reason_code"]
        if not isinstance(result, str) or result not in RESULT_REASONS:
            raise ValueError("invalid validation result")
        if not isinstance(reason, str) or reason not in RESULT_REASONS[result]:
            raise ValueError("invalid reason_code for result")
        doc = read_snapshot(source, data_dir=self.data_dir)
        if result == "valid" and not doc.get("cookies"):
            raise ValueError("empty snapshot cannot be reported as valid")
        now = self.clock()
        meta = self._meta(source)
        if meta.get("snapshotVersion") != version:
            meta = {"lastNotifyAt": meta.get("lastNotifyAt")}
        meta.update({"snapshotVersion": version, "validation": result,
                     "reasonCode": reason, "checkedAt": now})
        last_notify = meta.get("lastNotifyAt")
        due = (type(last_notify) not in {float, int} or now - last_notify >= 900)
        should_notify = bool(notify and result == "invalid" and due)
        if should_notify:
            # Reserve before sending; avoids notification floods after restarts/failures.
            meta["lastNotifyAt"] = now
        self._write_meta(source, meta)
        return ({"ok": True, "source": source, "snapshot_version": version,
                 "validation": result, "checkedAt": utc_at(now),
                 "notificationScheduled": should_notify}, should_notify)

    def status(self, source: str, *, stale_after: int = 86400) -> dict[str, Any]:
        now = self.clock()
        doc = read_snapshot(source, data_dir=self.data_dir)
        doc = doc if isinstance(doc, dict) and doc.get("schema_version") == 2 else {}
        version = self.version(source)
        meta = self._meta(source)
        current = bool(version and meta.get("snapshotVersion") == version)
        seen = meta.get("lastSeen") if current else _timestamp(doc.get("updatedAt"))
        age = _age(seen, now)
        freshness = ("missing" if not doc else "cleared" if doc.get("cleared")
                     else "unknown" if age is None else "stale" if age > stale_after else "fresh")
        checked_age = _age(meta.get("checkedAt"), now) if current else None
        result = meta.get("validation", "unverified") if current else "unverified"
        if not isinstance(result, str) or result not in RESULT_REASONS:
            result = "unverified"
        reason = meta.get("reasonCode") if result != "unverified" else None
        if reason not in RESULT_REASONS.get(result, set()):
            reason = None
        effective = "expired" if result != "unverified" and (
            checked_age is None or checked_age > stale_after) else result
        return {"snapshot_version": version,
                "lastSeenAt": utc_at(seen) if age is not None else None,
                "ageSeconds": age, "freshness": freshness, "staleAfterSeconds": stale_after,
                "validation": effective, "validationDetail": {
                    "reportedResult": result, "reasonCode": reason,
                    "ageSeconds": checked_age,
                    "origin": "crawler_report" if current and "validation" in meta else None,
                }}

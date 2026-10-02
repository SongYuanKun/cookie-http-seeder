"""Explicit site observations, version-bound feedback and durable consumer pause.

The caller owns the HTTP transport and site rules. Operational state contains no
Cookie, Token, request headers or response bodies. One request at a time owns a
consumer state file; use a private writable state directory outside read-only data.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urlsplit

from .client import ClientError
from .cookies import request_url
from .paths import atomic_write_text, ensure_data_dir, xdg_data_home
from .process_lock import DataDirectoryInUse, DataDirectoryLock
from .senders import sender_tag
from .store import SnapshotUnavailable, load_request_credentials, read_snapshot
from .sync_state import RESULT_REASONS, VERSION

MAX_FEEDBACK_ATTEMPTS = 3


class LoginRequired(RuntimeError):
    """This exact snapshot was confirmed invalid; wait for a replacement."""


class CredentialsUnavailable(ValueError):
    """No nonempty, URL-scoped versioned credentials are available."""


class ConsumerBusy(RuntimeError):
    """Another request owns this consumer state file."""


@dataclass(frozen=True)
class Observation:
    result: str
    reason_code: str

    def __post_init__(self):
        if (not isinstance(self.result, str) or self.result not in RESULT_REASONS
                or not isinstance(self.reason_code, str)
                or self.reason_code not in RESULT_REASONS[self.result]):
            raise ValueError("invalid observation result/reason combination")


@dataclass(frozen=True)
class Response:
    status: int
    body: str = field(default="", repr=False)
    headers: dict[str, str] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class RequestOutcome:
    observation: Observation
    snapshot_version: str
    feedback_status: str
    paused: bool
    response: Any = field(default=None, repr=False)


class ResponseRules:
    """Opt-in response markers; never infer login from a status code alone."""

    @classmethod
    def from_document(cls, raw: dict) -> ResponseRules:
        fields = {"schema_version", "valid_body_contains", "invalid_body_contains",
                  "valid_json", "invalid_json", "login_redirect_paths"}
        if (not isinstance(raw, dict) or raw.get("schema_version") != 1
                or set(raw) - fields):
            raise ValueError("invalid response rule document")
        instance = cls()
        for name in ("valid_body_contains", "invalid_body_contains", "login_redirect_paths"):
            values = raw.get(name, [])
            if (not isinstance(values, list) or len(values) > 32
                    or any(not isinstance(v, str) or not 1 <= len(v) <= 2048 for v in values)):
                raise ValueError("rules require bounded nonempty string markers")
            if name == "login_redirect_paths" and any(
                not v.startswith("/") or v.startswith("//") or "?" in v or "#" in v
                for v in values
            ):
                raise ValueError("login redirects require absolute URL paths")
            setattr(instance, name, tuple(values))
        for name in ("valid_json", "invalid_json"):
            rules = raw.get(name, [])
            if not isinstance(rules, list) or len(rules) > 32:
                raise ValueError("JSON rules require a bounded list")
            for rule in rules:
                allowed = {"path", "equals"}
                if name == "invalid_json":
                    allowed |= {"reason_code"}
                if (not isinstance(rule, dict) or set(rule) - allowed
                        or not {"path", "equals"} <= set(rule)
                        or not isinstance(rule["path"], list) or not 1 <= len(rule["path"]) <= 16
                        or any(not isinstance(k, str) or not 1 <= len(k) <= 128
                               for k in rule["path"])
                        or type(rule["equals"]) not in {str, int, bool, float, type(None)}
                        or (type(rule["equals"]) is float and not math.isfinite(rule["equals"]))):
                    raise ValueError("invalid JSON path/equality rule")
                if "reason_code" in rule:
                    Observation("invalid", rule["reason_code"])
            setattr(instance, name, tuple(json.loads(json.dumps(rules))))
        if not any(getattr(instance, key) for key in fields - {"schema_version"}):
            raise ValueError("configure explicit site response evidence")
        return instance

    @staticmethod
    def _json_matches(body: str, rules: tuple) -> dict | None:
        if not rules:
            return None
        try:
            document = json.loads(body)
        except (ValueError, TypeError, RecursionError):
            return None
        for rule in rules:
            value = document
            for key in rule["path"]:
                if not isinstance(value, dict) or key not in value:
                    break
                value = value[key]
            else:
                expected = rule["equals"]
                if type(value) is type(expected) and value == expected:
                    return rule
        return None

    def __call__(self, response: Response) -> Observation:
        if (not isinstance(response, Response) or type(response.status) is not int
                or not 100 <= response.status <= 599
                or not isinstance(response.body, str) or len(response.body) > 1024 * 1024
                or not isinstance(response.headers, dict)
                or any(not isinstance(k, str) or not isinstance(v, str)
                       for k, v in response.headers.items())):
            return Observation("error", "unexpected_response")
        if response.status == 429:
            return Observation("error", "rate_limited")
        if response.status >= 500:
            return Observation("error", "unexpected_response")
        if 300 <= response.status < 400:
            location = next((v for k, v in response.headers.items()
                             if k.lower() == "location"), "")
            try:
                path = urlsplit(location).path
            except (ValueError, TypeError):
                path = ""
            if any(path == p or path.startswith(p.rstrip("/") + "/")
                   for p in self.login_redirect_paths):
                return Observation("invalid", "login_required")
        invalid_json = self._json_matches(response.body, self.invalid_json)
        if invalid_json:
            return Observation("invalid", invalid_json.get("reason_code", "login_required"))
        if any(marker in response.body for marker in self.invalid_body_contains):
            return Observation("invalid", "login_required")
        if 200 <= response.status < 300 and (
            any(marker in response.body for marker in self.valid_body_contains)
            or self._json_matches(response.body, self.valid_json)
        ):
            return Observation("valid", "logged_in")
        return Observation("error", "unexpected_response")


class CookieConsumer:
    def __init__(self, *, source: str, data_dir: Path, client, classify,
                 state_path: Path | None = None, clock=time.monotonic, sleep=time.sleep):
        from .store import cookie_path_for_source
        cookie_path_for_source(source, data_dir=data_dir)
        if not callable(classify):
            raise ValueError("an explicit site classifier is required")
        self.source, self.data_dir = source, Path(data_dir)
        self.sender_tag = sender_tag(client.sender_tag)
        self.client, self.classify = client, classify
        identity = f"{self.data_dir.resolve()}\n{client.endpoint}\n{self.sender_tag}\n{source}"
        self.identity = hashlib.sha256(identity.encode()).hexdigest()
        self.state_path = Path(state_path) if state_path else (
            xdg_data_home() / "cookie-http-seeder" / "consumer-state" / self.identity / "state.json"
        )
        self.clock, self.sleep = clock, sleep

    @contextmanager
    def _locked(self):
        lock_dir = ensure_data_dir(self.state_path.parent / f".{self.state_path.name}.lock")
        try:
            with DataDirectoryLock(lock_dir):
                yield
        except DataDirectoryInUse:
            raise ConsumerBusy("another request owns this consumer state") from None

    def _load(self) -> dict:
        if self.state_path.is_symlink():
            raise ValueError("consumer state must not be a symbolic link")
        if not self.state_path.exists():
            return {"schema_version": 1, "identity": self.identity, "blocked_version": None,
                    "pending_feedback": None, "feedback_attempts": 0}
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ValueError("unreadable consumer state") from None
        if (not isinstance(state, dict)
                or set(state) != {"schema_version", "identity", "blocked_version",
                                  "pending_feedback", "feedback_attempts"}
                or type(state.get("schema_version")) is not int
                or state.get("schema_version") != 1
                or state.get("identity") != self.identity
                or (state.get("blocked_version") is not None
                    and (not isinstance(state["blocked_version"], str)
                         or not VERSION.fullmatch(state["blocked_version"])))
                or type(state.get("feedback_attempts")) is not int
                or not 0 <= state["feedback_attempts"] <= MAX_FEEDBACK_ATTEMPTS):
            raise ValueError("invalid consumer state")
        pending = state.get("pending_feedback")
        if pending is not None:
            if (not isinstance(pending, dict)
                    or set(pending) != {"source", "snapshot_version", "result", "reason_code"}
                    or pending["source"] != self.source
                    or not isinstance(pending["snapshot_version"], str)
                    or not VERSION.fullmatch(pending["snapshot_version"])):
                raise ValueError("invalid pending feedback")
            Observation(pending["result"], pending["reason_code"])
        return state

    def _save(self, state: dict) -> None:
        ensure_data_dir(self.state_path.parent)
        atomic_write_text(self.state_path, json.dumps(state, sort_keys=True) + "\n")

    def _credentials(self, url: str) -> dict:
        request_url(url)
        try:
            credentials = load_request_credentials(source=self.source, url=url,
                                                   data_dir=self.data_dir,
                                                   sender_tag=self.sender_tag)
        except (SnapshotUnavailable, OSError, json.JSONDecodeError):
            raise CredentialsUnavailable("push a versioned snapshot for this source/tag") from None
        if not credentials["cookie_header"]:
            raise CredentialsUnavailable("no nonempty cookies apply to this URL")
        return credentials

    def _flush(self, state: dict) -> str:
        pending = state["pending_feedback"]
        if pending is None:
            return "idle"
        if state["feedback_attempts"] >= MAX_FEEDBACK_ATTEMPTS:
            return "exhausted"
        state["feedback_attempts"] += 1
        self._save(state)  # Record intent before the network attempt.
        try:
            self.client.report(pending["source"], pending["snapshot_version"],
                               pending["result"], pending["reason_code"])
        except ClientError as error:
            if error.code == "stale_snapshot":
                status = "discarded"
            else:
                exhausted = state["feedback_attempts"] == MAX_FEEDBACK_ATTEMPTS
                return "exhausted" if exhausted else "pending"
        else:
            status = "sent"
        state["pending_feedback"] = None
        state["feedback_attempts"] = 0
        self._save(state)
        return status

    def flush_feedback(self) -> str:
        """Try one pending report, with at most three total attempts per report."""
        with self._locked():
            return self._flush(self._load())

    def request(self, url: str, send) -> RequestOutcome:
        """Send one URL only. The transport must disable automatic redirects."""
        with self._locked():
            state = self._load()
            credentials = self._credentials(url)
            version = credentials["snapshot_version"]
            if state["blocked_version"] == version:
                raise LoginRequired(
                    "this snapshot was confirmed invalid; login and push a replacement")
            response = None
            try:
                response = send(url, {"Cookie": credentials["cookie_header"]})
            except (URLError, TimeoutError, OSError):
                observation = Observation("error", "network_error")
            else:
                observation = self.classify(response)
                if not isinstance(observation, Observation):
                    raise ValueError("classifier must return Observation")
            try:
                current = read_snapshot(self.source, data_dir=self.data_dir,
                                        sender_tag=self.sender_tag)
            except (OSError, ValueError):
                # Retain the invalid version when current storage cannot be checked.
                # This never blocks a different version after storage recovers.
                current = None
                if observation.result == "invalid":
                    state["blocked_version"] = version
            if isinstance(current, dict) and current.get("snapshot_version") == version:
                if observation.result == "invalid":
                    state["blocked_version"] = version
                elif observation.result == "valid":
                    state["blocked_version"] = None
            state["pending_feedback"] = {"source": self.source, "snapshot_version": version,
                                         "result": observation.result,
                                         "reason_code": observation.reason_code}
            state["feedback_attempts"] = 0
            self._save(state)  # Pause survives offline feedback and process restarts.
            feedback = self._flush(state)
            return RequestOutcome(observation, version, feedback,
                                  bool(state["blocked_version"]), response)

    def wait_for_update(self, url: str, *, timeout: float = 300,
                        poll_interval: float = 5) -> bool:
        """Wait for usable replacement credentials; True permits a verification request."""
        if (type(timeout) not in {int, float} or not math.isfinite(timeout)
                or not 0 <= timeout <= 86400
                or type(poll_interval) not in {int, float} or not math.isfinite(poll_interval)
                or not 0.1 <= poll_interval <= 3600):
            raise ValueError("bounded timeout and positive poll interval required")
        deadline = self.clock() + timeout
        while True:
            state = self._load()
            try:
                credentials = self._credentials(url)
            except CredentialsUnavailable:
                pass
            else:
                if credentials["snapshot_version"] != state["blocked_version"]:
                    return True
            remaining = deadline - self.clock()
            if remaining <= 0:
                return False
            self.sleep(min(poll_interval, remaining))

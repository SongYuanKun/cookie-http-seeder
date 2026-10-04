"""URL-scoped consumer orchestration; compatibility exports for response rules."""
from __future__ import annotations

import json
import math
import time
from pathlib import Path
from urllib.error import URLError

from .client import ClientError
from .consumer_state import (
    MAX_FEEDBACK_ATTEMPTS,
    ConsumerState,
    consumer_identity,
    default_state_path,
)
from .consumer_state import ConsumerBusy as ConsumerBusy
from .cookies import request_url
from .response_rules import Observation as Observation
from .response_rules import RequestOutcome as RequestOutcome
from .response_rules import Response as Response
from .response_rules import ResponseRules as ResponseRules
from .senders import sender_tag
from .store import SnapshotUnavailable, load_request_credentials, read_snapshot


class LoginRequired(RuntimeError):
    """This snapshot was confirmed invalid; wait for a replacement."""


class CredentialsUnavailable(ValueError):
    """No nonempty URL-scoped versioned credentials are available."""


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
        self.identity = consumer_identity(self.data_dir, client.endpoint, self.sender_tag, source)
        self.state_path = Path(state_path) if state_path else default_state_path(self.identity)
        self.state_store = ConsumerState(self.state_path, self.identity, source)
        self.clock, self.sleep = clock, sleep

    def _locked(self):
        return self.state_store.locked()

    def _load(self):
        return self.state_store.load()

    def _save(self, state):
        self.state_store.save(state)

    def status(self):
        """Read local safe state only; no lock, migration write, auth or snapshot access."""
        return self.state_store.status()

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
        pending = next((r for r in state["outbox"] if r["status"] == "pending"), None)
        if pending is None:
            if not state["outbox"]:
                return "idle"
            return ("blocked" if any(r["status"] == "blocked" for r in state["outbox"])
                    else "exhausted")
        pending["attempts"] += 1
        # Persist intent even if process dies during the network request.
        pending["status"] = ("exhausted" if pending["attempts"] == MAX_FEEDBACK_ATTEMPTS
                             else "pending")
        self._save(state)
        try:
            self.client.report(pending["source"], pending["snapshot_version"],
                               pending["result"], pending["reason_code"])
        except ClientError as error:
            if error.code == "stale_snapshot":
                status = "discarded"
            elif error.code in {"network_error", "receiver_error"}:
                return pending["status"]
            else:
                pending["status"] = "blocked"
                self._save(state)
                return "blocked"
        else:
            status = "sent"
        state["outbox"].remove(pending)
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
            self._save(state)  # Check durable storage before sending credentials.
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
            if isinstance(current, dict) and current.get("snapshot_version") == version:
                # Local receiver snapshot gives version evidence, never silently overwrite reports.
                state["outbox"] = [r for r in state["outbox"] if r["snapshot_version"] == version]
            self.state_store.enqueue(state, {"source": self.source, "snapshot_version": version,
                                             "result": observation.result,
                                             "reason_code": observation.reason_code})
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

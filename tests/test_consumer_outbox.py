import json

import pytest
from test_consumer import seed
from test_consumer import setup as setup

from cookie_http_seeder.client import ClientError
from cookie_http_seeder.consumer import ConsumerBusy, LoginRequired, Response
from cookie_http_seeder.consumer_state import ConsumerState


def test_v1_migration_preserves_pause_identity_and_budget(setup):
    state, client, consumer = setup
    version = seed(state)["snapshot_version"]
    path = consumer.state_path
    path.parent.mkdir(parents=True)
    original = {"schema_version": 1, "identity": consumer.identity, "blocked_version": version,
                "pending_feedback": {"source": "site", "snapshot_version": version,
                                     "result": "invalid", "reason_code": "login_required"},
                "feedback_attempts": 2}
    path.write_text(json.dumps(original))
    before = path.read_bytes()
    assert consumer.status()["paused"] is True
    assert path.read_bytes() == before
    assert consumer.flush_feedback() == "sent"
    migrated = json.loads(path.read_text())
    assert migrated["schema_version"] == 2
    assert migrated["blocked_version"] == version
    assert migrated["identity"] == consumer.identity
    with pytest.raises(LoginRequired):
        consumer.request("https://example.test/me", lambda *_: pytest.fail("paused"))


@pytest.mark.parametrize("code", ["unauthorized", "forbidden", "invalid_request",
                                  "invalid_response", "redirect_blocked", "upgrade_required"])
def test_terminal_feedback_failure_is_durable_blocked(setup, code):
    state, client, consumer = setup
    seed(state)
    calls = []
    def fail(*_):
        calls.append(True)
        raise ClientError(code)
    client.report = fail
    result = consumer.request("https://example.test/me", lambda *_: Response(200, "LOGIN_REQUIRED"))
    assert result.feedback_status == "blocked"
    assert consumer.flush_feedback() == "blocked"
    assert len(calls) == 1
    assert consumer.status()["outbox"][0]["attempts"] == 1


def test_outbox_coalesces_without_resetting_budget_and_never_overwrites_invalid(setup):
    state, client, consumer = setup
    seed(state)
    client.fail = True
    consumer.request("https://example.test/me", lambda *_: Response(503, "unavailable"))
    consumer.request("https://example.test/me", lambda *_: Response(200, "LOGIN_REQUIRED"))
    assert consumer.status()["outbox"][0]["result"] == "invalid"
    assert consumer.status()["outbox"][0]["attempts"] == 2
    with pytest.raises(LoginRequired):
        consumer.request("https://example.test/me", lambda *_: pytest.fail("no repeat"))
    assert consumer.flush_feedback() == "exhausted"
    assert consumer.status()["outbox"][0]["result"] == "invalid"


def test_outbox_limit_and_one_attempt_per_flush(setup):
    _, client, consumer = setup
    store = ConsumerState(consumer.state_path, consumer.identity, "site")
    data = store.load()
    for i in range(16):
        store.enqueue(data, {"source": "site", "snapshot_version": f"{i:032x}",
                             "result": "invalid", "reason_code": "login_required"})
    with pytest.raises(ValueError, match="full"):
        store.enqueue(data, {"source": "site", "snapshot_version": "f" * 32,
                             "result": "valid", "reason_code": "logged_in"})
    store.save(data)
    calls = []
    client.report = lambda *args: calls.append(args)
    assert consumer.flush_feedback() == "sent"
    assert len(calls) == 1
    assert len(consumer.status()["outbox"]) == 15


def test_disk_failure_prevents_send_and_lock_serializes(setup, monkeypatch):
    state, _, consumer = setup
    seed(state)
    with consumer._locked():
        with pytest.raises(ConsumerBusy):
            consumer.flush_feedback()
    def fail(*_):
        raise OSError("synthetic disk failure")
    monkeypatch.setattr(consumer, "_save", fail)
    with pytest.raises(OSError):
        consumer.request("https://example.test/me", lambda *_: Response(200, "LOGIN_REQUIRED"))

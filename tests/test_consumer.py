"""Exercise consumer state with synthetic snapshots; never contact a website."""
from __future__ import annotations

import json
import uuid

import pytest

from cookie_http_seeder.client import ClientError
from cookie_http_seeder.consumer import (
    CookieConsumer,
    CredentialsUnavailable,
    LoginRequired,
    Observation,
    Response,
    ResponseRules,
)
from cookie_http_seeder.receiver import ReceiverState
from cookie_http_seeder.sync_state import SnapshotConflict


@pytest.fixture
def setup(tmp_path):
    root = ReceiverState({"site": {"domains": ["example.test"],
                                  "target_url": "https://example.test/me"}}, tmp_path / "data")
    state = root.for_sender("home-pc")

    class Client:
        sender_tag = "home-pc"
        endpoint = "http://127.0.0.1:18765"
        fail = False

        def report(self, source, version, result, reason):
            if self.fail:
                raise ClientError("network_error")
            try:
                return state.report({"source": source, "snapshot_version": version,
                                     "result": result, "reason_code": reason})
            except SnapshotConflict:
                raise ClientError("stale_snapshot") from None

    rules = ResponseRules.from_document({
        "schema_version": 1, "valid_body_contains": ["ACCOUNT_OK"],
        "invalid_body_contains": ["LOGIN_REQUIRED"], "login_redirect_paths": ["/login"],
    })
    client = Client()
    consumer = CookieConsumer(source="site", data_dir=root.data_dir, client=client,
                              classify=rules, state_path=tmp_path / "consumer" / "state.json")
    return state, client, consumer


def seed(state, value="synthetic-cookie"):
    return state.ingest({"schema_version": 2, "complete": True, "source": "site",
                         "config_revision": state.revision,
                         "expected_version": state.sync.version("site"),
                         "request_id": uuid.uuid4().hex,
                         "cookies": [{"name": "sid", "value": value,
                                      "domain": "example.test", "path": "/",
                                      "hostOnly": True, "secure": True, "httpOnly": True,
                                      "session": True}] if value is not None else []})


@pytest.mark.parametrize("status,body,expected", [
    (200, "ACCOUNT_OK", ("valid", "logged_in")),
    (200, "LOGIN_REQUIRED", ("invalid", "login_required")),
    (200, "ACCOUNT_OK LOGIN_REQUIRED", ("invalid", "login_required")),
    (200, "unknown", ("error", "unexpected_response")),
    (403, "denied", ("error", "unexpected_response")),
    (429, "ACCOUNT_OK", ("error", "rate_limited")),
    (503, "ACCOUNT_OK", ("error", "unexpected_response")),
])
def test_explicit_rules_never_infer_login_from_http_status(setup, status, body, expected):
    _, _, consumer = setup
    assert consumer.classify(Response(status, body)) == Observation(*expected)


def test_json_rules_use_exact_types_and_explicit_positive_evidence():
    rules = ResponseRules.from_document({"schema_version": 1,
                                        "valid_json": [{"path": ["user", "loggedIn"],
                                                        "equals": True}],
                                        "invalid_json": [{"path": ["code"],
                                                          "equals": "SESSION_EXPIRED"}]})
    assert rules(Response(200, '{"user":{"loggedIn":true}}')).result == "valid"
    assert rules(Response(200, '{"user":{"loggedIn":1}}')).result == "error"
    assert rules(Response(200, '{"code":"SESSION_EXPIRED"}')).result == "invalid"
    assert rules(Response(200, 'not-json')).result == "error"


def test_site_json_rules_preserve_expiry_and_account_mismatch_reasons():
    rules = ResponseRules.from_document({"schema_version": 1, "invalid_json": [
        {"path": ["code"], "equals": "SESSION_EXPIRED", "reason_code": "session_expired"},
        {"path": ["account"], "equals": "wrong", "reason_code": "account_mismatch"},
    ]})
    assert rules(Response(200, '{"code":"SESSION_EXPIRED"}')) == Observation(
        "invalid", "session_expired")
    assert rules(Response(200, '{"account":"wrong"}')) == Observation("invalid", "account_mismatch")


@pytest.mark.parametrize("response", [Response(200, "ACCOUNT_OK", {1: "bad"}),
                                      Response(-1, "LOGIN_REQUIRED"),
                                      Response(200, "ACCOUNT_OK", ["bad"])])
def test_malformed_response_never_crashes_or_claims_invalid(setup, response):
    _, _, consumer = setup
    assert consumer.classify(response) == Observation("error", "unexpected_response")


def test_only_explicit_login_redirect_path_is_invalid(setup):
    _, _, consumer = setup
    assert consumer.classify(Response(302, "", {"Location": "/login?next=/me"})).result == "invalid"
    assert consumer.classify(Response(302, "", {"Location": "/loginfoo"})).result == "error"


def test_valid_response_reports_exact_used_version_and_hides_sensitive_body(setup):
    state, _, consumer = setup
    version = seed(state)["snapshot_version"]
    seen = []

    def send(url, headers):
        seen.append((url, headers))
        return Response(200, "ACCOUNT_OK sensitive-response")

    result = consumer.request("https://example.test/me", send)
    assert seen == [("https://example.test/me", {"Cookie": "sid=synthetic-cookie"})]
    assert result.observation == Observation("valid", "logged_in")
    assert result.snapshot_version == version
    assert result.feedback_status == "sent"
    assert state.status()["sources"]["site"]["validation"] == "valid"
    assert "synthetic-cookie" not in repr(result)
    assert "sensitive-response" not in repr(result)


def test_invalid_pauses_same_version_across_consumer_restart(setup):
    state, client, consumer = setup
    seed(state)
    first = consumer.request("https://example.test/me", lambda *_: Response(200, "LOGIN_REQUIRED"))
    assert first.paused
    restarted = CookieConsumer(source="site", data_dir=consumer.data_dir, client=client,
                               classify=consumer.classify, state_path=consumer.state_path)
    calls = []
    with pytest.raises(LoginRequired):
        restarted.request("https://example.test/me", lambda *args: calls.append(args))
    assert not calls
    serialized = consumer.state_path.read_text()
    assert "synthetic-cookie" not in serialized
    assert "LOGIN_REQUIRED" not in serialized


def test_new_snapshot_is_verified_before_crawl_resumes(setup):
    state, _, consumer = setup
    seed(state)
    consumer.request("https://example.test/me", lambda *_: Response(200, "LOGIN_REQUIRED"))
    seed(state, "new-synthetic-cookie")
    valid = consumer.request("https://example.test/me", lambda *_: Response(200, "ACCOUNT_OK"))
    assert not valid.paused
    assert consumer.request("https://example.test/me",
                            lambda *_: Response(200, "ACCOUNT_OK")).feedback_status == "sent"


def test_unverified_new_snapshot_does_not_clear_pause(setup):
    state, _, consumer = setup
    seed(state)
    consumer.request("https://example.test/me", lambda *_: Response(200, "LOGIN_REQUIRED"))
    seed(state, "new-synthetic-cookie")
    result = consumer.request("https://example.test/me", lambda *_: Response(503, "unknown"))
    assert result.paused
    # It may be probed again: the new version has not been confirmed invalid.
    assert not consumer.request("https://example.test/me",
                                lambda *_: Response(200, "ACCOUNT_OK")).paused


def test_feedback_for_old_version_is_discarded_and_new_snapshot_untouched(setup):
    state, _, consumer = setup
    old = seed(state)["snapshot_version"]

    def send(*_):
        seed(state, "new-synthetic-cookie")
        return Response(200, "LOGIN_REQUIRED")

    result = consumer.request("https://example.test/me", send)
    assert result.snapshot_version == old
    assert result.feedback_status == "discarded"
    assert state.status()["sources"]["site"]["validation"] == "unverified"
    assert not consumer.request("https://example.test/me",
                                lambda *_: Response(200, "ACCOUNT_OK")).paused


def test_offline_feedback_keeps_pause_and_can_be_flushed_without_repeating_request(setup):
    state, client, consumer = setup
    seed(state)
    client.fail = True
    result = consumer.request("https://example.test/me", lambda *_: Response(200, "LOGIN_REQUIRED"))
    assert result.paused and result.feedback_status == "pending"
    client.fail = False
    assert consumer.flush_feedback() == "sent"
    assert state.status()["sources"]["site"]["validation"] == "invalid"
    with pytest.raises(LoginRequired):
        consumer.request("https://example.test/me", lambda *_: pytest.fail("must remain paused"))


def test_feedback_retry_budget_is_bounded(setup):
    state, client, consumer = setup
    seed(state)
    client.fail = True
    consumer.request("https://example.test/me", lambda *_: Response(200, "LOGIN_REQUIRED"))
    assert consumer.flush_feedback() == "pending"
    assert consumer.flush_feedback() == "exhausted"
    client.fail = False
    assert consumer.flush_feedback() == "exhausted"


def test_network_failure_is_error_and_never_blocks_current_cookie(setup):
    state, _, consumer = setup
    seed(state)

    def offline(*_):
        raise TimeoutError("must-not-log-signed-url")

    result = consumer.request("https://example.test/me", offline)
    assert result.observation == Observation("error", "network_error")
    assert not result.paused
    assert "must-not-log" not in repr(result)
    assert not consumer.request("https://example.test/me",
                                lambda *_: Response(200, "ACCOUNT_OK")).paused


@pytest.mark.parametrize("cleared", [False, True])
def test_missing_or_cleared_credentials_never_send(setup, cleared):
    state, _, consumer = setup
    if cleared:
        seed(state, None)
    with pytest.raises(CredentialsUnavailable):
        consumer.request("https://example.test/me", lambda *_: pytest.fail("no request"))


def test_out_of_scope_request_never_sends(setup):
    state, _, consumer = setup
    seed(state)
    with pytest.raises(ValueError):
        consumer.request("https://other.test/me", lambda *_: pytest.fail("no request"))


def test_wait_has_deadline_and_requires_nonempty_new_version(setup):
    state, _, consumer = setup
    seed(state)
    consumer.request("https://example.test/me", lambda *_: Response(200, "LOGIN_REQUIRED"))
    now = [0.0]
    consumer.clock = lambda: now[0]
    consumer.sleep = lambda delay: now.__setitem__(0, now[0] + delay)
    assert not consumer.wait_for_update("https://example.test/me", timeout=2, poll_interval=1)
    assert now[0] == 2
    seed(state, None)
    assert not consumer.wait_for_update("https://example.test/me", timeout=0, poll_interval=1)
    seed(state, "replacement")
    assert consumer.wait_for_update("https://example.test/me", timeout=0, poll_interval=1)


def test_state_does_not_need_writable_snapshot_mount(setup, tmp_path, monkeypatch):
    state, client, consumer = setup
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    instance = CookieConsumer(source="site", data_dir=state.notification_dir, client=client,
                              classify=consumer.classify)
    assert instance.state_path.is_relative_to(tmp_path / "xdg")
    assert not instance.state_path.is_relative_to(state.notification_dir)


@pytest.mark.parametrize("raw", [
    {"schema_version": 1},
    {"schema_version": 1, "valid_body_contains": [""]},
    {"schema_version": 1, "valid_json": [{"path": [], "equals": True}]},
    {"schema_version": 1, "token": "do-not-accept"},
])
def test_invalid_classifier_configuration_is_rejected(raw):
    with pytest.raises(ValueError):
        ResponseRules.from_document(raw)


def test_malformed_persisted_consumer_state_fails_closed(setup):
    state, _, consumer = setup
    seed(state)
    consumer.state_path.parent.mkdir(parents=True, exist_ok=True)
    consumer.state_path.write_text(json.dumps({"schema_version": 999}))
    with pytest.raises(ValueError):
        consumer.request("https://example.test/me", lambda *_: pytest.fail("no request"))


@pytest.mark.parametrize("mutation", ["missing_blocked", "missing_pending", "extra_field"])
def test_incomplete_or_extra_consumer_state_stops_before_sending(setup, mutation):
    state, _, consumer = setup
    seed(state)
    saved = consumer._load()
    if mutation == "missing_blocked":
        del saved["blocked_version"]
    elif mutation == "missing_pending":
        del saved["pending_feedback"]
    else:
        saved["unexpected"] = "synthetic-should-not-persist"
    consumer.state_path.parent.mkdir(parents=True, exist_ok=True)
    consumer.state_path.write_text(json.dumps(saved))
    with pytest.raises(ValueError, match="invalid consumer state"):
        consumer.request("https://example.test/me", lambda *_: pytest.fail("must not send"))


def test_snapshot_read_failure_after_invalid_response_keeps_durable_pause(setup, monkeypatch):
    import cookie_http_seeder.consumer as module
    state, client, consumer = setup
    version = seed(state)["snapshot_version"]
    client.fail = True
    with monkeypatch.context() as patch:
        def unavailable(*args, **kwargs):
            raise OSError("synthetic temporary read failure")
        patch.setattr(module, "read_snapshot", unavailable)
        result = consumer.request("https://example.test/me",
                                  lambda *_: Response(200, "LOGIN_REQUIRED"))
    assert result.paused and result.feedback_status == "pending"
    assert consumer._load()["blocked_version"] == version
    with pytest.raises(LoginRequired):
        consumer.request("https://example.test/me", lambda *_: pytest.fail("must remain paused"))


@pytest.mark.parametrize("field", ["blocked_version", "pending_feedback"])
def test_numeric_snapshot_version_in_consumer_state_fails_closed(setup, field):
    state, _, consumer = setup
    seed(state)
    saved = consumer._load()
    numeric_version = int("1" * 32)
    if field == "blocked_version":
        saved[field] = numeric_version
    else:
        saved[field] = {"source": "site", "snapshot_version": numeric_version,
                        "result": "invalid", "reason_code": "login_required"}
    consumer.state_path.parent.mkdir(parents=True, exist_ok=True)
    consumer.state_path.write_text(json.dumps(saved))
    with pytest.raises(ValueError):
        consumer.request("https://example.test/me", lambda *_: pytest.fail("must not send"))

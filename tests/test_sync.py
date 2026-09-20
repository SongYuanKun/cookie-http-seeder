from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from test_receiver import SPEC, TOKEN, cookie, push_body
from test_receiver import receiver as receiver

from cookie_http_seeder.client import ClientError, ReceiverClient, receiver_origin
from cookie_http_seeder.diagnostics import diagnose
from cookie_http_seeder.receiver import ReceiverState
from cookie_http_seeder.store import load_request_credentials, read_snapshot
from cookie_http_seeder.sync_state import SnapshotConflict


def body(state, cookies=None, **extra):
    return {"schema_version": 2, "complete": True, "source": "site",
            "cookies": [cookie()] if cookies is None else cookies,
            "config_revision": state.revision, "expected_version": state.sync.version("site"),
            "request_id": str(uuid.uuid4()), **extra}


def feedback(version, result="valid", reason="logged_in"):
    return {"source": "site", "snapshot_version": version, "result": result, "reason_code": reason}


def test_requires_precondition_before_writing(receiver):
    req, data, _ = receiver
    payload = push_body(req)
    del payload["expected_version"]
    assert req("POST", "/v2/cookies", payload)[0] == 428
    assert not (data / "site-cookies.json").exists()


@pytest.mark.parametrize("change", [{"expected_version": []}, {"expected_version": "bad"},
                                    {"request_id": "short"}, {"request_id": []},
                                    {"extra": "do-not-store-this"}])
def test_invalid_conditional_fields(receiver, change):
    req, _, _ = receiver
    assert req("POST", "/v2/cookies", push_body(req, **change))[0] == 400


def test_cas_rejects_old_payload(receiver):
    req, data, _ = receiver
    first, stale = push_body(req), push_body(req, cookies=[cookie(value="old")])
    assert req("POST", "/v2/cookies", first)[0] == 200
    code, result = req("POST", "/v2/cookies", stale)
    assert code == 409 and result["error"] == "snapshot_conflict"
    assert read_snapshot("site", data_dir=data)["cookies"][0]["value"] == "test-secret-value"


def test_replay_and_noop_do_not_rewrite_cookie_file(receiver):
    req, data, _ = receiver
    payload = push_body(req)
    first = req("POST", "/v2/cookies", payload)[1]
    path = data / "site-cookies.json"
    before = path.read_bytes(), path.stat().st_mtime_ns
    replay = req("POST", "/v2/cookies", payload)[1]
    assert replay["replayed"] and replay["unchanged"]
    noop = req("POST", "/v2/cookies", push_body(req))[1]
    assert noop["unchanged"] and noop["snapshot_version"] == first["snapshot_version"]
    assert before == (path.read_bytes(), path.stat().st_mtime_ns)


def test_request_id_cannot_be_reused_with_different_cookie_content(receiver):
    req, _, _ = receiver
    payload = push_body(req)
    req("POST", "/v2/cookies", payload)
    payload["cookies"] = [cookie(value="different")]
    assert req("POST", "/v2/cookies", payload)[0] == 400


def test_version_survives_restart_and_old_config_does_not(tmp_path):
    state = ReceiverState({"site": SPEC}, tmp_path)
    result = state.ingest(body(state))
    restarted = ReceiverState({"site": SPEC}, tmp_path)
    assert restarted.revision != state.revision
    assert restarted.sync.version("site") == result["snapshot_version"]
    assert restarted.ingest(body(restarted))["unchanged"]


def test_parallel_writers_exactly_one_cas_wins(tmp_path):
    state = ReceiverState({"site": SPEC}, tmp_path)
    payloads = [body(state, [cookie(value=str(i))]) for i in range(2)]
    barrier = threading.Barrier(2)

    def write(payload):
        barrier.wait()
        try:
            state.ingest(payload)
            return "written"
        except SnapshotConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(write, payloads)) == ["conflict", "written"]


def test_metadata_failure_after_write_can_be_safely_retried(tmp_path):
    state = ReceiverState({"site": SPEC}, tmp_path)
    payload = body(state)
    with patch("cookie_http_seeder.sync_state.atomic_write_text", side_effect=OSError("disk")):
        with pytest.raises(OSError):
            state.ingest(payload)
    version = state.sync.version("site")
    result = state.ingest(payload)
    assert result["replayed"] and result["snapshot_version"] == version


def test_feedback_bound_to_snapshot_and_redacted(receiver):
    req, _, _ = receiver
    first = req("POST", "/v2/cookies", push_body(req))[1]
    assert req("POST", "/v1/feedback", feedback(first["snapshot_version"]))[0] == 200
    status = req("GET", "/v1/status")[1]["sources"]["site"]
    assert status["validation"] == "valid"
    assert status["validationDetail"]["origin"] == "crawler_report"
    encoded = json.dumps(status)
    assert TOKEN not in encoded and "test-secret-value" not in encoded
    newer = req("POST", "/v2/cookies", push_body(req, cookies=[cookie(value="new")]))[1]
    assert newer["snapshot_version"] != first["snapshot_version"]
    old_report = feedback(first["snapshot_version"], "invalid", "session_expired")
    assert req("POST", "/v1/feedback", old_report)[0] == 409
    assert req("GET", "/v1/status")[1]["sources"]["site"]["validation"] == "unverified"


@pytest.mark.parametrize("change", [
    {"reason_code": "Cookie: secret"}, {"result": "valid", "reason_code": "session_expired"},
    {"body": "secret"}, {"snapshot_version": None}, {"result": []},
])
def test_feedback_rejects_unstructured_or_inconsistent_data(receiver, change):
    req, _, _ = receiver
    version = req("POST", "/v2/cookies", push_body(req))[1]["snapshot_version"]
    assert req("POST", "/v1/feedback", {**feedback(version), **change})[0] == 400


def test_empty_snapshot_cannot_be_reported_valid(receiver):
    req, _, _ = receiver
    version = req("POST", "/v2/cookies", push_body(req, cookies=[]))[1]["snapshot_version"]
    assert req("POST", "/v1/feedback", feedback(version))[0] == 400


def test_receipt_and_validation_have_independent_clocks(tmp_path):
    state = ReceiverState({"site": SPEC}, tmp_path)
    now = [1_800_000_000.0]
    state.sync.clock = lambda: now[0]
    result = state.ingest(body(state))
    state.report(feedback(result["snapshot_version"]))
    now[0] += 90_000
    before = state.sync.status("site")
    assert before["freshness"] == "stale" and before["validation"] == "expired"
    same = state.ingest(body(state))
    after = state.sync.status("site")
    assert same["unchanged"] and after["ageSeconds"] == 0 and after["freshness"] == "fresh"
    assert after["validation"] == "expired"  # transfer is not a new login check


def test_invalid_notification_cooldown_persists_and_noop_keeps_feedback(tmp_path):
    state = ReceiverState({"site": SPEC}, tmp_path)
    now = [1_800_000_000.0]
    state.sync.clock = lambda: now[0]
    version = state.ingest(body(state))["snapshot_version"]
    report = feedback(version, "invalid", "session_expired")
    assert state.sync.feedback(report, notify=True)[1]
    assert not state.sync.feedback(report, notify=True)[1]
    restarted = ReceiverState({"site": SPEC}, tmp_path)
    restarted.sync.clock = lambda: now[0]
    assert not restarted.sync.feedback(report, notify=True)[1]
    assert restarted.ingest(body(restarted))["unchanged"]
    assert restarted.sync.status("site")["validation"] == "invalid"
    now[0] += 901
    assert restarted.sync.feedback(report, notify=True)[1]


def test_clear_pauses_and_invalidates_feedback(receiver):
    req, _, _ = receiver
    payload = push_body(req)
    version = req("POST", "/v2/cookies", payload)[1]["snapshot_version"]
    req("DELETE", "/v2/cookies/site", headers={"If-Match": payload["config_revision"]})
    assert req("POST", "/v1/feedback", feedback(version))[0] == 400
    result = req("GET", "/v1/status")[1]["sources"]["site"]
    assert result["freshness"] == "cleared" and result["validation"] == "unverified"


def test_operational_metadata_and_status_have_no_cookies(tmp_path):
    state = ReceiverState({"site": SPEC}, tmp_path)
    state.ingest(body(state))
    for data in [(tmp_path / ".site-sync.json").read_text(), json.dumps(state.status())]:
        assert "test-secret-value" not in data and TOKEN not in data
        assert "cookie_header" not in data


def test_credentials_header_and_feedback_version_are_from_single_read(tmp_path):
    state = ReceiverState({"site": SPEC}, tmp_path)
    version = state.ingest(body(state))["snapshot_version"]
    with patch("cookie_http_seeder.store.read_snapshot", wraps=read_snapshot) as read:
        result = load_request_credentials(
            source="site", url="https://example.com/", data_dir=tmp_path)
    assert read.call_count == 1
    assert result == {"cookie_header": "sid=test-secret-value", "snapshot_version": version}


@pytest.mark.parametrize("url", ["http://192.0.2.1:18765", "https://user:secret@example.com/",
                                  "https://receiver.test/path",
                                  "https://receiver.test/?token=secret"])
def test_client_rejects_unsafe_endpoint(url):
    with pytest.raises(ValueError):
        receiver_origin(url)


def test_client_http_roundtrip_and_auth_error(receiver):
    req, _, port = receiver
    version = req("POST", "/v2/cookies", push_body(req))[1]["snapshot_version"]
    client = ReceiverClient(f"http://127.0.0.1:{port}", TOKEN)
    assert client.report("site", version, "valid", "logged_in")["validation"] == "valid"
    with pytest.raises(ClientError, match="unauthorized"):
        ReceiverClient(f"http://127.0.0.1:{port}", "wrong-token-0123456789").request("/v1/status")


def test_doctor_missing_directory_does_not_create_it(tmp_path, monkeypatch):
    monkeypatch.delenv("COOKIE_HTTP_SEEDER_TOKEN", raising=False)
    path = tmp_path / "missing"
    assert not diagnose(path, local_only=True)["ok"]
    assert not path.exists()


def test_doctor_live_diagnostics_redact_credentials(receiver, monkeypatch):
    monkeypatch.delenv("COOKIE_HTTP_SEEDER_TOKEN", raising=False)
    req, data, port = receiver
    (data / "cookie-receiver.token").write_text(TOKEN)
    req("POST", "/v2/cookies", push_body(req))
    result = diagnose(data, endpoint=f"http://127.0.0.1:{port}")
    text = json.dumps(result)
    assert TOKEN not in text and "test-secret-value" not in text
    assert result["ok"] and any(c["code"] == "authenticated" for c in result["checks"])
    assert not list(data.glob(".doctor-*"))


def test_corrupt_metadata_is_visible_and_never_marks_valid(tmp_path):
    state = ReceiverState({"site": SPEC}, tmp_path)
    state.ingest(body(state))
    (tmp_path / ".site-sync.json").write_text("broken")
    status = state.status()["sources"]["site"]
    assert status["validation"] == "unverified" and status["syncError"] == "unreadable_sync_state"

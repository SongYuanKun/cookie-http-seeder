import json

import pytest
from test_monitoring import push
from test_receiver import cookie, push_body
from test_receiver import receiver as receiver

from cookie_http_seeder.incidents import IncidentStore
from cookie_http_seeder.receiver import ReceiverState
from cookie_http_seeder.sync_state import SnapshotConflict


def invalid(state, version):
    return state.report({"source": "site", "snapshot_version": version,
                         "result": "invalid", "reason_code": "login_required"})


def valid(state, version):
    return state.report({"source": "site", "snapshot_version": version,
                         "result": "valid", "reason_code": "logged_in"})


def test_login_recovery_requires_new_snapshot_actual_validation(tmp_path):
    state = ReceiverState({"site": {"domains": ["example.com"]}}, tmp_path)
    old = push(state)["snapshot_version"]
    invalid(state, old)
    first = state.incidents.list()["active"][0]
    invalid(state, old)
    assert len(state.incidents.list()["active"]) == 1
    state.incidents.act("site", first["incident_id"], "acknowledge")
    valid(state, old)
    assert state.incidents.list()["active"][0]["status"] == "acknowledged"
    new = state.ingest({"schema_version": 2, "complete": True, "source": "site",
                        "config_revision": state.revision, "expected_version": old,
                        "request_id": "synthetic-recovery-new", "cookies": [cookie(value="new")]})
    assert state.incidents.list()["active"][0]["status"] == "awaiting_validation"
    with pytest.raises(SnapshotConflict):
        valid(state, old)
    assert state.incidents.list()["active"][0]["status"] == "awaiting_validation"
    valid(state, new["snapshot_version"])
    assert state.incidents.list()["active"] == []
    assert state.incidents.list()["history"][-1]["status"] == "resolved"
    invalid(state, new["snapshot_version"])
    assert state.incidents.list()["active"][0]["incident_id"] != first["incident_id"]


def test_actions_persist_and_old_id_rejected(tmp_path):
    now = [1000]
    store = IncidentStore(tmp_path, clock=lambda: now[0])
    store.observe("site", {"snapshot_version": "a" * 32, "validation": "invalid"})
    event = store.list()["active"][0]
    store.act("site", event["incident_id"], "snooze", 60)
    assert not store.may_notify("site", "login_invalid")
    reloaded = IncidentStore(tmp_path, clock=lambda: now[0])
    assert reloaded.list()["active"][0]["status"] == "snoozed"
    assert (tmp_path / ".incidents.json").stat().st_mode & 0o777 == 0o600
    now[0] += 61
    assert reloaded.may_notify("site", "login_invalid")
    reloaded.act("site", event["incident_id"], "acknowledge")
    assert not reloaded.may_notify("site", "login_invalid")
    reloaded.act("site", event["incident_id"], "reopen")
    assert reloaded.may_notify("site", "login_invalid")
    with pytest.raises(ValueError):
        reloaded.act("site", "f" * 32, "acknowledge")
    for value in (True, 59, 86401, None):
        with pytest.raises(ValueError):
            reloaded.act("site", event["incident_id"], "snooze", value)


def test_stale_expired_independent_history_bounded_and_read_only(tmp_path):
    store = IncidentStore(tmp_path, clock=lambda: 1000)
    for i in range(105):
        store.observe("site", {"snapshot_version": f"{i:032x}",
                               "validation": "expired", "freshness": "stale"})
        kinds = {e["kind"] for e in store.list()["active"]}
        assert kinds == {"snapshot_stale", "validation_expired"}
        store.observe("site", {"snapshot_version": f"{i:032x}", "validation": "valid",
                               "freshness": "fresh"})
    before = (tmp_path / ".incidents.json").read_bytes()
    assert len(store.list()["history"]) == 100
    assert before == (tmp_path / ".incidents.json").read_bytes()
    assert "cookie" not in json.dumps(store.list())


def test_read_status_no_incident_write_and_tag_isolation(tmp_path):
    root = ReceiverState({"site": {"domains": ["example.com"]}}, tmp_path)
    child = root.for_sender("work")
    version = push(child)["snapshot_version"]
    invalid(child, version)
    assert root.incidents.list()["active"] == []
    path = child.data_dir / ".incidents.json"
    before = path.read_bytes()
    assert len(child.status()["incidents"]["active"]) == 1
    assert path.read_bytes() == before
    assert "source_incidents" in root.document()["capabilities"]


def test_incident_api_validates_identity_actions_and_extra_fields(receiver):
    req, directory, _ = receiver
    version = req("POST", "/v2/cookies", push_body(req))[1]["snapshot_version"]
    assert req("POST", "/v1/feedback", {"source": "site", "snapshot_version": version,
              "result": "invalid", "reason_code": "login_required"})[0] == 200
    event = req("GET", "/v1/incidents")[1]["active"][0]
    action = {"incident_id": event["incident_id"], "action": "acknowledge"}
    assert req("POST", "/v1/incidents/site", action, token=None)[0] == 401
    assert req("POST", "/v1/incidents/other", action)[0] == 400
    assert req("POST", "/v1/incidents/site", {**action, "cookie": "secret"})[0] == 400
    assert req("POST", "/v1/incidents/site", action)[1]["active"][0]["status"] == "acknowledged"
    before = (directory / ".incidents.json").read_bytes()
    assert req("GET", "/v1/incidents")[0] == 200
    assert before == (directory / ".incidents.json").read_bytes()


def test_scan_tracks_incidents_without_notifications_and_ack_suppresses(tmp_path):
    from cookie_http_seeder.monitoring import StaleMonitor
    state = ReceiverState({"site": {"domains": ["example.com"], "stale_after_seconds": 60}},
                          tmp_path)
    now = [2000000]
    state.sync.clock = lambda: now[0]
    push(state)
    now[0] += 61
    calls = []
    StaleMonitor(state, enabled=False, notifier=lambda **kw: calls.append(kw)).scan()
    event = state.incidents.list()["active"][0]
    assert event["kind"] == "snapshot_stale"
    assert not calls
    state.incidents.act("site", event["incident_id"], "acknowledge")
    (tmp_path / "feishu-webhook").write_text("synthetic")
    StaleMonitor(state, enabled=True, notifier=lambda **kw: calls.append(kw)).scan()
    assert not calls

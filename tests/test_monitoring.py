"""Thresholds and stale scans use synthetic state and injected notifications."""
from __future__ import annotations

import json
import uuid

import pytest
from test_receiver import TOKEN, cookie
from test_receiver import receiver as receiver

from cookie_http_seeder.cli import main
from cookie_http_seeder.client import ReceiverClient
from cookie_http_seeder.cookies import normalize_sources
from cookie_http_seeder.monitoring import StaleMonitor
from cookie_http_seeder.receiver import ReceiverState
from cookie_http_seeder.store import read_snapshot


def push(state):
    return state.ingest({"schema_version": 2, "complete": True, "source": "site",
                         "config_revision": state.revision,
                         "expected_version": state.sync.version("site"),
                         "request_id": uuid.uuid4().hex, "cookies": [cookie()]})


@pytest.mark.parametrize("value", [True, "300", 59, 2592001, 300.5, None])
@pytest.mark.parametrize("field", ["stale_after_seconds", "validation_ttl_seconds"])
def test_invalid_source_threshold_is_rejected(value, field):
    with pytest.raises(ValueError):
        normalize_sources({"site": {"domains": ["example.com"], field: value}})


def test_source_thresholds_are_optional_and_roundtrip():
    assert normalize_sources({"site": {"domains": ["example.com"]}})["site"] == {
        "label": "site", "domains": ["example.com"], "target_url": "", "enabled": True}
    spec = normalize_sources({"site": {"domains": ["example.com"],
                                       "stale_after_seconds": 300,
                                       "validation_ttl_seconds": 600}})["site"]
    assert spec["stale_after_seconds"] == 300 and spec["validation_ttl_seconds"] == 600


def test_freshness_and_feedback_ttl_are_independent(tmp_path):
    state = ReceiverState({"site": {"domains": ["example.com"], "stale_after_seconds": 300,
                                   "validation_ttl_seconds": 600}}, tmp_path)
    clock = [2_000_000.0]
    state.sync.clock = lambda: clock[0]
    version = push(state)["snapshot_version"]
    state.report({"source": "site", "snapshot_version": version,
                  "result": "valid", "reason_code": "logged_in"})
    clock[0] += 301
    status = state.status()["sources"]["site"]
    assert status["freshness"] == "stale" and status["validation"] == "valid"
    assert status["validationTtlSeconds"] == 600
    clock[0] += 300
    assert state.status()["sources"]["site"]["validation"] == "expired"


def test_threshold_edit_preserves_snapshot_and_validation(tmp_path):
    state = ReceiverState({"site": {"domains": ["example.com"]}}, tmp_path)
    version = push(state)["snapshot_version"]
    state.report({"source": "site", "snapshot_version": version,
                  "result": "valid", "reason_code": "logged_in"})
    source = {**state.sources["site"], "stale_after_seconds": 300}
    state.replace_sources({"site": source}, state.revision)
    assert read_snapshot("site", data_dir=tmp_path)["snapshot_version"] == version
    assert state.status()["sources"]["site"]["validation"] == "valid"


def test_sender_summary_contains_only_metadata_and_does_not_create_namespace(tmp_path):
    root = ReceiverState({"site": {"domains": ["example.com"]}}, tmp_path)
    home = root.for_sender("home-pc")
    push(home)
    directory = tmp_path / "senders" / "broken-pc"
    directory.mkdir()
    (directory / "sources.json").write_text("{malformed")
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    summary = root.sender_statuses()
    assert set(summary["senders"]) == {"default", "home-pc", "broken-pc"}
    assert summary["senders"]["home-pc"]["sources"]["site"]["present"]
    assert summary["senders"]["broken-pc"]["error"] == "unreadable_sender_state"
    assert "test-secret-value" not in json.dumps(summary)
    assert "cookie_header" not in json.dumps(summary)
    assert before == sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))


def test_sender_summary_http_requires_auth_and_does_not_initialize_requested_label(receiver):
    req, data_dir, port = receiver
    assert req("GET", "/v1/senders", token="wrong")[0] == 401
    code, summary = req("GET", "/v1/senders", headers={"X-Sender-Tag": "unknown-pc"})
    assert code == 200 and set(summary["senders"]) == {"default"}
    assert not (data_dir / "senders").exists()
    client = ReceiverClient(f"http://127.0.0.1:{port}", TOKEN)
    assert set(client.request("/v1/senders")["senders"]) == {"default"}


def test_local_cli_summary_includes_all_tags_without_credentials(tmp_path, capsys):
    root = ReceiverState({"site": {"domains": ["example.com"]}}, tmp_path)
    push(root.for_sender("home-pc"))
    # CLI default config must match this receiver's own sources.
    (tmp_path / "sources.json").write_text(json.dumps({"sources": root.sources}))
    assert main(["--data-dir", str(tmp_path), "status", "--all-senders", "--json"]) == 0
    output = capsys.readouterr().out
    assert set(json.loads(output)["senders"]) == {"default", "home-pc"}
    assert "test-secret-value" not in output


def test_stale_monitor_notifies_once_then_observes_persisted_cooldown(tmp_path):
    root = ReceiverState({"site": {"domains": ["example.com"],
                                  "stale_after_seconds": 60}}, tmp_path)
    state = root.for_sender("home-pc")
    clock = [2_000_000.0]
    state.sync.clock = lambda: clock[0]
    push(state)
    hook = tmp_path / "feishu-webhook"
    hook.write_text("synthetic-notifier-config")
    hook.chmod(0o600)
    notifications = []

    def notify(**kwargs):
        notifications.append(kwargs["reason"])
        return "sent"

    monitor = StaleMonitor(root, enabled=True, notifier=notify)
    assert monitor.scan() == []
    clock[0] += 61
    assert len(monitor.scan()) == 1
    assert notifications == ["home-pc/site: snapshot_stale"]
    assert monitor.scan() == []
    restarted = ReceiverState(root.sources, tmp_path)
    restarted.for_sender("home-pc").sync.clock = lambda: clock[0]
    assert StaleMonitor(restarted, enabled=True, notifier=notify).scan() == []
    clock[0] += 900
    assert len(monitor.scan()) == 1
    assert len(notifications) == 2


def test_monitor_skips_paused_and_missing_sources_and_no_webhook(tmp_path):
    root = ReceiverState({"site": {"domains": ["example.com"],
                                  "stale_after_seconds": 60}}, tmp_path)
    clock = [2_000_000.0]
    root.sync.clock = lambda: clock[0]
    calls = []
    monitor = StaleMonitor(root, enabled=True, notifier=lambda **kw: calls.append(kw))
    assert monitor.scan() == []
    push(root)
    clock[0] += 61
    assert monitor.scan() == []
    meta = json.loads((tmp_path / ".site-sync.json").read_text())
    assert meta.get("lastStaleNotifyAt") is None
    (tmp_path / "feishu-webhook").write_text("synthetic")
    root.sources["site"]["enabled"] = False
    assert monitor.scan() == [] and not calls
    root.sources["site"]["enabled"] = True
    assert StaleMonitor(root, enabled=False, notifier=lambda **kw: calls.append(kw)).scan() == []
    assert not calls


def test_monitor_handles_failed_notification_without_tight_loop(tmp_path):
    root = ReceiverState({"site": {"domains": ["example.com"],
                                  "stale_after_seconds": 60}}, tmp_path)
    clock = [2_000_000.0]
    root.sync.clock = lambda: clock[0]
    push(root)
    (tmp_path / "feishu-webhook").write_text("synthetic")
    clock[0] += 61
    monitor = StaleMonitor(root, enabled=True, notifier=lambda **_: "failed: synthetic")
    assert monitor.scan()[0]["notification"] == "failed"
    assert monitor.scan() == []

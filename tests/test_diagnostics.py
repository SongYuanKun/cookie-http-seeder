import json
import sys
from types import SimpleNamespace

import pytest
from test_receiver import TOKEN, push_body
from test_receiver import receiver as receiver

from cookie_http_seeder import __version__, diagnostics
from cookie_http_seeder.time_display import display_times


def distribution(version, metadata_file):
    return SimpleNamespace(metadata={"Name": "cookie-http-seeder"}, version=version,
                           files=[metadata_file])


def test_doctor_separates_running_code_from_installed_metadata_and_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics.metadata, "distributions", lambda: iter([
        distribution("0.5.0", "cookie_http_seeder.egg-info/PKG-INFO"),
        distribution("0.4.0", "cookie_http_seeder-0.4.0.dist-info/METADATA"),
    ]))
    result = diagnostics.diagnose(tmp_path, local_only=True)
    assert result["runtime_version"] == __version__
    assert result["installed_version"] == "0.4.0"
    assert result["python_version"] == ".".join(map(str, sys.version_info[:3]))
    assert "use_installed_package" in result["next_steps"]


def test_checkout_metadata_alone_is_not_an_installation(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics.metadata, "distributions", lambda: iter([
        distribution("0.5.0", "cookie_http_seeder.egg-info/PKG-INFO"),
    ]))
    result = diagnostics.diagnose(tmp_path, local_only=True)
    assert result["installed_version"] is None
    assert "install_package" in result["next_steps"]


def test_doctor_exposes_safe_health_incidents_capabilities_and_thresholds(receiver, monkeypatch):
    monkeypatch.delenv("COOKIE_HTTP_SEEDER_TOKEN", raising=False)
    req, data, port = receiver
    (data / "cookie-receiver.token").write_text(TOKEN)
    sources = req("GET", "/v1/sources")[1]["sources"]
    (data / "sources.json").write_text(json.dumps({"sources": sources}))
    version = req("POST", "/v2/cookies", push_body(req))[1]["snapshot_version"]
    req("POST", "/v1/feedback", {"source": "site", "snapshot_version": version,
                                  "result": "invalid", "reason_code": "login_required"})
    assert req("POST", "/v1/client-health", {
        "schema_version": 1, "client_version": "0.5.0", "interval_minutes": 15,
        "sources": {"site": {"approved": True, "permission_granted": True,
                             "sync_phase": "succeeded", "error_code": "none",
                             "last_success_at": None}},
    })[0] == 200
    result = diagnostics.diagnose(data, endpoint=f"http://127.0.0.1:{port}")
    assert result["receiver_version"] == "0.5.0"
    assert set(result["capabilities"]) == diagnostics.CAPABILITIES
    assert result["clientHealth"]["state"] == "recent"
    assert result["incidents"]["active"][0]["kind"] == "login_invalid"
    assert result["sourceThresholds"]["site"] == {
        "stale_after_seconds": 86400, "validation_ttl_seconds": 86400,
    }
    assert "relogin" in result["next_steps"]
    assert TOKEN not in json.dumps(result) and "test-secret-value" not in json.dumps(result)
    assert set(result["next_steps"]) <= diagnostics.NEXT_STEPS


@pytest.mark.parametrize("malformed", [False, True])
def test_untrusted_diagnostic_fields_are_not_returned(tmp_path, monkeypatch, malformed):
    (tmp_path / "cookie-receiver.token").write_text(TOKEN)
    (tmp_path / "sources.json").write_text(json.dumps({"sources": {
        "site": {"domains": ["example.com"]}}}))

    class UntrustedReceiver:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, path):
            return {"ok": True, "receiver_version": "synthetic-secret",
                    "capabilities": ["synthetic-secret", "client_health", "source_incidents"],
                    "sources": {}, "clientHealth": {"state": [] if malformed
                                                      else "synthetic-secret"},
                    "incidents": {"ok": True, "active": [{"source": "site",
                        "incident_id": "a" * 32, "kind": {} if malformed
                        else "synthetic-secret", "status": "open"}],
                        "history": [], "body": "synthetic-secret"}}

    monkeypatch.setattr(diagnostics, "ReceiverClient", UntrustedReceiver)
    result = diagnostics.diagnose(tmp_path)
    assert "synthetic-secret" not in json.dumps(result)
    assert result["receiver_version"] is None
    assert result["clientHealth"]["state"] == "unknown"
    assert result["incidents"]["active"] == []
    assert result["capabilities"] == ["client_health", "source_incidents"]
    assert set(result["next_steps"]) <= diagnostics.NEXT_STEPS


def test_health_and_incident_times_have_local_display_without_changing_machine_output():
    timestamp = "2026-10-01T00:00:00Z"
    raw = {"receivedAt": timestamp, "sources": {"site": {"last_success_at": timestamp}},
           "incidents": {"active": [{"opened_at": timestamp, "updated_at": timestamp,
                                     "snoozed_until": timestamp}]}}
    display = display_times(raw)
    assert display["receivedAt"] != timestamp
    assert display["incidents"]["active"][0]["opened_at"] != timestamp
    assert raw["receivedAt"] == timestamp

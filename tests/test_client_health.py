import json

import pytest

from cookie_http_seeder.client_health import ClientHealthStore
from cookie_http_seeder.receiver import ReceiverState

NOW = 1791093600
SOURCES = {"demo": {"domains": ["example.test"]}}


def payload():
    return {"schema_version": 1, "client_version": "0.5.0", "interval_minutes": 15,
            "sources": {"demo": {"approved": True, "permission_granted": False,
                                 "sync_phase": "blocked", "error_code": "permission_denied",
                                 "last_success_at": "2026-10-01T00:00:00Z"}}}


def test_persist_restart_age_and_permissions(tmp_path):
    store = ClientHealthStore(tmp_path, clock=lambda: NOW)
    assert store.status()["state"] == "not_reported"
    assert store.accept(payload(), SOURCES)["state"] == "recent"
    path = tmp_path / ".client-health.json"
    assert path.stat().st_mode & 0o777 == 0o600
    later = ClientHealthStore(tmp_path, clock=lambda: NOW + 2101)
    assert later.status()["state"] == "overdue"
    assert later.status()["ageSeconds"] == 2101
    assert later.status()["sources"]["demo"]["approved"] is True
    assert "example.test" not in path.read_text()
    path.write_text('{"invalid": true}')
    assert store.status()["state"] == "unknown"
    assert store.status()["error"] == "unreadable_client_health"


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(cookie="synthetic-secret"),
    lambda p: p.update(schema_version=True),
    lambda p: p.update(client_version="unknown"),
    lambda p: p.update(interval_minutes=True),
    lambda p: p.update(interval_minutes=14),
    lambda p: p.update(interval_minutes=10081),
    lambda p: p["sources"].update(other=p["sources"]["demo"]),
    lambda p: p["sources"]["demo"].update(approved="yes"),
    lambda p: p["sources"]["demo"].update(error_code="synthetic secret text"),
    lambda p: p["sources"]["demo"].update(sync_phase="online"),
    lambda p: p["sources"]["demo"].update(last_success_at="2099-01-01T00:00:00Z"),
    lambda p: p["sources"]["demo"].update(last_success_at="2026-10-01T00:00:00+08:00"),
    lambda p: p["sources"]["demo"].update(url="https://example.test"),
])
def test_strict_schema_rejected_without_writing(tmp_path, mutation):
    doc = payload()
    mutation(doc)
    with pytest.raises(ValueError):
        ClientHealthStore(tmp_path, clock=lambda: NOW).accept(doc, SOURCES)
    assert not (tmp_path / ".client-health.json").exists()


def test_tag_health_isolation_and_scope_change(tmp_path):
    root = ReceiverState(SOURCES, tmp_path)
    child = root.for_sender("laptop")
    child.client_health.accept(payload(), SOURCES)
    assert root.status()["clientHealth"]["state"] == "not_reported"
    assert root.sender_statuses()["senders"]["laptop"]["clientHealth"]["state"] == "recent"
    changed = {"demo": {"domains": ["changed.test"]}}
    child.replace_sources(changed, child.revision)
    assert child.status()["clientHealth"]["sources"] == {}
    assert "client_health" in root.document()["capabilities"]
    assert json.loads((child.data_dir / ".client-health.json").read_text())["sources"] == {}

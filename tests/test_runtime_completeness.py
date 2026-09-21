"""Post-merge runtime smoke tests and single-writer protection."""
from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from unittest.mock import Mock

import pytest

from cookie_http_seeder import receiver
from cookie_http_seeder.process_lock import DataDirectoryInUse, DataDirectoryLock

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "test-token-0123456789ab"


@pytest.fixture
def endpoint(tmp_path):
    receiver.configure(sources={"demo": {"domains": ["example.test"]}}, data_dir=tmp_path)
    server = receiver.ThreadingHTTPServer(("127.0.0.1", 0), receiver.build_handler(TOKEN))
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01))
    thread.start()

    def request(method="GET", path="/v1/sources", payload=None, headers=None):
        connection = http.client.HTTPConnection(*server.server_address, timeout=3)
        try:
            body = json.dumps(payload) if payload is not None else None
            connection.request(method, path, body, {
                "Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json",
                **(headers or {}),
            })
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()
    yield request, server.server_address
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def test_receiver_imports_in_clean_process():
    result = subprocess.run(
        [sys.executable, "-c", "import cookie_http_seeder.receiver"],
        cwd=ROOT, capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 0, result.stderr


def test_valid_extension_origin_and_capabilities(endpoint):
    request, _ = endpoint
    code, doc = request(headers={"Origin": "chrome-extension://" + "a" * 32})
    assert code == 200
    assert "conditional_snapshots" in doc["capabilities"]
    assert TOKEN not in json.dumps(doc)


@pytest.mark.parametrize("origin", ["https://example.test", "null", "chrome-extension://bad"])
def test_untrusted_origins_are_rejected(endpoint, origin):
    code, body = endpoint[0](headers={"Origin": origin})
    assert code == 403
    assert body["error"] == "origin_not_allowed"


def test_config_conflict_is_409_not_a_name_error(endpoint):
    request, _ = endpoint
    code, body = request("PUT", "/v1/sources", {"sources": {}, "revision": "stale"})
    assert (code, body["error"]) == (409, "configuration_changed")


def test_snapshot_preconditions_and_clear_still_work(endpoint):
    request, _ = endpoint
    _, config = request()
    payload = {"schema_version": 2, "complete": True, "source": "demo", "cookies": [],
               "config_revision": config["revision"]}
    assert request("POST", "/v2/cookies", payload)[0] == 428
    payload.update(expected_version=None, request_id=str(uuid.uuid4()))
    code, accepted = request("POST", "/v2/cookies", payload)
    assert code == 200 and accepted["cleared"]
    payload["request_id"] = str(uuid.uuid4())
    code, body = request("POST", "/v2/cookies", payload)
    assert (code, body["error"]) == (409, "snapshot_conflict")
    assert request("POST", "/v1/cookies", {"source": "demo", "cookie_header": "demo=x"})[0] == 410


def test_unauthorized_request_rejected_without_waiting_for_body(endpoint):
    _, address = endpoint
    connection = http.client.HTTPConnection(*address, timeout=2)
    try:
        connection.putrequest("POST", "/v2/cookies")
        connection.putheader("Authorization", "Bearer incorrect")
        connection.putheader("Content-Length", "500000")
        connection.putheader("Content-Type", "application/json")
        connection.endheaders()  # Intentionally never send the promised body.
        response = connection.getresponse()
        assert response.status == 401
        response.read()
    finally:
        connection.close()


def test_one_lock_per_directory_and_independent_directories(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    with DataDirectoryLock(tmp_path), DataDirectoryLock(other):
        with pytest.raises(DataDirectoryInUse):
            with DataDirectoryLock(tmp_path):
                pytest.fail("second writer acquired the directory")
    # The harmless file remains; its existence is not treated as a stale lock.
    assert (tmp_path / ".receiver.lock").is_file()
    with DataDirectoryLock(tmp_path):
        pass


def test_lock_is_released_on_exception(tmp_path):
    with pytest.raises(ValueError):
        with DataDirectoryLock(tmp_path):
            raise ValueError("simulated crash")
    with DataDirectoryLock(tmp_path):
        pass


def test_lock_released_when_owner_process_is_terminated(tmp_path):
    ready = tmp_path / "ready"
    script = (
        "import sys,time; from pathlib import Path; "
        "from cookie_http_seeder.process_lock import DataDirectoryLock; "
        "lock=DataDirectoryLock(Path(sys.argv[1])); lock.__enter__(); "
        "Path(sys.argv[2]).touch(); time.sleep(30)"
    )
    child = subprocess.Popen([sys.executable, "-c", script, str(tmp_path), str(ready)], cwd=ROOT)
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), "child did not acquire the directory"
        with pytest.raises(DataDirectoryInUse):
            with DataDirectoryLock(tmp_path):
                pytest.fail("cross-process exclusivity lost")
        child.terminate()
        child.wait(timeout=5)
        with DataDirectoryLock(tmp_path):
            pass
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink/hardlink semantics")
@pytest.mark.parametrize("hardlink", [False, True])
def test_lock_does_not_follow_link_to_other_file(tmp_path, hardlink):
    target = tmp_path / "important"
    target.write_text("do not touch")
    link = tmp_path / ".receiver.lock"
    if hardlink:
        os.link(target, link)
    else:
        link.symlink_to(target)
    with pytest.raises(ValueError):
        with DataDirectoryLock(tmp_path):
            pass
    assert target.read_text() == "do not touch"


def test_serve_owns_lock_for_whole_lifetime_and_releases_on_failure(tmp_path, monkeypatch):
    receiver.configure(sources={}, data_dir=tmp_path)

    def fail(**_kwargs):
        with pytest.raises(DataDirectoryInUse):
            with DataDirectoryLock(tmp_path):
                pytest.fail("serve did not acquire a lock")
        raise RuntimeError("simulated server failure")
    monkeypatch.setattr(receiver, "_serve", fail)
    with pytest.raises(RuntimeError, match="simulated server"):
        receiver.serve(host="127.0.0.1", port=18765, token=TOKEN)
    with DataDirectoryLock(tmp_path):
        pass


def test_duplicate_serve_fails_before_binding(tmp_path, monkeypatch):
    receiver.configure(sources={}, data_dir=tmp_path)
    start = Mock()
    monkeypatch.setattr(receiver, "_serve", start)
    with DataDirectoryLock(tmp_path):
        with pytest.raises(SystemExit, match="data directory already in use"):
            receiver.serve(host="127.0.0.1", port=18766, token=TOKEN)
    start.assert_not_called()


def test_bind_helpers_kept_and_log_advertises_v2(tmp_path, monkeypatch, capsys):
    assert receiver.format_listen_url("[::1]", 18765) == "http://[::1]:18765"
    assert receiver.is_ipv6_literal("::1")
    receiver.configure(sources={}, data_dir=tmp_path)
    server = Mock()
    server.serve_forever.side_effect = KeyboardInterrupt
    monkeypatch.setattr(receiver, "http_server_class_for_host", lambda _: Mock(return_value=server))
    receiver.serve(host="127.0.0.1", port=18765, token=TOKEN)
    output = capsys.readouterr().out
    assert "POST /v2/cookies" in output and "POST /v1/cookies" not in output
    server.server_close.assert_called_once()


def test_shutdown_waits_for_request_threads_before_unlock(tmp_path, monkeypatch):
    receiver.configure(sources={}, data_dir=tmp_path)
    server = Mock()
    server.serve_forever.side_effect = KeyboardInterrupt

    def close():
        assert server.daemon_threads is False
        assert server.block_on_close is True
        with pytest.raises(DataDirectoryInUse):
            with DataDirectoryLock(tmp_path):
                pytest.fail("unlocked before accepted requests were drained")
    server.server_close.side_effect = close
    monkeypatch.setattr(receiver, "http_server_class_for_host", lambda _: Mock(return_value=server))
    receiver.serve(host="127.0.0.1", port=18765, token=TOKEN)
    with DataDirectoryLock(tmp_path):
        pass

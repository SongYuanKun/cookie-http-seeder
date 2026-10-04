import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from test_consumer import seed
from test_consumer import setup as setup

from cookie_http_seeder.cli import main
from cookie_http_seeder.consumer_cli import send_request


def test_status_without_auth_snapshot_or_filesystem_writes(tmp_path, capsys, monkeypatch):
    def forbidden(*_, **__):
        pytest.fail("status must not read snapshots or contact receiver")
    monkeypatch.delenv("COOKIE_HTTP_SEEDER_TOKEN", raising=False)
    monkeypatch.setattr("cookie_http_seeder.consumer.load_request_credentials", forbidden)
    monkeypatch.setattr("cookie_http_seeder.client.ReceiverClient.request", forbidden)
    data = tmp_path / "absent-data"
    state = tmp_path / "absent-state" / "state.json"
    assert main(["--data-dir", str(data), "consumer-status", "demo",
                 "--state-path", str(state)]) == 0
    assert json.loads(capsys.readouterr().out)["paused"] is False
    assert list(tmp_path.iterdir()) == []
    state.parent.mkdir()
    state.write_text('{"schema_version":999,"secret":"must-not-echo"}')
    before = state.read_bytes()
    assert main(["--data-dir", str(data), "consumer-status", "demo",
                 "--state-path", str(state)]) == 1
    assert "must-not-echo" not in capsys.readouterr().out
    assert before == state.read_bytes()


def test_transport_does_not_follow_redirects_and_limits_response():
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            calls.append(self.path)
            self.send_response(302 if self.path == "/redirect" else 200)
            self.send_header("Location", "/login")
            self.end_headers()
            try:
                self.wfile.write(b"X" * (1024 * 1024 + 1) if self.path == "/large" else b"test")
            except (BrokenPipeError, ConnectionResetError):
                pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01})
    worker.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        assert send_request(base + "/redirect", {"Cookie": "synthetic"}).status == 302
        assert calls == ["/redirect"]
        assert send_request(base + "/large", {}).status == 599
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


@pytest.mark.parametrize("body,exit_code", [("ACCOUNT_OK private-body", 0),
                                           ("LOGIN_REQUIRED private-body", 3),
                                           ("unexpected private-body", 4)])
def test_consume_exit_codes_and_no_secrets(setup, tmp_path, capsys, monkeypatch, body, exit_code):
    state, client, consumer = setup
    seed(state)
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps({"schema_version": 1, "valid_body_contains": ["ACCOUNT_OK"],
                                "invalid_body_contains": ["LOGIN_REQUIRED"]}))
    from cookie_http_seeder.consumer import Response
    monkeypatch.setenv("COOKIE_HTTP_SEEDER_TOKEN", "synthetic-token-01234567")
    monkeypatch.setattr("cookie_http_seeder.consumer_cli.ReceiverClient", lambda *a, **k: client)
    monkeypatch.setattr("cookie_http_seeder.consumer_cli.send_request",
                        lambda *_: Response(200, body))
    args = ["--data-dir", str(consumer.data_dir), "consume", "site", "--sender-tag", "home-pc",
            "--url", "https://example.test/me", "--rules", str(rules),
            "--state-path", str(consumer.state_path)]
    assert main(args) == exit_code
    output = capsys.readouterr().out
    assert "private-body" not in output and "synthetic-cookie" not in output
    assert "synthetic-token" not in output
    if exit_code == 3:
        assert main(args + ["--wait-seconds", "0"]) == 3

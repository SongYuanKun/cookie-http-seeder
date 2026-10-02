"""Run the real CLI against a synthetic website and a local receiver."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cookie_http_seeder.receiver import build_handler, configure
from cookie_http_seeder.store import read_snapshot


def test_feedback_example_reports_pauses_and_resumes(tmp_path):
    calls = []
    reply = ["ACCOUNT_OK"]

    class Site(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):  # noqa: N802
            calls.append(self.headers.get("Cookie"))
            body = reply[0].encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    data_dir = tmp_path / "data"
    token = "synthetic-feedback-example-token"
    token_path = tmp_path / "token"
    token_path.write_text(token)
    configure(sources={"site": {"domains": ["127.0.0.1"]}}, data_dir=data_dir)
    receiver = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(token))
    site = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (receiver, site)]
    for thread in threads:
        thread.start()
    from cookie_http_seeder.client import ReceiverClient
    from cookie_http_seeder.receiver import _state
    state = _state().for_sender("home-pc")
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps({"schema_version": 1, "valid_body_contains": ["ACCOUNT_OK"],
                                      "invalid_body_contains": ["LOGIN_REQUIRED"]}))
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(root)}
    command = [sys.executable, str(root / "examples/crawl_with_feedback.py"), "site",
               "--data-dir", str(data_dir), "--sender-tag", "home-pc", "--url",
               f"http://127.0.0.1:{site.server_port}/me", "--rules", str(rules_path),
               "--endpoint", f"http://127.0.0.1:{receiver.server_port}", "--token-file",
               str(token_path), "--state-file", str(tmp_path / "consumer/state.json")]

    def push(value):
        state.ingest({"schema_version": 2, "complete": True, "source": "site",
                      "config_revision": state.revision,
                      "expected_version": state.sync.version("site"),
                      "request_id": uuid.uuid4().hex,
                      "cookies": [{"name": "sid", "value": value, "domain": "127.0.0.1",
                                   "path": "/", "hostOnly": True, "secure": False,
                                   "httpOnly": True, "session": True}]})

    try:
        push("synthetic-first")
        valid = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
        assert valid.returncode == 0, valid.stderr
        assert "valid" in valid.stdout and "feedback=sent" in valid.stdout
        client = ReceiverClient(f"http://127.0.0.1:{receiver.server_port}", token,
                                sender_tag="home-pc")
        assert client.request("/v1/status")["sources"]["site"]["validation"] == "valid"
        reply[0] = "LOGIN_REQUIRED"
        invalid = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
        assert invalid.returncode == 2
        paused = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
        assert paused.returncode == 2
        assert calls == ["sid=synthetic-first", "sid=synthetic-first"]
        assert client.request("/v1/status")["sources"]["site"]["validation"] == "invalid"
        push("synthetic-replacement")
        reply[0] = "ACCOUNT_OK"
        resumed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
        assert resumed.returncode == 0, resumed.stderr
        assert calls[-1] == "sid=synthetic-replacement"
        assert read_snapshot("site", data_dir=data_dir,
                             sender_tag="home-pc")["cookies"][0]["value"] == "synthetic-replacement"
        outputs = valid.stdout + invalid.stdout + paused.stdout + resumed.stdout
        assert token not in outputs and "synthetic-first" not in outputs
        assert "synthetic-replacement" not in outputs and "LOGIN_REQUIRED" not in outputs
    finally:
        for server, thread in zip((receiver, site), threads, strict=True):
            server.shutdown()
            server.server_close()
            thread.join()

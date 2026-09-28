"""Opaque session storage is isolated, conditional and never exposed by HTTP."""
import base64
import json
import threading
import uuid
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from cookie_http_seeder.receiver import build_handler, configure

TOKEN = "test-token-0123456789ab"
ENVELOPE = {"schema_version": 1, "source": "site", "kdf": "PBKDF2-SHA256",
            "iterations": 310000, "cipher": "AES-256-GCM",
            "salt": base64.b64encode(b"s" * 16).decode(),
            "nonce": base64.b64encode(b"n" * 12).decode(),
            "ciphertext": base64.b64encode(b"hidden-credential-data").decode()}


@pytest.fixture
def endpoint(tmp_path):
    configure(sources={"site": {"domains": ["example.com"]}}, data_dir=tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(TOKEN))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opener = build_opener(ProxyHandler({}))

    def request(method, path, payload=None, *, token=TOKEN, tag="default"):
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                   "X-Sender-Tag": tag}
        req = Request(f"http://127.0.0.1:{server.server_port}{path}", data=body,
                      headers=headers, method=method)
        try:
            with opener.open(req, timeout=3) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    yield request, tmp_path
    server.shutdown()
    server.server_close()
    thread.join(3)


def upload(request, *, tag="default", expected=None, **patch):
    config = request("GET", "/v1/sources", tag=tag)[1]
    body = {"source": "site", "envelope": ENVELOPE, "config_revision": config["revision"],
            "expected_version": expected, "request_id": str(uuid.uuid4()), **patch}
    return request("POST", "/v3/session-bundles", body, tag=tag)


def test_opaque_conditional_upload_and_metadata_only(endpoint):
    request, root = endpoint
    assert request("POST", "/v3/session-bundles", {}, token="invalid-token-0123456789")[0] == 401
    assert request("GET", "/v3/session-bundles/site")[1]["bundle_version"] is None
    code, first = upload(request)
    assert code == 200
    assert first["bundle_version"] and first["peer"] == {"source": "receiver_peer",
                                                          "ip": "127.0.0.1"}
    assert "ciphertext" not in json.dumps(first)
    code, status = request("GET", "/v3/session-bundles/site")
    assert code == 200 and status["bundle_version"] == first["bundle_version"]
    assert "ciphertext" not in json.dumps(status)
    path = root / "site-session.json"
    assert path.stat().st_mode & 0o777 == 0o600
    assert json.loads(path.read_text())["envelope"] == ENVELOPE
    assert upload(request)[0] == 409
    assert upload(request, expected=first["bundle_version"])[0] == 200


def test_sender_and_source_policy_and_pause_invalidation(endpoint):
    request, root = endpoint
    assert upload(request)[0] == 200
    assert request("GET", "/v3/session-bundles/site", tag="other")[1]["bundle_version"] is None
    assert upload(request, tag="other")[0] == 200
    assert (root / "senders" / "other" / "site-session.json").exists()
    assert upload(request, source="unknown")[0] == 400
    assert upload(request, envelope={**ENVELOPE, "source": "other"})[0] == 400
    config = request("GET", "/v1/sources")[1]
    sources = config["sources"]
    sources["site"]["enabled"] = False
    update = {"sources": sources, "revision": config["revision"]}
    assert request("PUT", "/v1/sources", update)[0] == 200
    assert not (root / "site-session.json").exists()
    assert request("GET", "/v3/session-bundles/site")[1]["bundle_version"] is None

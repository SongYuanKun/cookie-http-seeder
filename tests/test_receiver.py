from __future__ import annotations

import json
import os
import socket
import threading
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from cookie_http_seeder.cookies import (
    cookies_to_header,
    domain_name,
    normalize_cookies,
    normalize_sources,
    request_url,
)
from cookie_http_seeder.paths import atomic_write_text, default_data_dir
from cookie_http_seeder.receiver import (
    ConflictError,
    ReceiverState,
    build_handler,
    configure,
    ensure_token_file,
)
from cookie_http_seeder.store import (
    load_cookie_header,
    load_sources,
    read_snapshot,
    save_cookie_header,
    save_snapshot,
)

TOKEN = "test-token-0123456789ab"
SPEC = {"domains": ["example.com", "other.test"], "target_url": "https://example.com/api/me"}


def cookie(**overrides):
    return {
        "name": "sid", "value": "test-secret-value", "domain": ".example.com", "path": "/",
        "hostOnly": False, "secure": True, "httpOnly": True, "session": True,
        "sameSite": "lax", "storeId": "0", **overrides,
    }


def test_legacy_roundtrip(tmp_path):
    path = tmp_path / "legacy.json"
    save_cookie_header("a=1; b=2", path, updated_at="2026-09-16T00:00:00Z")
    assert load_cookie_header(path) == "a=1; b=2"
    assert json.loads(path.read_text())["updatedAt"] == "2026-09-16T00:00:00Z"
    with pytest.raises(ValueError, match="legacy"):
        load_cookie_header(path, url="https://example.com/")


def test_atomic_write_leaves_no_tmp(tmp_path):
    path = tmp_path / "data.json"
    atomic_write_text(path, '{"ok":true}\n')
    assert json.loads(path.read_text()) == {"ok": True}
    assert not list(tmp_path.glob(".*.tmp"))
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600


def test_load_sources_from_examples():
    sources = load_sources(Path("examples/sources.json"))
    assert "fang" in sources and "beike" in sources


def test_load_sources_from_data_dir_override(tmp_path, monkeypatch):
    monkeypatch.delenv("COOKIE_HTTP_SEEDER_SOURCES", raising=False)
    (tmp_path / "sources.json").write_text(
        json.dumps({"sources": {"mysite": {"domains": ["example.com"]}}})
    )
    assert list(load_sources(data_dir=tmp_path)) == ["mysite"]


def test_load_sources_packaged_fallback(monkeypatch):
    monkeypatch.delenv("COOKIE_HTTP_SEEDER_SOURCES", raising=False)
    with mock.patch("cookie_http_seeder.store._REPO_ROOT", Path("/nonexistent")), \
         mock.patch("cookie_http_seeder.store.Path.cwd", return_value=Path("/tmp")), \
         mock.patch("cookie_http_seeder.store.sources_override_file",
                    return_value=Path("/nonexistent/sources.json")):
        assert load_sources()["fang"]["domains"]


def test_default_data_dir_prefers_env(monkeypatch):
    monkeypatch.setenv("COOKIE_HTTP_SEEDER_DATA", "/tmp/chs-data")
    assert default_data_dir() == Path("/tmp/chs-data")


def test_ensure_token_file(tmp_path):
    path = tmp_path / "token"
    assert ensure_token_file(path) == ensure_token_file(path)
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600


def test_header_keeps_same_names_and_scopes_domains_paths():
    jar = [cookie(value="root"), cookie(value="api", path="/api"),
           cookie(value="other", domain="other.test"), cookie(value="wrong-path", path="/apix")]
    assert cookies_to_header(jar, "https://example.com/api/me", domains=SPEC["domains"]) == (
        "sid=api; sid=root"
    )
    assert cookies_to_header(jar, "https://other.test/api/me", domains=SPEC["domains"]) == (
        "sid=other"
    )


@pytest.mark.parametrize("path,expected", [
    ("/api", True), ("/api/", True), ("/api/x", True), ("/apix", False),
    ("/API", False), ("/", False), ("/api%2Fx", False),
])
def test_path_match(path, expected):
    header = cookies_to_header([cookie(path="/api")], f"https://example.com{path}",
                               domains=SPEC["domains"])
    assert bool(header) is expected


def test_host_only_secure_and_expiry():
    jar = [cookie(value="host", hostOnly=True), cookie(name="domain", value="yes"),
           cookie(name="expired", session=False, expirationDate=100),
           cookie(name="alive", session=False, expirationDate=101)]
    assert cookies_to_header(jar, "https://sub.example.com/", domains=SPEC["domains"], now=100) == (
        "domain=yes; alive=test-secret-value"
    )
    assert cookies_to_header(jar, "http://example.com/", domains=SPEC["domains"], now=100) == ""


@pytest.mark.parametrize("url", ["https://badexample.com/", "https://example.com.evil.test/"])
def test_target_outside_allowlist(url):
    with pytest.raises(ValueError):
        cookies_to_header([cookie()], url, domains=SPEC["domains"])


@pytest.mark.parametrize("bad", [
    "*", "*.example.com", "com", "https://example.com", "example.com/path", "example.com.",
    "example.com\r\n", "a..test", "user@example.com", "127.1", "0127.0.0.1", "a.123",
])
def test_invalid_domain(bad):
    with pytest.raises(ValueError):
        domain_name(bad)


@pytest.mark.parametrize("bad", [
    "file:///etc/passwd", "https://user:pass@example.com", "https://example.com/#fragment",
    "https://example.com:99999/", "https://example.com:0/", " https://example.com",
    "https://example.com\\@evil.test", "https://example.com/\npath",
    "https://example.com/api/../x", "https://example.com/api/%2e%2e/x",
])
def test_invalid_target_url(bad):
    with pytest.raises(ValueError):
        request_url(bad)


@pytest.mark.parametrize("overrides", [
    {"domain": "evil.test"}, {"name": "bad\r\nname"}, {"value": "secret;Injected=yes"},
    {"value": "secret\r\nInjected: yes"}, {"value": "bad,value"}, {"value": "back\\slash"},
    {"hostOnly": "false"}, {"secure": 0}, {"sameSite": []}, {"path": "relative"},
    {"path": "/bad\n"}, {"session": False}, {"expirationDate": float("nan")},
    {"session": False, "expirationDate": 10 ** 1000}, {"partitionKey": {}},
    {"partitionKey": {"topLevelSite": "https://example.com"}},
])
def test_invalid_cookies(overrides):
    with pytest.raises(ValueError):
        normalize_cookies([cookie(**overrides)], SPEC["domains"])


def test_duplicate_identity_and_stores():
    one = cookie()
    assert len(normalize_cookies([one, one.copy()], SPEC["domains"])) == 1
    with pytest.raises(ValueError, match="conflicting"):
        normalize_cookies([one, cookie(value="second")], SPEC["domains"])
    with pytest.raises(ValueError, match="mixed"):
        normalize_cookies([one, cookie(storeId="1")], SPEC["domains"])


def test_empty_snapshot_and_quoted_value():
    assert normalize_cookies([], SPEC["domains"]) == []
    assert cookies_to_header([cookie(value='"abc"')], "https://example.com/",
                             domains=SPEC["domains"]) == 'sid="abc"'


def test_snapshot_recomputes_instead_of_trusting_cached_header(tmp_path):
    spec = normalize_sources({"site": SPEC})["site"]
    save_snapshot([cookie()], source="site", spec=spec, updated_at="test", data_dir=tmp_path)
    path = tmp_path / "site-cookies.json"
    data = json.loads(path.read_text())
    data["cookie_header"] = "poisoned=cache"
    path.write_text(json.dumps(data))
    assert load_cookie_header(source="site", data_dir=tmp_path,
                              url="https://example.com/") == "sid=test-secret-value"
    assert load_cookie_header(source="site", data_dir=tmp_path, url="https://other.test/") is None


def test_tombstone_overrides_legacy_environment(tmp_path, monkeypatch):
    spec = normalize_sources({"site": SPEC})["site"]
    save_snapshot([], source="site", spec=spec, updated_at="test", data_dir=tmp_path)
    monkeypatch.setenv("COOKIE_HTTP_SEEDER_SITE_HEADER", "old=secret")
    assert load_cookie_header(source="site", data_dir=tmp_path) is None


def test_no_implicit_target_for_old_config(tmp_path):
    spec = normalize_sources({"site": {"domains": ["example.com"]}})["site"]
    save_snapshot([cookie()], source="site", spec=spec, updated_at="test", data_dir=tmp_path)
    assert "cookie_header" not in read_snapshot("site", data_dir=tmp_path)
    with pytest.raises(ValueError, match="URL"):
        load_cookie_header(source="site", data_dir=tmp_path)
    assert load_cookie_header(source="site", data_dir=tmp_path, url="https://example.com/")


@pytest.mark.parametrize("sources", [
    {"../escape": SPEC}, {"x": {"domains": []}}, {"x": {**SPEC, "enabled": "true"}},
    {"x": {**SPEC, "token": "secret"}}, {"x": {**SPEC, "target_url": "https://evil.test"}},
])
def test_bad_configuration(sources):
    with pytest.raises(ValueError):
        normalize_sources(sources)


@pytest.fixture
def receiver(tmp_path):
    configure(sources={"site": SPEC}, data_dir=tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(TOKEN))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opener = build_opener(ProxyHandler({}))
    base = f"http://127.0.0.1:{server.server_port}"

    def request(method, path, payload=None, *, token=TOKEN, headers=None, raw=None):
        data = raw if raw is not None else (
            json.dumps(payload).encode() if payload is not None else None
        )
        request_headers = {"Content-Type": "application/json"}
        if token is not None:
            request_headers["Authorization"] = f"Bearer {token}"
        request_headers.update(headers or {})
        try:
            req = Request(base + path, data=data, method=method, headers=request_headers)
            with opener.open(req, timeout=3) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    yield request, tmp_path, server.server_port
    server.shutdown()
    server.server_close()
    thread.join(3)


def push_body(request, **updates):
    _, doc = request("GET", "/v1/sources")
    return {"schema_version": 2, "complete": True, "config_revision": doc["revision"],
            "source": "site", "cookies": [cookie()],
            "expected_version": request("GET", "/v2/sync/site")[1]["snapshot_version"],
            "request_id": str(uuid.uuid4()), **updates}


def test_http_auth_and_push(receiver):
    req, data, _ = receiver
    assert req("GET", "/healthz", token=None)[0] == 200
    payload = push_body(req)
    assert req("POST", "/v2/cookies", payload, token=None)[0] == 401
    assert not (data / "site-cookies.json").exists()
    code, doc = req("POST", "/v2/cookies", payload)
    assert code == 200 and doc["ok"]
    assert load_cookie_header(source="site", data_dir=data,
                              url="https://example.com/") == "sid=test-secret-value"
    status = json.dumps(req("GET", "/v1/status")[1])
    assert "test-secret-value" not in status and TOKEN not in status and "unverified" in status


def test_legacy_network_write_is_rejected(receiver):
    req, _, _ = receiver
    assert req("POST", "/v1/cookies", {"source": "site", "cookie_header": "sid=raw"})[0] == 410
    assert req("POST", "/v2/cookies", push_body(req, cookie_header="sid=raw"))[0] == 400


def test_empty_snapshot_replaces_and_partial_failure_preserves(receiver):
    req, data, _ = receiver
    assert req("POST", "/v2/cookies", push_body(req))[0] == 200
    assert req("POST", "/v2/cookies", push_body(req, cookies=[], complete=False))[0] == 400
    assert load_cookie_header(source="site", data_dir=data)
    assert req("POST", "/v2/cookies", push_body(req, cookies=[]))[0] == 200
    assert load_cookie_header(source="site", data_dir=data) is None


def test_sources_persist_and_last_source_can_be_removed(receiver):
    req, data, _ = receiver
    doc = req("GET", "/v1/sources")[1]
    sources = {
        **doc["sources"], "new": {"domains": ["new.test"], "target_url": "https://new.test/"}
    }
    code, updated = req("PUT", "/v1/sources", {"sources": sources, "revision": doc["revision"]})
    assert code == 200 and "new" in load_sources(data_dir=data)
    code, empty = req("PUT", "/v1/sources", {"sources": {}, "revision": updated["revision"]})
    assert code == 200 and empty["sources"] == {} and load_sources(data_dir=data) == {}


def test_clear_pauses_and_prevents_stale_pushes_after_restart(receiver):
    req, data, _ = receiver
    old = push_body(req)
    req("POST", "/v2/cookies", old)
    code, cleared = req("DELETE", "/v2/cookies/site", headers={"If-Match": old["config_revision"]})
    assert code == 200 and cleared["paused"] and cleared["cleared"]
    assert load_cookie_header(source="site", data_dir=data) is None
    assert req("POST", "/v2/cookies", old)[0] == 409
    assert req("POST", "/v2/cookies", push_body(req))[0] == 400
    restarted = ReceiverState(load_sources(data_dir=data), data)
    assert restarted.document()["sources"]["site"]["enabled"] is False
    with pytest.raises(ConflictError):
        restarted.ingest(old)


def test_reenable_does_not_reuse_old_config_revision(receiver):
    req, _, _ = receiver
    old = push_body(req)
    _, paused = req("DELETE", "/v2/cookies/site", headers={"If-Match": old["config_revision"]})
    paused["sources"]["site"]["enabled"] = True
    _, enabled = req("PUT", "/v1/sources", {
        "sources": paused["sources"], "revision": paused["revision"],
    })
    assert enabled["revision"] != old["config_revision"]
    assert req("POST", "/v2/cookies", old)[0] == 409


def test_config_change_invalidates_old_header(receiver):
    req, data, _ = receiver
    req("POST", "/v2/cookies", push_body(req))
    doc = req("GET", "/v1/sources")[1]
    doc["sources"]["site"]["domains"] = ["other.test"]
    doc["sources"]["site"]["target_url"] = "https://other.test/"
    assert req("PUT", "/v1/sources", {
        "sources": doc["sources"], "revision": doc["revision"],
    })[0] == 200
    assert load_cookie_header(source="site", data_dir=data) is None


@pytest.mark.parametrize("change", [
    {"source": "../../tmp"}, {"source": []}, {"cookies": [cookie(domain="evil.test")]},
    {"cookies": None}, {"cookies": [cookie(value="secret\r\nX: y")]},
    {"cookies": [cookie(partitionKey={})]},
])
def test_http_rejects_unsafe_payloads(receiver, change):
    req, data, _ = receiver
    assert req("POST", "/v2/cookies", push_body(req, **change))[0] == 400
    assert not (data / "site-cookies.json").exists()


def test_http_origins_and_json_rejection(receiver):
    req, _, _ = receiver
    assert req("GET", "/v1/status", headers={"Origin": "https://evil.test"})[0] == 403
    assert req("GET", "/v1/status", headers={"Origin": "chrome-extension://" + "a" * 32})[0] == 200
    assert req("PUT", "/v1/sources", raw=b'{"sources":{},"sources":{}}')[0] == 400
    assert req("POST", "/v2/cookies", raw=b'{"schema_version":NaN}')[0] == 400
    assert req("POST", "/v2/cookies", raw=b'{}',
               headers={"Content-Type": "text/plain"})[0] == 400


def test_auth_precedes_body_read(receiver):
    _, _, port = receiver
    with socket.create_connection(("127.0.0.1", port), timeout=2) as connection:
        connection.sendall(b"POST /v2/cookies HTTP/1.1\r\nHost: localhost\r\n"
                           b"Content-Length: 524288\r\n\r\n")
        assert b"401" in connection.recv(4096).split(b"\r\n")[0]


@pytest.mark.parametrize("extra", [
    "Content-Length: 2\r\nContent-Length: 3",
    "Content-Length: 2\r\nTransfer-Encoding: chunked", "Content-Length: 524289",
])
def test_http_bad_framing(receiver, extra):
    _, _, port = receiver
    with socket.create_connection(("127.0.0.1", port), timeout=2) as connection:
        connection.sendall((f"POST /v2/cookies HTTP/1.1\r\nHost: localhost\r\n"
                            f"Authorization: Bearer {TOKEN}\r\n{extra}\r\n"
                            "Content-Type: application/json\r\n\r\n").encode())
        assert b"400" in connection.recv(4096).split(b"\r\n")[0]

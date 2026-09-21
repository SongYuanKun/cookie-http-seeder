"""Sender grouping uses synthetic cookies only; no external sites or real credentials."""
from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

import pytest

from cookie_http_seeder.cli import main
from cookie_http_seeder.client import ClientError, ReceiverClient
from cookie_http_seeder.diagnostics import diagnose
from cookie_http_seeder.receiver import ReceiverState, build_handler, configure
from cookie_http_seeder.senders import list_sender_tags, sender_directory, sender_tag
from cookie_http_seeder.store import (
    load_cookie_header,
    load_request_credentials,
    read_snapshot,
)

TOKEN = "synthetic-test-token-0123456789"
SOURCES = {"demo": {"domains": ["example.test"], "target_url": "https://example.test/me"}}


def cookie(value):
    return {"name": "sid", "value": value, "domain": ".example.test", "path": "/",
            "hostOnly": False, "secure": True, "httpOnly": True, "session": True,
            "sameSite": "lax", "storeId": "0"}


def payload(state, value, expected=None):
    return {"schema_version": 2, "complete": True, "source": "demo",
            "cookies": [cookie(value)] if value is not None else [],
            "config_revision": state.revision, "expected_version": expected,
            "request_id": uuid.uuid4().hex}


@pytest.mark.parametrize("tag", ["default", "home-pc", "work_pc", "1", "a" * 32])
def test_labels_valid(tag):
    assert sender_tag(tag) == tag


@pytest.mark.parametrize("tag", ["", None, False, 1, "../work", "a/b", "a\\b", ".",
                                  "..", "Home", " a", "a ", "家里", "a\n", "a" * 33,
                                  "con", "aux", "lpt1", "com9", "nul", "-x", "_x"])
def test_labels_invalid(tag):
    with pytest.raises(ValueError):
        sender_tag(tag)


def test_namespace_selection_does_not_create_or_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("COOKIE_HTTP_SEEDER_DEMO_HEADER", "sid=legacy-default")
    assert sender_directory(tmp_path, "home-pc") == tmp_path / "senders/home-pc"
    assert not (tmp_path / "senders").exists()
    assert load_cookie_header(source="demo", data_dir=tmp_path) == "sid=legacy-default"
    assert load_cookie_header(source="demo", data_dir=tmp_path, sender_tag="home-pc") is None
    assert not (tmp_path / "senders").exists()
    with pytest.raises(ValueError):
        load_cookie_header(tmp_path / "x", sender_tag="home-pc")


def test_same_source_isolated_and_default_unchanged(tmp_path):
    root = ReceiverState(SOURCES, tmp_path)
    root.ingest(payload(root, "default-value"))
    home, work = root.for_sender("home-pc"), root.for_sender("work-pc")
    assert read_snapshot("demo", data_dir=tmp_path, sender_tag="home-pc") is None
    home.ingest(payload(home, "home-value"))
    work.ingest(payload(work, "work-value"))
    for tag, expected in [("default", "default-value"), ("home-pc", "home-value"),
                          ("work-pc", "work-value")]:
        assert load_cookie_header(source="demo", data_dir=tmp_path, sender_tag=tag,
                                  url="https://example.test/me") == "sid=" + expected
    assert list_sender_tags(tmp_path) == ["default", "home-pc", "work-pc"]


def test_registry_is_singleton_per_label_under_concurrency(tmp_path):
    root = ReceiverState(SOURCES, tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        states = list(pool.map(root.for_sender, ["home-pc"] * 20))
    assert len({id(s) for s in states}) == 1
    assert states[0].sender_tag == "home-pc"


def test_clear_pause_config_and_validation_scoped(tmp_path):
    root = ReceiverState(SOURCES, tmp_path)
    home, work = root.for_sender("home-pc"), root.for_sender("work-pc")
    a, b = home.ingest(payload(home, "a")), work.ingest(payload(work, "b"))
    home.report({"source": "demo", "snapshot_version": a["snapshot_version"],
                 "result": "valid", "reason_code": "logged_in"})
    assert home.status()["sources"]["demo"]["validation"] == "valid"
    assert work.status()["sources"]["demo"]["validation"] == "unverified"
    work_bytes = (tmp_path / "senders/work-pc/demo-cookies.json").read_bytes()
    work_revision = work.revision
    home.clear_and_pause("demo", home.revision)
    assert not home.sources["demo"]["enabled"]
    assert work.sources["demo"]["enabled"]
    assert work.revision == work_revision
    assert work.sync.version("demo") == b["snapshot_version"]
    assert (tmp_path / "senders/work-pc/demo-cookies.json").read_bytes() == work_bytes
    assert load_cookie_header(source="demo", data_dir=tmp_path, sender_tag="home-pc") is None
    restarted = ReceiverState(SOURCES, tmp_path)
    assert not restarted.for_sender("home-pc").sources["demo"]["enabled"]
    assert restarted.for_sender("work-pc").sync.version("demo") == b["snapshot_version"]


def test_sdk_reads_header_and_version_from_selected_snapshot(tmp_path):
    root = ReceiverState(SOURCES, tmp_path)
    selected = root.for_sender("home-pc")
    result = selected.ingest(payload(selected, "a"))
    credentials = load_request_credentials(source="demo", url="https://example.test/",
                                           data_dir=tmp_path, sender_tag="home-pc")
    assert credentials == {"cookie_header": "sid=a", "snapshot_version": result["snapshot_version"]}
    with pytest.raises(ValueError):
        load_request_credentials(source="demo", url="https://example.test/",
                                 data_dir=tmp_path, sender_tag="unknown-pc")


def test_limits_survive_restart(tmp_path, monkeypatch):
    monkeypatch.setattr("cookie_http_seeder.receiver.MAX_SENDERS", 1)
    ReceiverState(SOURCES, tmp_path).for_sender("home-pc")
    root = ReceiverState(SOURCES, tmp_path)
    assert root.for_sender("home-pc").sender_tag == "home-pc"
    with pytest.raises(ValueError, match="limit"):
        root.for_sender("work-pc")
    assert not (tmp_path / "senders/work-pc").exists()


def test_symlink_namespace_rejected(tmp_path):
    outside = tmp_path / "other"
    outside.mkdir()
    (tmp_path / "senders").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        ReceiverState(SOURCES, tmp_path).for_sender("home-pc")
    assert not list(outside.iterdir())


@pytest.fixture
def http_receiver(tmp_path):
    configure(sources=SOURCES, data_dir=tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(TOKEN))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, tmp_path
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def request(port, path, tag=None, method="GET", body=None, token=TOKEN, duplicates=False, extra=None):
    connection = HTTPConnection("127.0.0.1", port, timeout=4)
    encoded = json.dumps(body).encode() if body is not None else b""
    connection.putrequest(method, path)
    connection.putheader("Authorization", f"Bearer {token}")
    connection.putheader("Content-Type", "application/json")
    connection.putheader("Content-Length", str(len(encoded)))
    if tag is not None:
        connection.putheader("X-Sender-Tag", tag)
    if duplicates:
        connection.putheader("X-Sender-Tag", "work-pc")
    for key, value in (extra or {}).items():
        connection.putheader(key, value)
    connection.endheaders(encoded)
    result = connection.getresponse()
    status, data = result.status, json.loads(result.read())
    connection.close()
    return status, data


def http_push(port, tag, value, expected=None, revision=None):
    if revision is None:
        _, doc = request(port, "/v1/sources", tag)
        revision = doc["revision"]
    return request(port, "/v2/cookies", tag, "POST", {
        "schema_version": 2, "complete": True, "source": "demo",
        "cookies": [cookie(value)] if value is not None else [],
        "config_revision": revision, "expected_version": expected,
        "request_id": uuid.uuid4().hex,
    })


def test_http_concurrent_senders_and_scoped_feedback(http_receiver):
    port, data_dir = http_receiver
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = list(pool.map(lambda t: http_push(port, t, t), ["home-pc", "work-pc"]))
    assert a[0] == b[0] == 200
    assert a[1]["snapshot_version"] != b[1]["snapshot_version"]
    # Feedback for A cannot validate B even if the caller accidentally selects B.
    status, _ = request(port, "/v1/feedback", "work-pc", "POST", {
        "source": "demo", "snapshot_version": a[1]["snapshot_version"],
        "result": "valid", "reason_code": "logged_in"})
    assert status == 409
    shown = request(port, "/v1/status", "work-pc")[1]
    assert shown["sources"]["demo"]["validation"] == "unverified"
    assert http_push(port, "home-pc", "later-with-stale-version")[0] == 409
    assert load_cookie_header(source="demo", data_dir=data_dir,
                              sender_tag="work-pc") == "sid=work-pc"


def test_http_missing_tag_is_legacy_default(http_receiver):
    port, _ = http_receiver
    assert request(port, "/v1/sources")[1]["sender_tag"] == "default"
    assert http_push(port, None, "legacy")[0] == 200
    assert request(port, "/v1/status", "home-pc")[1]["sources"]["demo"]["present"] is False


@pytest.mark.parametrize("tag,duplicates", [("../bad", False), ("", False), ("UPPER", False),
                                          ("home-pc", True)])
def test_http_bad_tags_rejected_before_directory_creation(http_receiver, tag, duplicates):
    port, data_dir = http_receiver
    assert request(port, "/v1/sources", tag, duplicates=duplicates)[0] == 400
    assert not (data_dir / "senders").exists()


def test_auth_stays_required_and_checked_first(http_receiver):
    port, data_dir = http_receiver
    assert request(port, "/v1/sources", "home-pc", token="incorrect")[0] == 401
    assert not (data_dir / "senders").exists()


def test_client_selected_label_and_default_compatibility(http_receiver):
    port, _ = http_receiver
    client = ReceiverClient(f"http://127.0.0.1:{port}", TOKEN, sender_tag="home-pc")
    assert client.request("/v1/sources")["sender_tag"] == "home-pc"
    _, pushed = http_push(port, "home-pc", "a")
    result = client.report("demo", pushed["snapshot_version"], "valid", "logged_in")
    assert result["sender_tag"] == "home-pc"


def test_cli_scoped_read_status_list_and_doctor(tmp_path, capsys):
    root = ReceiverState(SOURCES, tmp_path)
    home = root.for_sender("home-pc")
    home.ingest(payload(home, "private-test"))
    base = ["--data-dir", str(tmp_path)]
    assert main(base + ["header", "demo", "--url", "https://example.test/",
                        "--sender-tag", "home-pc"]) == 0
    assert capsys.readouterr().out.strip() == "sid=private-test"
    assert main(base + ["status", "--sender-tag", "home-pc", "--json"]) == 0
    shown = capsys.readouterr().out
    assert json.loads(shown)["sender_tag"] == "home-pc"
    assert "private-test" not in shown
    assert main(base + ["senders"]) == 0
    assert json.loads(capsys.readouterr().out)["senders"] == ["default", "home-pc"]
    (tmp_path / "cookie-receiver.token").write_text(TOKEN)
    result = diagnose(tmp_path, local_only=True, sender_tag="home-pc")
    assert result["ok"] and result["sender_tag"] == "home-pc"
    assert not diagnose(tmp_path, local_only=True, sender_tag="missing")["ok"]
    assert not (tmp_path / "senders/missing").exists()


def test_python_client_rejects_legacy_before_post(monkeypatch):
    client = ReceiverClient("http://127.0.0.1:18765", TOKEN, sender_tag="home-pc")
    methods = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size):
            return b'{"ok":true,"capabilities":["conditional_snapshots"]}'

    def open_request(req, **kwargs):
        methods.append(req.method)
        return Response()

    monkeypatch.setattr(client.opener, "open", open_request)
    with pytest.raises(ClientError, match="sender_tags_unsupported"):
        client.report("demo", "a" * 32, "valid", "logged_in")
    assert methods == ["GET"]


def test_http_clear_does_not_pause_other_sender(http_receiver):
    port, _ = http_receiver
    assert http_push(port, "home-pc", "a")[0] == 200
    assert http_push(port, "work-pc", "b")[0] == 200
    home_doc = request(port, "/v1/sources", "home-pc")[1]
    status, result = request(port, "/v2/cookies/demo", "home-pc", "DELETE",
                             extra={"If-Match": home_doc["revision"]})
    assert status == 200 and result["sender_tag"] == "home-pc"
    other = request(port, "/v1/status", "work-pc")[1]["sources"]["demo"]
    assert other["enabled"] and other["present"]
    selected = request(port, "/v1/status", "home-pc")[1]["sources"]["demo"]
    assert not selected["enabled"] and selected["cleared"]


def test_notification_uses_root_webhook_and_sender_prefix(tmp_path, monkeypatch):
    state = ReceiverState(SOURCES, tmp_path).for_sender("home-pc")
    sent = []
    monkeypatch.delenv("COOKIE_HTTP_SEEDER_WEBHOOK_FILE", raising=False)
    monkeypatch.setattr("cookie_http_seeder.receiver.notify_pushed",
                        lambda **kwargs: sent.append(kwargs) or "sent")
    state.pending_notify.add("demo")
    state.flush_notify()
    assert sent == [{"sources": ["home-pc/demo"],
                     "webhook_path": tmp_path / "feishu-webhook"}]

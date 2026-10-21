"""Two receiver applications must never share mutable configuration or sync state."""
import json
import socket
import threading
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
from urllib.request import ProxyHandler, Request, build_opener

from cookie_http_seeder.receiver import ReceiverState, build_handler, configure

TOKEN = "synthetic-token-0123456789"


def test_explicit_applications_survive_global_reconfiguration(tmp_path):
    first = ReceiverState({"first": {"domains": ["example.test"]}}, tmp_path / "first")
    second = ReceiverState({"second": {"domains": ["example.test"]}}, tmp_path / "second")
    opener = build_opener(ProxyHandler({}))
    with ExitStack() as cleanup:
        servers = []
        for state in (first, second):
            server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(TOKEN, state=state))
            worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01})
            worker.start()
            cleanup.callback(worker.join)
            cleanup.callback(server.server_close)
            cleanup.callback(server.shutdown)
            servers.append(server)
        configure(sources={"unrelated": {"domains": ["other.test"]}}, data_dir=tmp_path / "global")

        def request(index, path, body=None):
            req = Request(f"http://127.0.0.1:{servers[index].server_port}{path}",
                          data=json.dumps(body).encode() if body is not None else None,
                          headers={"Authorization": f"Bearer {TOKEN}",
                                   "Content-Type": "application/json"})
            with opener.open(req, timeout=2) as response:
                return json.load(response)

        assert set(request(0, "/v1/sources")["sources"]) == {"first"}
        assert set(request(1, "/v1/sources")["sources"]) == {"second"}
        version = request(0, "/v2/cookies", {
            "schema_version": 2, "complete": True, "source": "first", "cookies": [],
            "config_revision": first.revision, "expected_version": None,
            "request_id": "synthetic-isolation-01",
        })["snapshot_version"]
        assert request(0, "/v2/sync/first")["snapshot_version"] == version
        assert request(1, "/v2/sync/second")["snapshot_version"] is None
        assert second.sources.keys() == {"second"}


def test_slow_request_body_does_not_hold_state_lock(tmp_path):
    state = ReceiverState({"demo": {"domains": ["example.test"]}}, tmp_path)
    opener = build_opener(ProxyHandler({}))
    body = json.dumps({"schema_version": 1, "client_version": "0.5.0", "interval_minutes": 15,
                       "sources": {}}).encode()
    with ExitStack() as cleanup:
        server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(TOKEN, state=state))
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01})
        worker.start()
        cleanup.callback(worker.join)
        cleanup.callback(server.server_close)
        cleanup.callback(server.shutdown)
        slow = socket.create_connection(("127.0.0.1", server.server_port), timeout=4)
        cleanup.callback(slow.close)
        # 只发一半 body，模拟仍在上传的慢客户端
        slow.sendall((f"POST /v1/client-health HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                      f"Authorization: Bearer {TOKEN}\r\nContent-Type: application/json\r\n"
                      f"Content-Length: {len(body)}\r\n\r\n").encode() + body[:10])
        status = Request(f"http://127.0.0.1:{server.server_port}/v1/status",
                         headers={"Authorization": f"Bearer {TOKEN}"})
        with opener.open(status, timeout=2) as response:
            assert json.load(response)["ok"] is True
        slow.sendall(body[10:])
        assert b" 200 " in slow.recv(4096).split(b"\r\n", 1)[0]


def test_client_health_reads_body_before_target_sender_lock():
    """A half-read client-health body must not block its sender's state lock."""
    body_read_started = threading.Event()
    finish_body = threading.Event()
    observer_acquired_lock = threading.Event()
    accepted = []
    responses = []

    class ProbeLock:
        def __init__(self):
            self._lock = threading.Lock()
            self.owner = None

        def __enter__(self):
            self._lock.acquire()
            self.owner = threading.get_ident()
            return self

        def __exit__(self, *_args):
            self.owner = None
            self._lock.release()

        def owned_by_current_thread(self):
            return self.owner == threading.get_ident()

    class ClientHealth:
        def accept(self, payload, sources):
            assert sender.lock.owned_by_current_thread()
            accepted.append((payload, sources))
            return {"ok": True}

    class SenderState:
        def __init__(self):
            self.lock = ProbeLock()
            self.sources = {"demo": {"domains": ["example.test"]}}
            self.client_health = ClientHealth()

    class RootState:
        def for_sender(self, tag):
            assert tag == "target"
            return sender

    sender = SenderState()
    handler = object.__new__(build_handler(TOKEN, state=RootState()))
    handler.command = "POST"
    handler.path = "/v1/client-health"
    handler._authorize = lambda: setattr(handler, "sender_tag", "target") or True

    def read_half_body():
        body_read_started.set()
        finish_body.wait()
        return {"schema_version": 1}

    handler._read_json = read_half_body
    handler._send = lambda code, payload: responses.append((code, payload))
    request = threading.Thread(target=handler.do_POST)

    def observe_state_lock():
        with sender.lock:
            observer_acquired_lock.set()

    observer = threading.Thread(target=observe_state_lock)
    request.start()
    try:
        assert body_read_started.wait(timeout=1)
        observer.start()
        assert observer_acquired_lock.wait(timeout=1)
    finally:
        finish_body.set()
        request.join(timeout=1)
        observer.join(timeout=1)

    assert not request.is_alive()
    assert not observer.is_alive()
    assert accepted == [({"schema_version": 1}, sender.sources)]
    assert responses == [(200, {"ok": True})]

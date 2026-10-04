"""Binding, locking and shutdown lifecycle of an explicit receiver application."""
from __future__ import annotations

import errno
import socket
import time
from http.server import ThreadingHTTPServer

from .process_lock import DataDirectoryInUse, DataDirectoryLock
from .receiver_http import build_handler
from .receiver_state import ReceiverState

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

def normalize_bind_host(host: str) -> str:
    """Strip URL brackets so socket.bind / inet_pton see a bare address."""
    value = host.strip()
    if value.startswith("[") and value.endswith("]"):
        return value[1:-1]
    return value


def is_ipv6_literal(host: str) -> bool:
    candidate = normalize_bind_host(host)
    if candidate in {"::", "::1"}:
        return True
    try:
        socket.inet_pton(socket.AF_INET6, candidate)
    except OSError:
        return False
    return True


def format_listen_url(host: str, port: int) -> str:
    display = normalize_bind_host(host)
    if is_ipv6_literal(display):
        return f"http://[{display}]:{port}"
    return f"http://{display}:{port}"


def http_server_class_for_host(host: str) -> type[ThreadingHTTPServer]:
    if not is_ipv6_literal(host):
        return ThreadingHTTPServer

    class IPv6ThreadingHTTPServer(ThreadingHTTPServer):
        address_family = socket.AF_INET6

    return IPv6ThreadingHTTPServer


def serve(*, state: ReceiverState, host: str, port: int, token: str,
          notify: bool = False, runner=None) -> None:
    try:
        with DataDirectoryLock(state.data_dir):
            (runner or _serve)(state=state, host=host, port=port, token=token, notify=notify)
    except DataDirectoryInUse:
        raise SystemExit("data directory already in use; stop the other receiver first") from None


def _serve(*, host: str, port: int, token: str, state: ReceiverState,
           notify: bool = False, server_factory=None) -> None:
    bind_host = normalize_bind_host(host)
    listen_url = format_listen_url(bind_host, port)
    if bind_host not in _LOOPBACK_HOSTS:
        print(
            f"[cookie-http-seeder] warning: binding {bind_host}; keep it private.",
            flush=True,
        )
    server_cls = (server_factory or http_server_class_for_host)(bind_host)
    # Tailscale / interface addresses may appear after the service starts.
    delay = 1.0
    while True:
        try:
            server = server_cls((bind_host, port), build_handler(token, state=state, notify=notify))
            break
        except OSError as error:
            if error.errno not in {errno.EADDRNOTAVAIL, errno.EADDRINUSE}:
                raise
            print(
                f"[cookie-http-seeder] bind {listen_url} failed ({error}); "
                f"retry in {delay:.0f}s",
                flush=True,
            )
            time.sleep(delay)
            delay = min(delay * 2, 30.0)
    # Drain accepted requests before releasing the directory lock on shutdown.
    server.daemon_threads = False
    server.block_on_close = True
    server.timeout = 30
    from .monitoring import StaleMonitor
    monitor = StaleMonitor(state, enabled=notify)
    try:
        server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    except OSError:
        pass
    print(
        f"[cookie-http-seeder] listening {listen_url} "
        f"(POST /v2/cookies, GET /v1/status, GET /healthz, notify={notify})",
        flush=True,
    )
    try:
        monitor.start()
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        monitor.stop()
        server.server_close()

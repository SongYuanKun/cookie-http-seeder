"""Loopback-only synthetic receiver/site for an isolated Chrome acceptance profile.

Run with --data-dir and --ready-file inside a disposable test directory. Contains
only a documented synthetic token and cookie; never reads real browser profiles.
"""
from __future__ import annotations

import argparse
import json
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cookie_http_seeder.receiver import ReceiverState, build_handler

TOKEN = "synthetic-browser-token-01234567"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--site-port", type=int, default=0)
    parser.add_argument("--receiver-port", type=int, default=0)
    args = parser.parse_args()
    fixture = {"generation": 1, "valid": True}

    class Site(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            if self.path == "/expire":
                fixture["valid"] = False
            if self.path == "/login":
                fixture["valid"] = True
                fixture["generation"] += 1
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            if self.path == "/login":
                self.send_header("Set-Cookie", f"sid=synthetic-{fixture['generation']}; "
                                 "Path=/; HttpOnly; SameSite=Lax")
            self.end_headers()
            marker = "ACCOUNT_OK" if fixture["valid"] else "LOGIN_REQUIRED"
            self.wfile.write(f"<!doctype html><h1>Synthetic account</h1><p>{marker}</p>"
                             '<a href="/login">Login with a new synthetic session</a>'.encode())

    site = ThreadingHTTPServer(("127.0.0.1", args.site_port), Site)
    state = ReceiverState({"local": {"domains": ["127.0.0.1"],
                                    "target_url": f"http://127.0.0.1:{site.server_port}/login"}},
                          args.data_dir)
    (args.data_dir / "sources.json").write_text(json.dumps({"sources": state.sources}))
    (args.data_dir / "cookie-receiver.token").write_text(TOKEN)
    (args.data_dir / "cookie-receiver.token").chmod(0o600)
    receiver = ThreadingHTTPServer(("127.0.0.1", args.receiver_port),
                                   build_handler(TOKEN, state=state))
    servers = (site, receiver)
    workers = [threading.Thread(target=s.serve_forever, kwargs={"poll_interval": .01})
               for s in servers]
    for worker in workers:
        worker.start()
    args.ready_file.parent.mkdir(parents=True, exist_ok=True)
    args.ready_file.write_text(json.dumps({"site_port": site.server_port,
                                         "receiver_port": receiver.server_port}))
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    try:
        stopped.wait()
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for worker in workers:
            worker.join()


if __name__ == "__main__":
    main()

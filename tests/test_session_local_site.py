"""A synthetic HTTPS site checks the actual Chrome storage import path."""
from __future__ import annotations

import json
import shutil
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

import pytest

from cookie_http_seeder.session_import import import_into_profile, prepare_import
from cookie_http_seeder.receiver import build_handler, configure
from test_session_bundle import PAYLOAD, _encrypted


@pytest.mark.skipif(not shutil.which("google-chrome") or not shutil.which("openssl"),
                    reason="local Chrome and OpenSSL required")
def test_portable_bundle_opens_synthetic_https_with_cookie_and_storage(tmp_path):
    pytest.importorskip("cryptography")
    playwright = pytest.importorskip("playwright.sync_api")
    key, cert = tmp_path / "key.pem", tmp_path / "cert.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", "/CN=localhost", "-keyout", str(key), "-out", str(cert)],
                   check=True, capture_output=True)

    class Site(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            content = b"<!doctype html><title>synthetic login state</title>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert, key)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"https://localhost:{server.server_port}/account"
        payload = {**PAYLOAD, "domains": ["localhost"],
                   "navigation": {**PAYLOAD["navigation"], "url": url, "final_url": url},
                   "cookies": [{"name": "sid", "value": "private-value", "domain": "localhost",
                                "path": "/account", "secure": True, "hostOnly": True}],
                   "origins": [{"origin": f"https://localhost:{server.server_port}",
                                "localStorage": [{"name": "token", "value": "private-value"}],
                                "sessionStorage": [{"name": "page", "value": "ready"}]}]}
        receiver_data = tmp_path / "receiver"
        configure(sources={"site": {"domains": ["localhost"]}}, data_dir=receiver_data)
        receiver = ThreadingHTTPServer(("127.0.0.1", 0), build_handler("test-token-0123456789ab"))
        receiver_thread = threading.Thread(target=receiver.serve_forever, daemon=True)
        receiver_thread.start()
        try:
            base = f"http://127.0.0.1:{receiver.server_port}"
            headers = {"Authorization": "Bearer test-token-0123456789ab",
                       "Content-Type": "application/json"}
            config = json.loads(urlopen(Request(base + "/v1/sources", headers=headers)).read())
            upload = {"source": "site", "envelope": _encrypted(payload),
                      "config_revision": config["revision"], "expected_version": None,
                      "request_id": "11111111-1111-4111-8111-111111111111"}
            req = Request(base + "/v3/session-bundles", headers=headers,
                          data=json.dumps(upload).encode(), method="POST")
            posted = json.loads(urlopen(req).read())
            assert posted["bundle_version"]
        finally:
            receiver.shutdown()
            receiver.server_close()
            receiver_thread.join(3)
        bundle = receiver_data / "site-session.json"
        profile = tmp_path / "separate-chrome"
        prepared = prepare_import("site", bundle, {"site": {"enabled": True,
                                                              "domains": ["localhost"]}},
                                  "correct passphrase", profile)
        observed = {}

        with playwright.sync_playwright() as runner:
            class PageAdapter:
                def __init__(self, page):
                    self.page = page

                def goto(self, destination, **kwargs):
                    self.page.goto(destination, **kwargs)
                    observed["account"] = self.page.evaluate("""() => ({
                      cookie: document.cookie,
                      local: localStorage.getItem('token'),
                      session: sessionStorage.getItem('page')
                    })""")
                    self.page.goto(f"https://localhost:{server.server_port}/other")
                    observed["other"] = self.page.evaluate("document.cookie")
                    self.page.goto(f"https://localhost:{server.server_port}/account/")
                    observed["account_slash"] = self.page.evaluate("document.cookie")
                    self.page.goto(f"https://localhost:{server.server_port}/account/child")
                    observed["child"] = self.page.evaluate("document.cookie")

            class ContextAdapter:
                def __init__(self, context):
                    self.context = context

                def set_storage_state(self, value):
                    self.context.set_storage_state(value)

                def add_init_script(self, script):
                    self.context.add_init_script(script=script)

                def new_page(self):
                    return PageAdapter(self.context.new_page())

                def new_cdp_session(self, page):
                    return self.context.new_cdp_session(page.page)

                def close(self):
                    self.context.close()

            def launch(path):
                context = runner.chromium.launch_persistent_context(
                    str(path), channel="chrome", headless=True, ignore_https_errors=True)
                return ContextAdapter(context)

            import_into_profile(prepared, profile, open_url=url, launcher=launch)
        assert observed == {"account": {"cookie": "sid=private-value",
                                       "local": "private-value", "session": "ready"},
                            "other": "", "account_slash": "sid=private-value",
                            "child": "sid=private-value"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)

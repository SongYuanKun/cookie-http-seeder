from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from cookie_http_seeder.paths import atomic_write_text, default_data_dir
from cookie_http_seeder.receiver import (
    build_handler,
    configure,
    cookies_to_header,
    ensure_token_file,
    format_listen_url,
    http_server_class_for_host,
    is_ipv6_literal,
    normalize_bind_host,
)
from cookie_http_seeder.store import load_cookie_header, load_sources, save_cookie_header


class StoreTests(unittest.TestCase):
    def test_roundtrip(self) -> None:
        path = Path(self._testMethodName + ".json")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        save_cookie_header("a=1; b=2", path, updated_at="2026-09-16T00:00:00Z")
        self.assertEqual(load_cookie_header(path), "a=1; b=2")
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["updatedAt"], "2026-09-16T00:00:00Z")

    def test_atomic_write_leaves_no_tmp(self) -> None:
        path = Path(self._testMethodName + ".json")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        atomic_write_text(path, '{"ok":true}\n')
        self.assertTrue(path.is_file())
        leftovers = list(path.parent.glob(f".{path.name}.*.tmp"))
        self.assertEqual(leftovers, [])

    def test_load_sources_from_examples(self) -> None:
        sources = load_sources(Path("examples/sources.json"))
        self.assertIn("fang", sources)
        self.assertIn("beike", sources)

    def test_load_sources_from_data_dir_override(self) -> None:
        data_dir = Path(tempfile.mkdtemp(prefix="chs-data-"))
        self.addCleanup(lambda: shutil.rmtree(data_dir, ignore_errors=True))
        (data_dir / "sources.json").write_text(
            json.dumps({"sources": {"mysite": {"domains": ["example.com"]}}}),
            encoding="utf-8",
        )
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("COOKIE_HTTP_SEEDER_SOURCES", None)
            sources = load_sources(data_dir=data_dir)
        self.assertEqual(list(sources), ["mysite"])

    def test_load_sources_packaged_fallback(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("COOKIE_HTTP_SEEDER_SOURCES", None)
            with mock.patch("cookie_http_seeder.store._REPO_ROOT", Path("/nonexistent")):
                with mock.patch("cookie_http_seeder.store.Path.cwd", return_value=Path("/tmp")):
                    with mock.patch(
                        "cookie_http_seeder.store.sources_override_file",
                        return_value=Path("/nonexistent/sources.json"),
                    ):
                        sources = load_sources()
        self.assertIn("fang", sources)
        self.assertTrue(sources["fang"]["domains"])

    def test_default_data_dir_prefers_env(self) -> None:
        with mock.patch.dict(os.environ, {"COOKIE_HTTP_SEEDER_DATA": "/tmp/chs-data"}):
            self.assertEqual(default_data_dir(), Path("/tmp/chs-data"))


class ReceiverTests(unittest.TestCase):
    def test_cookies_to_header_dedupes(self) -> None:
        self.assertEqual(
            cookies_to_header(
                [
                    {"name": "a", "value": "1"},
                    {"name": "b", "value": "2"},
                    {"name": "a", "value": "3"},
                ]
            ),
            "a=1; b=2",
        )

    def test_http_auth_and_push(self) -> None:
        data_dir = Path(self._testMethodName + "-data")
        data_dir.mkdir(exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(data_dir, ignore_errors=True))
        configure(sources={"fang": {"domains": [".fang.com"]}}, data_dir=data_dir)
        token = "test-token-0123456789ab"
        server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(token))
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{port}"

        with urllib.request.urlopen(f"{base}/healthz", timeout=3) as response:
            self.assertEqual(response.status, 200)

        request = urllib.request.Request(
            f"{base}/v1/cookies",
            data=json.dumps({"source": "fang", "cookie_header": "k=v"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request, timeout=3)
        self.assertEqual(raised.exception.code, 401)

        request = urllib.request.Request(
            f"{base}/v1/cookies",
            data=json.dumps({"source": "fang", "cookie_header": "k=v"}).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            body = json.loads(response.read())
        self.assertTrue(body["ok"])
        self.assertEqual(load_cookie_header(source="fang", data_dir=data_dir), "k=v")

    def test_ensure_token_file(self) -> None:
        path = Path(self._testMethodName + "-token")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        first = ensure_token_file(path)
        second = ensure_token_file(path)
        self.assertEqual(first, second)

    def test_ipv6_bind_helpers(self) -> None:
        self.assertEqual(normalize_bind_host("[::1]"), "::1")
        self.assertTrue(is_ipv6_literal("fd7a:115c:a1e0::dc34:822e"))
        self.assertFalse(is_ipv6_literal("127.0.0.1"))
        self.assertEqual(
            format_listen_url("fd7a:115c:a1e0::dc34:822e", 18765),
            "http://[fd7a:115c:a1e0::dc34:822e]:18765",
        )
        self.assertIs(http_server_class_for_host("127.0.0.1"), ThreadingHTTPServer)
        self.assertEqual(
            http_server_class_for_host("::1").address_family,
            __import__("socket").AF_INET6,
        )


if __name__ == "__main__":
    unittest.main()

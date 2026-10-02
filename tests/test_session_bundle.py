from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from cookie_http_seeder.session_bundle import decrypt_bundle, validate_payload

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = {
    "schema_version": 1, "source": "site", "domains": ["example.com"],
    "captured_at": "2026-09-28T00:00:00Z",
    "navigation": {"url": "https://example.com/account", "method": "GET",
                   "final_url": "https://example.com/account", "status": 200,
                   "headers": [{"name": "Cookie", "value": "sid=private-value"}]},
    "cookies": [{"name": "sid", "value": "private-value", "domain": ".example.com",
                 "path": "/", "secure": True}],
    "origins": [{"origin": "https://example.com",
                 "localStorage": [{"name": "token", "value": "private-value"}],
                 "sessionStorage": []}],
    "environment": {"userAgent": "test browser", "timezone": "Asia/Shanghai"},
    "location": {"city": "", "district": "", "source": "not_observed"},
    "coverage": {"cookies": "captured", "localStorage": "selected_origin_only",
                 "sessionStorage": "selected_tab_origin_only",
                 "requestHeaders": "one_top_level_navigation", "indexedDB": "unsupported",
                 "partitionedCookies": "unsupported", "serviceWorkers": "unsupported",
                 "deviceBoundCredentials": "unsupported", "siteEgressIP": "not_observed",
                 "geolocation": "not_observed"},
}


def _encrypted(payload: dict) -> dict:
    script = """import {encryptBundle} from './extension/session_bundle.js';
const chunks=[]; for await (const chunk of process.stdin) chunks.push(chunk);
const payload=JSON.parse(Buffer.concat(chunks).toString());
process.stdout.write(JSON.stringify(await encryptBundle(payload,'correct passphrase')));"""
    result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT,
                            input=json.dumps(payload), text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


def test_python_decrypts_browser_encrypted_bundle_without_plaintext_envelope():
    envelope = _encrypted(PAYLOAD)
    assert "private-value" not in json.dumps(envelope)
    assert decrypt_bundle(envelope, "correct passphrase") == PAYLOAD
    with pytest.raises(ValueError):
        decrypt_bundle(envelope, "wrong passphrase")


def test_python_rejects_foreign_origin_and_navigation():
    with pytest.raises(ValueError):
        validate_payload({**PAYLOAD, "origins": [{"origin": "https://evil.test"}]},
                         ["example.com"])
    with pytest.raises(ValueError):
        validate_payload({**PAYLOAD, "navigation": {**PAYLOAD["navigation"],
                                                 "url": "https://evil.test/"}},
                         ["example.com"])
    with pytest.raises(ValueError):
        validate_payload({**PAYLOAD, "coverage": {**PAYLOAD["coverage"],
                                                 "indexedDB": "captured"}}, ["example.com"])

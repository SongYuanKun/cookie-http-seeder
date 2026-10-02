"""Import must authenticate and scope-check before opening a fresh browser profile."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_session_bundle import PAYLOAD, _encrypted

from cookie_http_seeder.session_import import import_into_profile, prepare_import


@pytest.fixture
def stored(tmp_path):
    path = tmp_path / "site-session.json"
    path.write_text(json.dumps({"source": "site", "envelope": _encrypted(PAYLOAD),
                                "bundle_version": "a" * 32}), encoding="utf-8")
    path.chmod(0o600)
    return path


def test_wrong_passphrase_and_scope_rejection_leave_profile_absent(stored: Path):
    profile = stored.parent / "fresh-chrome-profile"
    spec = {"site": {"domains": ["example.com"], "enabled": True}}
    with pytest.raises(ValueError):
        prepare_import("site", stored, spec, "wrong passphrase", profile)
    assert not profile.exists()
    with pytest.raises(ValueError):
        prepare_import("site", stored, {"site": {"domains": ["evil.test"],
                                               "enabled": True}}, "correct passphrase", profile)
    assert not profile.exists()


def test_import_populates_cookie_and_web_storage_in_new_profile(stored: Path):
    profile = stored.parent / "new-profile"
    prepared = prepare_import("site", stored, {"site": {"domains": ["example.com"],
                                                   "enabled": True}}, "correct passphrase", profile)
    calls = {}

    class FakePage:
        def goto(self, url, **kwargs):
            calls["url"] = url

    class FakeContext:
        def set_storage_state(self, value):
            calls["state"] = value

        def add_init_script(self, script):
            calls["script"] = script

        def new_page(self):
            return FakePage()

        def close(self):
            calls["closed"] = True

    def launcher(path):
        calls["profile"] = path
        return FakeContext()

    import_into_profile(prepared, profile, open_url="https://example.com/account",
                        launcher=launcher)
    assert calls["profile"] == profile
    assert calls["state"]["cookies"][0]["value"] == "private-value"
    assert calls["state"]["origins"][0]["localStorage"][0]["value"] == "private-value"
    assert "sessionStorage" in calls["script"]
    assert calls["url"] == "https://example.com/account"
    assert calls["closed"] is True
    assert profile.is_dir() and profile.stat().st_mode & 0o777 == 0o700


def test_existing_profile_rejected(stored: Path):
    profile = stored.parent / "existing"
    profile.mkdir()
    with pytest.raises(ValueError):
        prepare_import("site", stored, {"site": {"domains": ["example.com"],
                                               "enabled": True}}, "correct passphrase", profile)

"""The distributable client contains its complete module graph and no loose data."""
from __future__ import annotations

import json
import shutil
import tomllib
import zipfile
from pathlib import Path

import pytest

from cookie_http_seeder import __version__
from scripts.build_extension import build_extension

ROOT = Path(__file__).resolve().parents[1]


def test_build_complete_versioned_client_without_extra_sensitive_files(tmp_path):
    source = tmp_path / "extension"
    shutil.copytree(ROOT / "extension", source)
    (source / "cookie-receiver.token").write_text("synthetic-excluded-token")
    (source / "cookies.json").write_text("synthetic-excluded-cookies")
    (source / "unreferenced-debug.js").write_text("synthetic-excluded-debug")
    archive = build_extension(source, tmp_path / "dist")
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    with zipfile.ZipFile(archive) as package:
        names = set(package.namelist())
        manifest = json.loads(package.read("manifest.json"))
        assert manifest["version"] == __version__ == metadata["project"]["version"] == "0.4.0"
        assert {"login_monitor.js", "sender_summary.js", "background.js", "options.js",
                "options.html", "popup.html", "settings_lock.js", "icons/icon128.png"} <= names
        assert "cookie-receiver.token" not in names
        assert "cookies.json" not in names
        assert "unreferenced-debug.js" not in names
        assert all(not name.startswith("/") and ".." not in Path(name).parts for name in names)
        assert package.testzip() is None
    assert archive.name == "cookie-http-seeder-extension-0.4.0.zip"
    original = archive.read_bytes()
    assert build_extension(source, tmp_path / "second-dist").read_bytes() == original


def test_missing_imported_module_rejects_incomplete_client(tmp_path):
    source = tmp_path / "extension"
    shutil.copytree(ROOT / "extension", source)
    (source / "login_monitor.js").unlink()
    with pytest.raises(ValueError, match="missing"):
        build_extension(source, tmp_path / "dist")
    assert not list((tmp_path / "dist").glob("*.zip"))


def test_symlinked_asset_is_not_packaged(tmp_path):
    source = tmp_path / "extension"
    shutil.copytree(ROOT / "extension", source)
    icon = source / "icons/icon128.png"
    icon.unlink()
    target = tmp_path / "sensitive.bin"
    target.write_bytes(b"synthetic-excluded-file")
    icon.symlink_to(target)
    with pytest.raises(ValueError):
        build_extension(source, tmp_path / "dist")

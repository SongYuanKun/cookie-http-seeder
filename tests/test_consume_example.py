"""Keep the documented sender-tag example executable without real network calls."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def example(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "examples" / "consume_cookies.py"
    spec = importlib.util.spec_from_file_location("consume_example_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    opener = MagicMock()
    opener.open.return_value.__enter__.return_value.status = 200
    monkeypatch.setattr(module, "build_opener", MagicMock(return_value=opener))
    return module, opener


def snapshot(root, tag="default", value="example-default", *, empty=False):
    directory = root if tag == "default" else root / "senders" / tag
    directory.mkdir(parents=True, exist_ok=True)
    cookies = [] if empty else [{
        "name": "sid", "value": value, "domain": "example.com", "path": "/",
        "hostOnly": True, "secure": True, "httpOnly": True, "session": True,
        "sameSite": "lax", "storeId": "0",
    }]
    document = {
        "schema_version": 2, "source": "mysite", "domains": ["example.com"],
        "target_url": "https://example.com/", "cookies": cookies, "cleared": empty,
        "updatedAt": "2026-09-21T00:00:00Z", "snapshot_version": "a" * 32,
    }
    (directory / "mysite-cookies.json").write_text(json.dumps(document), encoding="utf-8")


def test_inspect_default_without_network(example, tmp_path, capsys):
    module, opener = example
    snapshot(tmp_path)
    assert module.main(["mysite", "--data-dir", str(tmp_path)]) == 0
    assert "sender_tag=default" in capsys.readouterr().out
    opener.open.assert_not_called()


def test_inspect_selected_sender_without_network(example, tmp_path, capsys):
    module, opener = example
    snapshot(tmp_path, "home-pc", "example-home")
    assert module.main([
        "mysite", "--data-dir", str(tmp_path), "--sender-tag", "home-pc",
    ]) == 0
    output = capsys.readouterr().out
    assert "sender_tag=home-pc" in output
    assert "example-home" not in output
    opener.open.assert_not_called()


def test_missing_sender_never_falls_back_to_default(example, tmp_path):
    module, opener = example
    snapshot(tmp_path)
    assert module.main([
        "mysite", "--data-dir", str(tmp_path), "--sender-tag", "missing-pc",
    ]) == 1
    opener.open.assert_not_called()


@pytest.mark.parametrize("tag", ["home-pc", "work-pc"])
def test_request_uses_only_selected_sender(example, tmp_path, capsys, tag):
    module, opener = example
    for label in ("default", "home-pc", "work-pc"):
        snapshot(tmp_path, label, f"example-{label}")
    assert module.main([
        "mysite", "--data-dir", str(tmp_path), "--sender-tag", tag,
        "--url", "https://example.com/",
    ]) == 0
    request = opener.open.call_args.args[0]
    assert request.get_header("Cookie") == f"sid=example-{tag}"
    assert opener.open.call_args.kwargs["timeout"] == 15
    assert isinstance(module.build_opener.call_args.args[0], module.NoRedirect)
    assert "sid=" not in capsys.readouterr().out


def test_invalid_sender_is_rejected_before_read_or_network(example, tmp_path):
    module, opener = example
    with pytest.raises(SystemExit) as error:
        module.main(["mysite", "--data-dir", str(tmp_path), "--sender-tag", "../other"])
    assert error.value.code == 2
    opener.open.assert_not_called()
    assert not list(tmp_path.iterdir())


def test_empty_sender_never_reuses_default_cookie(example, tmp_path):
    module, opener = example
    snapshot(tmp_path)
    snapshot(tmp_path, "home-pc", empty=True)
    assert module.main([
        "mysite", "--data-dir", str(tmp_path), "--sender-tag", "home-pc",
        "--url", "https://example.com/",
    ]) == 1
    opener.open.assert_not_called()


def test_out_of_scope_url_is_not_requested(example, tmp_path):
    module, opener = example
    snapshot(tmp_path, "home-pc")
    assert module.main([
        "mysite", "--data-dir", str(tmp_path), "--sender-tag", "home-pc",
        "--url", "https://other.example/",
    ]) == 1
    opener.open.assert_not_called()

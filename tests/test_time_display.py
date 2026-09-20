from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime

import pytest

from cookie_http_seeder.time_display import display_times, format_local_time


@pytest.mark.parametrize(("zone", "value", "expected"), [
    ("Asia/Shanghai", "2026-09-20T00:49:34Z", "2026-09-20 08:49:34"),
    ("Asia/Singapore", "2026-09-20T00:49:34Z", "2026-09-20 08:49:34"),
    ("UTC", "2026-09-20T00:49:34Z", "2026-09-20 00:49:34"),
    ("America/New_York", "2026-09-20T00:49:34Z", "2026-09-19 20:49:34"),
    ("Asia/Kathmandu", "2026-09-20T00:49:34Z", "2026-09-20 06:34:34"),
    ("Asia/Shanghai", "2026-09-20T23:59:59Z", "2026-09-21 07:59:59"),
    ("UTC", "2026-09-20T08:49:34.123456+08:00", "2026-09-20 00:49:34"),
    ("America/New_York", "2026-03-08T06:59:59Z", "2026-03-08 01:59:59"),
    ("America/New_York", "2026-03-08T07:00:00Z", "2026-03-08 03:00:00"),
])
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX TZ subprocess tests")
def test_local_time_in_system_timezone(zone, value, expected):
    script = (
        "from cookie_http_seeder.time_display import format_local_time; "
        f"print(format_local_time({value!r}))"
    )
    output = subprocess.check_output([sys.executable, "-c", script],
                                     env={**os.environ, "TZ": zone}, text=True)
    assert output.strip() == expected


@pytest.mark.parametrize("value", [
    None, "", "invalid", "2026-09-20", "2026-09-20T00:49:34", "2026-09-20 08:49:34",
    "2026-02-30T01:00:00Z", "2026-13-01T00:00:00Z", "2026-09-20T24:00:00Z",
    0, True, [], {}, float("nan"), datetime(2026, 9, 20),
])
def test_invalid_or_ambiguous_time_is_placeholder(value):
    assert format_local_time(value) == "-"


def test_aware_datetime_uses_same_format_as_iso():
    value = datetime(2026, 9, 20, 0, 49, 34, tzinfo=UTC)
    assert format_local_time(value) == format_local_time("2026-09-20T00:49:34Z")


def test_display_copy_changes_only_time_fields():
    instant = "2026-09-20T00:49:34Z"
    original = {"sources": {"site": {"updatedAt": instant, "lastSeenAt": instant,
                                   "ageSeconds": 60, "snapshot_version": instant}},
                "observedAt": instant, "details": [{"checkedAt": None}, instant]}
    before = json.dumps(original)
    result = display_times(original)
    assert result["sources"]["site"]["updatedAt"] == format_local_time(instant)
    assert result["sources"]["site"]["ageSeconds"] == 60
    assert result["sources"]["site"]["snapshot_version"] == instant
    assert result["details"] == [{"checkedAt": "-"}, instant]
    assert json.dumps(original) == before


@pytest.mark.parametrize("raw_json", [False, True])
def test_status_cli_display_and_machine_output(monkeypatch, capsys, tmp_path, raw_json):
    from cookie_http_seeder import cli

    instant = "2026-09-20T00:49:34Z"
    document = {"ok": True, "observedAt": instant,
                "sources": {"site": {"updatedAt": instant, "lastSeenAt": None}}}
    before = json.dumps(document)
    monkeypatch.setattr(cli, "ensure_data_dir", lambda path: path)
    monkeypatch.setattr(cli, "load_sources", lambda *a, **kw: {})
    monkeypatch.setattr(cli, "configure", lambda **kw: None)
    monkeypatch.setattr(cli, "status_document", lambda: document)
    monkeypatch.setattr(cli, "_paths_document", lambda path: {})
    args = ["--data-dir", str(tmp_path), "status"] + (["--json"] if raw_json else [])
    assert cli.main(args) == 0
    output = json.loads(capsys.readouterr().out)
    assert output == (document if raw_json else display_times(document))
    assert json.dumps(document) == before


@pytest.mark.parametrize("args", [
    ["status"], ["doctor", "--local-only"],
    ["report", "site", "--snapshot-version", "a" * 32,
     "--result", "invalid", "--reason-code", "session_expired"],
])
def test_json_flag_is_available_for_all_human_status_commands(args):
    from cookie_http_seeder.cli import _build_parser

    assert not _build_parser().parse_args(args).raw_json
    assert _build_parser().parse_args([*args, "--json"]).raw_json


@pytest.mark.parametrize("operation", ["pushed", "needed"])
def test_notification_time_is_local_without_network(monkeypatch, tmp_path, operation):
    from cookie_http_seeder import notify

    fixed = datetime(2026, 9, 20, 0, 49, 34, tzinfo=UTC)

    class Clock:
        @staticmethod
        def now(tz):
            return fixed

    sent = []
    monkeypatch.setattr(notify, "datetime", Clock)
    monkeypatch.setattr(notify, "resolve_webhook_path", lambda path: tmp_path / "unused")
    monkeypatch.setattr(notify, "_read_webhook", lambda path: "unused-test-destination")
    monkeypatch.setattr(notify, "_post_text", lambda destination, text: sent.append(text))
    result = (notify.notify_pushed(sources=["site"]) if operation == "pushed"
              else notify.notify_needed(reason="session_expired"))
    assert result == "sent"
    assert len(sent) == 1
    assert f"time: {format_local_time(fixed)} (receiver local time)" in sent[0]
    assert fixed.isoformat() not in sent[0]

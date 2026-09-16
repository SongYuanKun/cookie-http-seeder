"""Optional webhook notifier (Feishu/Lark text bot compatible)."""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

from .paths import webhook_file

_HOOK = re.compile(
    r"https://open\.feishu\.cn/open-apis/bot/v2/hook/"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)

HUMAN_ACTION = (
    "Open the cookie-http-seeder Chrome extension on your daily browser, "
    "ensure the receiver is up, SSH-forward port 18765 if needed, then Push now."
)


def resolve_webhook_path(
    explicit: Path | None = None, *, data_dir: Path | None = None
) -> Path | None:
    if explicit is not None:
        return explicit
    candidate = webhook_file(data_dir)
    # env override is already handled inside webhook_file()
    try:
        if candidate.exists():
            return candidate
    except OSError:
        return None
    return None


def _read_webhook(path: Path) -> str:
    mode = path.stat().st_mode & 0o777
    if mode != 0o600:
        raise ValueError("webhook file mode must be 0600")
    value = path.read_text(encoding="utf-8").strip()
    if not _HOOK.fullmatch(value):
        raise ValueError("webhook URL format is invalid")
    return value


def _post_text(webhook: str, text: str) -> None:
    body = json.dumps(
        {"msg_type": "text", "content": {"text": text[:1500]}},
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        webhook,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        raw = response.read(65_536)
    result = json.loads(raw.decode("utf-8"))
    if not isinstance(result, dict) or result.get("code") != 0:
        raise RuntimeError("webhook rejected notification")


def notify_pushed(*, sources: list[str], webhook_path: Path | None = None) -> str:
    path = resolve_webhook_path(webhook_path)
    if path is None:
        return "skipped: no webhook file"
    try:
        webhook = _read_webhook(path)
        text = (
            "【cookie-http-seeder】cookies updated\n"
            f"sources: {','.join(sources)}\n"
            "Next crawl can reuse the new cookie_header files."
        )
        _post_text(webhook, text)
    except Exception as error:  # noqa: BLE001
        return f"failed: {type(error).__name__}: {error}"
    return "sent"


def notify_needed(*, reason: str, webhook_path: Path | None = None) -> str:
    path = resolve_webhook_path(webhook_path)
    if path is None:
        return "skipped: no webhook file"
    try:
        webhook = _read_webhook(path)
        text = (
            "【cookie-http-seeder】cookies need refresh\n"
            f"reason: {reason[:240]}\n"
            f"action: {HUMAN_ACTION}"
        )
        _post_text(webhook, text)
    except Exception as error:  # noqa: BLE001
        return f"failed: {type(error).__name__}: {error}"
    return "sent"

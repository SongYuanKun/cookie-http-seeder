"""Import supported browser state into a brand-new, dedicated Chrome profile."""
from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .cookies import allowed_domain, domain_name, request_url
from .session_bundle import decrypt_bundle, validate_payload


@dataclass(frozen=True)
class PreparedImport:
    source: str
    payload: dict
    bundle_version: str


def read_passphrase(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("passphrase file must be a regular file")
    if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise ValueError("passphrase file must have mode 0600")
    raw = path.read_bytes()
    if len(raw) > 4096:
        raise ValueError("passphrase file is too large")
    return raw.decode("utf-8").rstrip("\r\n")


def prepare_import(source: str, bundle: Path, sources: dict, passphrase: str,
                   profile: Path) -> PreparedImport:
    """Finish all credential and scope checks before creating a profile directory."""
    spec = sources.get(source)
    if not isinstance(spec, dict) or spec.get("enabled") is not True:
        raise ValueError("source is unknown or paused")
    if profile.exists() or profile.is_symlink():
        raise ValueError("profile directory already exists; choose a new dedicated path")
    if bundle.is_symlink() or not bundle.is_file() or bundle.stat().st_size > 768 * 1024:
        raise ValueError("invalid bundle file")
    if os.name == "posix" and stat.S_IMODE(bundle.stat().st_mode) != 0o600:
        raise ValueError("bundle file must have mode 0600")
    try:
        doc = json.loads(bundle.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or doc.get("source") != source:
            raise ValueError("bundle source mismatch")
        payload = decrypt_bundle(doc["envelope"], passphrase)
        validate_payload(payload, spec["domains"])
        if payload["source"] != source:
            raise ValueError("bundle source mismatch")
        version = doc.get("bundle_version", "")
        if not isinstance(version, str) or len(version) != 32:
            raise ValueError("invalid bundle version")
        return PreparedImport(source, payload, version)
    except (KeyError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid encrypted session bundle") from exc


def _browser_cookie(item: dict) -> dict:
    result: dict[str, Any] = {"name": item["name"], "value": item["value"],
                              "path": item["path"], "secure": item.get("secure", False),
                              "httpOnly": item.get("httpOnly", False),
                              "domain": item["domain"]}
    if not item.get("session", True) and item.get("expirationDate") is not None:
        result["expires"] = item["expirationDate"]
    same_site = {"lax": "Lax", "strict": "Strict", "no_restriction": "None"}.get(
        item.get("sameSite"))
    if same_site:
        result["sameSite"] = same_site
    return result


def _host_only_cookie(item: dict) -> dict:
    """CDP preserves host-only plus exact path; Playwright's URL form derives a path."""
    browser = _browser_cookie(item)
    browser.pop("domain")
    browser["url"] = f"https://{domain_name(item['domain'])}/"
    return browser


def _session_script(origins: list[dict]) -> str:
    values = {item["origin"]: item["sessionStorage"] for item in origins}
    encoded = json.dumps(values, ensure_ascii=True, separators=(",", ":"))
    return ("(() => { const origins = " + encoded + "; const entries = origins[location.origin]; "
            "if (entries) for (const item of entries) { "
            "if (sessionStorage.getItem(item.name) === null) "
            "sessionStorage.setItem(item.name, item.value); } })();")


def import_into_profile(prepared: PreparedImport, profile: Path, *, open_url: str | None = None,
                        launcher: Callable[[Path], Any] | None = None, hold: bool = False) -> dict:
    domains = prepared.payload["domains"]
    if open_url:
        scheme, host, _ = request_url(open_url)
        if scheme != "https" or not allowed_domain(host, domains):
            raise ValueError("open URL outside approved HTTPS domains")
    if profile.exists() or profile.is_symlink():
        raise ValueError("profile directory already exists")
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.mkdir(mode=0o700)
    play = None
    context = None
    try:
        if launcher is None:
            from playwright.sync_api import sync_playwright
            play = sync_playwright().start()
            context = play.chromium.launch_persistent_context(str(profile), channel="chrome",
                                                               headless=False)
        else:
            context = launcher(profile)
        origins = prepared.payload["origins"]
        captured_cookies = prepared.payload["cookies"]
        storage_state = {
            "cookies": [_browser_cookie(c) for c in captured_cookies if not c.get("hostOnly")],
            "origins": [{"origin": o["origin"], "localStorage": o["localStorage"]}
                        for o in origins],
        }
        context.set_storage_state(storage_state)
        context.add_init_script(_session_script(origins))
        host_only = [c for c in captured_cookies if c.get("hostOnly")]
        page = context.new_page() if host_only or open_url else None
        if host_only:
            session = context.new_cdp_session(page)
            try:
                session.send("Network.enable")
                for cookie in host_only:
                    result = session.send("Network.setCookie", _host_only_cookie(cookie))
                    if result.get("success") is not True:
                        raise ValueError("Chrome rejected a host-only cookie")
            finally:
                session.detach()
        if open_url:
            page.goto(open_url, wait_until="domcontentloaded", timeout=30000)
            if hold:
                print("独立 Chrome 已打开；关闭该标签后命令结束。", flush=True)
                page.wait_for_event("close", timeout=0)
        coverage = {**prepared.payload["coverage"],
                    "sessionStorageImport": "active_tab_only" if open_url else
                    "not_applied_without_open_url"}
        return {"ok": True, "source": prepared.source,
                "bundle_version": prepared.bundle_version,
                "cookie_count": len(prepared.payload["cookies"]),
                "storage_origin_count": len(origins),
                "coverage": coverage}
    finally:
        if context is not None:
            context.close()
        if play is not None:
            play.stop()

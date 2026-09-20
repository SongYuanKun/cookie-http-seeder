"""Validate non-partitioned snapshots and select cookies for one request URL.

SameSite is retained, not simulated: an HTTP client has no browser site context.
Partitioned cookies and mixed stores are unsupported and fail closed.
"""
from __future__ import annotations

import ipaddress
import re
import time
from typing import Any
from urllib.parse import unquote, urlsplit

SOURCE_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
VALUE = re.compile(r'^[\x21\x23-\x2b\x2d-\x3a\x3c-\x5b\x5d-\x7e]*$')
LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
SAME_SITE = {"unspecified", "no_restriction", "lax", "strict"}


def domain_name(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 253:
        raise ValueError("invalid domain")
    domain = value.removeprefix(".").lower()
    if not all(LABEL.fullmatch(label) for label in domain.split(".")):
        raise ValueError("use an ASCII hostname (punycode for IDNs), not a URL/wildcard")
    if "." not in domain and domain != "localhost":
        raise ValueError("use a qualified hostname or localhost")
    if re.fullmatch(r"[0-9.]+", domain):
        try:
            ipaddress.IPv4Address(domain)
        except ValueError as error:
            raise ValueError("use a canonical IPv4 address") from error
    # WHATWG URL parsers treat numeric final labels as IPv4, not DNS names.
    elif domain.split(".")[-1].isdigit() or domain.split(".")[-1].startswith("0x"):
        raise ValueError("ambiguous numeric hostname")
    return domain


def domain_matches(host: str, domain: str) -> bool:
    if host == domain:
        return True
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        return host.endswith("." + domain)


def allowed_domain(host: str, domains: list[str]) -> bool:
    return any(domain_matches(host, domain) for domain in domains)


def request_url(value: object) -> tuple[str, str, str]:
    if not isinstance(value, str) or len(value) > 8192:
        raise ValueError("invalid request URL")
    if any(ord(c) <= 32 or ord(c) >= 127 for c in value) or "\\" in value:
        raise ValueError("use an ASCII, percent-encoded request URL")
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError as error:
        raise ValueError("invalid request URL") from error
    if (url.scheme not in {"http", "https"} or not url.hostname
            or url.username is not None or url.password is not None or url.fragment
            or "#" in value or port == 0):
        raise ValueError("use an HTTP(S) URL without credentials or fragment")
    path = url.path or "/"
    if any(unquote(part).lower() in {".", ".."} for part in path.split("/")):
        raise ValueError("normalize dot segments before selecting cookies")
    return url.scheme, domain_name(url.hostname), path


def normalize_sources(raw: object) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict) or len(raw) > 100:
        raise ValueError("sources must be an object with at most 100 entries")
    result: dict[str, dict[str, Any]] = {}
    for name, spec in raw.items():
        if not isinstance(name, str) or not SOURCE_NAME.fullmatch(name):
            raise ValueError("invalid source name")
        if not isinstance(spec, dict) or set(spec) - {"domains", "label", "target_url", "enabled"}:
            raise ValueError("invalid source fields; do not import credentials")
        values = spec.get("domains")
        if not isinstance(values, list) or not 1 <= len(values) <= 32:
            raise ValueError("each source requires 1 to 32 domains")
        domains = list(dict.fromkeys(domain_name(item) for item in values))
        label, enabled = spec.get("label", name), spec.get("enabled", True)
        if (not isinstance(label, str) or not 1 <= len(label) <= 80
                or any(ord(c) < 32 or ord(c) == 127 for c in label)):
            raise ValueError("invalid source label")
        if type(enabled) is not bool:
            raise ValueError("enabled must be boolean")
        target = spec.get("target_url", "")
        if not isinstance(target, str):
            raise ValueError("invalid target_url")
        if target and not allowed_domain(request_url(target)[1], domains):
            raise ValueError("target URL is outside the source allow-list")
        result[name] = {
            "label": label, "domains": domains, "target_url": target, "enabled": enabled
        }
    return result


def normalize_cookies(raw: object, domains: list[str]) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or len(raw) > 1000:
        raise ValueError("cookies must be an array with at most 1000 entries")
    result: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    stores: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("cookie must be an object")
        if item.get("partitionKey") is not None:
            raise ValueError("partitioned cookies are unsupported in phase 1")
        name, value, path = item.get("name"), item.get("value"), item.get("path")
        if not isinstance(name, str) or len(name) > 1024 or not NAME.fullmatch(name):
            raise ValueError("invalid cookie name")
        if not isinstance(value, str) or len(value) > 16384:
            raise ValueError("invalid cookie value")
        unquoted = value[1:-1] if len(value) >= 2 and value[0] == value[-1] == '"' else value
        if not VALUE.fullmatch(unquoted):
            raise ValueError("invalid cookie value octets")
        domain = domain_name(item.get("domain"))
        if not allowed_domain(domain, domains):
            raise ValueError("cookie domain is outside the source allow-list")
        if (not isinstance(path, str) or not path.startswith("/") or len(path) > 4096
                or any(ord(c) < 32 or ord(c) == 127 for c in path)):
            raise ValueError("invalid cookie path")
        cookie = {"name": name, "value": value, "domain": domain, "path": path}
        for flag in ("hostOnly", "secure", "httpOnly", "session"):
            if type(item.get(flag)) is not bool:
                raise ValueError(f"cookie {flag} must be boolean")
            cookie[flag] = item[flag]
        same_site = item.get("sameSite", "unspecified")
        if not isinstance(same_site, str) or same_site not in SAME_SITE:
            raise ValueError("invalid sameSite")
        cookie["sameSite"] = same_site
        store = item.get("storeId", "0")
        if not isinstance(store, str) or not store or len(store) > 128:
            raise ValueError("invalid storeId")
        cookie["storeId"] = store
        stores.add(store)
        expiry = item.get("expirationDate")
        if expiry is not None:
            if type(expiry) not in {int, float} or not 0 <= expiry <= 253402300799:
                raise ValueError("invalid expirationDate")
            if cookie["session"]:
                raise ValueError("session cookie must not have an expirationDate")
            cookie["expirationDate"] = expiry
        elif not cookie["session"]:
            raise ValueError("persistent cookie requires expirationDate")
        key = (domain, path, name, store)
        if key in result and result[key] != cookie:
            raise ValueError("conflicting duplicate cookie identity")
        result.setdefault(key, cookie)
    if len(stores) > 1:
        raise ValueError("mixed cookie stores are unsupported in phase 1")
    return list(result.values())


def cookies_to_header(
    cookies: list[dict[str, Any]], url: str, *, domains: list[str], now: float | None = None
) -> str:
    scheme, host, path = request_url(url)
    if not allowed_domain(host, domains):
        raise ValueError("request URL is outside the source allow-list")
    timestamp = time.time() if now is None else now
    selected = []
    for cookie in normalize_cookies(cookies, domains):
        domain = cookie["domain"]
        if host != domain and (cookie["hostOnly"] or not domain_matches(host, domain)):
            continue
        cookie_path = cookie["path"]
        if path != cookie_path and not (
            path.startswith(cookie_path)
            and (cookie_path.endswith("/") or path[len(cookie_path):].startswith("/"))
        ):
            continue
        if cookie["secure"] and scheme != "https":
            continue
        if cookie.get("expirationDate", float("inf")) <= timestamp:
            continue
        selected.append(cookie)
    # Preserve Chrome's order for equal-length paths, including distinct same-name cookies.
    selected.sort(key=lambda item: -len(item["path"]))
    return "; ".join(f"{item['name']}={item['value']}" for item in selected)

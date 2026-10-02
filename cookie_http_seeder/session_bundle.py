"""Validate and decrypt a user-encrypted, source-scoped portable session bundle."""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from urllib.parse import urlsplit

from .cookies import allowed_domain, domain_name, request_url

MAX_PLAINTEXT = 512 * 1024
ITERATIONS = 310_000
_SOURCE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_HEADER = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_ENVELOPE_FIELDS = {"schema_version", "source", "kdf", "iterations", "cipher",
                    "salt", "nonce", "ciphertext"}
SESSION_COVERAGE = {
    "cookies": "captured", "localStorage": "selected_origin_only",
    "sessionStorage": "selected_tab_origin_only", "requestHeaders": "one_top_level_navigation",
    "indexedDB": "unsupported", "partitionedCookies": "unsupported",
    "serviceWorkers": "unsupported", "deviceBoundCredentials": "unsupported",
    "siteEgressIP": "not_observed", "geolocation": "not_observed",
}


def _decode(value: object, maximum: int) -> bytes:
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError("invalid bundle encoding")
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("invalid bundle encoding") from exc


def validate_envelope(envelope: object) -> dict:
    if (not isinstance(envelope, dict) or set(envelope) != _ENVELOPE_FIELDS
            or envelope.get("schema_version") != 1
            or not isinstance(envelope.get("source"), str)
            or not _SOURCE.fullmatch(envelope["source"])
            or envelope.get("kdf") != "PBKDF2-SHA256"
            or type(envelope.get("iterations")) is not int
            or envelope["iterations"] != ITERATIONS
            or envelope.get("cipher") != "AES-256-GCM"):
        raise ValueError("invalid bundle envelope")
    if len(_decode(envelope["salt"], 24)) != 16 or len(_decode(envelope["nonce"], 16)) != 12:
        raise ValueError("invalid bundle encoding")
    if len(_decode(envelope["ciphertext"], 700_000)) < 16:
        raise ValueError("invalid bundle encoding")
    return envelope


def decrypt_bundle(envelope: object, passphrase: str) -> dict:
    """Decrypt in memory; errors never contain credentials or raw payloads."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    envelope = validate_envelope(envelope)
    if not isinstance(passphrase, str) or not 12 <= len(passphrase) <= 1024:
        raise ValueError("invalid passphrase length")
    salt = _decode(envelope["salt"], 24)
    nonce = _decode(envelope["nonce"], 16)
    ciphertext = _decode(envelope["ciphertext"], 700_000)
    key = hashlib.pbkdf2_hmac("sha256", passphrase.encode(), salt, ITERATIONS, dklen=32)
    associated = f"portable-session:{envelope['source']}:1".encode()
    try:
        raw = AESGCM(key).decrypt(nonce, ciphertext, associated)
        if len(raw) > MAX_PLAINTEXT:
            raise ValueError("session payload is too large")
        payload = json.loads(raw.decode("utf-8"))
        validate_payload(payload, payload["domains"])
        if payload["source"] != envelope["source"]:
            raise ValueError("bundle source mismatch")
        return payload
    except (InvalidTag, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError("cannot decrypt or validate session bundle") from exc


def _scoped_url(value: object, domains: list[str], *, origin_only: bool = False) -> str:
    scheme, host, path = request_url(value)
    parsed = urlsplit(value)
    if (scheme != "https" or not allowed_domain(host, domains)
            or (origin_only and (path != "/" or parsed.query or value !=
                                 f"https://{parsed.netloc}"))):
        raise ValueError("session URL outside approved HTTPS domains")
    return value


def _storage_entries(value: object) -> None:
    if (not isinstance(value, list) or len(value) > 500
            or any(not isinstance(item, dict) or not isinstance(item.get("name"), str)
                   or not isinstance(item.get("value"), str)
                   or len(item["name"]) > 4096 or len(item["value"]) > 32768
                   for item in value)):
        raise ValueError("invalid browser storage entries")


def validate_payload(payload: object, allowed_domains: list[str]) -> dict:
    if (not isinstance(payload, dict) or payload.get("schema_version") != 1
            or not isinstance(payload.get("source"), str)
            or not _SOURCE.fullmatch(payload["source"])
            or not isinstance(payload.get("domains"), list)
            or not 1 <= len(payload["domains"]) <= 32):
        raise ValueError("invalid session payload")
    domains = [domain_name(item) for item in payload["domains"]]
    allowed = [domain_name(item) for item in allowed_domains]
    if len(set(domains)) != len(domains) or any(not allowed_domain(d, allowed) for d in domains):
        raise ValueError("session domains outside approved source")
    nav = payload.get("navigation")
    if (not isinstance(nav, dict) or nav.get("method") not in {"GET", "HEAD"}
            or type(nav.get("status")) is not int or not 100 <= nav["status"] <= 599
            or not isinstance(nav.get("headers"), list) or len(nav["headers"]) > 128):
        raise ValueError("invalid captured navigation")
    _scoped_url(nav.get("url"), domains)
    _scoped_url(nav.get("final_url"), domains)
    for item in nav["headers"]:
        if (not isinstance(item, dict) or not isinstance(item.get("name"), str)
                or not _HEADER.fullmatch(item["name"])
                or not isinstance(item.get("value"), str) or len(item["value"]) > 8192
                or any(char in item["value"] for char in "\r\n\x00")):
            raise ValueError("invalid request header")
    cookies = payload.get("cookies")
    if not isinstance(cookies, list) or len(cookies) > 500:
        raise ValueError("invalid cookies")
    for item in cookies:
        if (not isinstance(item, dict) or item.get("partitionKey") is not None
                or not allowed_domain(domain_name(item.get("domain")), domains)
                or not isinstance(item.get("name"), str)
                or not isinstance(item.get("value"), str)
                or not isinstance(item.get("path"), str) or not item["path"].startswith("/")):
            raise ValueError("cookie outside session scope")
    origins = payload.get("origins")
    if not isinstance(origins, list) or len(origins) > 16:
        raise ValueError("invalid origins")
    for item in origins:
        if not isinstance(item, dict):
            raise ValueError("invalid storage origin")
        _scoped_url(item.get("origin"), domains, origin_only=True)
        _storage_entries(item.get("localStorage"))
        _storage_entries(item.get("sessionStorage"))
    if (not isinstance(payload.get("environment"), dict)
            or not isinstance(payload.get("location"), dict)
            or payload.get("coverage") != SESSION_COVERAGE):
        raise ValueError("missing session context")
    if len(json.dumps(payload, ensure_ascii=False).encode()) > MAX_PLAINTEXT:
        raise ValueError("session payload is too large")
    return payload

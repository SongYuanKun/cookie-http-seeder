"""Small authenticated receiver client: TLS/loopback, no proxies or redirects."""
from __future__ import annotations

import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .cookies import request_url
from .senders import sender_tag as validate_sender_tag


class ClientError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ClientError("redirect_blocked")


def receiver_origin(endpoint: str) -> str:
    scheme, host, path = request_url(endpoint)
    url = urlsplit(endpoint)
    if path != "/" or url.query:
        raise ValueError("receiver endpoint must be an origin")
    if scheme == "http" and host not in {"127.0.0.1", "localhost"}:
        raise ValueError("remote receiver requires HTTPS or a local SSH tunnel")
    return f"{scheme}://{host}" + (f":{url.port}" if url.port else "")


class ReceiverClient:
    def __init__(self, endpoint: str, token: str, *, timeout: float = 8,
                 sender_tag: str = "default"):
        self.sender_tag = validate_sender_tag(sender_tag)
        self.endpoint = receiver_origin(endpoint)
        if not re.fullmatch(r"[A-Za-z0-9._-]{16,256}", token):
            raise ValueError("invalid receiver token")
        if not 0 < timeout <= 30:
            raise ValueError("invalid timeout")
        self.token, self.timeout = token, timeout
        self.opener = build_opener(ProxyHandler({}), _NoRedirects())

    def request(self, path: str, *, payload: dict | None = None) -> dict:
        allowed_path = r"/(?:v1/(?:status|sources|feedback)|v2/sync/[a-z][a-z0-9_-]{0,31})"
        if not re.fullmatch(allowed_path, path):
            raise ValueError("unsupported receiver path")
        if payload is not None and self.sender_tag != "default":
            # A legacy server might silently ignore X-Sender-Tag. Verify before mutation.
            doc = self.request("/v1/sources")
            if "sender_tags" not in doc.get("capabilities", []):
                raise ClientError("sender_tags_unsupported")
        data = json.dumps(payload).encode() if payload is not None else None
        request = Request(self.endpoint + path, data=data,
                          method="POST" if data is not None else "GET",
                          headers={"Authorization": f"Bearer {self.token}",
                                   "Content-Type": "application/json",
                                   "X-Sender-Tag": self.sender_tag})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ClientError("response_too_large")
            body = json.loads(raw)
            if not isinstance(body, dict) or body.get("ok") is not True:
                raise ClientError("invalid_response")
            if self.sender_tag != "default" and body.get("sender_tag") != self.sender_tag:
                raise ClientError("sender_tags_unsupported")
            return body
        except HTTPError as error:
            error.close()
            raise ClientError({401: "unauthorized", 403: "forbidden", 409: "stale_snapshot",
                               400: "invalid_request", 428: "upgrade_required"}.get(
                                   error.code, "receiver_error")) from None
        except (URLError, TimeoutError, OSError):
            raise ClientError("network_error") from None
        except (ValueError, UnicodeError):
            raise ClientError("invalid_response") from None

    def report(self, source: str, snapshot_version: str, result: str, reason_code: str) -> dict:
        return self.request("/v1/feedback", payload={
            "source": source, "snapshot_version": snapshot_version,
            "result": result, "reason_code": reason_code,
        })

"""Explicit site response evidence and safe observation categories."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from .sync_state import RESULT_REASONS


@dataclass(frozen=True)
class Observation:
    result: str
    reason_code: str

    def __post_init__(self):
        if (not isinstance(self.result, str) or self.result not in RESULT_REASONS
                or not isinstance(self.reason_code, str)
                or self.reason_code not in RESULT_REASONS[self.result]):
            raise ValueError("invalid observation result/reason combination")


@dataclass(frozen=True)
class Response:
    status: int
    body: str = field(default="", repr=False)
    headers: dict[str, str] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class RequestOutcome:
    observation: Observation
    snapshot_version: str
    feedback_status: str
    paused: bool
    response: Any = field(default=None, repr=False)


class ResponseRules:
    """Opt-in response markers; never infer login from a status code alone."""

    @classmethod
    def from_document(cls, raw: dict) -> ResponseRules:
        fields = {"schema_version", "valid_body_contains", "invalid_body_contains",
                  "valid_json", "invalid_json", "login_redirect_paths"}
        if (not isinstance(raw, dict) or raw.get("schema_version") != 1
                or set(raw) - fields):
            raise ValueError("invalid response rule document")
        instance = cls()
        for name in ("valid_body_contains", "invalid_body_contains", "login_redirect_paths"):
            values = raw.get(name, [])
            if (not isinstance(values, list) or len(values) > 32
                    or any(not isinstance(v, str) or not 1 <= len(v) <= 2048 for v in values)):
                raise ValueError("rules require bounded nonempty string markers")
            if name == "login_redirect_paths" and any(
                not v.startswith("/") or v.startswith("//") or "?" in v or "#" in v
                for v in values
            ):
                raise ValueError("login redirects require absolute URL paths")
            setattr(instance, name, tuple(values))
        for name in ("valid_json", "invalid_json"):
            rules = raw.get(name, [])
            if not isinstance(rules, list) or len(rules) > 32:
                raise ValueError("JSON rules require a bounded list")
            for rule in rules:
                allowed = {"path", "equals"}
                if name == "invalid_json":
                    allowed |= {"reason_code"}
                if (not isinstance(rule, dict) or set(rule) - allowed
                        or not {"path", "equals"} <= set(rule)
                        or not isinstance(rule["path"], list) or not 1 <= len(rule["path"]) <= 16
                        or any(not isinstance(k, str) or not 1 <= len(k) <= 128
                               for k in rule["path"])
                        or type(rule["equals"]) not in {str, int, bool, float, type(None)}
                        or (type(rule["equals"]) is float and not math.isfinite(rule["equals"]))):
                    raise ValueError("invalid JSON path/equality rule")
                if "reason_code" in rule:
                    Observation("invalid", rule["reason_code"])
            setattr(instance, name, tuple(json.loads(json.dumps(rules))))
        if not any(getattr(instance, key) for key in fields - {"schema_version"}):
            raise ValueError("configure explicit site response evidence")
        return instance

    @staticmethod
    def _json_matches(body: str, rules: tuple) -> dict | None:
        if not rules:
            return None
        try:
            document = json.loads(body)
        except (ValueError, TypeError, RecursionError):
            return None
        for rule in rules:
            value = document
            for key in rule["path"]:
                if not isinstance(value, dict) or key not in value:
                    break
                value = value[key]
            else:
                expected = rule["equals"]
                if type(value) is type(expected) and value == expected:
                    return rule
        return None

    def __call__(self, response: Response) -> Observation:
        if (not isinstance(response, Response) or type(response.status) is not int
                or not 100 <= response.status <= 599
                or not isinstance(response.body, str) or len(response.body) > 1024 * 1024
                or not isinstance(response.headers, dict)
                or any(not isinstance(k, str) or not isinstance(v, str)
                       for k, v in response.headers.items())):
            return Observation("error", "unexpected_response")
        if response.status == 429:
            return Observation("error", "rate_limited")
        if response.status >= 500:
            return Observation("error", "unexpected_response")
        if 300 <= response.status < 400:
            location = next((v for k, v in response.headers.items()
                             if k.lower() == "location"), "")
            try:
                path = urlsplit(location).path
            except (ValueError, TypeError):
                path = ""
            if any(path == p or path.startswith(p.rstrip("/") + "/")
                   for p in self.login_redirect_paths):
                return Observation("invalid", "login_required")
        invalid_json = self._json_matches(response.body, self.invalid_json)
        if invalid_json:
            return Observation("invalid", invalid_json.get("reason_code", "login_required"))
        if any(marker in response.body for marker in self.invalid_body_contains):
            return Observation("invalid", "login_required")
        if 200 <= response.status < 300 and (
            any(marker in response.body for marker in self.valid_body_contains)
            or self._json_matches(response.body, self.valid_json)
        ):
            return Observation("valid", "logged_in")
        return Observation("error", "unexpected_response")


"""Bind auxiliary metadata to source policy without persisting site URLs."""
from __future__ import annotations

import hashlib
import json
import re

from .cookies import SOURCE_NAME, normalize_sources


def scope_fingerprints(sources):
    thresholds = {"stale_after_seconds", "validation_ttl_seconds"}
    return {name: hashlib.sha256(json.dumps(
        {key: value for key, value in spec.items() if key not in thresholds},
        sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest() for name, spec in normalize_sources(sources).items()}


def validate_fingerprints(value):
    if (not isinstance(value, dict) or len(value) > 100 or any(
        not isinstance(name, str) or not SOURCE_NAME.fullmatch(name)
        or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)
        for name, digest in value.items()
    )):
        raise ValueError("invalid metadata policy")
    return value

#!/usr/bin/env python3
"""Sync packaged example configs; site permissions are now granted at runtime."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "cookie_http_seeder/resources/sources.json"
EXAMPLES = ROOT / "examples/sources.json"


def sync(*, check: bool) -> int:
    raw = json.loads(CANONICAL.read_text(encoding="utf-8"))
    text = json.dumps(raw, indent=2, ensure_ascii=False) + "\n"
    if check:
        if EXAMPLES.read_text(encoding="utf-8") != text:
            print("example sources differ; run scripts/sync_extension_sources.py")
            return 1
        manifest = json.loads((ROOT / "extension/manifest.json").read_text(encoding="utf-8"))
        if set(manifest["host_permissions"]) != {
            "http://127.0.0.1/*", "http://localhost/*",
        }:
            print("site permissions must be optional and granted at runtime")
            return 1
        print("example sources in sync; no hard-coded site permissions")
        return 0
    EXAMPLES.write_text(text, encoding="utf-8")
    print("updated examples/sources.json (extension configuration is dynamic)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    return sync(check=parser.parse_args(argv).check)


if __name__ == "__main__":
    raise SystemExit(main())

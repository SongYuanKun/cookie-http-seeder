#!/usr/bin/env python3
"""Keep packaged sources, examples/, and the Chrome extension in sync.

Canonical file: cookie_http_seeder/resources/sources.json

Usage:
  python scripts/sync_extension_sources.py          # write
  python scripts/sync_extension_sources.py --check  # exit 1 if drift
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "cookie_http_seeder" / "resources" / "sources.json"
EXAMPLES = ROOT / "examples" / "sources.json"
SHARED_JS = ROOT / "extension" / "shared.js"
MANIFEST = ROOT / "extension" / "manifest.json"

_SOURCES_BLOCK = re.compile(
    r"export const SOURCES = \{.*?\n\};",
    re.DOTALL,
)


def _domain_to_host_permission(domain: str) -> str | None:
    d = domain.lstrip(".").lower()
    if not d or "/" in d:
        return None
    return f"*://*.{d}/*"


def _render_shared_sources(sources: dict[str, dict]) -> str:
    lines = ["export const SOURCES = {"]
    for name in sorted(sources):
        spec = sources[name]
        domains = spec["domains"]
        domain_literal = json.dumps(domains, ensure_ascii=False)
        lines.append(f"  {name}: {{")
        lines.append(f'    label: "{name}",')
        lines.append(f"    domains: {domain_literal},")
        lines.append("  },")
    lines.append("};")
    return "\n".join(lines)


def _build_host_permissions(sources: dict[str, dict]) -> list[str]:
    perms: list[str] = []
    seen: set[str] = set()
    for name in sorted(sources):
        for domain in sources[name]["domains"]:
            perm = _domain_to_host_permission(str(domain))
            if perm and perm not in seen:
                seen.add(perm)
                perms.append(perm)
    perms.extend(["http://127.0.0.1/*", "http://localhost/*"])
    return perms


def sync(*, check: bool) -> int:
    raw = json.loads(CANONICAL.read_text(encoding="utf-8"))
    sources = raw["sources"]
    canonical_text = json.dumps(raw, indent=2, ensure_ascii=False) + "\n"

    shared_text = SHARED_JS.read_text(encoding="utf-8")
    new_block = _render_shared_sources(sources)
    if not _SOURCES_BLOCK.search(shared_text):
        print("extension/shared.js: SOURCES block not found", file=sys.stderr)
        return 2
    new_shared = _SOURCES_BLOCK.sub(new_block, shared_text, count=1)

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    new_manifest = dict(manifest)
    new_manifest["host_permissions"] = _build_host_permissions(sources)
    new_manifest_text = json.dumps(new_manifest, indent=2, ensure_ascii=False) + "\n"

    planned = {
        EXAMPLES: canonical_text,
        SHARED_JS: new_shared,
        MANIFEST: new_manifest_text,
    }

    drifted: list[str] = []
    for path, content in planned.items():
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        if current != content:
            drifted.append(str(path.relative_to(ROOT)))

    if check:
        if drifted:
            print("sources out of sync:", ", ".join(drifted), file=sys.stderr)
            print("run: python scripts/sync_extension_sources.py", file=sys.stderr)
            return 1
        print("sources in sync")
        return 0

    for path, content in planned.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify sync without writing (CI)",
    )
    args = parser.parse_args(argv)
    return sync(check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())

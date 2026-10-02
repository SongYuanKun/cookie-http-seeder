#!/usr/bin/env python3
"""Build a deterministic Chrome client ZIP from its manifest and local module graph."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path

_IMPORT = re.compile(r'''(?:from\s+|import\s*)["'](\.[^"']+)["']''')
_SCRIPT = re.compile(r'''<script\b[^>]*\bsrc=["']([^"']+)["']''', re.IGNORECASE)


def build_extension(source_dir: Path, output_dir: Path) -> Path:
    root = source_dir.resolve()
    pending = ["manifest.json"]
    files: dict[str, bytes] = {}
    version = ""
    while pending:
        name = pending.pop()
        if name in files:
            continue
        path = root / name
        if (path.is_symlink() or not path.resolve().is_relative_to(root)
                or any((root / Path(*Path(name).parts[:i])).is_symlink()
                       for i in range(1, len(Path(name).parts)))
                or not path.is_file()):
            raise ValueError("missing or unsafe extension dependency")
        content = path.read_bytes()
        files[name] = content
        if name == "manifest.json":
            manifest = json.loads(content)
            version = manifest.get("version", "")
            if manifest.get("manifest_version") != 3 or not re.fullmatch(r"\d+\.\d+\.\d+", version):
                raise ValueError("expected a versioned Chrome MV3 client")
            pending.extend(manifest.get("icons", {}).values())
            pending.append(manifest["background"]["service_worker"])
            pending.append(manifest["action"]["default_popup"])
            pending.append(manifest["options_ui"]["page"])
        elif path.suffix == ".html":
            pending.extend((Path(name).parent / p).as_posix()
                           for p in _SCRIPT.findall(content.decode("utf-8")))
        elif path.suffix == ".js":
            pending.extend((Path(name).parent / p).as_posix()
                           for p in _IMPORT.findall(content.decode("utf-8")))
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"cookie-http-seeder-extension-{version}.zip"
    temporary = target.with_suffix(".zip.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in sorted(files.items()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                archive.writestr(info, content)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path,
                        default=Path(__file__).resolve().parents[1] / "extension")
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    args = parser.parse_args()
    target = build_extension(args.source_dir, args.output_dir)
    print(f"{target} sha256={hashlib.sha256(target.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

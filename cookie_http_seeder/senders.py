"""Sender labels are storage namespaces, not authentication identities."""
from __future__ import annotations

import re
from pathlib import Path

DEFAULT_SENDER = "default"
MAX_SENDERS = 100
_TAG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_RESERVED = re.compile(r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])$")


def sender_tag(value: object) -> str:
    """Reject ambiguity, path traversal and non-portable Windows directory names."""
    if not isinstance(value, str) or not _TAG.fullmatch(value) or _RESERVED.fullmatch(value):
        raise ValueError("sender tag must be 1-32 lowercase letters/digits, '-' or '_'")
    return value


def sender_directory(data_dir: Path, tag: str = DEFAULT_SENDER) -> Path:
    """Resolve without creating directories or falling back to another sender.

    The configured data root is trusted. Refuse symlinked namespace components;
    local administrators must protect the root against untrusted filesystem writes.
    """
    tag = sender_tag(tag)
    root = Path(data_dir)
    if tag == DEFAULT_SENDER:
        return root
    parent, selected = root / "senders", root / "senders" / tag
    for path in (parent, selected):
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise ValueError("sender directory must be a real directory")
    return selected


def list_sender_tags(data_dir: Path) -> list[str]:
    """List initialized namespaces; never expose credentials or follow symlinks."""
    root = Path(data_dir)
    parent = root / "senders"
    if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
        raise ValueError("senders directory must be a real directory")
    result = [DEFAULT_SENDER]
    if parent.is_dir():
        for path in parent.iterdir():
            try:
                tag = sender_tag(path.name)
                if tag != DEFAULT_SENDER and not path.is_symlink() and path.is_dir():
                    config = path / "sources.json"
                    if config.is_file() and not config.is_symlink():
                        result.append(tag)
            except ValueError:
                continue
    return sorted(result)

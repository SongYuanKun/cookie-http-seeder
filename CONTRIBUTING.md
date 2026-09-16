# Contributing

Thanks for helping improve cookie-http-seeder.

## Development setup

```bash
git clone https://github.com/SongYuanKun/cookie-http-seeder.git
cd cookie-http-seeder
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

## Project layout

| Path | Role |
|------|------|
| `cookie_http_seeder/` | Python package (CLI + receiver) |
| `cookie_http_seeder/paths.py` | Data-dir / env resolution |
| `cookie_http_seeder/resources/sources.json` | Packaged default sources (shipped in wheels) |
| `deploy/` | Docker Compose + systemd templates |
| `docs/deploy.md` | Deployment guide |
| `examples/` | Editable examples for local/dev use |
| `extension/` | Chrome MV3 extension |
| `tests/` | pytest suite |
| `scripts/` | Maintainer helpers |

## Sources must stay in sync

Domain allow-lists live in three places that must match:

1. `cookie_http_seeder/resources/sources.json` (packaged default)
2. `examples/sources.json` (repo checkout default)
3. `extension/shared.js` `SOURCES` + `extension/manifest.json` `host_permissions`

After editing sources JSON:

```bash
python scripts/sync_extension_sources.py
```

CI runs the same command with `--check`.

## Pull requests

1. Branch from `main` (do not commit directly to `main`).
2. Keep changes focused; include tests for behavior changes.
3. Do not commit tokens, webhook URLs, or cookie files under `data/`.
4. Open a PR with a short “why” summary.

## Code style

- Python 3.11+, stdlib-first (no runtime deps by design)
- `ruff check` should be clean
- Chinese comments are fine when clarifying ops intent; keep user-facing docs in English

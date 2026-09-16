# cookie-http-seeder

[![CI](https://github.com/SongYuanKun/cookie-http-seeder/actions/workflows/ci.yml/badge.svg)](https://github.com/SongYuanKun/cookie-http-seeder/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

**Session seeder for Cookie-HTTP crawlers.**

Push logged-in browser cookies (including HttpOnly) from a Chrome extension to a
loopback receiver on your crawler host. The crawler keeps reading a stable
`cookie_header` JSON file and does **not** need a long-running Chrome.

```text
Daily Chrome (already logged in)
        │  extension (domain allow-list)
        ▼
  receiver :18765  (Bearer token, 127.0.0.1)
        │
        ▼
  data/{source}-cookies.json   →   your HTTP crawler
```

This is intentionally narrow ops glue, not a general session-mirroring platform.

## Features

- Chrome MV3 extension with per-source domain allow-list
- Python receiver bound to loopback by default
- Writes `{ "cookie_header": "...", "updatedAt": "..." }` files (`0600`, atomic replace)
- Portable data dir (`COOKIE_HTTP_SEEDER_DATA`, `./data`, or XDG)
- Docker Compose + systemd deploy templates
- Optional Feishu/Lark webhook notify on successful push
- SSH / Tailscale port-forward friendly
- Zero runtime dependencies (stdlib only)

## Requirements

- Python 3.11+
- Chrome / Chromium (for the unpacked extension)
- Optional: SSH or Tailscale when the browser and crawler are on different hosts

## Install

```bash
git clone https://github.com/SongYuanKun/cookie-http-seeder.git
cd cookie-http-seeder
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## Quick start

### 1. Receiver (crawler host)

```bash
.venv/bin/cookie-http-seeder init-token
.venv/bin/cookie-http-seeder serve
```

Token file defaults to `./data/cookie-receiver.token` (mode `0600`).

From your laptop:

```bash
ssh -N -L 18765:127.0.0.1:18765 user@crawler-host
```

### 2. Extension (daily browser)

1. Open `chrome://extensions` → Developer mode → **Load unpacked**
2. Select the `extension/` directory
3. Set endpoint `http://127.0.0.1:18765` and paste the token
4. Log into the target sites, then click **Push now**

### 3. Consume cookies in your crawler

```python
import json
from pathlib import Path

payload = json.loads(Path("data/fang-cookies.json").read_text())
headers = {"Cookie": payload["cookie_header"]}
```

Or use the example helper:

```bash
python examples/consume_cookies.py fang
```

## Configuration

Default sources ship in two places (kept identical):

- Packaged: `cookie_http_seeder/resources/sources.json` (used after `pip install`)
- Checkout: `examples/sources.json` (preferred when developing from git)

The extension `SOURCES` map and `host_permissions` must match. After editing
sources JSON:

```bash
python scripts/sync_extension_sources.py
```

Then reload the unpacked extension.

Minimal template: `examples/sources.minimal.json`.

```json
{
  "sources": {
    "fang": {
      "domains": [".fang.com", "fang.com"]
    },
    "beike": {
      "domains": [".ke.com", "ke.com", ".lianjia.com", "lianjia.com"]
    }
  }
}
```

Pass a custom file with `--sources /path/to/sources.json`, set
`COOKIE_HTTP_SEEDER_SOURCES`, or place `sources.json` inside the data directory
(useful for Docker/systemd volumes).

## Data storage

Cookie files stay on local disk so crawlers can keep doing a simple file read.
One durable directory holds everything:

```text
$data/
  {source}-cookies.json
  cookie-receiver.token
  sources.json              # optional override
  feishu-webhook            # optional
```

Resolution order: `--data-dir` → `COOKIE_HTTP_SEEDER_DATA` → existing `./data` →
`$XDG_DATA_HOME/cookie-http-seeder`.

```bash
cookie-http-seeder paths
```

## Deployment

| Mode | Entry |
|------|-------|
| Docker Compose | `deploy/docker-compose.yml` (publishes `127.0.0.1:18765` only) |
| systemd | `deploy/systemd/cookie-http-seeder.service` |
| bare process | `COOKIE_HTTP_SEEDER_DATA=... cookie-http-seeder serve` |

Full guide: [docs/deploy.md](docs/deploy.md).

```bash
mkdir -p data
docker compose -f deploy/docker-compose.yml up -d --build
```

## Optional Feishu notify

```bash
# file contains only the webhook URL, mode 0600
install -m 600 /dev/stdin data/feishu-webhook <<'EOF'
https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
EOF

cookie-http-seeder serve          # notifies on push by default
cookie-http-seeder notify-needed  # manual “please refresh” ping
```

Disable with `--no-notify`.

## Security

- Prefer `127.0.0.1` only; forward via SSH/Tailscale
- Treat cookies and webhook files as credentials (`0600`)
- Domain allow-list only — do not sync your entire browser jar
- Does **not** bypass CAPTCHA / login walls; humans re-login, then re-seed

See [SECURITY.md](SECURITY.md) for reporting vulnerabilities.

## CLI

```text
cookie-http-seeder init-token
cookie-http-seeder paths
cookie-http-seeder serve [--host 127.0.0.1] [--port 18765]
cookie-http-seeder notify-needed
cookie-http-seeder status
```

Environment variables:

| Variable | Meaning |
|----------|---------|
| `COOKIE_HTTP_SEEDER_DATA` | Data directory |
| `COOKIE_HTTP_SEEDER_HOST` / `_PORT` | Bind address for `serve` |
| `COOKIE_HTTP_SEEDER_SOURCES` | Path to sources JSON |
| `COOKIE_HTTP_SEEDER_TOKEN` / `_TOKEN_FILE` | Bearer token |
| `COOKIE_HTTP_SEEDER_WEBHOOK_FILE` | Feishu webhook file path |
| `COOKIE_HTTP_SEEDER_NO_NOTIFY` | Disable push notifications (`1`/`true`) |

## Project layout

```text
cookie_http_seeder/     # Python package
  paths.py              # data-dir / env resolution
  resources/            # Packaged default sources.json
deploy/                 # Docker Compose + systemd templates
docs/deploy.md          # Deployment guide
extension/              # Chrome MV3 extension
examples/               # Sample sources + consumer snippet
scripts/                # Maintainer helpers (sources sync)
tests/                  # pytest
```

## Development

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
.venv/bin/ruff check cookie_http_seeder tests scripts
```

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).

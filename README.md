# cookie-http-seeder

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
- Writes `{ "cookie_header": "...", "updatedAt": "..." }` files (`0600`)
- Optional Feishu/Lark webhook notify on successful push
- SSH / Tailscale port-forward friendly

## Quick start

### 1. Receiver (crawler host)

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
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

## Configuration

Default sources are examples for Chinese listing sites. Edit both:

- `examples/sources.json` (or pass `--sources`)
- `extension/shared.js` `SOURCES` map (must match)

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

## CLI

```text
cookie-http-seeder init-token
cookie-http-seeder serve [--host 127.0.0.1] [--port 18765]
cookie-http-seeder notify-needed
cookie-http-seeder status
```

## License

MIT

# Deployment

cookie-http-seeder is file-backed on purpose: crawlers keep reading
`{source}-cookies.json`. Prefer mounting one durable directory everywhere.

## Data directory layout

```text
$data/
  {source}-cookies.json     # crawler input (mode 0600, atomic replace)
  cookie-receiver.token     # bearer token (mode 0600)
  sources.json              # optional per-host allow-list override
  feishu-webhook            # optional Feishu bot URL (mode 0600)
```

Resolution order for `$data`:

1. `--data-dir`
2. `COOKIE_HTTP_SEEDER_DATA`
3. `./data` if that directory already exists
4. `$XDG_DATA_HOME/cookie-http-seeder` (default `~/.local/share/cookie-http-seeder`)

Inspect resolved paths:

```bash
cookie-http-seeder paths
cookie-http-seeder status
```

## Option A — Docker Compose (recommended on a crawler host)

From the repo root:

```bash
mkdir -p data
cp examples/sources.minimal.json data/sources.json   # or your real sources
docker compose -f deploy/docker-compose.yml up -d --build
docker compose -f deploy/docker-compose.yml logs -f
```

Compose publishes **only** `127.0.0.1:18765`. Forward from your laptop:

```bash
ssh -N -L 18765:127.0.0.1:18765 user@crawler-host
```

Shared volume tip: mount the same `data/` into your crawler container so it can
read the cookie JSON without another copy step.

## Option B — systemd

```bash
python3 -m pip install .
sudo useradd --system --home /var/lib/cookie-http-seeder --shell /usr/sbin/nologin cookie-seeder
sudo mkdir -p /var/lib/cookie-http-seeder
sudo cp examples/sources.minimal.json /var/lib/cookie-http-seeder/sources.json
sudo chown -R cookie-seeder:cookie-seeder /var/lib/cookie-http-seeder
sudo chmod 700 /var/lib/cookie-http-seeder
sudo cp deploy/systemd/cookie-http-seeder.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cookie-http-seeder
sudo -u cookie-seeder cookie-http-seeder init-token
sudo systemctl restart cookie-http-seeder
```

## Option C — bare process / tmux

```bash
export COOKIE_HTTP_SEEDER_DATA="$PWD/data"
cookie-http-seeder init-token
cookie-http-seeder serve
```

## Security checklist

- Bind `127.0.0.1` on the host (Compose already does this via port publish)
- Treat `$data` as a secrets volume (`0700` dir, `0600` files)
- Do not put the receiver on a public ingress / reverse proxy
- Domain allow-list only; keep extension `host_permissions` narrow

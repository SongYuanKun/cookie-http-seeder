# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | yes       |

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security problems.

Email the maintainer via the contact listed on the
[GitHub profile](https://github.com/SongYuanKun), or open a private
[GitHub security advisory](https://github.com/SongYuanKun/cookie-http-seeder/security/advisories/new)
if available.

Include:

- Affected version / commit
- Impact (token leak, cookie overwrite, SSRF, etc.)
- Minimal reproduction **without** real session cookies

## Design expectations

- Receiver should bind to loopback (`127.0.0.1` / `::1`) by default
- Bearer tokens and cookie files are credentials (`0600`)
- Domain allow-lists are intentional; do not widen to “all cookies”
- Webhook files must be mode `0600` and validated before use

# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Portable data directory resolution (`COOKIE_HTTP_SEEDER_DATA`, `./data`, or XDG)
- Atomic cookie file writes; `cookie-http-seeder paths` command
- Auto-load `$data/sources.json` for per-host deploy overrides
- Docker (`Dockerfile`, `deploy/docker-compose.yml`) and systemd unit templates
- Deployment guide: `docs/deploy.md`
- Env knobs: `COOKIE_HTTP_SEEDER_HOST` / `_PORT` / `_TOKEN_FILE` / `_NO_NOTIFY`

### Changed

- Ship default `sources.json` inside the package so non-editable installs work
- Default data directory prefers existing `./data`, else XDG share dir
- Align project layout with common open-source Python conventions (CI, CONTRIBUTING, SECURITY, CHANGELOG)
- Resolve Feishu webhook path via data-dir helpers

## [0.1.0] - 2026-09-16

### Added

- Initial release: Chrome MV3 extension, loopback receiver, CLI, optional Feishu notify

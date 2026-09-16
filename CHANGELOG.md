# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Ship default `sources.json` inside the package so non-editable installs work
- Default data directory is now `./data` relative to the process CWD (not the package install path)
- Align project layout with common open-source Python conventions (CI, CONTRIBUTING, SECURITY, CHANGELOG)
- Resolve Feishu webhook path via `COOKIE_HTTP_SEEDER_DATA` / `default_data_dir()`

### Added

- `scripts/sync_extension_sources.py` to keep extension sources in sync with JSON
- GitHub Actions CI (pytest + ruff + sources sync check)
- Example crawler snippet under `examples/`

## [0.1.0] - 2026-09-16

### Added

- Initial release: Chrome MV3 extension, loopback receiver, CLI, optional Feishu notify

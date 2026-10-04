# Public roadmap

This roadmap tracks user-visible delivery and verified adoption. Internal implementation plans are historical engineering records. Checkboxes describe evidence, not the size of the codebase or eligibility for a sponsorship program.

## v0.5.0 delivery

- [x] Version-bound snapshots and crawler feedback; durable feedback outbox.
- [x] Same-version login pause, new-version validation, persistent incidents and operator actions.
- [x] Unified extension dashboard; optional client health heartbeat, disabled by default.
- [x] Linux automated tests and real Chrome 149 controlled-profile acceptance.
- [x] Receiver and existing Grok extension upgraded in place with preservation receipts.
- [ ] Public v0.5.0 tag, wheel, extension ZIP and checksums from successful exact-commit CI.
- [ ] Windows/macOS/Linux CI and installed-wheel verification.

## Actual crawler and browser acceptance

- [ ] Connect the real Tianjin crawler to snapshot-version feedback and controlled login recovery.
- [ ] Verify the actual site's invalid → pause → new snapshot → valid → recovery flow while preserving administrator pauses.
- [ ] Record Windows and macOS native Chrome installation and permission/reload acceptance.

## Adoption and maintenance

- [x] Publish onboarding, support channels, contribution guidance and a community code of conduct.
- [ ] Obtain independently verifiable feedback from a real external user.
- [ ] Resolve confirmed feedback with public issues, focused changes and verified releases.
- [ ] Accumulate sustained maintenance history over actual elapsed time.

Submit a [usage report or issue](https://github.com/SongYuanKun/cookie-http-seeder/issues/new/choose). Public reports must exclude real session data. Project maturity and any JetBrains support decision depend on actual evidence and the program's review.

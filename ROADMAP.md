# Public roadmap

This roadmap tracks user-visible delivery and verified adoption. Internal implementation plans are historical engineering records. Checkboxes describe evidence, not the size of the codebase or eligibility for a sponsorship program.

## v0.5.0 delivery

- [x] Version-bound snapshots and crawler feedback; durable feedback outbox.
- [x] Same-version login pause, new-version validation, persistent incidents and operator actions.
- [x] Unified extension dashboard; optional client health heartbeat, disabled by default.
- [x] Linux automated tests and real Chrome 149 controlled-profile acceptance.
- [x] Receiver and existing Grok extension upgraded in place with preservation receipts.
- [x] Public v0.5.0 tag, wheel, extension ZIP and checksums from successful exact-commit CI.
- [x] Windows/macOS/Linux CI and installed-wheel verification.

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

Delivery evidence: [v0.5.0 release](https://github.com/SongYuanKun/cookie-http-seeder/releases/tag/v0.5.0), [exact-commit CI](https://github.com/SongYuanKun/cookie-http-seeder/actions/runs/37199076796), and [release workflow](https://github.com/SongYuanKun/cookie-http-seeder/actions/runs/37199340334). Downloaded wheel and extension ZIP both match the published SHA256SUMS. Native macOS Chrome 154 loaded the published extension at runtime version 0.5.0 in an isolated profile; its storage/reload verification is incomplete and is not counted as full native acceptance.

## JetBrains support assessment (2026-10-04)

The [current program page](https://www.jetbrains.com/community/opensource/) describes All Products Pack support for established projects with ongoing development and community impact, reviewed individually. The [application form](https://www.jetbrains.com/shop/eform/opensource) requires regular, visible contributions and non-commercial use of the granted IDE licenses; non-code commits do not count as active development. These pages do not publish a fixed star count or three-month qualification threshold.

This project has a public MIT repository and substantive code development. Its repository was created on 2026-09-16; independently verified external adoption and sustained maintenance are still unproven. Releases, platform checks and onboarding support delivery quality; completing them does not establish eligibility or approval. Independent feedback is evidence we plan to collect, not a separately stated mandatory application field.

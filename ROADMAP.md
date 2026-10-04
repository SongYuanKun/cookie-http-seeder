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

- [x] Deliver the real Tianjin crawler's opt-in snapshot-version feedback and controlled login recovery on a focused integration branch.
- [ ] Activate the approved production crawler integration without bypassing administrator pauses.
- [ ] Verify the actual site's invalid → pause → new snapshot → valid → recovery flow while preserving administrator pauses.
- [x] Verify macOS native Chrome release installation, Developer Mode, reload and synthetic settings preservation.
- [x] Verify Windows native Chrome release installation, permission consent/revocation, reload and full settings/approval preservation.
- [ ] Finish macOS native permission consent and revocation. The latest [native consent diagnostic](docs/acceptance/2026-10-04-macos-permission-diagnostic.json) still failed; the correct owned prompt was located, but remote input did not grant the permission. Manual local interaction remains pending.

Crawler code delivery: the independent `codex/cookie-session-recovery` branch contains 21 new tests. The latest clean-worktree suite passed 2509 tests (1 skipped, 38 subtests); 135 focused tests and 11 subtests passed. Production activation and actual-site login acceptance remain pending. The maintainer's unrelated working-directory changes were preserved.

## Adoption and maintenance

- [x] Publish onboarding, support channels, contribution guidance and a community code of conduct.
- [ ] Obtain independently verifiable feedback from a real external user.
- [ ] Resolve confirmed feedback with public issues, focused changes and verified releases.
- [ ] Accumulate sustained maintenance history over actual elapsed time.

Submit a [usage report or issue](https://github.com/SongYuanKun/cookie-http-seeder/issues/new/choose). Public reports must exclude real session data. Project maturity and any JetBrains support decision depend on actual evidence and the program's review.

Delivery evidence: [v0.5.0 release](https://github.com/SongYuanKun/cookie-http-seeder/releases/tag/v0.5.0), [exact-commit CI](https://github.com/SongYuanKun/cookie-http-seeder/actions/runs/37199076796), and [release workflow](https://github.com/SongYuanKun/cookie-http-seeder/actions/runs/37199340334). Downloaded wheel and extension ZIP both match the published SHA256SUMS. Native macOS Chrome 154 loaded the published extension at runtime version 0.5.0 in an isolated profile. The native manager reload and synthetic settings preservation passed after waiting for reload completion. Optional example-host permissions remained denied before and after reload; macOS permission consent/revocation remains open. See the [scoped macOS receipt](docs/acceptance/2026-10-04-macos-chrome-0.5.0.json). Windows native Chrome 154.0.8037.58 passed the actual headed installation, owned native Allow prompt, permission grant, manager reload, all settings/source approval preservation and UI revocation gate. Its temporary profile was removed. See the [Windows receipt](docs/acceptance/2026-10-04-windows-chrome-0.5.0.json) and [native run](https://github.com/SongYuanKun/cookie-http-seeder/actions/runs/37204759247).

## JetBrains support assessment (2026-10-04)

The [current program page](https://www.jetbrains.com/community/opensource/) describes All Products Pack support for established projects with ongoing development and community impact, reviewed individually. The [application form](https://www.jetbrains.com/shop/eform/opensource) requires regular, visible contributions and non-commercial use of the granted IDE licenses; non-code commits do not count as active development. These pages do not publish a fixed star count or three-month qualification threshold.

This project has a public MIT repository and substantive code development. Its repository was created on 2026-09-16; independently verified external adoption and sustained maintenance are still unproven. Releases, platform checks and onboarding support delivery quality; completing them does not establish eligibility or approval. Independent feedback is evidence we plan to collect, not a separately stated mandatory application field.

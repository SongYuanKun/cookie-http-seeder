# Open-source readiness implementation plan

> Execute inline with superpowers:executing-plans. The user requested completion of the five assessed TODOs on 2026-10-04.

## Goal and acceptance

Deliver a public v0.5.0 distribution, accurate current documentation, verified Windows/macOS/Linux installation, the user's actual crawler feedback/recovery integration, and public onboarding and feedback channels. Actual independent user feedback and sustained maintenance remain requirements; publication alone cannot prove them.

## Constraints

- Keep the MIT license, Python >=3.11 and zero third-party runtime dependencies.
- Preserve production credentials, snapshots, Chrome profile, existing authorization, disabled heartbeat and paused Grok routine.
- Publish only artifacts from successful CI for the exact tagged commit; reject mismatched tags and versions.
- Preserve operator-imposed production pauses. Login recovery may only remove a pause created by the crawler for the corresponding snapshot version.
- Keep real credentials and response bodies out of public examples, logs, issues and acceptance records.
- The main thread is the only writer; independent agents are read-only.

## Tasks

- [x] Task 1: Correct current README/SECURITY/CHANGELOG and label pre-rollout documents as historical; add SUPPORT, code of conduct, public roadmap and a sanitized onboarding case.
- [x] Task 2: Run Python 3.11/3.12/3.13 and Node tests on Ubuntu, Windows and macOS; build and import the installed wheel outside the checkout on all three platforms; retain canonical Ubuntu artifacts.
- [x] Task 3: Add tag/manual release automation that checks the exact commit's successful CI, validates the version, downloads canonical artifacts, and publishes their SHA256SUMS. Merge only after the cross-platform gate passes, then create v0.5.0 and inspect the published assets.
- [ ] Task 4: Integrate the real Tianjin crawler's atomic snapshot version, invalid/valid feedback, same-version pause and new-version validation. Test isolated state first; inspect production pause policy before live acceptance.
- [ ] Task 5: Publish useful roadmap/feedback issues and a reproducible onboarding case; collect independently verifiable real-user feedback and subsequent maintenance evidence.

## Verification and completion evidence

- Docs: relative links resolve, current instructions use 0.5.0, historical claims carry dated scope.
- CI: all nine OS/Python jobs pass; installed-wheel and package build checks cover each OS.
- Release: public tag points to the reviewed merge commit, exact-commit CI is green, wheel/ZIP hashes match published SHA256SUMS.
- Crawler: actual request path emits version-bound feedback, stops repeated invalid-version requests, validates a new version before recovery, and preserves the operator pause.
- Community: public entrypoints and issues exist; independent feedback must come from a real user, never a fabricated issue or maintainer self-report.

## Current evidence

- Base main: `8cdb6e22633d503f2a805d2d9c1a86cec265176f`, latest CI successful.
- v0.5.0 GTR receiver and Grok extension are installed; private acceptance receipt records preservation checks.
- Actual Tianjin crawler currently uses snapshot-to-browser sync and a 24-hour local auth pause, with no 0.5 CookieConsumer integration. A separate administrator pause also gates production Beike batches.
- No public tags/releases or independent user issues existed at the start of this task.

## Local verification (2026-10-04)

Python 3.11 suite: 337 passed; Node suite: 246 passed; Ruff and default-source sync passed. Both workflow YAML files parse, the matrix has nine combinations, 32 repository Markdown files have no unresolved local links, and a mismatched release tag is rejected before network/publishing. Hosted OS jobs and public release remain pending.

## Public delivery (2026-10-04)

PR #6 merged as `e9a93981ed827ea299c131a2c79917f244971260`. All nine jobs in [main CI 37199076796](https://github.com/SongYuanKun/cookie-http-seeder/actions/runs/37199076796) succeeded, including each OS's installed-wheel probe. Tag v0.5.0 identifies that commit. [Release workflow 37199340334](https://github.com/SongYuanKun/cookie-http-seeder/actions/runs/37199340334) published the canonical artifacts and SHA256SUMS; both downloaded assets verified against it.

The actual crawler now has an independently committed and pushed `codex/cookie-session-recovery` branch based on its current acquisition branch. The latest clean-worktree suite passed 2509 tests (1 skipped, 38 subtests); 135 related tests and 11 subtests passed. Production activation and live site verification remain pending, so Task 4 stays open. Native macOS Chrome loaded the release extension in an isolated profile; native manager reload and synthetic storage preservation now passed. Permission consent/revocation remains incomplete; see `docs/acceptance/2026-10-04-macos-chrome-0.5.0.json`. No independent external feedback or sustained maintenance evidence has been fabricated.

Windows native Chrome acceptance passed on the checksum-verified public 0.5.0 extension: actual permission consent, reload with full settings/source approval preservation, and UI revocation. See `docs/acceptance/2026-10-04-windows-chrome-0.5.0.json` and native run 37204759247. This does not establish macOS native permission acceptance or actual-site login recovery.

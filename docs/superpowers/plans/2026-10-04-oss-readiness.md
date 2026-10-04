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
- [ ] Task 2: Run Python 3.11/3.12/3.13 and Node tests on Ubuntu, Windows and macOS; build and import the installed wheel outside the checkout on all three platforms; retain canonical Ubuntu artifacts.
- [ ] Task 3: Add tag/manual release automation that checks the exact commit's successful CI, validates the version, downloads canonical artifacts, and publishes their SHA256SUMS. Merge only after the cross-platform gate passes, then create v0.5.0 and inspect the published assets.
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

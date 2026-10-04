# Windows native Chrome acceptance implementation plan

> Execute inline with superpowers:executing-plans under the already approved cross-platform acceptance task.

**Goal:** Prove Windows native Chrome release installation, explicit optional permission consent, reload/settings preservation and withdrawal through the actual extension UI.

**Architecture:** A dedicated standard Windows runner loads the checksum-verified public 0.5.0 extension in a new temporary profile. Node 22 drives Chrome DevTools Protocol and a synthetic loopback receiver; Windows UI Automation invokes only the native Allow control belonging to the owned Chrome process and example.com permission prompt.

**Spec:** `docs/superpowers/plans/2026-10-04-oss-readiness.md`, Windows native browser gate.

## Constraints and review focus

- Headed official Chrome; never grant permissions through mocked APIs, stored preference edits or headless settings.
- New temporary profile; no real site requests, Cookie, account or production token.
- Only example.com and a synthetic loopback receiver; automatic synchronization and health reporting stay off.
- UIA filters the owned browser PID and requires extension/domain labels before invoking Allow.
- Missing UIA desktop, missing prompt, timeouts and any false requirement fail the gate; partial progress is recorded honestly.
- Logs/job summary contain sanitized stage/version/boolean evidence only. No profile upload or raw receiver/browser storage dump.
- Use standard public-repository Windows runners; no artifact/cache storage or paid runner.
- Preserve all outstanding production, macOS permission, external-user feedback and elapsed-maintenance requirements.

## Task 1: Native acceptance harness

Create `scripts/native_browser_acceptance.mjs` and `scripts/windows_native_consent.ps1`.

- [x] Verify the absent harness cannot run (RED).
- [x] Implement bounded CDP launch/navigation, actual extension source approval with trusted click, owned native permission consent, manager reload and actual UI revocation.
- [x] Require initial denial, consent, permission/approval persistence, version/settings preservation and final denial/approval removal in the final passed value.
- [x] Check JS syntax and fail-closed non-Windows invocation.
- [ ] Run the actual Windows native acceptance gate.

## Task 2: Controlled delivery and evidence

Create `.github/workflows/native-browser.yml` with manual dispatch and an owned-PR path gate so the first real acceptance runs before merging. Future ordinary changes do not run it.

- [x] Implement download of v0.5.0 ZIP from the public release, compare its published and pinned SHA256, extract, then run headed Chrome with a 5-minute job bound.
- [x] Run one independent read-only review; fix concrete findings and verify them.
- [ ] Push the focused branch and attach its PR. Inspect the native job's exact stage and evidence; merge only after its required checks pass.
- [ ] Update roadmap/receipts only for requirements actually proven by the real native run.

Review findings fixed before the first Windows run: extract the root-layout ZIP into a dedicated extension directory; authenticate the synthetic receiver and compare every stored setting/approval using the production approval predicate; wait for the owned process exit, kill only its process tree on timeout, and remove its temporary profile. Windows execution is still pending.

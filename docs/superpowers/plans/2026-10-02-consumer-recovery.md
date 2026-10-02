# Consumer Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the agreed crawler feedback/recovery workflow and operational enhancements, push main and update the browser client through grok bot.

**Architecture:** A standard-library consumer wraps atomic snapshot reads, explicit site classifiers and version-bound feedback. Receiver monitoring and optional extension recovery operate on credential-free state; label selection remains explicit.

**Tech Stack:** Python 3.11+, Chrome MV3 120+, Node 22; no new runtime dependencies.

**Spec:** `docs/superpowers/specs/2026-10-02-consumer-recovery-design.md`

## Global Constraints

- Preserve V2/CAS, default layout, explicit browser consent and URL-scoped Cookie selection.
- Never persist/log Cookie values, Tokens or response bodies in operational state.
- Source thresholds: 60–2592000 seconds; defaults 86400 seconds.
- Recovery/login polling: default 0, enabled interval 15–10080 minutes.
- No automatic login, redirects or sender/account fallback.
- Invalid notifications retain 900-second per-source cooldown; stale uses a separate cooldown.
- User explicitly authorizes implementation, main push and bot update; execute inline with one final review.

## Review Focus

- A snapshot changes while an old request completes: feedback is discarded, newer state survives.
- Process restarts after confirmed invalid: no request with that same snapshot version.
- Offline feedback: pause is retained, network errors never falsely report invalid.
- Changed/cleared configuration: waiting cannot resume with empty or out-of-scope credentials.
- Browser switches connection during probe/poll: no success or notification attributed to the new label.

### Task 1: Consumer workflow and explicit classifiers

**Files:** create `cookie_http_seeder/consumer.py`, `tests/test_consumer.py`; update examples.
**Interfaces:** `Observation(result: str, reason_code: str)`, `ResponseRules.from_document(raw: dict)`,
`CookieConsumer(source, data_dir, client, classify)`, `request(url, send)`, `wait_for_update(url, timeout, poll_interval)`.
- [x] Write synthetic tests for valid/invalid/error classification, paused versions across restarts, new-version verification, stale/offline feedback, missing/cleared cookies and bounded wait.
- [x] Run `.venv/bin/python -m pytest tests/test_consumer.py` (expected missing feature failures).
- [x] Implement standard-library helper and a complete no-redirect feedback example with explicit rules.
- [x] Verify consumer tests and full Python suite; commit.

### Task 2: Thresholds, monitoring and sender summary

**Files:** modify `cookies.py`, `receiver.py`, `sync_state.py`, `client.py`, `cli.py`; create `monitoring.py`, `tests/test_monitoring.py`.
**Interfaces:** source optional thresholds; `ReceiverState.sender_statuses()`; `/v1/senders`; `status --all-senders`;
`StaleMonitor.scan()` with injected notifier for tests, periodic receiver lifecycle.
- [x] Write tests for policy validation/defaults, independent TTL, no clearing on threshold-only edits, stale cooldown/restart, paused/no-webhook sources, credential-free summary and partial corruption.
- [x] Run targeted Python tests (expected missing features), implement, verify and commit.

### Task 3: Optional low-frequency recovery

**Files:** modify `extension/sync.js`, `shared.js`, `background.js`; add behavior tests to `tests/sync.test.mjs`.
**Interfaces:** `recoveryProbeMinutes`, persisted `probeAt`; existing retry alarm schedules exhausted probes.
- [x] Test default exhausted behavior, delayed successful recovery, failed probe limits, terminal failures, interrupted probes, revoked consent and disabled recovery.
- [x] Run `node --test tests/sync.test.mjs` (expected missing recovery), implement, verify and commit.

### Task 4: Browser login alerts and management UI

**Files:** create `extension/login_monitor.js`, `tests/login_monitor.test.mjs`; update shared/options/background/source status.
**Interfaces:** `loginPollMinutes`; current-connection credential-free notification fingerprints; source threshold inputs; sender summary view.
- [x] Test invalid/stale deduplication, new versions, disconnects, switched connections, disabled/revoked sources and failure retries without secrets.
- [x] Run targeted Node tests (expected missing monitor), implement alerts/settings/summary UI and verify full Node suite.
- [x] Commit verified changes.

### Task 5: Versioned delivery and main push

**Files:** metadata 0.4.0, README/CHANGELOG/protocol/deploy/examples, upgrade handoff, packaging check.
- [x] Update accurate feature/compatibility documentation and client version display.
- [x] Run full Python and Node suites, Ruff, source sync check and isolated package/extension artifact validation.
- [x] One independent whole-branch review; fix concrete findings with regression tests.
- [ ] Commit, fetch latest main, integrate without force and push; verify remote SHA and CI.
- [ ] Send grok bot the exact update task after identity is verified; verify target client's 0.4.0 version and preserve connection/consent.

## Local verification evidence

- Python baseline: 220 passed; implementation: 276 passed (27.53 s), followed by 4 consumer state/read-failure regression tests (35 targeted passed); final full suite: 280 passed (27.64 s).
- Node baseline: 173 passed; implementation: 230 passed, no failures.
- Ruff and source sync check passed; deterministic extension ZIP and Python wheel build passed; extracted wheel imported 0.4.0 and bundled sources outside the checkout.
- Isolated Chromium/profile with synthetic local receiver: 0.4.0 loaded, connection save and sender summary worked, source thresholds 300/600 matched, real login and retry alarms created, no page errors.
- This is isolated browser evidence; the user's installed client and actual site response adapters still require target identity/site evidence.

- Independent review found numeric snapshot versions accepted in state; strict string checks added, both regression cases reproduced then passed. Consumer suite: 37 passed; Ruff passed.

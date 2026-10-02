# Portable Session Bundle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Export a source-scoped encrypted Chrome session bundle and import supported state into an isolated Chrome profile without changing existing cookie sync.

**Architecture:** The extension captures one user-initiated navigation and source-scoped browser state, encrypts it locally, and uploads an opaque envelope. The receiver stores one versioned bundle per sender/source. A local CLI decrypts and imports supported state to a dedicated profile.

**Tech Stack:** Chrome MV3 extension, WebCrypto, Python 3.11+, `cryptography` optional session extra, Playwright optional session extra, existing HTTP receiver.

**Spec:** `docs/superpowers/specs/2026-09-28-portable-session-bundle-design.md`

## Global Constraints

- Preserve `/v2/cookies` wire format and existing scheduled sync.
- Do not send plaintext session data, passphrases, or real site requests in tests.
- Do not contact or resume blocked Beike during development or validation.
- Bundle status and terminal output contain metadata only; all file writes are atomic with mode `0600`.

## Review Focus

- A wrong passphrase or altered ciphertext must fail before any browser/profile mutation.
- A source or URL outside the approved domains must fail before capture/import.
- Stale concurrent uploads must not replace the latest bundle.
- A missing permission, browser storage error, or interrupted capture must leave the previous bundle intact.
- An import must not clear cookies or storage in the existing GTR multi-source browser.

---

### Task 1: Bundle codec and scope

**Files:** Create `cookie_http_seeder/session_bundle.py`, `extension/session_bundle.js`, `tests/test_session_bundle.py`, `tests/session_bundle.test.mjs`; modify `pyproject.toml`.

**Interfaces:** JS `encryptBundle(payload, passphrase) -> envelope`; Python `decrypt_bundle(envelope, passphrase) -> payload`, `validate_payload(payload, allowed_domains) -> payload`.

- [x] Write tests for JS/Python cross-language ciphertext, tampering, wrong passphrase, bounds, and out-of-scope origins/URLs; run them and confirm RED.
- [x] Implement PBKDF2-SHA-256 + AES-256-GCM envelope and strict schema/size checks; run focused tests GREEN.
- [x] Add the optional `session` dependencies to package metadata and document the exact interoperable wire format.

### Task 2: Opaque receiver protocol

**Files:** Create `cookie_http_seeder/session_store.py`, `tests/test_session_receiver.py`; modify `cookie_http_seeder/receiver.py`.

**Interfaces:** `POST /v3/session-bundles`, `GET /v3/session-bundles/{source}`; `ReceiverState.ingest_session(payload, peer_ip)`; metadata-only response.

- [x] Write receiver integration tests for authorization, source scope, sender isolation, CAS, mode `0600`, payload rejection, status redaction, and pause invalidation; confirm RED.
- [x] Implement opaque atomic storage and routes; run focused tests GREEN.

### Task 3: Explicit extension capture

**Files:** Create `extension/capture.html`, `extension/capture.js`, `extension/session_capture.js`, `tests/session_capture.test.mjs`; modify `extension/manifest.json`, `extension/popup.html`, `extension/popup.js`.

**Interfaces:** One capture page launched from the popup. It selects an approved source/tab, triggers one reload from the explicit button click, collects the resulting top-level GET/HEAD request plus source cookies and tab-local web storage/environment, encrypts, then uploads through the new route.

- [x] Write Node tests for source/tab scoping, GET/HEAD-only capture, coverage flags, no plaintext upload, and explicit reload trigger; confirm RED.
- [x] Implement explicit capture UI and request listener; run focused tests GREEN.

### Task 4: Isolated target import

**Files:** Create `cookie_http_seeder/session_import.py`, `tests/test_session_import.py`; modify `cookie_http_seeder/cli.py`.

**Interfaces:** `cookie-http-seeder --data-dir DIR session-import SOURCE --bundle PATH --profile DIR --passphrase-file FILE [--open-url URL]` imports to a new dedicated profile, reports redacted coverage, and never alters the existing browser.

- [x] Write tests for wrong passphrase/no profile mutation, scope rejection, storage import, and distinct profile path; confirm RED.
- [x] Implement isolated import and explicit open behavior; run focused tests GREEN.

### Task 5: Documentation and end-to-end validation

**Files:** Modify `README.md`, `SECURITY.md`, `docs/deploy.md`; add local synthetic-site test if needed.

- [x] Document capture/import flow, passphrase handling, coverage limits, and no guarantee of site acceptance.
- [x] Run the full Python suite, Node suite, syntax checks, and a local synthetic-site round trip; Ruff was unavailable locally (not installed or cached). Check `git diff --check` and inspect only non-secret metadata in output.
- [x] Commit only files from this feature on `codex/portable-session-bundle`.

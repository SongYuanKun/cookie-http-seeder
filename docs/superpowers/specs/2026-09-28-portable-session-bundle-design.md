# Portable Session Bundle Design

## Purpose and limits

Allow a user to move transferable authentication state from a Chrome tab to a separate Chrome profile on another machine. A successful upload or import is **not** proof that a site accepts the session. The source IP, network route, site-side account state, hardware-bound credentials, and anti-abuse decisions cannot be copied. The tool records such observations with provenance and never spoofs them.

The first supported route is a user-controlled Chrome extension to a trusted receiver and a local import CLI on GTR. The existing cookie-only protocol and scheduled sync remain compatible. The blocked Beike source stays paused; acceptance uses a local synthetic site.

## Source capture

The user explicitly chooses one already-approved source and one active tab on that source. A capture-page button reloads the chosen tab and captures that user-initiated top-level GET/HEAD navigation; it records the actual URL, method, sent headers, final status/redirect target, and time. It does not record login form submissions, passwords, arbitrary background traffic, response bodies, or requests from unrelated tabs/domains. The extension then collects that source's cookies and the tab origin's localStorage and sessionStorage. Browser/OS user agent, language, timezone, viewport, and screen values are observations only. City/district are optional user-entered labels; receiver-observed peer IP is labeled as such and may be a tunnel or proxy address. Unsupported state, including IndexedDB, partitioned cookies, service workers, device-bound keys, and unavailable geolocation, is reported explicitly in `coverage` rather than silently treated as copied.

The capture page encrypts the complete sensitive payload in memory with a user-supplied passphrase using PBKDF2-SHA-256 and AES-256-GCM. No plaintext bundle is written to extension storage or sent to the receiver. A fresh salt and nonce are generated for each bundle; payload and ciphertext have strict size limits. The passphrase is never persisted or logged.

## Receiver and transport

Add an authenticated, sender-tag-scoped versioned bundle endpoint independent of `/v2/cookies`. The existing source authorization and host allow-list apply. The receiver validates only the envelope and ciphertext bounds, stores an opaque bundle atomically under the selected sender/source with mode `0600`, and returns metadata only. A GET returns version/status, never ciphertext. Conditional writes and request IDs prevent stale uploads. The receiver may store its observed TCP peer IP as metadata, with provenance `receiver_peer`; it must not call this the site's egress IP. Source removal or pause invalidates the corresponding bundle. Existing cookie snapshots continue to work unchanged.

## Target import

The local CLI reads the opaque file, asks for a passphrase from a restricted file descriptor or mode-`0600` file, decrypts in memory, validates the source domain and URL scope, and imports cookies and supported web storage into a **new dedicated Chrome profile**. It never overwrites the existing GTR multi-source browser profile. It reports a redacted coverage matrix and opens the target only on an explicit command. Import does not resume any paused collector. The user may manually verify login in that browser; if a site rejects the session, the report says so without retry loops or identity emulation.

## Security and validation

- Domain allow-lists are enforced both before capture and before import. No cross-source data is accepted.
- A bundle is treated as a credential: no plaintext in logs, terminal output, git, status APIs, or notifications; no remote download endpoint.
- Envelope size, field types, version, nonce/salt lengths, and origin count are bounded. Authentication failure does not write files.
- Tests use a local synthetic site and fixture browser data to prove capture, encryption/decryption, receiver authorization/CAS, scope rejection, and isolated import. They do not contact Beike or any live site.
- Coverage is truthful: V1 cannot promise a complete browser or server-side session clone. Its report names every captured and unsupported state class.

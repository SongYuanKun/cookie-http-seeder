import test from "node:test";
import assert from "node:assert/strict";
import { encryptBundle, decryptBundle, validateSessionPayload } from "../extension/session_bundle.js";

const payload = {
  schema_version: 1, source: "site", domains: ["example.com"],
  captured_at: "2026-09-28T00:00:00Z",
  navigation: { url: "https://example.com/account", method: "GET", final_url: "https://example.com/account", status: 200,
    headers: [{ name: "Cookie", value: "sid=private-value" }] },
  cookies: [{ name: "sid", value: "private-value", domain: ".example.com", path: "/", secure: true }],
  origins: [{ origin: "https://example.com", localStorage: [{ name: "token", value: "private-value" }],
    sessionStorage: [] }],
  environment: { userAgent: "test browser", timezone: "Asia/Shanghai" },
  location: { city: "", district: "", source: "not_observed" },
  coverage: { cookies: "captured", localStorage: "selected_origin_only",
    sessionStorage: "selected_tab_origin_only", requestHeaders: "one_top_level_navigation",
    indexedDB: "unsupported", partitionedCookies: "unsupported", serviceWorkers: "unsupported",
    deviceBoundCredentials: "unsupported", siteEgressIP: "not_observed", geolocation: "not_observed" },
};

test("encrypted bundle hides credentials and decrypts to the original scoped state", async () => {
  const envelope = await encryptBundle(payload, "correct passphrase");
  assert.equal(envelope.schema_version, 1);
  assert.equal(JSON.stringify(envelope).includes("private-value"), false);
  assert.deepEqual(await decryptBundle(envelope, "correct passphrase"), payload);
  await assert.rejects(decryptBundle(envelope, "wrong passphrase"));
  const altered = { ...envelope, ciphertext: envelope.ciphertext.slice(0, -4) + "AAAA" };
  await assert.rejects(decryptBundle(altered, "correct passphrase"));
});

test("bundle rejects foreign navigation and storage origins before encryption", async () => {
  assert.throws(() => validateSessionPayload({ ...payload, navigation: { ...payload.navigation, url: "https://evil.test/" } }));
  assert.throws(() => validateSessionPayload({ ...payload, origins: [{ ...payload.origins[0], origin: "https://evil.test" }] }));
  assert.throws(() => validateSessionPayload({ ...payload, cookies: [{ ...payload.cookies[0], domain: ".evil.test" }] }));
});

test("coverage cannot claim unsupported state was copied or omit limitations", () => {
  assert.throws(() => validateSessionPayload({ ...payload,
    coverage: { ...payload.coverage, indexedDB: "captured" } }));
  const { deviceBoundCredentials, ...incomplete } = payload.coverage;
  assert.throws(() => validateSessionPayload({ ...payload, coverage: incomplete }));
  assert.throws(() => validateSessionPayload({ ...payload,
    coverage: { ...payload.coverage, arbitrary: "copied" } }));
});

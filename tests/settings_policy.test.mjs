import test, { beforeEach } from "node:test";
import assert from "node:assert/strict";
import { loadSettings, normalizeSources, saveSettings, sourceApproved } from "../extension/shared.js";

let storage;
beforeEach(() => {
  storage = { connectionId: "existing", approvedConnectionId: "existing",
    approvedSources: { site: { domains: ["example.test"], label: "site", enabled: true, target_url: "" } } };
  globalThis.chrome = { storage: { local: {
    async get(keys) { return Object.fromEntries(keys.filter(k => k in storage).map(k => [k, storage[k]])); },
    async set(patch) { Object.assign(storage, patch); },
  } } };
});
test("source thresholds roundtrip without changing the old default shape", () => {
  const old = normalizeSources({ site: { domains: ["example.test"] } }).site;
  assert.deepEqual(old, { label: "site", domains: ["example.test"], target_url: "", enabled: true });
  const policy = normalizeSources({ site: { ...old, stale_after_seconds: 300, validation_ttl_seconds: 600 } }).site;
  assert.equal(policy.stale_after_seconds, 300); assert.equal(policy.validation_ttl_seconds, 600);
});
for (const field of ["stale_after_seconds", "validation_ttl_seconds"]) {
  for (const value of [true, "300", 59, 2592001, 300.5, null]) {
    test(`reject invalid ${field}: ${value}`, () => {
      assert.throws(() => normalizeSources({ site: { domains: ["example.test"], [field]: value } }));
    });
  }
}
test("threshold-only updates retain local domain consent", () => {
  const old = normalizeSources({ site: { domains: ["example.test"] } }).site;
  const settings = { connectionId: "existing", approvedConnectionId: "existing", approvedSources: { site: old } };
  assert.equal(sourceApproved(settings, "site", { ...old, stale_after_seconds: 300 }), true);
  assert.equal(sourceApproved(settings, "site", { ...old, domains: ["other.test"] }), false);
});
test("Chrome storage property ordering does not revoke unchanged source consent", () => {
  const approved = { domains: ["example.test"], enabled: true, label: "site", target_url: "" };
  const remote = { label: "site", domains: ["example.test"], target_url: "", enabled: true };
  assert.equal(sourceApproved({ connectionId: "saved", approvedConnectionId: "saved",
    approvedSources: { site: approved } }, "site", remote), true);
});
for (const field of ["recoveryProbeMinutes", "loginPollMinutes"]) {
  test(`${field} defaults off and saves without invalidating authorization`, async () => {
    assert.equal((await loadSettings())[field], 0);
    await saveSettings({ [field]: 15 });
    assert.equal((await loadSettings())[field], 15);
    assert.equal(storage.connectionId, "existing");
    assert.ok(storage.approvedSources.site);
  });
  for (const value of [-1, 1, 14, 10081, 15.5, true, null]) {
    test(`reject invalid ${field}: ${value}`, async () => {
      await assert.rejects(saveSettings({ [field]: value }));
    });
  }
}

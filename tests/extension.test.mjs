import test, { beforeEach } from "node:test";
import assert from "node:assert/strict";
import {
  approveSources, collectSourceCookies, configExport, configImport, domainName,
  endpointURL, fetchSources, loadSettings, normalizeSources, pushAll, receiverRequest,
  saveSettings, sourceOrigins, targetURL,
} from "../extension/shared.js";

const TOKEN = "test-token-0123456789";
const sources = normalizeSources({ site: { domains: ["example.com"], target_url: "https://example.com/" } });
const makeCookie = (patch = {}) => ({ name: "sid", value: "secret", domain: ".example.com", path: "/", storeId: "0", ...patch });
let storage, calls, permission, rows;
function reply(body, status = 200) { return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }); }
beforeEach(() => {
  storage = { endpoint: "http://127.0.0.1:18765", token: TOKEN, connectionId: "local-connection", autoPushMinutes: 0 };
  calls = []; permission = true; rows = [makeCookie()];
  globalThis.chrome = {
    storage: { local: {
      async get(keys) { return structuredClone(Object.fromEntries(keys.filter(k => k in storage).map(k => [k, storage[k]]))); },
      async set(patch) { Object.assign(storage, structuredClone(patch)); },
    } },
    permissions: { async contains() { return permission; } },
    cookies: { async getAll(filter) { calls.push(["cookies", filter]); return structuredClone(rows); } },
  };
  globalThis.fetch = async (url, options) => {
    calls.push(["fetch", url, options]);
    return url.endsWith("/v1/sources")
      ? reply({ ok: true, protocol_version: 2, capabilities: ["conditional_snapshots"], sources, revision: "revision-1" })
      : url.includes("/v2/sync/") ? reply({ ok: true, enabled: true, snapshot_version: null, config_revision: "revision-1" })
      : reply({ ok: true, source: "site", cookieCount: JSON.parse(options.body).cookies.length });
  };
});
for (const bad of ["com", "*.example.com", "https://example.com", "example.com.", "127.1", "0127.0.0.1", "a.123"]) {
  test(`reject invalid domain ${bad}`, () => assert.throws(() => domainName(bad)));
}
for (const bad of ["file:///etc/passwd", "http://user:pass@example.com", "https://example.com/#x", "https://example.com/a/../x", "https://example.com/a/%2e%2e/x", "https://127.1/", "https://example.com\\@evil.test/"]) {
  test(`reject invalid URL ${bad}`, () => assert.throws(() => targetURL(bad)));
}
test("remote HTTP is rejected and HTTPS accepted", () => {
  assert.throws(() => endpointURL("http://10.0.0.1:18765"));
  assert.throws(() => endpointURL("https://receiver.test/path"));
  assert.equal(endpointURL("https://receiver.test"), "https://receiver.test");
});
test("configuration roundtrip contains no credentials", () => {
  assert.deepEqual(configImport(configExport(sources)), sources);
  assert.throws(() => configImport({ ...configExport(sources), token: TOKEN }));
  assert.throws(() => configImport({ schema_version: 1, sources: { site: { ...sources.site, cookies: [] } } }));
  assert.equal(JSON.stringify(configExport(sources)).includes(TOKEN), false);
});
test("out-of-scope target rejected", () => {
  assert.throws(() => normalizeSources({ site: { domains: ["example.com"], target_url: "https://evil.test/" } }));
});
test("same-name cookies retain different paths and domains", async () => {
  rows = [makeCookie(), makeCookie({ path: "/api" }), makeCookie({ domain: "sub.example.com" })];
  assert.equal((await collectSourceCookies(sources.site)).length, 3);
});
test("overlapping roots are collected once", async () => {
  const spec = { ...sources.site, domains: ["sub.example.com", "example.com"] };
  await collectSourceCookies(spec);
  assert.deepEqual(calls.filter(x => x[0] === "cookies").map(x => x[1].domain), ["example.com"]);
});
test("permission denial aborts before cookies are read", async () => {
  permission = false;
  await assert.rejects(collectSourceCookies(sources.site), /permission/);
  assert.equal(calls.length, 0);
});
test("permission revoked during collection does not submit empty snapshot", async () => {
  chrome.cookies.getAll = async () => { permission = false; return []; };
  await assert.rejects(collectSourceCookies(sources.site), /permission changed/);
});
test("partial collection failure aborts entire source", async () => {
  let n = 0;
  chrome.cookies.getAll = async () => { if (n++) throw new Error("read failure"); return rows; };
  await assert.rejects(collectSourceCookies({ ...sources.site, domains: ["example.com", "other.test"] }), /read failure/);
});
test("partitioned, foreign and mixed-store cookies fail closed", async () => {
  rows = [makeCookie({ partitionKey: {} })];
  await assert.rejects(collectSourceCookies(sources.site), /Partitioned/);
  rows = [makeCookie({ domain: "evil.test" })];
  await assert.rejects(collectSourceCookies(sources.site), /allow-list/);
  rows = [makeCookie(), makeCookie({ storeId: "1" })];
  await assert.rejects(collectSourceCookies(sources.site), /Mixed stores/);
});
test("changed source must be approved before collection", async () => {
  const result = await pushAll();
  assert.equal(result.site.ok, false);
  assert.equal(calls.some(x => x[0] === "cookies"), false);
});
test("successful empty read sends explicit complete empty snapshot", async () => {
  await approveSources(await fetchSources(await loadSettings()));
  rows = [];
  const result = await pushAll();
  assert.equal(result.site.ok, true);
  const post = calls.find(x => x[0] === "fetch" && x[1].endsWith("/v2/cookies"));
  const sent = JSON.parse(post[2].body);
  assert.match(sent.request_id, /^[a-f0-9-]{36}$/);
  assert.deepEqual(sent, {
    schema_version: 2, complete: true, source: "site", cookies: [], config_revision: "revision-1",
    expected_version: null, request_id: sent.request_id,
  });
});
test("token change invalidates approval", async () => {
  const doc = await fetchSources(await loadSettings());
  await approveSources(doc);
  await saveSettings({ token: TOKEN + "-changed" });
  assert.deepEqual(storage.approvedSources, {});
  await assert.rejects(approveSources(doc), /Connection changed/);
});
test("invalid timer rejected without saving partial settings", async () => {
  await assert.rejects(saveSettings({ autoPushMinutes: 1 }), /15/);
  assert.equal(storage.autoPushMinutes, 0);
  await saveSettings({ autoPushMinutes: 15 });
  assert.equal(storage.autoPushMinutes, 15);
});
test("connection switch during collection cancels push", async () => {
  await approveSources(await fetchSources(await loadSettings()));
  chrome.cookies.getAll = async () => { await saveSettings({ token: TOKEN + "-new" }); return rows; };
  const result = await pushAll();
  assert.equal(result.site.ok, false);
  assert.equal(calls.some(x => x[0] === "fetch" && x[1].endsWith("/v2/cookies")), false);
});
test("request disables redirects, browser credentials, and has timeout", async () => {
  await receiverRequest(await loadSettings(), "/v1/sources");
  const options = calls[0][2];
  assert.equal(options.redirect, "error"); assert.equal(options.credentials, "omit");
  assert.ok(options.signal instanceof AbortSignal);
});
test("untrusted receiver errors are not reflected", async () => {
  globalThis.fetch = async () => reply({ ok: false, error: TOKEN }, 401);
  await assert.rejects(receiverRequest(await loadSettings(), "/v1/sources"), error => {
    assert.equal(error.message, "Token rejected"); return true;
  });
});
test("runtime site permissions are scoped", () => {
  assert.deepEqual(sourceOrigins(sources.site), ["*://*.example.com/*"]);
});

for (const status of [408, 429, 500, 503]) {
  test(`HTTP ${status} uses bounded retryable errors`, async () => {
    globalThis.fetch = async () => new Response("private error page", { status, headers: { "Retry-After": "120" } });
    await assert.rejects(receiverRequest(await loadSettings(), "/v1/sources"), error => {
      assert.equal(error.retryable, true); assert.equal(error.retryAfterMs, 120_000);
      assert.equal(error.message.includes("private error page"), false); return true;
    });
  });
}
for (const status of [400, 401, 403, 404, 410, 428]) {
  test(`HTTP ${status} is terminal until user fixes it`, async () => {
    globalThis.fetch = async () => reply({ error: TOKEN }, status);
    await assert.rejects(receiverRequest(await loadSettings(), "/v1/sources"), error => {
      assert.equal(error.retryable, false); assert.equal(error.message.includes(TOKEN), false); return true;
    });
  });
}
test("snapshot and configuration conflicts have different retry policy", async () => {
  globalThis.fetch = async () => reply({ error: "snapshot_conflict" }, 409);
  await assert.rejects(receiverRequest(await loadSettings(), "/v1/sources"), error => error.code === "snapshot_conflict" && error.retryable);
  globalThis.fetch = async () => reply({ error: "configuration_changed" }, 409);
  await assert.rejects(receiverRequest(await loadSettings(), "/v1/sources"), error => error.code === "configuration_changed" && !error.retryable);
});
test("successful non-JSON response is not accepted", async () => {
  globalThis.fetch = async () => new Response("not json");
  await assert.rejects(receiverRequest(await loadSettings(), "/v1/sources"), error => error.code === "invalid_response");
});
test("response size is bounded", async () => {
  globalThis.fetch = async () => new Response("x".repeat(1024 * 1024 + 1));
  await assert.rejects(receiverRequest(await loadSettings(), "/v1/sources"), error => error.code === "invalid_response");
});
test("network exceptions are sanitized and retryable", async () => {
  globalThis.fetch = async () => { throw new TypeError(TOKEN); };
  await assert.rejects(receiverRequest(await loadSettings(), "/v1/sources"), error => {
    assert.equal(error.message.includes(TOKEN), false); return error.code === "network_error" && error.retryable;
  });
});
test("receiver version is read before browser cookies", async () => {
  await approveSources(await fetchSources(await loadSettings()));
  calls.length = 0;
  await pushAll();
  const version = calls.findIndex(x => x[0] === "fetch" && x[1].includes("/v2/sync/"));
  const collected = calls.findIndex(x => x[0] === "cookies");
  assert.ok(version >= 0 && version < collected);
});
test("revoking local approval during collection prevents posting", async () => {
  await approveSources(await fetchSources(await loadSettings()));
  chrome.cookies.getAll = async () => { storage.approvedSources = {}; return rows; };
  const result = await pushAll();
  assert.equal(result.site.ok, false);
  assert.equal(calls.some(x => x[0] === "fetch" && x[1].endsWith("/v2/cookies")), false);
});
test("old receivers without conditional-write capability require upgrade", async () => {
  globalThis.fetch = async () => reply({ ok: true, protocol_version: 2, sources, revision: "r" });
  await assert.rejects(fetchSources(await loadSettings()), error => error.code === "upgrade_required");
});

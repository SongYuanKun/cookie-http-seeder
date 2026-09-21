import assert from "node:assert/strict";
import test from "node:test";
import { senderTag } from "../extension/senders.js";
import { configExport, loadSettings, saveSettings, receiverRequest, fetchSources,
  pushSource, approveSources } from "../extension/shared.js";
import { SyncEngine } from "../extension/sync.js";

const spec = { label: "Demo", domains: ["example.test"], enabled: true, target_url: "" };
const token = "synthetic-test-token-0123456789";
function mock(tag = "home-pc") {
  let state = { senderTag: tag, token, endpoint: "http://127.0.0.1:18765",
    connectionId: "original", approvedConnectionId: "original", approvedSources: { demo: spec } };
  const reads = [];
  globalThis.chrome = {
    storage: { local: {
      get: async keys => Object.fromEntries(keys.filter(k => Object.hasOwn(state, k)).map(k => [k, structuredClone(state[k])])),
      set: async patch => { state = { ...state, ...structuredClone(patch) }; },
    } },
    permissions: { contains: async () => true },
    cookies: { getAll: async () => { reads.push("cookies"); return []; } },
    alarms: { get: async () => null, clear: async () => true, create: async () => {} },
  };
  return { state: () => structuredClone(state), reads };
}
function doc(tag = "home-pc") {
  return { ok: true, sender_tag: tag, protocol_version: 2, revision: "revision",
    capabilities: ["conditional_snapshots", "sender_tags"], sources: { demo: spec } };
}
const response = body => new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });

for (const tag of ["default", "home-pc", "work_pc", "1", "a".repeat(32)]) {
  test(`valid sender label ${tag}`, () => assert.equal(senderTag(tag), tag));
}
for (const tag of [null, "", false, 1, "../bad", "a/b", "a\\b", "Home", "a ", " a", "a\n",
  "家里", "con", "aux", "lpt1", "com9", "nul", "a".repeat(33), "-x", "_x"]) {
  test(`invalid sender label ${JSON.stringify(tag)}`, () => assert.throws(() => senderTag(tag)));
}

test("legacy settings keep default label", async () => {
  mock(undefined);
  // Truly old storage does not contain senderTag.
  const originalGet = chrome.storage.local.get;
  chrome.storage.local.get = async keys => { const s = await originalGet(keys); delete s.senderTag; return s; };
  assert.equal((await loadSettings()).senderTag, "default");
});

test("tag change invalidates approvals and old retry queue even with unchanged token", async () => {
  mock();
  const engine = new SyncEngine();
  await engine.enqueue(["demo"]);
  assert.equal((await engine.status()).jobs.demo.phase, "pending");
  const previous = (await loadSettings()).connectionId;
  await saveSettings({ senderTag: "work-pc" });
  const next = await loadSettings();
  assert.notEqual(next.connectionId, previous);
  assert.equal(next.token, token);
  assert.deepEqual(next.approvedSources, {});
  await engine.recover();
  assert.deepEqual((await engine.status()).jobs, {});
});

test("same tag is stable; invalid label cannot partly save settings", async () => {
  const data = mock();
  await saveSettings({ senderTag: "home-pc" });
  assert.equal((await loadSettings()).connectionId, "original");
  await assert.rejects(saveSettings({ senderTag: "../bad", autoPushMinutes: 20 }));
  assert.equal(data.state().autoPushMinutes, undefined);
});

test("config exports do not copy sender identity or tokens", () => {
  assert.deepEqual(Object.keys(configExport({ demo: spec })), ["schema_version", "sources"]);
});

test("every request explicitly carries sender label; unsafe redirects remain disabled", async () => {
  mock();
  let options;
  globalThis.fetch = async (_url, opts) => { options = opts; return response(doc()); };
  assert.equal((await fetchSources(await loadSettings())).sender_tag, "home-pc");
  assert.equal(options.headers["X-Sender-Tag"], "home-pc");
  assert.equal(options.headers.Authorization, `Bearer ${token}`);
  assert.equal(options.redirect, "error");
});

test("old receiver rejected before any mutation", async () => {
  mock(); const methods = [];
  globalThis.fetch = async (_url, opts) => { methods.push(opts.method || "GET"); return response({ ok: true }); };
  await assert.rejects(receiverRequest(await loadSettings(), "/v1/sources", {
    method: "PUT", body: JSON.stringify({ sources: {}, revision: "old" }),
  }), error => error.code === "upgrade_required");
  assert.deepEqual(methods, ["GET"]);
});

test("old receiver remains compatible with default label", async () => {
  mock("default");
  globalThis.fetch = async () => response({ ...doc(), sender_tag: undefined });
  assert.equal((await fetchSources(await loadSettings())).ok, true);
});

test("mismatched response namespace is rejected", async () => {
  mock();
  globalThis.fetch = async () => response(doc("work-pc"));
  await assert.rejects(fetchSources(await loadSettings()), e => e.code === "upgrade_required");
});

test("label change during capability preflight stops original mutation", async () => {
  mock(); let calls = 0;
  const old = await loadSettings();
  globalThis.fetch = async () => { calls++; await saveSettings({ senderTag: "work-pc" }); return response(doc()); };
  await assert.rejects(receiverRequest(old, "/v2/cookies", { method: "POST", body: "{}" }),
    e => e.code === "approval_required");
  assert.equal(calls, 1);
});

test("tagged upload uses a complete isolated snapshot and recollects only after preflight", async () => {
  const data = mock(); const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    if (url.endsWith("/v2/sync/demo")) {
      return response({ ok: true, sender_tag: "home-pc", config_revision: "revision", enabled: true, snapshot_version: null });
    }
    if (url.endsWith("/v1/sources")) return response(doc());
    return response({ ok: true, sender_tag: "home-pc", cleared: true });
  };
  const settings = await loadSettings();
  const result = await pushSource("demo", { ...doc(), connectionId: settings.connectionId }, settings);
  assert.equal(result.cleared, true);
  assert.equal(data.reads.length, 1);
  const sent = calls.find(c => c.options.method === "POST");
  assert.equal(sent.options.headers["X-Sender-Tag"], "home-pc");
  assert.deepEqual(JSON.parse(sent.options.body).cookies, []);
  assert.equal(JSON.parse(sent.options.body).complete, true);
});

test("tag change during cookie collection prevents POST and needs fresh approval", async () => {
  mock(); const methods = [];
  const settings = await loadSettings();
  globalThis.fetch = async (_url, options) => {
    methods.push(options.method || "GET");
    return response({ ok: true, sender_tag: "home-pc", config_revision: "revision", enabled: true, snapshot_version: null });
  };
  chrome.cookies.getAll = async () => { await saveSettings({ senderTag: "work-pc" }); return []; };
  await assert.rejects(pushSource("demo", doc(), settings), e => e.code === "approval_required");
  assert.deepEqual(methods, ["GET"]);
  await assert.rejects(approveSources({ ...doc(), connectionId: settings.connectionId }));
});

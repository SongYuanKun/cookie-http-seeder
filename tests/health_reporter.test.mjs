import test, { beforeEach } from "node:test";
import assert from "node:assert/strict";
import { HEALTH_ALARM, HealthReporter } from "../extension/health_reporter.js";
import { DEFAULTS, loadSettings, saveSettings } from "../extension/shared.js";
import { receiverRequest } from "../extension/receiver_client.js";
import { QUEUE_KEY, SyncEngine } from "../extension/sync.js";

let settings, api, alarms, posts, reporter, storage, doc, queue, granted;
beforeEach(() => {
  storage = {}; alarms = new Map(); posts = []; granted = true;
  const spec = { label: "site", enabled: true, domains: ["example.test"], target_url: "" };
  settings = { ...DEFAULTS, connectionId: "home", approvedConnectionId: "home",
    approvedSources: { site: spec }, healthReportMinutes: 15 };
  doc = { sources: { site: spec }, capabilities: ["client_health"], revision: "revision" };
  queue = { connectionId: "home", jobs: { site: { phase: "blocked", errorCode: "permission_required",
    lastSuccessAt: 1791000000000 } } };
  api = { runtime: { getManifest: () => ({ version: "0.5.0" }) },
    cookies: { getAll: () => assert.fail("heartbeat must never read cookies") },
    permissions: { contains: async () => granted },
    storage: { local: {
      get: async keys => Object.fromEntries(keys.filter(k => k in storage).map(k => [k, storage[k]])),
      set: async patch => Object.assign(storage, patch),
    } }, alarms: {
      get: async name => alarms.get(name), clear: async name => alarms.delete(name),
      create: async (name, info) => alarms.set(name, info),
    } };
  globalThis.chrome = api;
  reporter = new HealthReporter({ api, now: () => 1791093600000,
    settings: async () => structuredClone(settings), getSources: async () => structuredClone(doc),
    queue: async () => structuredClone(queue),
    post: async (_settings, payload) => { posts.push(payload); return { ok: true }; } });
});
test("old storage retains consent with health disabled by default", async () => {
  storage = { connectionId: "original", approvedConnectionId: "original",
    approvedSources: settings.approvedSources, token: "synthetic-token-01234567" };
  assert.equal((await loadSettings()).healthReportMinutes, 0);
  await saveSettings({ healthReportMinutes: 15 });
  assert.equal(storage.connectionId, "original");
  assert.equal(storage.token, "synthetic-token-01234567");
  assert.ok(storage.approvedSources.site);
  await assert.rejects(saveSettings({ healthReportMinutes: 14 }));
});
test("worker rebuilds alarm, sleep yields one metadata report without cookies", async () => {
  await reporter.recover(); assert.equal(alarms.get(HEALTH_ALARM).periodInMinutes, 15);
  await reporter.report();
  assert.equal(posts.length, 1);
  assert.equal(posts[0].sources.site.error_code, "permission_denied");
  assert.equal(posts[0].sources.site.permission_granted, true);
  assert.equal(JSON.stringify(posts).includes("example.test"), false);
  assert.equal(JSON.stringify(posts).includes("token"), false);
  queue.jobs.site.errorCode = "arbitrary-secret-error";
  await reporter.report(); assert.equal(posts[1].sources.site.error_code, "unknown_error");
});
test("disabled and legacy receivers cancel alarm and do not post", async () => {
  await reporter.recover(); settings.healthReportMinutes = 0;
  await reporter.recover(); await reporter.report();
  assert.equal(alarms.has(HEALTH_ALARM), false); assert.equal(posts.length, 0);
  settings.healthReportMinutes = 15; doc.capabilities = [];
  assert.equal((await reporter.report()).code, "upgrade_required");
  assert.equal(posts.length, 0);
});
test("connection switch during fetch discards old report", async () => {
  reporter.getSources = async () => { settings.connectionId = "work"; return doc; };
  assert.equal((await reporter.report()).code, "connection_changed");
  assert.equal(posts.length, 0);
});
test("late report result cannot persist into a new connection", async () => {
  reporter.post = async () => { settings.connectionId = "work"; return { ok: true }; };
  assert.equal((await reporter.report()).code, "connection_changed");
  assert.equal(storage.clientHealthReportV1, undefined);
});
test("permission revoked during gathering is reflected in metadata", async () => {
  reporter.queue = async () => { granted = false; return queue; };
  await reporter.report(); assert.equal(posts[0].sources.site.permission_granted, false);
});
test("temporary disconnect keeps a scheduled recovery heartbeat", async () => {
  reporter.getSources = async () => { throw { code: "network_error", retryable: true }; };
  await reporter.recover();
  assert.equal(alarms.get(HEALTH_ALARM).periodInMinutes, 15);
  assert.equal((await reporter.report()).code, "unavailable");
  reporter.getSources = async () => doc;
  assert.equal((await reporter.report()).ok, true);
  assert.equal(posts.length, 1);
});

test("HTTP 429 survives the durable queue and reaches heartbeat as rate_limited", async () => {
  settings.token = "synthetic-token-01234567";
  settings.recoveryProbeMinutes = 15;
  const previousFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response("private error", {
    status: 429, headers: { "Retry-After": "120" },
  });
  try {
    const engine = new SyncEngine({ api, now: () => 1791093600000, random: () => 0,
      settings: async () => settings, getSources: async () => doc,
      push: async () => receiverRequest(settings, "/v1/status") });
    await engine.enqueue(); await engine.runDue();
    const job = storage[QUEUE_KEY].jobs.site;
    assert.equal(job.phase, "retrying");
    assert.equal(job.errorCode, "rate_limited");
    assert.equal(job.nextAt, 1791093720000);
    reporter.queue = async () => storage[QUEUE_KEY];
    await reporter.report();
    assert.equal(posts[0].sources.site.error_code, "rate_limited");
    job.phase = "exhausted"; job.attempts = 5; job.probeAt = null;
    await engine.recover();
    assert.equal(job.probeAt, 1791094500000);
  } finally { globalThis.fetch = previousFetch; }
});

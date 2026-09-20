import test, { beforeEach } from "node:test";
import assert from "node:assert/strict";
import { normalizeSources, ReceiverError } from "../extension/shared.js";
import { DEBOUNCE_MS, MAX_ATTEMPTS, QUEUE_KEY, RETRY_ALARM, retryDelay, SyncEngine } from "../extension/sync.js";

const sources = normalizeSources({ site: { domains: ["example.com"], target_url: "https://example.com/" } });
let storage, alarms, clock, engine, calls, perform, api, settings;
function makeEngine() {
  return new SyncEngine({ api, now: () => clock, random: () => 0,
    settings: async () => structuredClone(settings),
    getSources: async () => ({ sources: structuredClone(sources), revision: "r", capabilities: ["conditional_snapshots"] }),
    push: async (...args) => { calls.push(args[0]); return perform(...args); },
  });
}
const job = () => storage[QUEUE_KEY]?.jobs.site;
beforeEach(() => {
  storage = {}; alarms = new Map(); clock = 1_800_000_000_000; calls = [];
  settings = { connectionId: "connection-1", approvedConnectionId: "connection-1", approvedSources: structuredClone(sources),
    syncOnChange: true, autoPushMinutes: 15, token: "do-not-persist-token" };
  api = { storage: { local: {
    async get(keys) { return structuredClone(Object.fromEntries(keys.filter(k => k in storage).map(k => [k, storage[k]]))); },
    async set(value) { Object.assign(storage, structuredClone(value)); },
  } }, alarms: {
    async get(name) { return alarms.get(name); },
    async create(name, info) { alarms.set(name, { ...info, scheduledTime: info.when }); },
    async clear(name) { alarms.delete(name); },
  } };
  perform = async () => ({ ok: true, cookieCount: 2, unchanged: false });
  engine = makeEngine();
});

test("manual push is durable before sending and queue contains no secrets", async () => {
  await engine.enqueue();
  perform = async () => {
    assert.equal(job().phase, "syncing"); assert.equal(job().attempts, 1);
    return { ok: true, cookieCount: 1 };
  };
  await engine.runDue();
  assert.equal(job().phase, "succeeded"); assert.equal(calls.length, 1);
  assert.equal(JSON.stringify(storage).includes(settings.token), false);
  assert.equal(alarms.has(RETRY_ALARM), false);
});
test("exponential retry is bounded with jitter", () => {
  assert.equal(retryDelay(1, () => 0), 30_000);
  assert.equal(retryDelay(2, () => 0), 60_000);
  assert.equal(retryDelay(3, () => 1), 144_000);
  assert.ok(retryDelay(20, () => 1) <= 1_080_000);
});
test("transient failure retries only after due time", async () => {
  perform = async () => { throw new ReceiverError("network_error", "offline", { retryable: true }); };
  await engine.enqueue(); await engine.runDue();
  assert.equal(job().phase, "retrying"); assert.equal(job().nextAt, clock + 30_000);
  await engine.runDue(); assert.equal(calls.length, 1);
  clock += 30_000;
  perform = async () => ({ ok: true, cookieCount: 2 });
  await engine.runDue(); assert.equal(calls.length, 2); assert.equal(job().phase, "succeeded");
});
test("a fresh engine recovers persisted pending work", async () => {
  await engine.enqueue(); engine = makeEngine(); await engine.recover();
  await engine.runDue(); assert.equal(job().phase, "succeeded");
});
test("worker interruption consumes the recorded attempt rather than resetting budget", async () => {
  await engine.enqueue(); Object.assign(storage[QUEUE_KEY].jobs.site, { phase: "syncing", attempts: 4, nextAt: clock });
  engine = makeEngine(); await engine.recover();
  assert.equal(job().phase, "retrying"); assert.equal(job().attempts, 4);
  await engine.runDue(); assert.equal(job().attempts, 5);
});
test("interruption at final attempt becomes exhausted", async () => {
  await engine.enqueue(); Object.assign(storage[QUEUE_KEY].jobs.site, { phase: "syncing", attempts: 5 });
  await engine.recover(); assert.equal(job().phase, "exhausted");
  assert.equal(alarms.has(RETRY_ALARM), false);
});
test("max five automatic attempts and change events cannot replenish budget", async () => {
  perform = async () => { throw new ReceiverError("network_error", "offline", { retryable: true }); };
  await engine.enqueue();
  for (let i = 0; i < MAX_ATTEMPTS; i++) {
    await engine.runDue(); clock = job().nextAt ?? clock;
  }
  assert.equal(calls.length, 5); assert.equal(job().phase, "exhausted");
  await engine.enqueue(null, "change"); await engine.enqueue(null, "periodic");
  await engine.runDue(); assert.equal(calls.length, 5);
});
test("manual retry explicitly resets exhausted budget", async () => {
  await engine.enqueue(); Object.assign(storage[QUEUE_KEY].jobs.site, { phase: "exhausted", attempts: 5, nextAt: null });
  await engine.enqueue(null, "manual"); assert.equal(job().attempts, 0);
  await engine.runDue(); assert.equal(job().phase, "succeeded");
});
test("auth error blocks rather than retries", async () => {
  perform = async () => { throw new ReceiverError("unauthorized", "Token rejected"); };
  await engine.enqueue(); await engine.runDue();
  assert.equal(job().phase, "blocked"); assert.equal(job().nextAt, null);
  await engine.enqueue(null, "periodic"); clock += 1_000_000; await engine.runDue();
  assert.equal(calls.length, 1);
});
test("retry-after is honored", async () => {
  perform = async () => { throw new ReceiverError("server_unavailable", "busy", { retryable: true, retryAfterMs: 120_000 }); };
  await engine.enqueue(); await engine.runDue(); assert.equal(job().nextAt, clock + 120_000);
});
test("cookie changes are coalesced without storing event values", async () => {
  await engine.changed({ removed: true, cookie: { domain: ".example.com", value: "never-store-this-cookie" } });
  assert.equal(job().nextAt, clock + DEBOUNCE_MS);
  clock += 10_000;
  await engine.changed({ cookie: { domain: ".example.com", value: "new-value" } });
  assert.equal(job().nextAt, clock + DEBOUNCE_MS);
  assert.equal(JSON.stringify(storage).includes("never-store-this-cookie"), false);
  assert.equal(JSON.stringify(storage).includes("new-value"), false);
  await engine.runDue(); assert.equal(calls.length, 0);
});
test("continuous changes have a bounded coalescing window", async () => {
  const start = clock;
  await engine.enqueue(null, "change");
  for (let i = 0; i < 5; i++) { clock += 10_000; await engine.enqueue(null, "change"); }
  assert.ok(job().nextAt <= start + 60_000);
});
test("changes while retrying preserve backoff and read fresh on the next attempt", async () => {
  let value = "old", seen = [];
  perform = async () => { seen.push(value); throw new ReceiverError("network_error", "offline", { retryable: true }); };
  await engine.enqueue(); await engine.runDue(); const next = job().nextAt;
  value = "new"; await engine.enqueue(null, "change");
  assert.equal(job().nextAt, next); assert.equal(job().attempts, 1);
  clock = next; await engine.runDue(); assert.deepEqual(seen, ["old", "new"]);
});
test("change sync disabled by user prevents collection", async () => {
  settings.syncOnChange = false;
  await engine.changed({ cookie: { domain: "example.com" } });
  assert.equal(job(), undefined);
});
test("turning off change sync cancels an already queued automatic change", async () => {
  await engine.enqueue(null, "change"); settings.syncOnChange = false; clock += 60_000;
  await engine.runDue(); assert.equal(calls.length, 0); assert.equal(job().phase, "paused");
});
test("foreign and partitioned changes do not enqueue", async () => {
  await engine.changed({ cookie: { domain: "other.test" } });
  await engine.changed({ cookie: { domain: "example.com", partitionKey: {} } });
  assert.equal(job(), undefined);
});
test("source approval mismatch stops before push", async () => {
  await engine.enqueue(); settings.approvedSources.site.target_url = "https://example.com/changed";
  await engine.runDue(); assert.equal(calls.length, 0); assert.equal(job().errorCode, "approval_required");
});
test("source removed by receiver marks queue paused", async () => {
  await engine.enqueue(); engine.getSources = async () => ({ sources: {} });
  await engine.runDue(); assert.equal(calls.length, 0); assert.equal(job().phase, "paused");
});
test("connection switch discards all old pending jobs", async () => {
  await engine.enqueue(); settings.connectionId = "connection-2";
  await engine.recover(); assert.deepEqual(storage[QUEUE_KEY].jobs, {});
  await engine.runDue(); assert.equal(calls.length, 0);
});
test("connection switch during push does not attribute success to new connection", async () => {
  perform = async () => { settings.connectionId = "connection-2"; return { ok: true }; };
  await engine.enqueue(); const result = await engine.runDue();
  assert.equal(result.ok, false); assert.deepEqual((await engine.status()).jobs, {});
});
test("untrusted exception messages are never stored or returned", async () => {
  perform = async () => { throw new Error("sensitive-cookie-secret"); };
  await engine.enqueue(); const result = await engine.runDue();
  assert.equal(JSON.stringify([storage, result]).includes("sensitive-cookie-secret"), false);
  assert.equal(job().errorCode, "operation_failed");
});
test("snapshot conflict retries but config conflict blocks", async () => {
  perform = async () => { throw new ReceiverError("snapshot_conflict", "conflict", { retryable: true }); };
  await engine.enqueue(); await engine.runDue(); assert.equal(job().phase, "retrying");
  clock = job().nextAt;
  perform = async () => { throw new ReceiverError("configuration_changed", "approve"); };
  await engine.runDue(); assert.equal(job().phase, "blocked");
});
test("unchanged success remains successful", async () => {
  perform = async () => ({ ok: true, unchanged: true, cookieCount: 2 });
  await engine.enqueue(); await engine.runDue(); assert.equal(job().unchanged, true);
});
test("periodic alarms do not erase an existing retry deadline", async () => {
  perform = async () => { throw new ReceiverError("network_error", "offline", { retryable: true }); };
  await engine.enqueue(); await engine.runDue(); const before = structuredClone(job());
  await engine.enqueue(null, "periodic"); assert.deepEqual(job(), before);
});
test("alarm is recreated if Chrome discarded it", async () => {
  await engine.enqueue(); alarms.clear(); engine = makeEngine(); await engine.recover();
  assert.ok(alarms.has(RETRY_ALARM));
});
test("receiver preflight failures also consume bounded retry attempts", async () => {
  engine.getSources = async () => { throw new ReceiverError("network_error", "offline", { retryable: true }); };
  await engine.enqueue(); await engine.runDue(); assert.equal(job().attempts, 1);
  assert.equal(job().phase, "retrying"); assert.equal(calls.length, 0);
});

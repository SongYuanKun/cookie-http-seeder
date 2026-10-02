import test, { beforeEach } from "node:test";
import assert from "node:assert/strict";
import { LOGIN_ALARM, LOGIN_STATE_KEY, LoginMonitor } from "../extension/login_monitor.js";

let settings, storage, alarms, api, clock, notifications, remote, fetchCount, monitor;
const makeMonitor = () => new LoginMonitor({ api, now: () => clock,
  settings: async () => structuredClone(settings),
  getStatus: async () => { fetchCount++; return structuredClone(remote); },
});
beforeEach(() => {
  clock = 1_800_000_000_000; notifications = []; fetchCount = 0; storage = {}; alarms = new Map();
  settings = { connectionId: "home", approvedConnectionId: "home", senderTag: "home-pc",
    token: "never-persist-this-token", loginPollMinutes: 15,
    approvedSources: { site: { label: "never-notify-raw-label", domains: ["example.test"],
      target_url: "", enabled: true } } };
  remote = { sources: { site: { enabled: true, present: true, cleared: false, freshness: "fresh",
    snapshot_version: "a".repeat(32), validation: "invalid",
    validationDetail: { origin: "crawler_report", reasonCode: "session_expired" } } } };
  api = { storage: { local: {
    async get(keys) { return structuredClone(Object.fromEntries(keys.filter(k => k in storage).map(k => [k, storage[k]]))); },
    async set(patch) { Object.assign(storage, structuredClone(patch)); },
  } }, alarms: {
    async get(name) { return alarms.get(name); },
    async create(name, info) { alarms.set(name, { ...info, scheduledTime: clock + info.delayInMinutes * 60_000 }); },
    async clear(name) { alarms.delete(name); },
  }, notifications: {
    async create(info) { notifications.push(info); return "notification-id"; },
  } };
  monitor = makeMonitor();
});
test("new invalid feedback notifies the matching sender without secrets", async () => {
  assert.equal((await monitor.check()).notified, 1);
  assert.equal(notifications.length, 1);
  assert.match(notifications[0].message, /home-pc\/site/);
  const persisted = JSON.stringify(storage);
  assert.equal(persisted.includes(settings.token), false);
  assert.equal(persisted.includes("never-notify-raw-label"), false);
  assert.equal(JSON.stringify(notifications).includes("never-notify-raw-label"), false);
});
test("same invalid version is deduplicated across worker restarts", async () => {
  await monitor.check(); clock += 900_000; monitor = makeMonitor();
  assert.equal((await monitor.check()).notified, 0);
  assert.equal(notifications.length, 1);
});
test("new invalid snapshot can notify after per-source cooldown", async () => {
  await monitor.check();
  remote.sources.site.snapshot_version = "b".repeat(32);
  assert.equal((await monitor.check()).notified, 0);
  clock += 900_000;
  assert.equal((await monitor.check()).notified, 1);
  assert.equal(notifications.length, 2);
});
test("positive recovery allows a later loss of the same snapshot to notify", async () => {
  await monitor.check(); remote.sources.site.validation = "valid";
  await monitor.check(); clock += 900_000; remote.sources.site.validation = "invalid";
  assert.equal((await monitor.check()).notified, 1);
});
test("stale sync and expired crawler feedback have actionable notifications", async () => {
  remote.sources.site.validation = "unverified"; remote.sources.site.freshness = "stale";
  await monitor.check(); assert.match(notifications[0].message, /同步/);
  clock += 900_000; remote.sources.site.freshness = "fresh"; remote.sources.site.validation = "expired";
  await monitor.check(); assert.match(notifications[1].message, /验证/);
});
test("disabled polling never contacts receiver and cancels alarm", async () => {
  await monitor.recover(); assert.equal(alarms.get(LOGIN_ALARM).periodInMinutes, 15);
  settings.loginPollMinutes = 0;
  await monitor.recover(); await monitor.check();
  assert.equal(fetchCount, 0); assert.equal(notifications.length, 0);
  assert.equal(alarms.has(LOGIN_ALARM), false);
});
test("revoked, disabled and unapproved sources never notify", async () => {
  settings.approvedSources.site.enabled = false;
  await monitor.check(); assert.equal(notifications.length, 0);
  settings.approvedSources = {};
  await monitor.check(); assert.equal(fetchCount, 0);
  settings.approvedSources = { site: { enabled: true } }; settings.approvedConnectionId = "other";
  await monitor.check(); assert.equal(fetchCount, 0);
});
test("receiver disconnect returns unavailable instead of reusing past login status", async () => {
  monitor.getStatus = async () => { throw new Error("never-reflect-secret-error"); };
  const result = await monitor.check();
  assert.equal(result.ok, false); assert.equal(result.code, "unavailable");
  assert.equal(notifications.length, 0);
  assert.equal(JSON.stringify(result).includes("secret-error"), false);
});
test("connection switch during status fetch prevents old label notification", async () => {
  monitor.getStatus = async () => { settings.connectionId = "work"; return remote; };
  await monitor.check(); assert.equal(notifications.length, 0);
  assert.notEqual(storage[LOGIN_STATE_KEY]?.connectionId, "work");
});
test("revoke during status fetch stops the pending notification", async () => {
  monitor.getStatus = async () => { settings.approvedSources = {}; return remote; };
  await monitor.check(); assert.equal(notifications.length, 0);
});
test("notification failure can retry at the next poll without marking it delivered", async () => {
  api.notifications.create = async () => { throw new Error("notification unavailable"); };
  assert.equal((await monitor.check()).notified, 0);
  clock += 900_000;
  api.notifications.create = async info => { notifications.push(info); };
  assert.equal((await monitor.check()).notified, 1);
});
test("malformed or credential-bearing remote fields are not reflected", async () => {
  remote.sources.site.snapshot_version = "secret-not-a-version";
  await monitor.check(); assert.equal(notifications.length, 0);
  remote.sources.site.snapshot_version = "a".repeat(32);
  remote.sources.site.validationDetail.origin = "raw-response-secret";
  remote.sources.site.validation = "secret-invalid-value";
  await monitor.check(); assert.equal(notifications.length, 0);
});
test("remote paused and cleared snapshots are not reported as login loss", async () => {
  remote.sources.site.enabled = false;
  await monitor.check(); assert.equal(notifications.length, 0);
  remote.sources.site.enabled = true; remote.sources.site.cleared = true;
  await monitor.check(); assert.equal(notifications.length, 0);
});
test("alarm persists across worker evaluation without pushing its deadline back", async () => {
  await monitor.recover(); const deadline = alarms.get(LOGIN_ALARM).scheduledTime;
  clock += 1000; monitor = makeMonitor(); await monitor.recover();
  assert.equal(alarms.get(LOGIN_ALARM).scheduledTime, deadline);
});
test("simultaneous checks produce only one notification", async () => {
  await Promise.all([monitor.check(), monitor.check()]);
  assert.equal(notifications.length, 1);
});

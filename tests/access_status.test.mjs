import assert from "node:assert/strict";
import test from "node:test";
import { cachedSourceDocument, removableOrigins, revokeLocalSource } from "../extension/access.js";
import { sourceStatus } from "../extension/source_status.js";
import { approveSources, loadSettings, pushSource, sourceApproved } from "../extension/shared.js";
import { withSettingsLock } from "../extension/settings_lock.js";

const spec = (domains = ["example.test"]) => ({ domains, enabled: true, label: "demo", target_url: "" });
const fresh = () => ({ present: true, cookieCount: 2, freshness: "fresh", validation: "unverified",
  updatedAt: "2026-09-20T08:00:00Z", lastSeenAt: "2026-09-20T09:00:00Z" });
const context = () => ({ approved: true, permission: true, remoteAvailable: true, remote: fresh() });

function mockAccess() {
  let state = { connectionId: "connection-one", approvedConnectionId: "connection-one",
    endpoint: "http://127.0.0.1:18765", token: "not-used-in-this-operation",
    approvedSources: { demo: spec(), peer: spec(["other.test"]) } };
  const removed = [], messages = [];
  const api = {
    storage: { local: {
      get: async () => structuredClone(state),
      set: async patch => { state = { ...state, ...structuredClone(patch) }; },
    } },
    permissions: {
      getAll: async () => ({ origins: ["*://*.example.test/*", "*://*.other.test/*"] }),
      remove: async request => { removed.push(request); return true; },
    },
    runtime: {
      getManifest: () => ({ host_permissions: ["http://127.0.0.1/*"] }),
      sendMessage: async request => { messages.push(request); return { ok: true }; },
    },
    cookies: { getAll: () => { throw new Error("must not read cookies"); } },
  };
  return { api, settings: async () => structuredClone(state), removed, messages,
    state: () => structuredClone(state) };
}

test("revoke stops approval, keeps peer and requests queue recovery without reading cookies", async () => {
  const mock = mockAccess();
  const result = await revokeLocalSource("demo", "connection-one", mock);
  assert.equal(result.revoked, true);
  assert.equal(result.permissionCleanup, "removed");
  assert.equal(sourceApproved(mock.state(), "demo", spec()), false);
  assert.equal(sourceApproved(mock.state(), "peer", spec(["other.test"])), true);
  assert.deepEqual(mock.removed, [{ origins: ["*://*.example.test/*"] }]);
  assert.deepEqual(mock.messages, [{ type: "settings-saved" }]);
  assert.equal(mock.state().token, "not-used-in-this-operation");
});

for (const domain of ["example.test", "sub.example.test"]) {
  test(`shared scope ${domain} is retained`, () => {
    assert.deepEqual(removableOrigins(spec(), { peer: spec([domain]) }, "http://127.0.0.1",
      ["*://*.example.test/*"]), []);
  });
}

test("parent permissions and receiver permissions are retained", () => {
  assert.deepEqual(removableOrigins(spec(["sub.example.test"]), { peer: spec() }, "http://localhost",
    ["*://*.sub.example.test/*"]), []);
  assert.deepEqual(removableOrigins(spec(), {}, "https://receiver.example.test",
    ["*://*.example.test/*"]), []);
});

test("required, unrelated and broader preexisting grants are never removed", () => {
  assert.deepEqual(removableOrigins(spec(), {}, "http://localhost", ["*://*/*", "*://*.other.test/*"]), []);
  assert.deepEqual(removableOrigins(spec(), {}, "http://localhost", ["*://*.example.test/*"],
    ["*://*.example.test/*"]), []);
});

test("stale connection cannot revoke a different receiver's approvals", async () => {
  const mock = mockAccess(), before = mock.state();
  await assert.rejects(revokeLocalSource("demo", "other-connection", mock), /连接已变更/);
  assert.deepEqual(mock.state(), before); assert.deepEqual(mock.removed, []);
});

for (const failAt of ["getAll", "remove"]) {
  test(`cleanup failure at ${failAt} does not roll back revocation`, async () => {
    const mock = mockAccess(); mock.api.permissions[failAt] = async () => { throw new Error("denied"); };
    const result = await revokeLocalSource("demo", "connection-one", mock);
    assert.equal(result.permissionCleanup, "failed");
    assert.equal(sourceApproved(mock.state(), "demo", spec()), false);
  });
}

test("revocation is durable when background worker is temporarily unavailable", async () => {
  const mock = mockAccess(); mock.api.runtime.sendMessage = async () => { throw new Error("worker asleep"); };
  const result = await revokeLocalSource("demo", "connection-one", mock);
  assert.equal(result.revoked, true); assert.equal(result.workerNotified, false);
  assert.equal(sourceApproved(mock.state(), "demo", spec()), false);
});

test("revocation is idempotent", async () => {
  const mock = mockAccess();
  await revokeLocalSource("demo", "connection-one", mock);
  assert.equal((await revokeLocalSource("demo", "connection-one", mock)).permissionCleanup, "none");
});

for (const source of ["../x", "__proto__", "", "X"]) {
  test(`reject unsafe source identifier ${source}`, async () => {
    const mock = mockAccess();
    await assert.rejects(revokeLocalSource(source, "connection-one", mock), /无效/);
    assert.equal(mock.removed.length, 0);
  });
}

test("parallel revoke and peer approval do not resurrect the revoked source", async () => {
  const mock = mockAccess(); const original = globalThis.chrome; globalThis.chrome = mock.api;
  try {
    await Promise.all([
      revokeLocalSource("demo", "connection-one"),
      approveSources({ connectionId: "connection-one", sources: { demo: spec(), peer: spec(["updated.test"]) } }, ["peer"]),
    ]);
    const latest = await loadSettings();
    assert.equal(sourceApproved(latest, "demo", spec()), false);
    assert.deepEqual(latest.approvedSources.peer, spec(["updated.test"]));
  } finally { globalThis.chrome = original; }
});

test("settings lock recovers after a failed mutation", async () => {
  await assert.rejects(withSettingsLock(async () => { throw new Error("test"); }));
  assert.equal(await withSettingsLock(async () => "ok"), "ok");
});

test("file presence and successful sync do not imply valid login", () => {
  const result = sourceStatus(spec(), { ...context(), job: { phase: "succeeded" } });
  assert.match(result.text, /最近同步成功/); assert.match(result.text, /登录反馈：未验证/);
  assert.doesNotMatch(result.text, /报告有效/);
});

test("valid must have a crawler origin and a present nonempty snapshot", () => {
  const remote = { ...fresh(), validation: "valid" };
  assert.match(sourceStatus(spec(), { ...context(), remote }).text, /登录反馈：未验证/);
  remote.validationDetail = { origin: "crawler_report", reasonCode: "logged_in" };
  assert.match(sourceStatus(spec(), { ...context(), remote }).text, /爬虫最近报告有效/);
  remote.cleared = true;
  assert.match(sourceStatus(spec(), { ...context(), remote }).text, /登录反馈：未验证/);
});

for (const result of ["invalid", "expired", "error"]) {
  test(`crawler ${result} is visibly actionable`, () => {
    const remote = { ...fresh(), validation: result, validationDetail: { origin: "crawler_report" } };
    assert.equal(sourceStatus(spec(), { ...context(), remote }).warning, true);
  });
}

test("disconnect does not reuse previously valid remote state", () => {
  const remote = { ...fresh(), validation: "valid", validationDetail: { origin: "crawler_report" } };
  const result = sourceStatus(spec(), { ...context(), remote, remoteAvailable: false });
  assert.match(result.text, /状态不可用/); assert.doesNotMatch(result.text, /报告有效/);
});

test("revoked or missing browser permission is visible separately", () => {
  assert.match(sourceStatus(spec(), { ...context(), approved: false }).text, /未授权或已撤销/);
  assert.match(sourceStatus(spec(), { ...context(), permission: false }).text, /浏览器权限缺失/);
  assert.match(sourceStatus(spec(), { ...context(), queueAvailable: false }).text, /无法读取队列/);
});

test("all displayed timestamps use the existing local formatter, not ISO", () => {
  const result = sourceStatus(spec(), { ...context(), job: { phase: "retrying", nextAt: 1789894800000 } });
  assert.match(result.text, /\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}/);
  assert.doesNotMatch(result.text, /\d{2}T\d{2}|\d{2}Z/);
});

test("unknown server strings or credentials are not reflected into summaries", () => {
  const secret = "<script>private-cookie</script>";
  const result = sourceStatus(spec(), { ...context(), job: { phase: secret, errorCode: secret },
    remote: { ...fresh(), freshness: secret, validation: secret, error: secret, updatedAt: secret,
      validationDetail: { origin: "crawler_report", reasonCode: secret } } });
  assert.equal(result.text.includes(secret), false); assert.equal(result.warning, true);
});

test("browser contexts use Web Locks when available", async () => {
  const navigator = globalThis.navigator;
  const previous = Object.getOwnPropertyDescriptor(navigator, "locks");
  let name;
  Object.defineProperty(navigator, "locks", { configurable: true,
    value: { request: async (key, action) => { name = key; return action(); } } });
  try {
    assert.equal(await withSettingsLock(async () => 42), 42);
    assert.equal(name, "cookie-http-seeder-settings");
  } finally {
    if (previous) Object.defineProperty(navigator, "locks", previous);
    else delete navigator.locks;
  }
});

test("re-authorizing one source never sends configuration or snapshot requests", async () => {
  const mock = mockAccess(), original = globalThis.chrome, oldFetch = globalThis.fetch;
  globalThis.chrome = mock.api;
  globalThis.fetch = () => { throw new Error("must not contact receiver during local approval"); };
  try {
    await revokeLocalSource("demo", "connection-one");
    await approveSources({ connectionId: "connection-one", sources: { demo: spec(), peer: spec(["other.test"]) } }, ["demo"]);
    assert.equal(sourceApproved(await loadSettings(), "demo", spec()), true);
  } finally { globalThis.chrome = original; globalThis.fetch = oldFetch; }
});

test("revocation during collection prevents a later snapshot POST", async () => {
  const mock = mockAccess(), original = globalThis.chrome, oldFetch = globalThis.fetch;
  globalThis.chrome = mock.api;
  const requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push(options.method || "GET");
    assert.ok(url.endsWith("/v2/sync/demo"));
    return new Response(JSON.stringify({ ok: true, enabled: true, config_revision: "revision", snapshot_version: null }));
  };
  mock.api.permissions.contains = async () => true;
  mock.api.cookies.getAll = async () => {
    await revokeLocalSource("demo", "connection-one");
    return [];
  };
  try {
    const settings = await loadSettings();
    await assert.rejects(pushSource("demo", { sources: { demo: spec() }, revision: "revision" }, settings),
      error => error.code === "approval_required");
    assert.deepEqual(requests, ["GET"]);
  } finally { globalThis.chrome = original; globalThis.fetch = oldFetch; }
});


test("offline consent document is a source-only view, not server configuration", () => {
  const mock = mockAccess(), state = mock.state();
  const doc = cachedSourceDocument(state);
  assert.equal(doc.offline, true); assert.equal(doc.revision, null);
  assert.deepEqual(doc.sources, state.approvedSources);
  assert.equal(JSON.stringify(doc).includes(state.token), false);
  assert.equal(Object.hasOwn(doc, "endpoint"), false);
  doc.sources.demo.label = "changed";
  assert.equal(state.approvedSources.demo.label, "demo");
});

test("offline consent cache cannot follow a different receiver connection", () => {
  const state = mockAccess().state(); state.connectionId = "another-connection";
  assert.equal(cachedSourceDocument(state), null);
});

test("missing or malformed cached sources are not presented as a full configuration", () => {
  assert.equal(cachedSourceDocument(null), null);
  const state = mockAccess().state(); state.approvedSources = {};
  assert.equal(cachedSourceDocument(state), null);
  state.approvedSources = { demo: { domains: [] } };
  assert.equal(cachedSourceDocument(state), null);
});

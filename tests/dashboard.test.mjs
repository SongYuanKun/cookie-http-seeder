import test from "node:test";
import assert from "node:assert/strict";
import { buildDashboard } from "../extension/dashboard.js";
import { RuntimeController } from "../extension/runtime_controller.js";

const spec = { label: "secret-label", domains: ["example.test"], enabled: true, target_url: "https://example.test/login" };
const settings = { connectionId: "original", approvedConnectionId: "original", approvedSources: { site: spec },
  token: "synthetic-secret-token", senderTag: "default" };
const doc = { sources: { site: spec }, revision: "revision", capabilities: ["client_health", "source_incidents"] };
const remote = { ok: true, config_revision: "revision", sources: { site: { present: true,
  snapshot_version: "a".repeat(32), freshness: "fresh", validation: "valid", errorText: "secret-error",
  validationDetail: { origin: "crawler_report", reasonCode: "logged_in" } } },
  incidents: { ok: true, active: [] }, clientHealth: { state: "recent", ageSeconds: 10 } };
test("safe DTO joins consent, queue and version-bound validation without raw fields", () => {
  const result = buildDashboard({ settings, doc, remote, permissions: { site: true },
    queue: { jobs: { site: { phase: "blocked", errorCode: "secret-error" } } } });
  assert.equal(result.connection, "connected");
  assert.equal(result.sources.site.snapshot.validation, "valid");
  assert.ok(result.sources.site.actions.includes("push"));
  assert.equal(result.sources.site.sync.error_code, "unknown_error");
  assert.equal(JSON.stringify(result).includes("secret"), false);
  assert.equal(JSON.stringify(result).includes("https://"), false);
  assert.equal(result.clientHealth.ageSeconds, 10);
});
test("disconnect and revision mismatch drop prior login conclusion", () => {
  const offline = buildDashboard({ settings, doc, remote: null, permissions: { site: false } });
  assert.equal(offline.sources.site.snapshot.validation, "unavailable");
  assert.equal(offline.sources.site.approved, true);
  assert.equal(offline.sources.site.actions.includes("push"), false);
  const changed = buildDashboard({ settings, doc, remote: { ...remote, config_revision: "other" } });
  assert.equal(changed.sources.site.snapshot.validation, "unavailable");
});
test("controller discards late generations and switched connection results", async () => {
  let current = structuredClone(settings), resolveFirst, calls = 0;
  const controller = new RuntimeController({ api: { permissions: { contains: async () => true } },
    settings: async () => structuredClone(current), getSources: async () => doc,
    getStatus: async () => ++calls === 1 ? new Promise(resolve => { resolveFirst = resolve; }) : remote,
    queue: async () => ({ jobs: {} }) });
  const first = controller.refresh();
  while (!resolveFirst) await new Promise(resolve => setImmediate(resolve));
  assert.equal((await controller.refresh()).sources.site.snapshot.validation, "valid");
  resolveFirst(remote); assert.equal((await first).code, "superseded");
  controller.getStatus = async () => { current.connectionId = "switched"; return remote; };
  assert.equal((await controller.refresh()).code, "connection_changed");
});

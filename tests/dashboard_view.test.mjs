import test from "node:test";
import assert from "node:assert/strict";
import { DashboardView, sourceText } from "../extension/dashboard_view.js";

const row = { source: "site", enabled: true, approved: true, permission_granted: true,
  sync: { phase: "succeeded", attempts: 1, error_code: "secret-error-text" },
  snapshot: { validation: "valid", freshness: "fresh" },
  incidents: [{ incident_id: "a".repeat(32), kind: "login_invalid", status: "open" }],
  actions: ["acknowledge", "snooze"] };
class Element {
  children = []; listeners = {}; textContent = "";
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; this.textContent = ""; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
}
const document = { createElement: tag => Object.assign(new Element(), { tag }) };
test("source text uses known categories and disconnect drops login conclusion", () => {
  assert.equal(sourceText(row).includes("secret-error-text"), false);
  assert.match(sourceText(row), /爬虫最近报告有效/);
  assert.match(sourceText({ ...row, snapshot: { validation: "unavailable", freshness: "unknown" } }), /无法读取/);
});
test("DOM sends incident ID actions and removes revoked source rows", async () => {
  const root = new Element(), actions = [];
  const view = new DashboardView({ document, root, send: async message => {
    if (message.type === "source-action") actions.push(message); return { ok: true };
  } });
  view.render({ ok: true, connection: "connected", capabilities: ["source_incidents"], sources: { site: row }, clientHealth: { state: "recent", ageSeconds: 30 } });
  const buttons = root.children[1].children.filter(e => e.tag === "button");
  assert.equal(buttons.length, 2);
  await buttons[0].listeners.click(); await buttons[1].listeners.click();
  assert.equal(actions[0].action, "acknowledge");
  assert.equal(actions[0].incident_id, "a".repeat(32));
  assert.equal(actions[1].duration_seconds, 3600);
  view.render({ ok: true, connection: "unavailable", sources: {}, capabilities: [], clientHealth: { state: "unknown" } });
  assert.equal(root.children.length, 1);
});
test("late refresh cannot overwrite the latest DOM and unavailable clears old valid", async () => {
  let firstResolve, count = 0;
  const root = new Element();
  const view = new DashboardView({ document, root, send: async () => ++count === 1 ? new Promise(r => { firstResolve = r; }) :
    ({ ok: true, connection: "unavailable", sources: {}, capabilities: [], clientHealth: { state: "unknown" } }) });
  const first = view.refresh(); await view.refresh();
  firstResolve({ ok: true, connection: "connected", sources: { site: row }, clientHealth: { state: "recent" } });
  await first;
  assert.equal(root.children.length, 1);
  assert.match(root.children[0].textContent, /不可用/);
});

import test from "node:test";
import assert from "node:assert/strict";
import { senderSummary } from "../extension/sender_summary.js";

test("summary shows every sender using metadata and local times", () => {
  const text = senderSummary({ senders: {
    "home-pc": { sources: { site: { present: true, freshness: "fresh", validation: "valid",
      validationDetail: { origin: "crawler_report" }, lastSeenAt: "2026-10-02T01:02:03Z" } } },
    "work-pc": { sources: { site: { present: true, freshness: "stale", validation: "invalid",
      validationDetail: { origin: "crawler_report" } } } },
    "broken-pc": { error: "unreadable_sender_state" },
  } });
  assert.match(text, /home-pc/); assert.match(text, /work-pc/); assert.match(text, /broken-pc/);
  assert.match(text, /报告有效/); assert.match(text, /登录失效/); assert.match(text, /过旧/);
  assert.equal(text.includes("T01:02:03Z"), false);
});
test("summary never exposes credentials or untrusted server strings", () => {
  const text = senderSummary({ senders: {
    "home-pc": { token: "receiver-secret", sources: { site: {
      present: true, cookie_header: "sid=snapshot-secret", freshness: "raw-secret",
      validation: "invalid-raw-secret", label: "secret-label", target_url: "secret-url",
    } } },
    "../bad-secret": { sources: {} },
  } });
  for (const value of ["receiver-secret", "snapshot-secret", "raw-secret", "secret-label", "secret-url", "bad-secret"]) {
    assert.equal(text.includes(value), false);
  }
});
test("valid without crawler origin or nonempty snapshot stays unverified", () => {
  assert.match(senderSummary({ senders: { default: { sources: { site: {
    present: false, validation: "valid", validationDetail: { origin: "crawler_report" },
  } } } } }), /未验证/);
});

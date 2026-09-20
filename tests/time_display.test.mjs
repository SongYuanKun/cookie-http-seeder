import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { displayTimes, formatLocalTime, localTimeZone } from "../extension/time.js";

const timeModule = new URL("../extension/time.js", import.meta.url).href;
function inZone(zone, value) {
  return execFileSync(process.execPath, ["--input-type=module", "-e",
    `import {formatLocalTime} from ${JSON.stringify(timeModule)}; console.log(formatLocalTime(${JSON.stringify(value)}));`,
  ], { env: { ...process.env, TZ: zone }, encoding: "utf8" }).trim();
}

for (const [zone, value, expected] of [
  ["Asia/Shanghai", "2026-09-20T00:49:34Z", "2026-09-20 08:49:34"],
  ["Asia/Singapore", "2026-09-20T00:49:34Z", "2026-09-20 08:49:34"],
  ["UTC", "2026-09-20T00:49:34Z", "2026-09-20 00:49:34"],
  ["America/New_York", "2026-09-20T00:49:34Z", "2026-09-19 20:49:34"],
  ["Asia/Kathmandu", "2026-09-20T00:49:34Z", "2026-09-20 06:34:34"],
  ["Asia/Shanghai", "2026-09-20T23:59:59Z", "2026-09-21 07:59:59"],
  ["Asia/Shanghai", "2026-09-20T08:49:34+08:00", "2026-09-20 08:49:34"],
  ["UTC", "2026-09-20T08:49:34.123456+08:00", "2026-09-20 00:49:34"],
  ["UTC", 0, "1970-01-01 00:00:00"],
  ["America/New_York", "2026-03-08T06:59:59Z", "2026-03-08 01:59:59"],
  ["America/New_York", "2026-03-08T07:00:00Z", "2026-03-08 03:00:00"],
]) {
  test(`local display ${zone} ${value}`, () => assert.equal(inZone(zone, value), expected));
}
for (const value of [null, undefined, "", "not-a-date", "2026-09-20", "2026-09-20T00:49:34",
  "2026-09-20 08:49:34", "2026-02-30T01:00:00Z", "2026-13-01T00:00:00Z",
  "2026-09-20T24:00:00Z", true, false, NaN, Infinity, {}, [], 1e30]) {
  test(`invalid time ${String(value)}`, () => assert.equal(formatLocalTime(value), "-"));
}
test("diagnostics format only known timestamp fields without mutating state", () => {
  const instant = "2026-09-20T00:49:34Z", epoch = Date.parse(instant);
  const document = { sources: { site: { updatedAt: instant, lastSeenAt: instant,
    validationDetail: { checkedAt: null, ageSeconds: 60 }, snapshot_version: instant } },
    queue: { site: { nextAt: epoch, queuedAt: 0, lastAttemptAt: null, attempts: 2 } },
    observedAt: instant, other: [instant, { lastPushAt: instant }] };
  const before = JSON.stringify(document);
  const result = displayTimes(document);
  assert.equal(result.sources.site.updatedAt, formatLocalTime(instant));
  assert.equal(result.sources.site.snapshot_version, instant);
  assert.equal(result.queue.site.nextAt, formatLocalTime(epoch));
  assert.equal(result.queue.site.queuedAt, formatLocalTime(0));
  assert.equal(result.queue.site.lastAttemptAt, "-");
  assert.equal(result.queue.site.attempts, 2);
  assert.equal(result.sources.site.validationDetail.ageSeconds, 60);
  assert.equal(result.other[0], instant);
  assert.equal(result.other[1].lastPushAt, formatLocalTime(instant));
  assert.equal(JSON.stringify(document), before);
});
test("wrong timestamp units/types are not guessed", () => {
  assert.deepEqual(displayTimes({ updatedAt: 42, nextAt: "1760000000000" }), { updatedAt: "-", nextAt: "-" });
});
test("local timezone label is available", () => assert.ok(localTimeZone().length > 0));
test("popup renders full local dates without changing retry state", async () => {
  const originalDocument = globalThis.document, originalChrome = globalThis.chrome;
  const elements = new Map();
  const job = { phase: "retrying", attempts: 1,
    nextAt: Date.parse("2026-09-21T00:49:34Z"), lastSuccessAt: Date.parse("2026-09-20T00:49:34Z") };
  const before = JSON.stringify(job);
  globalThis.document = { getElementById(id) {
    if (!elements.has(id)) elements.set(id, { textContent: "", addEventListener() {} });
    return elements.get(id);
  } };
  globalThis.chrome = { runtime: {
    sendMessage: async () => ({ ok: true, queue: { jobs: { site: job } } }), openOptionsPage() {},
  }, storage: { onChanged: { addListener() {} } } };
  try {
    await import("../extension/popup.js?time-display-test");
    await new Promise(resolve => setImmediate(resolve));
    const rendered = elements.get("status").textContent;
    assert.ok(rendered.includes(`计划重试：${formatLocalTime(job.nextAt)}`));
    assert.ok(rendered.includes(`上次成功：${formatLocalTime(job.lastSuccessAt)}`));
    assert.ok(rendered.includes(localTimeZone()));
    assert.equal(JSON.stringify(job), before);
  } finally {
    globalThis.document = originalDocument;
    globalThis.chrome = originalChrome;
  }
});

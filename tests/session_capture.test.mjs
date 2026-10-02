import assert from "node:assert/strict";
import test from "node:test";
import { captureOneNavigation, buildSessionPayload,
  postBundleAtVersion } from "../extension/session_capture.js";

function event() {
  const callbacks = new Set();
  return { addListener(fn) { callbacks.add(fn); }, removeListener(fn) { callbacks.delete(fn); },
    emit(detail) { for (const fn of callbacks) fn(detail); }, get size() { return callbacks.size; } };
}

test("one selected tab top-level GET is captured, unrelated and POST traffic ignored", async () => {
  const before = event(), complete = event(), errors = event();
  const api = { webRequest: { onBeforeSendHeaders: before, onCompleted: complete,
    onErrorOccurred: errors } };
  const base = { tabId: 17, type: "main_frame", requestId: "one",
    method: "GET", url: "https://example.com/account" };
  const pending = captureOneNavigation(api, 17, ["example.com"], 1000, () => {
    before.emit({ ...base, tabId: 18, requestHeaders: [{ name: "Cookie", value: "foreign" }] });
    before.emit({ ...base, method: "POST", requestHeaders: [{ name: "Cookie", value: "form" }] });
    before.emit({ ...base, url: "https://evil.test/", requestHeaders: [] });
    before.emit({ ...base, requestHeaders: [{ name: "Cookie", value: "sid=secret" }] });
    complete.emit({ ...base, statusCode: 200 });
  });
  const result = await pending;
  assert.equal(result.method, "GET");
  assert.equal(result.headers[0].value, "sid=secret");
  assert.equal(before.size, 0);
  assert.equal(complete.size, 0);
  assert.equal(errors.size, 0);
});

test("payload builder rejects a foreign final URL before adding browser state", () => {
  const spec = { domains: ["example.com"] };
  const nav = { method: "GET", url: "https://example.com/", final_url: "https://evil.test/",
    status: 200, headers: [] };
  assert.throws(() => buildSessionPayload("site", spec, nav, [], [], {}, {}));
});

test("missing request headers aborts and removes listeners without uploading data", async () => {
  const before = event(), complete = event(), errors = event();
  const api = { webRequest: { onBeforeSendHeaders: before, onCompleted: complete,
    onErrorOccurred: errors } };
  const pending = captureOneNavigation(api, 17, ["example.com"], 1000, () => {
    before.emit({ tabId: 17, type: "main_frame", method: "GET", requestId: "r",
      url: "https://example.com/account" });
  });
  await assert.rejects(pending, /请求头/);
  assert.equal(before.size + complete.size + errors.size, 0);
});

test("capture starts only with an explicit reload trigger", async () => {
  const before = event(), complete = event(), errors = event();
  const api = { webRequest: { onBeforeSendHeaders: before, onCompleted: complete,
    onErrorOccurred: errors } };
  const detail = { tabId: 17, type: "main_frame", method: "GET", requestId: "r",
    url: "https://example.com/account", requestHeaders: [{ name: "Cookie", value: "ok" }] };
  let triggered = false;
  const pending = captureOneNavigation(api, 17, ["example.com"], 1000, () => {
    triggered = true;
    before.emit(detail);
    complete.emit({ ...detail, statusCode: 200 });
  });
  assert.equal(triggered, true);
  assert.equal((await pending).headers[0].value, "ok");
  await assert.rejects(captureOneNavigation(api, 17, ["example.com"], 1000), /用户操作/);
});

test("two captures retain their pre-capture version, so a late stale upload conflicts", async () => {
  let version = null;
  const request = async (_settings, _path, options) => {
    const body = JSON.parse(options.body);
    if (body.expected_version !== version) throw new Error("session_conflict");
    version = "new-version";
    return { ok: true, bundle_version: version };
  };
  const envelope = { source: "site", ciphertext: "opaque" };
  await postBundleAtVersion({}, "site", "revision", null, envelope, request);
  await assert.rejects(postBundleAtVersion({}, "site", "revision", null, envelope, request),
    /session_conflict/);
});

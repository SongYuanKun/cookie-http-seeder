import { loadSettings } from "./shared.js";
import { RETRY_ALARM, SyncEngine } from "./sync.js";
const AUTO_ALARM = "cookie-http-seeder-auto-push";
const engine = new SyncEngine();

async function syncAlarm() {
  const settings = await loadSettings();
  const minutes = Number(settings.autoPushMinutes);
  const current = await chrome.alarms.get(AUTO_ALARM);
  if (Number.isInteger(minutes) && minutes >= 15 && minutes <= 10080) {
    if (!current || current.periodInMinutes !== minutes) {
      await chrome.alarms.create(AUTO_ALARM, { periodInMinutes: minutes });
    }
  } else { await chrome.alarms.clear(AUTO_ALARM); }
}
async function badge() {
  const queue = await engine.status();
  const failures = Object.values(queue.jobs).filter(j => ["blocked", "exhausted"].includes(j.phase)).length;
  await chrome.action.setBadgeText({ text: failures ? String(failures) : "" });
}
async function publish(result, manual = false) {
  await badge();
  const values = Object.values(result.results || {});
  const failed = values.filter(r => !r.ok && !r.queued).length;
  const changed = values.filter(r => r.ok && !r.unchanged).length;
  if (!failed && !(manual && changed)) return;
  try {
    const now = Date.now();
    const saved = await chrome.storage.local.get(["lastAttentionNotificationAt"]);
    if (failed && !manual && now - (saved.lastAttentionNotificationAt || 0) < 600_000) return;
    if (failed) await chrome.storage.local.set({ lastAttentionNotificationAt: now });
    await chrome.notifications.create({
      type: "basic", iconUrl: "icons/icon128.png",
      title: failed ? "Cookie sync needs attention" : "Cookie snapshots updated",
      message: failed ? "Open diagnostics, fix the cause, then retry manually." :
        "Transfer completed. This does not verify login validity.",
    });
  } catch { /* Notification availability must not affect sync. */ }
}
async function handle(message) {
  if (message.type === "settings-saved") { await syncAlarm(); await engine.recover(); return { ok: true }; }
  if (message.type === "sync-status") return { ok: true, queue: await engine.status() };
  await engine.enqueue(message.source ? [message.source] : null, "manual");
  const result = await engine.runDue(); await publish(result, true); return result;
}
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (sender.id !== chrome.runtime.id || !["push-now", "settings-saved", "sync-status"].includes(message?.type)) return false;
  handle(message).then(sendResponse).catch(() => sendResponse({ ok: false, error: "Operation failed; open diagnostics" }));
  return true;
});
chrome.cookies.onChanged.addListener(info => { engine.changed(info).catch(() => {}); });
chrome.alarms.onAlarm.addListener(alarm => {
  (async () => {
    if (alarm.name === AUTO_ALARM) await engine.enqueue(null, "periodic");
    else if (alarm.name !== RETRY_ALARM) return;
    const result = await engine.runDue(); await publish(result);
  })().catch(() => {});
});
async function startup() {
  await chrome.storage.local.setAccessLevel({ accessLevel: "TRUSTED_CONTEXTS" });
  await engine.recover(); await syncAlarm(); await badge();
}
chrome.runtime.onInstalled.addListener(() => { startup().catch(() => {}); });
chrome.runtime.onStartup.addListener(() => { startup().catch(() => {}); });
// Recreate important alarms on EVERY worker evaluation, not just browser startup.
startup().catch(() => {});

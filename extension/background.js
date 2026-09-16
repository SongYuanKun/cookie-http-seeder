import {
  SOURCES,
  collectSourceCookies,
  cookiesToHeader,
  loadSettings,
  pushCookieHeader,
} from "./shared.js";

const ALARM_NAME = "cookie-http-seeder-auto-push";

async function pushAll() {
  const settings = await loadSettings();
  if (!settings.token?.trim()) throw new Error("token is not configured");
  if (!settings.endpoint?.trim()) throw new Error("endpoint is not configured");

  const results = {};
  for (const source of Object.keys(SOURCES)) {
    const cookies = await collectSourceCookies(source);
    const cookieHeader = cookiesToHeader(cookies);
    if (!cookieHeader) {
      results[source] = { ok: false, error: "no cookies (log in first)" };
      continue;
    }
    try {
      const body = await pushCookieHeader({
        endpoint: settings.endpoint.trim(),
        token: settings.token.trim(),
        source,
        cookieHeader,
      });
      results[source] = {
        ok: true,
        updatedAt: body.updatedAt,
        cookieCount: cookies.length,
      };
    } catch (error) {
      results[source] = { ok: false, error: String(error?.message || error) };
    }
  }

  await chrome.storage.local.set({
    lastPushAt: new Date().toISOString(),
    lastPushResults: results,
  });

  const failed = Object.entries(results).filter(([, value]) => !value.ok);
  if (failed.length) {
    await notify(
      "Cookie push partially failed",
      failed.map(([name, value]) => `${name}: ${value.error}`).join("; "),
    );
  } else {
    await notify("Cookies pushed", Object.keys(SOURCES).join(", "));
  }
  return results;
}

async function notify(title, message) {
  try {
    await chrome.notifications.create({
      type: "basic",
      iconUrl: "icons/icon128.png",
      title,
      message: message.slice(0, 250),
    });
  } catch {
    // ignore
  }
}

async function syncAlarm() {
  const settings = await loadSettings();
  await chrome.alarms.clear(ALARM_NAME);
  const minutes = Number(settings.autoPushMinutes) || 0;
  if (minutes >= 15) {
    await chrome.alarms.create(ALARM_NAME, { periodInMinutes: minutes });
  }
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "push-now") {
    pushAll()
      .then((results) => sendResponse({ ok: true, results }))
      .catch((error) => sendResponse({ ok: false, error: String(error?.message || error) }));
    return true;
  }
  if (message?.type === "settings-saved") {
    syncAlarm()
      .then(() => sendResponse({ ok: true }))
      .catch((error) => sendResponse({ ok: false, error: String(error?.message || error) }));
    return true;
  }
  return false;
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === ALARM_NAME) pushAll().catch(() => {});
});

chrome.runtime.onInstalled.addListener(() => {
  syncAlarm().catch(() => {});
});

chrome.runtime.onStartup.addListener(() => {
  syncAlarm().catch(() => {});
});

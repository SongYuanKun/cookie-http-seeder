/** Optional receiver polling. Persist only versions, conditions and delivery times. */
import { loadSettings, receiverRequest } from "./shared.js";
import { senderTag } from "./senders.js";

export const LOGIN_ALARM = "cookie-http-seeder-login-status";
export const LOGIN_STATE_KEY = "loginMonitorV1";
const COOLDOWN_MS = 900_000;
const FINGERPRINT = /^[a-f0-9]{32}:(invalid|expired|stale)$/;
const SOURCE = /^[a-z][a-z0-9_-]{0,31}$/;
function pollMinutes(settings) {
  const value = settings.loginPollMinutes;
  return Number.isInteger(value) && value >= 15 && value <= 10080 ? value : 0;
}
function activeSources(settings) {
  if (!settings.connectionId || settings.approvedConnectionId !== settings.connectionId) return [];
  return Object.entries(settings.approvedSources || {}).filter(([name, spec]) =>
    SOURCE.test(name) && spec?.enabled === true).map(([name]) => name).slice(0, 100);
}
function attention(remote) {
  if (!remote || remote.enabled !== true || remote.present !== true || remote.cleared ||
      remote.error || remote.syncError || !/^[a-f0-9]{32}$/.test(remote.snapshot_version || "")) return null;
  if (remote.validationDetail?.origin === "crawler_report" &&
      ["invalid", "expired"].includes(remote.validation)) return remote.validation;
  return remote.freshness === "stale" ? "stale" : null;
}

export class LoginMonitor {
  constructor({ api = globalThis.chrome, now = Date.now, settings = loadSettings,
                getStatus = current => receiverRequest(current, "/v1/status") } = {}) {
    this.api = api; this.now = now; this.settings = settings; this.getStatus = getStatus;
    this.pending = Promise.resolve();
  }
  serial(action) {
    const result = this.pending.then(action);
    this.pending = result.catch(() => {});
    return result;
  }
  async context() {
    const settings = await this.settings();
    const stored = (await this.api.storage.local.get([LOGIN_STATE_KEY]))[LOGIN_STATE_KEY];
    const state = { schema_version: 1, connectionId: settings.connectionId, seen: {} };
    if (stored?.schema_version === 1 && stored.connectionId === settings.connectionId &&
        stored.seen && typeof stored.seen === "object") {
      for (const source of activeSources(settings)) {
        const item = stored.seen[source];
        if (item && (item.fingerprint === null || FINGERPRINT.test(item.fingerprint || "")) &&
            Number.isFinite(item.lastNotifiedAt) && item.lastNotifiedAt <= this.now() + 300_000) {
          state.seen[source] = { fingerprint: item.fingerprint, lastNotifiedAt: item.lastNotifiedAt };
        }
      }
    }
    return { settings, state };
  }
  async save(state) {
    if ((await this.settings()).connectionId !== state.connectionId) return false;
    await this.api.storage.local.set({ [LOGIN_STATE_KEY]: state });
    return true;
  }
  recover() {
    return this.serial(async () => {
      const { settings, state } = await this.context();
      const minutes = pollMinutes(settings);
      if (!minutes || !activeSources(settings).length) {
        await this.api.alarms.clear(LOGIN_ALARM);
        state.seen = {}; await this.save(state);
        return;
      }
      const alarm = await this.api.alarms.get(LOGIN_ALARM);
      if (!alarm || alarm.periodInMinutes !== minutes || alarm.scheduledTime < this.now()) {
        await this.api.alarms.create(LOGIN_ALARM, { delayInMinutes: minutes, periodInMinutes: minutes });
      }
      await this.save(state);
    });
  }
  check() {
    return this.serial(async () => {
      const { settings, state } = await this.context();
      if (!pollMinutes(settings) || !activeSources(settings).length) return { ok: true, notified: 0 };
      let remote;
      try { remote = await this.getStatus(settings); }
      catch { return { ok: false, code: "unavailable", notified: 0 }; }
      if (!remote?.sources || typeof remote.sources !== "object" || Array.isArray(remote.sources)) {
        return { ok: false, code: "unavailable", notified: 0 };
      }
      let notified = 0;
      for (const source of activeSources(settings)) {
        const latest = await this.settings();
        if (latest.connectionId !== state.connectionId) {
          return { ok: false, code: "connection_changed", notified };
        }
        if (!pollMinutes(latest) || !activeSources(latest).includes(source)) continue;
        const status = remote.sources[source], kind = attention(status);
        if (!status || status.error || status.syncError) continue;
        const old = state.seen[source];
        if (!kind) {
          if (old) old.fingerprint = null;
          continue;
        }
        const fingerprint = `${status.snapshot_version}:${kind}`;
        if (old?.fingerprint === fingerprint || (old && this.now() - old.lastNotifiedAt < COOLDOWN_MS)) continue;
        const tag = senderTag(latest.senderTag || "default");
        const message = kind === "invalid" ? "登录已失效：请在对应浏览器重新登录并推送。" :
          kind === "expired" ? "登录验证结果已过期：请检查爬虫并重新验证。" :
            "长时间未同步：请检查浏览器、接收端和连接。";
        try {
          await this.api.notifications.create({ type: "basic", iconUrl: "icons/icon128.png",
            title: "Cookie HTTP Seeder 需要处理", message: `${tag}/${source}：${message}` });
        } catch { continue; }
        if ((await this.settings()).connectionId !== state.connectionId) {
          return { ok: false, code: "connection_changed", notified };
        }
        state.seen[source] = { fingerprint, lastNotifiedAt: this.now() };
        notified++;
      }
      await this.save(state);
      return { ok: true, notified };
    });
  }
}

/** Optional health heartbeat. Reads metadata and permissions; never reads Cookie. */
import { HEALTH_PHASES, healthError, safeTime } from "./dashboard.js";
import { fetchSources, receiverRequest } from "./receiver_client.js";
import { loadSettings, sourceApproved, sourceOrigins } from "./settings.js";

export const HEALTH_ALARM = "cookie-http-seeder-client-health";
const minutes = s => Number.isInteger(s.healthReportMinutes) && s.healthReportMinutes >= 15 &&
  s.healthReportMinutes <= 10080 ? s.healthReportMinutes : 0;
export class HealthReporter {
  constructor({ api = globalThis.chrome, now = Date.now, settings = loadSettings, getSources = fetchSources,
    queue = async () => ({ jobs: {} }), post = (s, payload) => receiverRequest(s, "/v1/client-health", {
      method: "POST", body: JSON.stringify(payload),
    }) } = {}) {
    Object.assign(this, { api, now, settings, getSources, queue, post }); this.busy = null;
  }
  async context() {
    const settings = await this.settings();
    if (!minutes(settings)) { await this.api.alarms.clear(HEALTH_ALARM); return { code: "disabled" }; }
    let doc;
    try { doc = await this.getSources(settings); }
    catch (error) {
      if (error?.retryable || error?.code === "network_error") return { settings, code: "unavailable", retry: true };
      await this.api.alarms.clear(HEALTH_ALARM); return { code: "unavailable" };
    }
    if ((await this.settings()).connectionId !== settings.connectionId) {
      await this.api.alarms.clear(HEALTH_ALARM); return { code: "connection_changed" };
    }
    if (!doc.capabilities?.includes("client_health")) {
      await this.api.alarms.clear(HEALTH_ALARM); return { code: "upgrade_required" };
    }
    return { settings, doc };
  }
  async recover() {
    const context = await this.context();
    if (!context.settings) return { ok: false, code: context.code };
    const period = minutes(context.settings), alarm = await this.api.alarms.get(HEALTH_ALARM);
    if (!alarm || alarm.periodInMinutes !== period) {
      await this.api.alarms.create(HEALTH_ALARM, { delayInMinutes: period, periodInMinutes: period });
    }
    return { ok: true };
  }
  report() {
    if (!this.busy) this.busy = this._report().finally(() => { this.busy = null; });
    return this.busy;
  }
  async _report() {
    const context = await this.context();
    if (!context.settings || context.code) return { ok: false, code: context.code };
    const { settings, doc } = context, queue = await this.queue(), sources = {};
    for (const [name, spec] of Object.entries(doc.sources)) {
      const job = queue.connectionId === settings.connectionId ? queue.jobs?.[name] || {} : {};
      let granted = false;
      try { granted = await this.api.permissions.contains({ origins: sourceOrigins(spec) }); } catch { /* unavailable */ }
      const latest = await this.settings();
      if (latest.connectionId !== settings.connectionId) return { ok: false, code: "connection_changed" };
      const success = safeTime(job.lastSuccessAt);
      sources[name] = { approved: sourceApproved(latest, name, spec), permission_granted: granted,
        sync_phase: HEALTH_PHASES.has(job.phase) ? job.phase : "idle", error_code: healthError(job.errorCode),
        last_success_at: success && Date.parse(success) <= this.now() ? success : null };
    }
    const latest = await this.settings();
    if (latest.connectionId !== settings.connectionId) return { ok: false, code: "connection_changed" };
    if (!minutes(latest)) { await this.api.alarms.clear(HEALTH_ALARM); return { ok: false, code: "disabled" }; }
    const payload = { schema_version: 1, client_version: this.api.runtime.getManifest().version,
      interval_minutes: minutes(latest), sources };
    try { await this.post(latest, payload); }
    catch { return { ok: false, code: "unavailable" }; }
    if ((await this.settings()).connectionId !== settings.connectionId) return { ok: false, code: "connection_changed" };
    await this.api.storage.local.set({ clientHealthReportV1: { connectionId: settings.connectionId, at: this.now() } });
    return { ok: true };
  }
}

/** One async dashboard boundary shared by all extension pages. */
import { cachedSourceDocument } from "./access.js";
import { buildDashboard } from "./dashboard.js";
import { fetchSources, receiverRequest } from "./receiver_client.js";
import { loadSettings, sourceOrigins, targetURL } from "./settings.js";

export class RuntimeController {
  constructor({ api = globalThis.chrome, settings = loadSettings, getSources = fetchSources,
    getStatus = s => receiverRequest(s, "/v1/status"), queue = async () => ({ jobs: {} }) } = {}) {
    Object.assign(this, { api, settings, getSources, getStatus, queue }); this.generation = 0;
  }
  async refresh() {
    const generation = ++this.generation, initial = await this.settings();
    let doc, remote = null, queue;
    const results = await Promise.allSettled([this.getSources(initial), this.queue()]);
    doc = results[0].status === "fulfilled" ? results[0].value : cachedSourceDocument(initial);
    queue = results[1].status === "fulfilled" ? results[1].value : null;
    if (doc && !doc.offline) { try { remote = await this.getStatus(initial); } catch { /* no cached valid */ } }
    const permissions = {};
    for (const [name, spec] of Object.entries(doc?.sources || {})) {
      try { permissions[name] = await this.api.permissions.contains({ origins: sourceOrigins(spec) }); }
      catch { permissions[name] = false; }
    }
    const latest = await this.settings();
    if (generation !== this.generation) return { ok: false, code: "superseded" };
    if (latest.connectionId !== initial.connectionId) return { ok: false, code: "connection_changed" };
    return buildDashboard({ settings: latest, doc, queue, remote, permissions });
  }
  async act(message) {
    const settings = await this.settings(), doc = await this.getSources(settings);
    if (!Object.hasOwn(doc.sources, message.source)) throw new Error("unknown_source");
    if ((await this.settings()).connectionId !== settings.connectionId) throw new Error("connection_changed");
    if (message.action === "open_login") {
      const url = targetURL(doc.sources[message.source].target_url);
      await this.api.tabs.create({ url: url.href });
      return { ok: true };
    }
    if (!doc.capabilities?.includes("source_incidents")) throw new Error("upgrade_required");
    if (!["acknowledge", "snooze", "reopen"].includes(message.action) ||
      !/^[a-f0-9]{32}$/.test(message.incident_id || "")) throw new Error("invalid_action");
    const payload = { action: message.action, incident_id: message.incident_id };
    if (message.action === "snooze") payload.duration_seconds = message.duration_seconds;
    await receiverRequest(settings, `/v1/incidents/${message.source}`, {
      method: "POST", body: JSON.stringify(payload),
    });
    if ((await this.settings()).connectionId !== settings.connectionId) return { ok: false, code: "connection_changed" };
    return { ok: true };
  }
}

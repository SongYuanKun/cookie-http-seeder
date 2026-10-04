import { senderTag } from "./senders.js";
import { withSettingsLock } from "./settings_lock.js";
/** Sources come from the receiver; browser access always needs local approval. */
export const DEFAULTS = {
  endpoint: "http://127.0.0.1:18765", token: "", autoPushMinutes: 0, syncOnChange: false,
  recoveryProbeMinutes: 0, loginPollMinutes: 0, healthReportMinutes: 0,
  senderTag: "default", approvedSources: {}, connectionId: "", approvedConnectionId: "",
};

export function domainName(value) {
  if (typeof value !== "string" || !value || value.length > 253) throw new Error("Invalid domain");
  const domain = value.replace(/^\./, "").toLowerCase();
  if (!domain.split(".").every(x => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(x))) {
    throw new Error("Use an ASCII hostname (punycode for IDNs), not a URL/wildcard");
  }
  if (!domain.includes(".") && domain !== "localhost") throw new Error("Use a qualified hostname");
  if (new URL(`https://${domain}`).hostname !== domain) throw new Error("Use a canonical hostname/IP");
  return domain;
}
export function domainMatches(host, domain) {
  return host === domain || (!/^[0-9.]+$/.test(host) && host.endsWith(`.${domain}`));
}
export function allowedDomain(host, domains) {
  return domains.some(domain => domainMatches(host, domain));
}
export function targetURL(value) {
  if (typeof value !== "string" || value.length > 8192 || /[^\x21-\x7e]|\\/.test(value)) {
    throw new Error("Use an ASCII, percent-encoded target URL");
  }
  const url = new URL(value);
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || value.includes("#") || url.port === "0") {
    throw new Error("Use an HTTP(S) URL without credentials or fragment");
  }
  const authority = value.match(/^https?:\/\/([^/?#]+)/i)?.[1];
  if (!authority || authority.includes("@")) throw new Error("Invalid URL authority");
  const originalHost = authority.split(":")[0];
  domainName(originalHost); // reject numeric/IP forms normalized by WHATWG URL
  const path = value.match(/^https?:\/\/[^/?#]+([^?#]*)/i)?.[1] || "/";
  if (path.split("/").some(part => [".", ".."].includes(decodeURIComponent(part)))) {
    throw new Error("Normalize dot segments before selecting cookies");
  }
  return url;
}
export function normalizeSources(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw) || Object.keys(raw).length > 100) {
    throw new Error("Sources must be an object with at most 100 entries");
  }
  const result = {};
  for (const [name, spec] of Object.entries(raw)) {
    if (!/^[a-z][a-z0-9_-]{0,31}$/.test(name) || !spec || typeof spec !== "object" || Array.isArray(spec)) {
      throw new Error("Invalid source definition");
    }
    if (Object.keys(spec).some(k => !["label", "domains", "target_url", "enabled",
      "stale_after_seconds", "validation_ttl_seconds"].includes(k))) {
      throw new Error("Unknown source fields; credentials cannot be imported");
    }
    if (!Array.isArray(spec.domains) || !spec.domains.length || spec.domains.length > 32) {
      throw new Error("Each source needs 1 to 32 domains");
    }
    const domains = [...new Set(spec.domains.map(domainName))];
    const label = spec.label ?? name, enabled = spec.enabled ?? true, target = spec.target_url ?? "";
    if (typeof label !== "string" || !label.length || label.length > 80 || /[\x00-\x1f\x7f]/.test(label)) {
      throw new Error("Invalid source label");
    }
    if (typeof enabled !== "boolean" || typeof target !== "string") throw new Error("Invalid source settings");
    if (target && !allowedDomain(domainName(targetURL(target).hostname), domains)) {
      throw new Error("Target URL is outside the source allow-list");
    }
    result[name] = { label, domains, target_url: target, enabled };
    for (const field of ["stale_after_seconds", "validation_ttl_seconds"]) {
      if (Object.hasOwn(spec, field)) {
        if (!Number.isInteger(spec[field]) || spec[field] < 60 || spec[field] > 2592000) {
          throw new Error("来源阈值须为 60–2592000 秒的整数");
        }
        result[name][field] = spec[field];
      }
    }
  }
  return result;
}
export function endpointURL(value) {
  const url = targetURL(value);
  if (url.search || url.pathname !== "/") throw new Error("Endpoint must be an origin without path/query");
  if (url.protocol === "http:" && !["127.0.0.1", "localhost"].includes(url.hostname)) {
    throw new Error("Remote receivers require HTTPS; use SSH forwarding for HTTP");
  }
  return url.origin;
}
export function sourceOrigins(spec) {
  return spec.domains.map(d => /^[0-9.]+$/.test(d) || d === "localhost" ? `*://${d}/*` : `*://*.${d}/*`);
}
export function configExport(sources) {
  return { schema_version: 1, sources: normalizeSources(sources) };
}
export function configImport(raw) {
  if (!raw || raw.schema_version !== 1 || Object.keys(raw).some(k => !["schema_version", "sources"].includes(k))) {
    throw new Error("Import only schema_version=1 and sources; no credentials/settings");
  }
  return normalizeSources(raw.sources);
}
export async function loadSettings() {
  return { ...DEFAULTS, ...await chrome.storage.local.get(Object.keys(DEFAULTS)) };
}
export async function saveSettings(patch) {
  return withSettingsLock(async () => {
    const old = await loadSettings();
    if (patch.senderTag !== undefined) patch.senderTag = senderTag(patch.senderTag);
    if (patch.endpoint !== undefined) patch.endpoint = endpointURL(patch.endpoint);
    if (patch.token !== undefined && !/^[A-Za-z0-9._-]{16,256}$/.test(patch.token)) throw new Error("Invalid token");
    if (patch.autoPushMinutes !== undefined) {
      const minutes = Number(patch.autoPushMinutes);
      if (!Number.isInteger(minutes) || (minutes !== 0 && (minutes < 15 || minutes > 10080))) {
        throw new Error("Auto push must be 0 (off), or 15 to 10080 minutes");
      }
      patch.autoPushMinutes = minutes;
    }
    if (patch.syncOnChange !== undefined && typeof patch.syncOnChange !== "boolean") {
      throw new Error("syncOnChange must be boolean");
    }
    for (const field of ["recoveryProbeMinutes", "loginPollMinutes", "healthReportMinutes"]) {
      if (patch[field] !== undefined && (!Number.isInteger(patch[field]) ||
          (patch[field] !== 0 && (patch[field] < 15 || patch[field] > 10080)))) {
        throw new Error("恢复探测/登录提醒须为 0（关闭）或 15–10080 分钟");
      }
    }
    if (!old.connectionId || (patch.endpoint !== undefined && patch.endpoint !== old.endpoint) ||
        (patch.token !== undefined && patch.token !== old.token) ||
        (patch.senderTag !== undefined && patch.senderTag !== old.senderTag)) {
      patch = { ...patch, connectionId: crypto.randomUUID(), approvedSources: {}, approvedConnectionId: "" };
    }
    await chrome.storage.local.set(patch);
  });
}
export async function approveSources(doc, names = Object.keys(doc.sources)) {
  return withSettingsLock(async () => {
    const settings = await loadSettings();
    if (!doc.connectionId || doc.connectionId !== settings.connectionId) throw new Error("Connection changed; reload configuration");
    const approved = { ...settings.approvedSources };
    for (const name of names) {
      if (Object.hasOwn(doc.sources, name)) approved[name] = doc.sources[name];
      else delete approved[name];
    }
    for (const name of Object.keys(approved)) if (!Object.hasOwn(doc.sources, name)) delete approved[name];
    await chrome.storage.local.set({ approvedSources: approved, approvedConnectionId: settings.connectionId });
    try { await chrome.runtime?.sendMessage?.({ type: "settings-saved" }); }
    catch { /* The worker rebuilds schedules on its next evaluation. */ }
  });
}
export function sourceApproved(settings, source, spec) {
  const scope = value => Object.fromEntries(Object.entries(value || {}).filter(([key]) =>
    !["stale_after_seconds", "validation_ttl_seconds"].includes(key)));
  return !!settings.connectionId && settings.approvedConnectionId === settings.connectionId &&
    Object.hasOwn(settings.approvedSources, source) &&
    JSON.stringify(scope(settings.approvedSources[source])) === JSON.stringify(scope(spec));
}

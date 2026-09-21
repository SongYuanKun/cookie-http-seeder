import { senderTag } from "./senders.js";
import { withSettingsLock } from "./settings_lock.js";
/** Sources come from the receiver; browser access always needs local approval. */
export const DEFAULTS = {
  endpoint: "http://127.0.0.1:18765", token: "", autoPushMinutes: 0, syncOnChange: false,
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
    if (Object.keys(spec).some(k => !["label", "domains", "target_url", "enabled"].includes(k))) {
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
    if (!old.connectionId || (patch.endpoint !== undefined && patch.endpoint !== old.endpoint) ||
        (patch.token !== undefined && patch.token !== old.token) ||
        (patch.senderTag !== undefined && patch.senderTag !== old.senderTag)) {
      patch = { ...patch, connectionId: crypto.randomUUID(), approvedSources: {}, approvedConnectionId: "" };
    }
    await chrome.storage.local.set(patch);
  });
}
export class ReceiverError extends Error {
  constructor(code, message, { retryable = false, retryAfterMs = 0 } = {}) {
    super(message); this.name = "ReceiverError"; this.code = code;
    this.retryable = retryable; this.retryAfterMs = retryAfterMs;
  }
}
export async function receiverRequest(settings, path, options = {}) {
  const base = endpointURL(settings.endpoint);
  const tag = senderTag(settings.senderTag);
  const mutation = options.method && !["GET", "HEAD"].includes(options.method.toUpperCase());
  if (mutation && tag !== "default") {
    // Fail BEFORE mutation when an old receiver silently ignores the namespace header.
    const doc = await receiverRequest(settings, "/v1/sources");
    if (!Array.isArray(doc.capabilities) || !doc.capabilities.includes("sender_tags")) {
      throw new ReceiverError("upgrade_required", "请升级接收端以支持发送端标签");
    }
  }
  if (mutation && settings.connectionId &&
      (await loadSettings()).connectionId !== settings.connectionId) {
    throw new ReceiverError("approval_required", "连接或标签已变更，请重新加载和授权");
  }
  if (!/^[A-Za-z0-9._-]{16,256}$/.test(settings.token)) {
    throw new ReceiverError("invalid_token", "Configure a valid receiver token");
  }
  let response, body;
  try {
    response = await fetch(`${base}${path}`, {
      ...options, redirect: "error", credentials: "omit", cache: "no-store",
      signal: AbortSignal.timeout(8000),
      headers: { "Content-Type": "application/json", ...options.headers, Authorization: `Bearer ${settings.token}`,
        "X-Sender-Tag": tag },
    });
    // Bound responses, including error pages, and never reflect receiver text.
    const reader = response.body?.getReader();
    const chunks = []; let length = 0;
    if (reader) {
      while (true) {
        const { done, value } = await reader.read(); if (done) break;
        length += value.byteLength;
        if (length > 1024 * 1024) {
          await reader.cancel(); throw new ReceiverError("invalid_response", "Receiver response too large");
        }
        chunks.push(value);
      }
    }
    const raw = new Uint8Array(length); let offset = 0;
    for (const chunk of chunks) { raw.set(chunk, offset); offset += chunk.byteLength; }
    try { body = JSON.parse(new TextDecoder().decode(raw)); } catch { body = null; }
  } catch (error) {
    if (error instanceof ReceiverError) throw error;
    throw new ReceiverError("network_error", "Receiver unavailable or request timed out", { retryable: true });
  }
  if (!response.ok) {
    const terminal = {
      400: ["invalid_payload", "Invalid snapshot/configuration; check domains, fields and cookie support"],
      401: ["unauthorized", "Token rejected"], 403: ["forbidden", "Origin rejected"],
      404: ["not_found", "Unknown receiver endpoint/source"],
      410: ["upgrade_required", "Upgrade receiver/extension together"],
      428: ["upgrade_required", "Receiver requires conditional snapshots; upgrade the extension"],
    };
    if (response.status === 409) {
      const conflict = body?.error === "snapshot_conflict";
      throw new ReceiverError(conflict ? "snapshot_conflict" : "configuration_changed",
        conflict ? "Snapshot changed; recollect before retrying" : "Configuration changed; reload and approve",
        { retryable: conflict });
    }
    const transient = [408, 429].includes(response.status) || response.status >= 500;
    const retryAfter = Number(response.headers.get("Retry-After"));
    const hint = terminal[response.status] || [transient ? "server_unavailable" : "request_rejected", `Receiver error HTTP ${response.status}`];
    throw new ReceiverError(...hint, {
      retryable: transient,
      retryAfterMs: Number.isFinite(retryAfter) ? Math.max(0, Math.min(3600, retryAfter)) * 1000 : 0,
    });
  }
  if (!body || typeof body !== "object" || body.ok !== true) {
    throw new ReceiverError("invalid_response", "Invalid receiver response");
  }
  if (tag !== "default" && body.sender_tag !== tag) {
    throw new ReceiverError("upgrade_required", "接收端未确认发送端标签，已停止操作");
  }
  return body;
}
export async function fetchSources(settings) {
  const doc = await receiverRequest(settings, "/v1/sources");
  if (doc.protocol_version !== 2 || typeof doc.revision !== "string") throw new ReceiverError("upgrade_required", "Receiver 0.3+ required");
  if (!doc.capabilities?.includes("conditional_snapshots")) {
    throw new ReceiverError("upgrade_required", "Receiver 0.3+ required");
  }
  if (senderTag(settings.senderTag) !== "default" && !doc.capabilities.includes("sender_tags")) {
    throw new ReceiverError("upgrade_required", "请升级接收端以支持发送端标签");
  }
  return { ...doc, sources: normalizeSources(doc.sources), connectionId: settings.connectionId };
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
  });
}
export async function collectSourceCookies(spec) {
  const origins = sourceOrigins(spec);
  if (!await chrome.permissions.contains({ origins })) throw new Error("Site permission missing; authorize in Manage sites");
  const byIdentity = new Map();
  // Query each root once to preserve Chrome creation order within overlapping scopes.
  const roots = spec.domains.filter(d => !spec.domains.some(p => d !== p && domainMatches(d, p)));
  for (const domain of roots) {
    const batch = await chrome.cookies.getAll({ domain }); // current store, non-partitioned only
    for (const cookie of batch) {
      const host = domainName(cookie.domain);
      if (!allowedDomain(host, spec.domains)) throw new Error("Cookie outside allow-list");
      if (cookie.partitionKey != null) throw new Error("Partitioned cookies are unsupported in phase 1");
      const key = JSON.stringify([host, cookie.path, cookie.name, cookie.storeId]);
      const previous = byIdentity.get(key);
      if (previous && JSON.stringify(previous) !== JSON.stringify(cookie)) throw new Error("Conflicting duplicate cookie identity");
      byIdentity.set(key, cookie);
    }
  }
  if (!await chrome.permissions.contains({ origins })) throw new Error("Site permission changed during collection");
  const cookies = [...byIdentity.values()];
  if (new Set(cookies.map(c => c.storeId)).size > 1) throw new Error("Mixed stores are unsupported in phase 1");
  return cookies;
}
export function sourceApproved(settings, source, spec) {
  return !!settings.connectionId && settings.approvedConnectionId === settings.connectionId &&
    Object.hasOwn(settings.approvedSources, source) &&
    JSON.stringify(settings.approvedSources[source]) === JSON.stringify(spec);
}
export async function pushSource(source, doc, settings) {
  const spec = doc.sources[source];
  if (!spec?.enabled || !sourceApproved(settings, source, spec)) {
    throw new ReceiverError("approval_required", "Source configuration needs your approval in Manage sites");
  }
  // Read the receiver version BEFORE collecting: an old collection cannot win a CAS race.
  const current = await receiverRequest(settings, `/v2/sync/${source}`);
  if (current.config_revision !== doc.revision || !current.enabled) {
    throw new ReceiverError("configuration_changed", "Configuration changed; reload and approve");
  }
  if (current.snapshot_version !== null && !/^[a-f0-9]{32}$/.test(current.snapshot_version || "")) {
    throw new ReceiverError("invalid_response", "Invalid snapshot version");
  }
  let cookies;
  try { cookies = await collectSourceCookies(spec); }
  catch (error) {
    const text = String(error?.message || "");
    if (/permission/i.test(text)) throw new ReceiverError("permission_required", "Site permission missing or revoked");
    if (/Partitioned|Mixed stores|allow-list|duplicate/.test(text)) {
      throw new ReceiverError("unsupported_cookies", "Unsupported or out-of-scope cookie data");
    }
    throw new ReceiverError("collection_failed", "Cookie collection failed; previous snapshot preserved", { retryable: true });
  }
  const latest = await loadSettings();
  if (latest.connectionId !== settings.connectionId || !sourceApproved(latest, source, spec)) {
    throw new ReceiverError("approval_required", "Connection/approval changed; push cancelled");
  }
  return receiverRequest(settings, "/v2/cookies", {
    method: "POST", body: JSON.stringify({
      schema_version: 2, complete: true, source, cookies, config_revision: doc.revision,
      expected_version: current.snapshot_version, request_id: crypto.randomUUID(),
    }),
  });
}
export async function pushAll(onlySource = null) {
  const settings = await loadSettings();
  const doc = await fetchSources(settings);
  const results = {};
  for (const [source, spec] of Object.entries(doc.sources)) {
    if (!spec.enabled || (onlySource && onlySource !== source)) continue;
    try { results[source] = await pushSource(source, doc, settings); }
    catch (error) {
      results[source] = { ok: false, error: error.message, code: error.code || "operation_failed" };
    }
  }
  await chrome.storage.local.set({ lastPushAt: new Date().toISOString(), lastPushResults: results, lastPushError: "" });
  return results;
}

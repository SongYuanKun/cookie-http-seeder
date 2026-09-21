/** Explicit, local-only withdrawal of consent. Never sends or deletes cookies. */
import { domainMatches, loadSettings, normalizeSources, sourceOrigins } from "./shared.js";
import { withSettingsLock } from "./settings_lock.js";

export function cachedSourceDocument(settings) {
  // This is only a local consent view, never a replacement for server configuration.
  if (!settings?.connectionId || settings.approvedConnectionId !== settings.connectionId) return null;
  try {
    const sources = normalizeSources(settings.approvedSources);
    return Object.keys(sources).length ? {
      sources, revision: null, connectionId: settings.connectionId, offline: true,
    } : null;
  } catch { return null; }
}

function overlap(a, b) {
  return domainMatches(a, b) || domainMatches(b, a);
}

export function removableOrigins(spec, remaining, endpoint, granted, required = []) {
  // Only remove exact permissions previously requested for this source. Shared
  // parent/child scopes and the receiver origin remain available to other users.
  let receiverHost;
  try { receiverHost = new URL(endpoint).hostname; } catch { return []; }
  const used = Object.values(remaining).flatMap(s => s.domains || []);
  const existing = new Set(granted);
  const fixed = new Set(required);
  return sourceOrigins(spec).filter((pattern, index) => {
    const domain = spec.domains[index];
    return existing.has(pattern) && !fixed.has(pattern) &&
      !overlap(domain, receiverHost) && !used.some(other => overlap(domain, other));
  });
}

export async function revokeLocalSource(source, connectionId, {
  api = globalThis.chrome, settings = loadSettings,
} = {}) {
  if (!/^[a-z][a-z0-9_-]{0,31}$/.test(source)) throw new Error("无效的来源标识");
  const cleanup = await withSettingsLock(async () => {
    const before = await settings();
    if (!connectionId || before.connectionId !== connectionId) {
      throw new Error("连接已变更，请重新加载配置后操作");
    }
    const spec = Object.hasOwn(before.approvedSources || {}, source) ? before.approvedSources[source] : null;
    const approvedSources = { ...before.approvedSources };
    delete approvedSources[source];
    // Include the connection binding, so a racing connection change fails closed
    // instead of making an old approval map valid for a different receiver.
    await api.storage.local.set({ approvedSources, approvedConnectionId: connectionId });
    let cleanup = "none";
    if (spec) {
      try {
        const current = await settings();
        if (current.connectionId !== connectionId ||
            JSON.stringify(current.approvedSources) !== JSON.stringify(approvedSources)) {
          cleanup = "deferred";
        } else {
          const granted = await api.permissions.getAll();
          const required = api.runtime.getManifest().host_permissions || [];
          const origins = removableOrigins(spec, approvedSources, current.endpoint,
            granted.origins || [], required);
          cleanup = origins.length ? (await api.permissions.remove({ origins }) ? "removed" : "failed") : "shared";
        }
      } catch { cleanup = "failed"; }
    }
    return cleanup;
  });
  // Recovery prunes jobs for unapproved sources and re-arms/clears retry alarms.
  // Revocation is already durable even when the worker is temporarily unavailable.
  let workerNotified = false;
  try { workerNotified = (await api.runtime.sendMessage({ type: "settings-saved" }))?.ok === true; }
  catch { /* The next worker evaluation also recovers from local storage. */ }
  return { revoked: true, permissionCleanup: cleanup, workerNotified };
}

/** Compatibility facade and coordinated snapshot push. */
export * from "./settings.js";
export * from "./receiver_client.js";
export * from "./cookie_collector.js";
import { loadSettings, sourceApproved } from "./settings.js";
import { fetchSources, ReceiverError, receiverRequest } from "./receiver_client.js";
import { collectSourceCookies } from "./cookie_collector.js";
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

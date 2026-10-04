/** Safe shared product model; never return settings, URLs, Cookie or raw remote errors. */
import { sourceApproved } from "./settings.js";

export const HEALTH_PHASES = new Set(["idle", "pending", "syncing", "probing", "retrying", "succeeded", "exhausted", "blocked", "paused"]);
const ERRORS = new Set(["none", "network_error", "unauthorized", "forbidden", "configuration_changed", "permission_denied", "snapshot_conflict", "rate_limited", "retry_exhausted", "worker_interrupted", "unknown_error"]);
const FRESHNESS = new Set(["missing", "cleared", "unknown", "fresh", "stale"]);
const VALIDATION = new Set(["unverified", "valid", "invalid", "error", "expired"]);
const REASONS = new Set(["logged_in", "login_required", "session_expired", "account_mismatch", "network_error", "rate_limited", "unexpected_response", "manual_reset"]);
const CAPABILITIES = new Set(["conditional_snapshots", "validation_feedback", "sender_tags", "source_thresholds", "sender_status_summary", "stale_notifications", "client_health", "source_incidents"]);
const KINDS = new Set(["login_invalid", "validation_expired", "snapshot_stale"]);
const INCIDENT_STATES = new Set(["open", "acknowledged", "snoozed", "awaiting_validation", "resolved"]);
const version = value => /^[a-f0-9]{32}$/.test(value || "") ? value : null;
const number = value => Number.isFinite(value) && value >= 0 ? value : null;
export function safeTime(value) {
  if (typeof value === "number") return number(value) !== null && value < 8e15 ? new Date(value).toISOString() : null;
  return typeof value === "string" && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z$/.test(value) &&
    Number.isFinite(Date.parse(value)) ? new Date(value).toISOString() : null;
}
export function healthError(code) {
  if (!code) return "none";
  const mapped = { permission_required: "permission_denied", approval_required: "configuration_changed",
    server_unavailable: "network_error", collection_failed: "unknown_error" }[code] || code;
  return ERRORS.has(mapped) ? mapped : "unknown_error";
}
export function safeIncidents(raw, source) {
  if (!Array.isArray(raw)) return [];
  return raw.slice(0, 300).filter(e => e && e.source === source && version(e.incident_id) &&
    KINDS.has(e.kind) && INCIDENT_STATES.has(e.status)).map(e => ({
    source, incident_id: e.incident_id, kind: e.kind, status: e.status,
    snapshot_version: version(e.snapshot_version), opened_at: safeTime(e.opened_at),
    updated_at: safeTime(e.updated_at), snoozed_until: safeTime(e.snoozed_until),
  }));
}
export function buildDashboard({ settings, doc, queue, remote, permissions = {} }) {
  const connected = doc?.offline !== true && remote?.ok === true && doc?.revision === remote.config_revision;
  const capabilities = (Array.isArray(doc?.capabilities) ? doc.capabilities : []).filter(c => CAPABILITIES.has(c));
  const sources = {};
  for (const [source, spec] of Object.entries(doc?.sources || {})) {
    if (!/^[a-z][a-z0-9_-]{0,31}$/.test(source)) continue;
    const approved = sourceApproved(settings, source, spec), permission = permissions[source] === true;
    const job = queue?.jobs?.[source] || {};
    const raw = connected ? remote.sources?.[source] : null;
    const readable = raw && !raw.error && !raw.syncError;
    const validation = !connected ? "unavailable" : readable && raw.present === true && !raw.cleared &&
      version(raw.snapshot_version) && raw.validationDetail?.origin === "crawler_report" &&
      VALIDATION.has(raw.validation) ? raw.validation : "unverified";
    const incidents = connected ? safeIncidents(remote.incidents?.active, source) : [];
    const actions = ["refresh"];
    if (connected && spec.enabled && approved && permission) actions.push("push");
    if (connected && spec.target_url) actions.push("open_login");
    if (connected && capabilities.includes("source_incidents") && incidents.length) {
      actions.push("acknowledge", "snooze", "reopen");
    }
    sources[source] = { source, enabled: spec.enabled === true, approved, permission_granted: permission,
      sync: { phase: HEALTH_PHASES.has(job.phase) ? job.phase : "idle", error_code: healthError(job.errorCode),
        attempts: Number.isInteger(job.attempts) && job.attempts >= 0 && job.attempts <= 5 ? job.attempts : 0,
        nextAt: number(job.nextAt), probeAt: number(job.probeAt), lastSuccessAt: safeTime(job.lastSuccessAt) },
      snapshot: { validation, freshness: connected && FRESHNESS.has(raw?.freshness) ? raw.freshness : "unknown",
        snapshot_version: connected ? version(raw?.snapshot_version) : null,
        reason_code: readable && REASONS.has(raw.validationDetail?.reasonCode) ? raw.validationDetail.reasonCode : null,
        lastSeenAt: connected ? safeTime(raw?.lastSeenAt) : null,
        cookieCount: readable && Number.isInteger(raw.cookieCount) && raw.cookieCount >= 0 ? raw.cookieCount : null },
      incidents, actions };
  }
  const health = connected ? remote.clientHealth : null;
  return { ok: true, connection: connected ? "connected" : "unavailable",
    capabilities, sender_tag: /^[a-z0-9][a-z0-9_-]{0,31}$/.test(settings.senderTag || "default") ? settings.senderTag || "default" : "default",
    clientHealth: { state: ["not_reported", "recent", "overdue", "unknown"].includes(health?.state) ? health.state : "unknown",
      ageSeconds: number(health?.ageSeconds), receivedAt: safeTime(health?.receivedAt) }, sources };
}

/** Durable source-only queue. Never persist Cookie values, headers or request bodies. */
import {
  allowedDomain, domainName, fetchSources, loadSettings, pushSource, ReceiverError,
  sourceApproved,
} from "./shared.js";

export const QUEUE_KEY = "syncQueueV1";
export const RETRY_ALARM = "cookie-http-seeder-pending";
export const MAX_ATTEMPTS = 5;
export const DEBOUNCE_MS = 30_000;
const PENDING = new Set(["pending", "retrying", "syncing"]);
const SAFE_CODES = new Set([
  "network_error", "server_unavailable", "collection_failed", "snapshot_conflict",
  "invalid_token", "unauthorized", "forbidden", "not_found", "upgrade_required",
  "configuration_changed", "approval_required", "permission_required", "unsupported_cookies",
  "invalid_payload", "invalid_response", "request_rejected", "operation_failed", "worker_interrupted",
]);
export function retryDelay(attempt, random = Math.random) {
  return Math.round(Math.min(900_000, 30_000 * 2 ** Math.max(0, attempt - 1)) * (1 + 0.2 * random()));
}
export function safeFailure(error) {
  return {
    code: SAFE_CODES.has(error?.code) ? error.code : "operation_failed",
    retryable: error instanceof ReceiverError && error.retryable === true,
    retryAfterMs: error instanceof ReceiverError ? Math.max(0, Math.min(3_600_000, error.retryAfterMs || 0)) : 0,
  };
}

export class SyncEngine {
  constructor({ api = globalThis.chrome, now = Date.now, random = Math.random,
                settings = loadSettings, getSources = fetchSources, push = pushSource } = {}) {
    this.api = api; this.now = now; this.random = random;
    this.settings = settings; this.getSources = getSources; this.push = push;
    this.pending = Promise.resolve();
  }
  serial(action) {
    const result = this.pending.then(action);
    this.pending = result.catch(() => {});
    return result;
  }
  async context() {
    const settings = await this.settings();
    const stored = (await this.api.storage.local.get([QUEUE_KEY]))[QUEUE_KEY];
    const state = stored?.schema_version === 1 && stored.connectionId === settings.connectionId &&
      stored.jobs && typeof stored.jobs === "object" && !Array.isArray(stored.jobs)
      ? stored : { schema_version: 1, connectionId: settings.connectionId, jobs: {} };
    // Bound history to current locally approved sources; removed sources leave no queued work.
    for (const name of Object.keys(state.jobs)) {
      if (!Object.hasOwn(settings.approvedSources || {}, name)) delete state.jobs[name];
      else if (!settings.approvedSources[name].enabled) {
        Object.assign(state.jobs[name], { phase: "paused", nextAt: null });
      }
    }
    return { settings, state };
  }
  async save(state) {
    // A stale writer can only save an old connectionId, which the next load ignores.
    if ((await this.settings()).connectionId !== state.connectionId) return false;
    await this.api.storage.local.set({ [QUEUE_KEY]: state });
    return true;
  }
  async arm(state) {
    const due = Object.values(state.jobs).filter(j => PENDING.has(j.phase) && Number.isFinite(j.nextAt));
    if (!due.length) { await this.api.alarms.clear(RETRY_ALARM); return; }
    const when = Math.max(this.now() + DEBOUNCE_MS, Math.min(...due.map(j => j.nextAt)));
    const existing = await this.api.alarms.get(RETRY_ALARM);
    if (!existing || existing.scheduledTime < this.now() || existing.scheduledTime > when) {
      await this.api.alarms.create(RETRY_ALARM, { when });
    }
  }
  recover() {
    return this.serial(async () => {
      const { state } = await this.context();
      for (const job of Object.values(state.jobs)) {
        if (job.phase !== "syncing") continue;
        job.phase = job.attempts >= MAX_ATTEMPTS ? "exhausted" : "retrying";
        job.errorCode = "worker_interrupted";
        job.nextAt = job.phase === "exhausted" ? null : Math.max(this.now(), job.nextAt || 0);
      }
      await this.save(state); await this.arm(state); return state;
    });
  }
  enqueue(names = null, reason = "manual") {
    return this.serial(async () => {
      const { settings, state } = await this.context();
      if (reason === "change" && !settings.syncOnChange) return state;
      const manual = reason === "manual", now = this.now();
      const requested = names ?? Object.keys(settings.approvedSources || {});
      for (const source of requested) {
        const spec = settings.approvedSources?.[source];
        if (!/^[a-z][a-z0-9_-]{0,31}$/.test(source) || !spec?.enabled || !sourceApproved(settings, source, spec)) continue;
        const old = state.jobs[source];
        // Automatic events do not silently reset the retry budget or unblock auth errors.
        if (!manual && old && ["blocked", "exhausted"].includes(old.phase)) continue;
        if (!manual && old && ["retrying", "syncing"].includes(old.phase)) continue;
        const first = !manual && old?.phase === "pending" ? old.queuedAt : now;
        state.jobs[source] = {
          phase: "pending", attempts: manual ? 0 : old?.phase === "pending" ? old.attempts : 0,
          reason, queuedAt: first, nextAt: manual ? now : Math.min(now + DEBOUNCE_MS, first + 60_000),
          lastSuccessAt: old?.lastSuccessAt ?? null, errorCode: null,
        };
      }
      await this.save(state); await this.arm(state); return state;
    });
  }
  async changed(info) {
    const settings = await this.settings();
    if (!settings.syncOnChange || info?.cookie?.partitionKey != null) return;
    let host;
    try { host = domainName(info.cookie.domain); } catch { return; }
    const names = Object.entries(settings.approvedSources || {}).filter(([source, spec]) =>
      spec.enabled && sourceApproved(settings, source, spec) && allowedDomain(host, spec.domains),
    ).map(([source]) => source);
    // Never pass the cookie/event body into persistence or the queue.
    if (names.length) await this.enqueue(names, "change");
  }
  runDue(limit = 3) {
    return this.serial(async () => {
      const { settings, state } = await this.context();
      const results = {};
      const due = Object.entries(state.jobs).filter(([, j]) =>
        PENDING.has(j.phase) && j.nextAt <= this.now(),
      ).slice(0, limit);
      for (const [source, job] of due) {
        if ((job.reason === "change" && !settings.syncOnChange) ||
            (job.reason === "periodic" && !settings.autoPushMinutes)) {
          Object.assign(job, { phase: "paused", nextAt: null }); continue;
        }
        if (job.attempts >= MAX_ATTEMPTS) {
          Object.assign(job, { phase: "exhausted", nextAt: null }); continue;
        }
        job.attempts += 1;
        Object.assign(job, { phase: "syncing", lastAttemptAt: this.now(),
          nextAt: this.now() + retryDelay(job.attempts, this.random) });
        if (!await this.save(state)) break; // Durable intent BEFORE any request.
        try {
          const doc = await this.getSources(settings);
          if (!Object.hasOwn(doc.sources, source) || !doc.sources[source].enabled) {
            Object.assign(job, { phase: "paused", nextAt: null, errorCode: null });
            results[source] = { ok: false, code: "paused", error: "Source removed or paused" };
          } else {
            if (!sourceApproved(settings, source, doc.sources[source])) {
              throw new ReceiverError("approval_required", "Source approval required");
            }
            // pushSource fetches a fresh version and recollects on EVERY attempt.
            const result = await this.push(source, doc, settings);
            Object.assign(job, { phase: "succeeded", nextAt: null, errorCode: null,
              lastSuccessAt: this.now(), unchanged: !!result.unchanged, cookieCount: result.cookieCount });
            results[source] = result;
          }
        } catch (error) {
          const failure = safeFailure(error);
          const retry = failure.retryable && job.attempts < MAX_ATTEMPTS;
          Object.assign(job, { phase: retry ? "retrying" : failure.retryable ? "exhausted" : "blocked",
            errorCode: failure.code,
            nextAt: retry ? this.now() + Math.max(retryDelay(job.attempts, this.random), failure.retryAfterMs) : null });
          results[source] = { ok: false, code: failure.code, error: failure.code,
            queued: retry, nextAttemptAt: job.nextAt, attempts: job.attempts };
        }
        if (!await this.save(state)) break;
      }
      if ((await this.settings()).connectionId !== state.connectionId) {
        await this.api.alarms.clear(RETRY_ALARM);
        return { ok: false, error: "Connection changed; retry after authorization" };
      }
      await this.save(state); await this.arm(state);
      await this.api.storage.local.set({ lastPushAt: new Date(this.now()).toISOString(),
        lastPushResults: results, lastPushError: "" });
      return { ok: true, results, queue: state };
    });
  }
  status() { return this.serial(async () => (await this.context()).state); }
}

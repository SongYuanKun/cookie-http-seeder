import { senderTag } from "./senders.js";
import { endpointURL, loadSettings, normalizeSources } from "./settings.js";
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
    const hint = terminal[response.status] || [response.status === 429 ? "rate_limited" :
      transient ? "server_unavailable" : "request_rejected", `Receiver error HTTP ${response.status}`];
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

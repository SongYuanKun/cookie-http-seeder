/** Human display only. Storage, API payloads and retry deadlines stay unchanged. */
const ISO_FIELDS = new Set(["updatedAt", "observedAt", "lastSeenAt", "checkedAt", "lastPushAt"]);
const MS_FIELDS = new Set(["queuedAt", "nextAt", "lastAttemptAt", "lastSuccessAt", "nextAttemptAt"]);
const ISO_INSTANT = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/;

export function formatLocalTime(value) {
  if (typeof value === "string") {
    const match = ISO_INSTANT.exec(value);
    if (!match) return "-"; // Never interpret a timezone-less string as UTC/local by guesswork.
    const [year, month, day, hour, minute, second] = match.slice(1).map(Number);
    // Date.parse normalizes invalid dates such as February 30. Reject them instead.
    const check = new Date(0);
    check.setUTCFullYear(year, month - 1, day);
    check.setUTCHours(hour, minute, second, 0);
    if (year < 1 || check.getUTCFullYear() !== year || check.getUTCMonth() !== month - 1 ||
        check.getUTCDate() !== day || check.getUTCHours() !== hour ||
        check.getUTCMinutes() !== minute || check.getUTCSeconds() !== second) return "-";
  } else if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  const date = new Date(value);
  if (!Number.isFinite(date.getTime()) || date.getFullYear() < 1 || date.getFullYear() > 9999) return "-";
  const pad = n => String(n).padStart(2, "0");
  return `${String(date.getFullYear()).padStart(4, "0")}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

export function localTimeZone() {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "本地时区";
}

export function displayTimes(value) {
  if (Array.isArray(value)) return value.map(displayTimes);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key,
      ISO_FIELDS.has(key) ? formatLocalTime(typeof item === "string" ? item : null) :
      MS_FIELDS.has(key) ? formatLocalTime(typeof item === "number" ? item : null) : displayTimes(item),
    ]));
  }
  return value;
}

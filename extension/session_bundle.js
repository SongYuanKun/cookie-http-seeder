import { allowedDomain, domainName, targetURL } from "./shared.js";

const MAX_PLAINTEXT = 512 * 1024;
const ITERATIONS = 310000;
const encoder = new TextEncoder();
const decoder = new TextDecoder("utf-8", { fatal: true });
const own = (value, key) => Object.hasOwn(value, key);
export const SESSION_COVERAGE = Object.freeze({
  cookies: "captured", localStorage: "selected_origin_only",
  sessionStorage: "selected_tab_origin_only", requestHeaders: "one_top_level_navigation",
  indexedDB: "unsupported", partitionedCookies: "unsupported",
  serviceWorkers: "unsupported", deviceBoundCredentials: "unsupported",
  siteEgressIP: "not_observed", geolocation: "not_observed",
});

function bytesToBase64(bytes) {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 8192) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
  }
  return btoa(binary);
}

function base64ToBytes(value, maxLength) {
  if (typeof value !== "string" || value.length > maxLength ||
      !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value)) {
    throw new Error("Invalid bundle encoding");
  }
  return Uint8Array.from(atob(value), char => char.charCodeAt(0));
}

function scopedURL(value, domains, originOnly = false) {
  const url = targetURL(value);
  if (url.protocol !== "https:" || !allowedDomain(domainName(url.hostname), domains) ||
      (originOnly && (url.pathname !== "/" || url.search))) {
    throw new Error("Session URL is outside approved HTTPS domains");
  }
  return url;
}

function storageEntries(value) {
  if (!Array.isArray(value) || value.length > 500 || value.some(item =>
    !item || typeof item !== "object" || typeof item.name !== "string" ||
    typeof item.value !== "string" || item.name.length > 4096 || item.value.length > 32768)) {
    throw new Error("Invalid browser storage entries");
  }
}

export function validateSessionPayload(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload) ||
      payload.schema_version !== 1 || !/^[a-z][a-z0-9_-]{0,31}$/.test(payload.source || "") ||
      !Array.isArray(payload.domains) || !payload.domains.length || payload.domains.length > 32) {
    throw new Error("Invalid session payload");
  }
  const domains = payload.domains.map(domainName);
  if (new Set(domains).size !== domains.length) throw new Error("Duplicate session domain");
  if (typeof payload.captured_at !== "string" || !Number.isFinite(Date.parse(payload.captured_at))) {
    throw new Error("Invalid capture time");
  }
  const nav = payload.navigation;
  if (!nav || !["GET", "HEAD"].includes(nav.method) ||
      !Number.isInteger(nav.status) || nav.status < 100 || nav.status > 599 ||
      !Array.isArray(nav.headers) || nav.headers.length > 128) {
    throw new Error("Invalid captured navigation");
  }
  scopedURL(nav.url, domains);
  scopedURL(nav.final_url, domains);
  for (const header of nav.headers) {
    if (!header || typeof header.name !== "string" ||
        !/^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/.test(header.name) ||
        typeof header.value !== "string" || header.value.length > 8192 ||
        /[\r\n\x00]/.test(header.value)) throw new Error("Invalid request header");
  }
  if (!Array.isArray(payload.cookies) || payload.cookies.length > 500) throw new Error("Invalid cookies");
  for (const cookie of payload.cookies) {
    if (!cookie || typeof cookie !== "object" || cookie.partitionKey != null ||
        !allowedDomain(domainName(cookie.domain), domains) ||
        typeof cookie.name !== "string" || typeof cookie.value !== "string" ||
        typeof cookie.path !== "string" || !cookie.path.startsWith("/")) {
      throw new Error("Cookie is outside session scope");
    }
  }
  if (!Array.isArray(payload.origins) || payload.origins.length > 16) throw new Error("Invalid origins");
  for (const item of payload.origins) {
    if (!item || typeof item.origin !== "string") throw new Error("Invalid storage origin");
    const url = scopedURL(item.origin, domains, true);
    if (item.origin !== url.origin) throw new Error("Invalid storage origin");
    storageEntries(item.localStorage);
    storageEntries(item.sessionStorage);
  }
  if (!payload.environment || typeof payload.environment !== "object" ||
      !payload.location || typeof payload.location !== "object" ||
      !payload.coverage || typeof payload.coverage !== "object" ||
      Object.keys(payload.coverage).length !== Object.keys(SESSION_COVERAGE).length ||
      Object.entries(SESSION_COVERAGE).some(([key, value]) => payload.coverage[key] !== value)) {
    throw new Error("Missing session context");
  }
  const encoded = encoder.encode(JSON.stringify(payload));
  if (encoded.length > MAX_PLAINTEXT) throw new Error("Session payload is too large");
  return payload;
}

async function key(passphrase, salt) {
  if (typeof passphrase !== "string" || passphrase.length < 12 || passphrase.length > 1024) {
    throw new Error("Passphrase must contain 12 to 1024 characters");
  }
  const material = await crypto.subtle.importKey("raw", encoder.encode(passphrase), "PBKDF2", false, ["deriveKey"]);
  return crypto.subtle.deriveKey({ name: "PBKDF2", hash: "SHA-256", salt, iterations: ITERATIONS },
    material, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
}

export async function encryptBundle(payload, passphrase) {
  validateSessionPayload(payload);
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const nonce = crypto.getRandomValues(new Uint8Array(12));
  const secret = await key(passphrase, salt);
  const ciphertext = await crypto.subtle.encrypt({ name: "AES-GCM", iv: nonce,
    additionalData: encoder.encode(`portable-session:${payload.source}:1`) }, secret,
  encoder.encode(JSON.stringify(payload)));
  return { schema_version: 1, source: payload.source, kdf: "PBKDF2-SHA256", iterations: ITERATIONS,
    cipher: "AES-256-GCM", salt: bytesToBase64(salt), nonce: bytesToBase64(nonce),
    ciphertext: bytesToBase64(new Uint8Array(ciphertext)) };
}

export async function decryptBundle(envelope, passphrase) {
  if (!envelope || typeof envelope !== "object" || envelope.schema_version !== 1 ||
      !/^[a-z][a-z0-9_-]{0,31}$/.test(envelope.source || "") ||
      envelope.kdf !== "PBKDF2-SHA256" || envelope.iterations !== ITERATIONS ||
      envelope.cipher !== "AES-256-GCM" ||
      Object.keys(envelope).some(field => !["schema_version", "source", "kdf", "iterations", "cipher",
        "salt", "nonce", "ciphertext"].includes(field))) throw new Error("Invalid bundle envelope");
  const salt = base64ToBytes(envelope.salt, 24);
  const nonce = base64ToBytes(envelope.nonce, 16);
  const ciphertext = base64ToBytes(envelope.ciphertext, 700000);
  if (salt.length !== 16 || nonce.length !== 12 || ciphertext.length < 16) throw new Error("Invalid bundle encoding");
  try {
    const secret = await key(passphrase, salt);
    const raw = await crypto.subtle.decrypt({ name: "AES-GCM", iv: nonce,
      additionalData: encoder.encode(`portable-session:${envelope.source}:1`) }, secret, ciphertext);
    if (raw.byteLength > MAX_PLAINTEXT) throw new Error("Session payload is too large");
    const payload = JSON.parse(decoder.decode(raw));
    validateSessionPayload(payload);
    if (payload.source !== envelope.source) throw new Error("Bundle source mismatch");
    return payload;
  } catch { throw new Error("Cannot decrypt or validate session bundle"); }
}

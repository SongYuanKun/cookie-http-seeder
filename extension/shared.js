/** Keep in sync with examples/sources.json */
export const SOURCES = {
  beike: {
    label: "beike",
    domains: [".ke.com", "ke.com", ".lianjia.com", "lianjia.com"],
  },
  fang: {
    label: "fang",
    domains: [".fang.com", "fang.com", ".soufun.com", "soufun.com"],
  },
};

export const DEFAULTS = {
  endpoint: "http://127.0.0.1:18765",
  token: "",
  autoPushMinutes: 0,
};

export function cookiesToHeader(cookies) {
  const seen = new Set();
  const parts = [];
  for (const cookie of cookies) {
    if (!cookie?.name || seen.has(cookie.name)) continue;
    seen.add(cookie.name);
    parts.push(`${cookie.name}=${cookie.value ?? ""}`);
  }
  return parts.join("; ");
}

export async function collectSourceCookies(source) {
  const spec = SOURCES[source];
  if (!spec) throw new Error(`unknown source: ${source}`);
  const byName = new Map();
  for (const domain of spec.domains) {
    const batch = await chrome.cookies.getAll({ domain });
    for (const cookie of batch) {
      if (!byName.has(cookie.name)) byName.set(cookie.name, cookie);
    }
  }
  return [...byName.values()];
}

export async function pushCookieHeader(params) {
  const base = params.endpoint.replace(/\/$/, "");
  const response = await fetch(`${base}/v1/cookies`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${params.token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      source: params.source,
      cookie_header: params.cookieHeader,
    }),
  });
  const text = await response.text();
  let body;
  try {
    body = JSON.parse(text);
  } catch {
    body = { ok: false, error: text.slice(0, 200) };
  }
  if (!response.ok || !body?.ok) {
    throw new Error(body?.error || `HTTP ${response.status}`);
  }
  return body;
}

export async function loadSettings() {
  const stored = await chrome.storage.local.get(Object.keys(DEFAULTS));
  return { ...DEFAULTS, ...stored };
}

export async function saveSettings(patch) {
  await chrome.storage.local.set(patch);
}

import { allowedDomain, collectSourceCookies, domainName, receiverRequest, targetURL } from "./shared.js";
import { SESSION_COVERAGE, validateSessionPayload } from "./session_bundle.js";

export function scopedSiteURL(value, domains) {
  const url = targetURL(value);
  if (url.protocol !== "https:" || !allowedDomain(domainName(url.hostname), domains)) {
    throw new Error("页面不在已批准的网站范围内");
  }
  return url;
}

/** Listen only after a user action. No background traffic or form submission is recorded. */
export function captureOneNavigation(api, tabId, domains, timeoutMs = 120000, trigger) {
  return new Promise((resolve, reject) => {
    if (typeof trigger !== "function") {
      reject(new Error("必须由明确的用户操作触发页面导航"));
      return;
    }
    let candidate = null;
    let finished = false;
    const finish = (error, value) => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      api.webRequest.onBeforeSendHeaders.removeListener(before);
      api.webRequest.onCompleted.removeListener(completed);
      api.webRequest.onErrorOccurred.removeListener(failed);
      if (error) reject(error); else resolve(value);
    };
    const before = details => {
      if (candidate || details.tabId !== tabId || details.type !== "main_frame" ||
          !["GET", "HEAD"].includes(details.method)) return;
      try { scopedSiteURL(details.url, domains); } catch { return; }
      if (!Array.isArray(details.requestHeaders)) {
        finish(new Error("Chrome 未提供请求头，请检查扩展权限"));
        return;
      }
      candidate = { requestId: details.requestId, method: details.method, url: details.url,
        headers: details.requestHeaders.map(({ name, value }) => ({ name, value: value || "" })) };
    };
    const completed = details => {
      if (!candidate || details.tabId !== tabId || details.requestId !== candidate.requestId) return;
      try {
        scopedSiteURL(details.url, domains);
        finish(null, { method: candidate.method, url: candidate.url, final_url: details.url,
          status: details.statusCode, headers: candidate.headers });
      } catch { finish(new Error("页面跳转到了网站授权范围外")); }
    };
    const failed = details => {
      if (candidate && details.tabId === tabId && details.requestId === candidate.requestId) {
        finish(new Error("选中的页面请求失败，请重新开始采集"));
      }
    };
    const timer = setTimeout(() => finish(new Error("两分钟内未捕获到该标签的页面导航")), timeoutMs);
    try {
      api.webRequest.onBeforeSendHeaders.addListener(before,
        { urls: ["https://*/*"], types: ["main_frame"] }, ["requestHeaders", "extraHeaders"]);
      api.webRequest.onCompleted.addListener(completed,
        { urls: ["https://*/*"], types: ["main_frame"] });
      api.webRequest.onErrorOccurred.addListener(failed,
        { urls: ["https://*/*"], types: ["main_frame"] });
    } catch { finish(new Error("无法监听选中标签的页面请求，请检查扩展权限")); return; }
    try {
      Promise.resolve(trigger()).catch(() => finish(new Error("无法重新加载选中标签")));
    } catch { finish(new Error("无法重新加载选中标签")); }
  });
}

/** The version is frozen before capture; never refresh it after gathering credentials. */
export function postBundleAtVersion(settings, source, revision, expectedVersion, envelope,
  request = receiverRequest) {
  return request(settings, "/v3/session-bundles", { method: "POST",
    body: JSON.stringify({ source, envelope, config_revision: revision,
      expected_version: expectedVersion, request_id: crypto.randomUUID() }) });
}

export async function waitForTab(api, tabId, finalURL, timeoutMs = 30000) {
  const tab = await api.tabs.get(tabId);
  if (tab.status === "complete" && tab.url === finalURL) return;
  return new Promise((resolve, reject) => {
    const done = error => {
      clearTimeout(timer);
      api.tabs.onUpdated.removeListener(updated);
      if (error) reject(error); else resolve();
    };
    const updated = (id, change, current) => {
      if (id !== tabId || change.status !== "complete") return;
      if (current.url !== finalURL) done(new Error("页面在读取存储前再次跳转"));
      else done();
    };
    const timer = setTimeout(() => done(new Error("页面未完成加载")), timeoutMs);
    api.tabs.onUpdated.addListener(updated);
  });
}

export async function collectTabState(api, tabId, spec, finalURL) {
  scopedSiteURL(finalURL, spec.domains);
  const results = await api.scripting.executeScript({ target: { tabId }, func: () => {
    const entries = storage => Array.from({ length: storage.length }, (_, index) => {
      const name = storage.key(index);
      return { name, value: storage.getItem(name) };
    });
    return { origin: location.origin, localStorage: entries(localStorage),
      sessionStorage: entries(sessionStorage), environment: {
        userAgent: navigator.userAgent, platform: navigator.platform,
        language: navigator.language, languages: navigator.languages,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        viewport: { width: innerWidth, height: innerHeight },
        screen: { width: screen.width, height: screen.height, devicePixelRatio },
      } };
  } });
  if (results.length !== 1 || !results[0]?.result) throw new Error("无法读取选中标签的浏览器存储");
  const data = results[0].result;
  scopedSiteURL(data.origin, spec.domains);
  if (new URL(finalURL).origin !== data.origin) throw new Error("页面在采集期间发生跳转");
  const cookies = await collectSourceCookies(spec);
  return { cookies, origins: [{ origin: data.origin, localStorage: data.localStorage,
    sessionStorage: data.sessionStorage }], environment: data.environment };
}

export function buildSessionPayload(source, spec, navigation, cookies, origins, environment, location) {
  const payload = { schema_version: 1, source, domains: spec.domains,
    captured_at: new Date().toISOString(), navigation, cookies, origins, environment,
    location: { city: location.city || "", district: location.district || "",
      source: "user_label" },
    coverage: { ...SESSION_COVERAGE } };
  return validateSessionPayload(payload);
}

import { fetchSources, loadSettings, receiverRequest, sourceApproved, sourceOrigins } from "./shared.js";
import { buildSessionPayload, captureOneNavigation, collectTabState, postBundleAtVersion, scopedSiteURL,
  waitForTab } from "./session_capture.js";
import { encryptBundle } from "./session_bundle.js";

const sourceSelect = document.getElementById("source");
const status = document.getElementById("status");
const button = document.getElementById("start");
const tabId = Number(new URL(location.href).searchParams.get("tab"));
let documentSources;
let settings;

function show(message, error = false) { status.textContent = message; status.className = error ? "error" : ""; }

try {
  if (!Number.isSafeInteger(tabId) || tabId < 0) throw new Error("未找到来源标签。请从网站标签上的扩展按钮打开此页");
  settings = await loadSettings();
  documentSources = await fetchSources(settings);
  for (const [name, spec] of Object.entries(documentSources.sources)) {
    if (!spec.enabled || !sourceApproved(settings, name, spec)) continue;
    const option = document.createElement("option");
    option.value = name; option.textContent = `${spec.label} (${name})`;
    sourceSelect.append(option);
  }
  if (!sourceSelect.options.length) throw new Error("尚无已批准且启用的网站；请先到“管理网站与连接”授权");
  show("就绪。点击开始会重新加载原网站标签，请先保存未提交的表单。");
} catch (error) { button.disabled = true; show(error.message, true); }

button.addEventListener("click", async () => {
  button.disabled = true;
  const passphrase = document.getElementById("passphrase").value;
  document.getElementById("passphrase").value = "";
  try {
    if (passphrase.length < 12 || passphrase.length > 1024) throw new Error("口令长度应为 12–1024 字符");
    const source = sourceSelect.value;
    const spec = documentSources.sources[source];
    const tab = await chrome.tabs.get(tabId);
    scopedSiteURL(tab.url, spec.domains);
    if (!await chrome.permissions.contains({ origins: sourceOrigins(spec) })) {
      throw new Error("网站访问权限缺失，请先在管理页重新授权");
    }
    const current = await receiverRequest(settings, `/v3/session-bundles/${source}`);
    if (current.config_revision !== documentSources.revision || !current.enabled) {
      throw new Error("接收端来源配置已变更，请重新开始");
    }
    const expectedVersion = current.bundle_version;
    show("正在重新加载原网站标签并采集该次页面请求（最多两分钟）…");
    const navigation = await captureOneNavigation(chrome, tabId, spec.domains, 120000,
      () => chrome.tabs.reload(tabId));
    await waitForTab(chrome, tabId, navigation.final_url);
    show("页面请求已捕获，正在读取浏览器存储并加密…");
    const browser = await collectTabState(chrome, tabId, spec, navigation.final_url);
    const latestSettings = await loadSettings();
    const latestDoc = await fetchSources(latestSettings);
    if (latestSettings.connectionId !== settings.connectionId ||
        latestDoc.revision !== documentSources.revision ||
        !sourceApproved(latestSettings, source, latestDoc.sources[source])) {
      throw new Error("连接或来源授权已变更，请重新开始");
    }
    const payload = buildSessionPayload(source, spec, navigation, browser.cookies, browser.origins,
      browser.environment, { city: document.getElementById("city").value.trim(),
        district: document.getElementById("district").value.trim() });
    const envelope = await encryptBundle(payload, passphrase);
    const result = await postBundleAtVersion(settings, source, latestDoc.revision,
      expectedVersion, envelope);
    if (!result.bundle_version) throw new Error("接收端未确认保存");
    show(`密文会话包已保存。版本 ${result.bundle_version}。接收端观察到的连接 IP：${result.peer?.ip || "未知"}（不是源站出口 IP）。`);
  } catch (error) {
    show(error.message || "采集失败；原有会话包未覆盖", true);
  } finally { button.disabled = false; }
});

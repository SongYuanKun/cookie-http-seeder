import {
  approveSources, configExport, configImport, endpointURL, fetchSources, loadSettings,
  normalizeSources, receiverRequest, saveSettings, sourceApproved, sourceOrigins,
} from "./shared.js";
import { cachedSourceDocument, revokeLocalSource } from "./access.js";
import { sourceStatus } from "./source_status.js";
import { displayTimes, formatLocalTime, localTimeZone } from "./time.js";
const $ = id => document.getElementById(id);
let doc = null, connection = null, imported = null, busy = false;
const statusCells = new Map();
let statusGeneration = 0;
const show = (text, error = false) => { $("status").textContent = text; $("status").className = error ? "error" : "ok"; };
// Start synchronously: permissions.request must run inside the user's click gesture.
function action(fn) {
  return async event => {
    event?.preventDefault();
    if (busy) return;
    busy = true;
    try { await fn(); } catch (error) { show(error.message || "操作失败", true); }
    finally { busy = false; }
  };
}
function requireDoc(online = true) {
  if (!doc || !connection) throw new Error("先连接接收端并加载配置");
  if (online && doc.offline) throw new Error("当前仅显示本机缓存；请连接接收端并重新加载配置");
}
function originsFor(sources) { return [...new Set(Object.values(sources).filter(s => s.enabled).flatMap(sourceOrigins))]; }
function wrapped(result) { return { ...result, sources: normalizeSources(result.sources), connectionId: connection.connectionId }; }
async function reload() {
  doc = null; statusCells.clear(); $("sources").replaceChildren();
  connection = await loadSettings();
  try { doc = await fetchSources(connection); }
  catch (error) {
    doc = cachedSourceDocument(connection);
    if (!doc) throw error;
  }
  render(); await refreshStatuses();
  show(doc.offline ? "接收端不可用：只显示本机已授权来源缓存，可撤销本机授权；不能修改服务端配置。" :
    "已加载配置。新增或变更的来源需要浏览器授权后才会同步。", !!doc.offline);
}
async function saveConfig(sources, approveNames) {
  await checkConnection();
  const result = await receiverRequest(connection, "/v1/sources", {
    method: "PUT", body: JSON.stringify({ sources, revision: doc.revision }),
  });
  doc = wrapped(result); await approveSources(doc, approveNames); render(); await refreshStatuses();
  show("配置已保存。受影响来源的旧快照已清空，请重新推送。");
}
async function push(source = null) {
  const response = await chrome.runtime.sendMessage({ type: "push-now", source });
  if (!response?.ok) throw new Error(response?.error || "推送失败");
  show(Object.entries(response.results || {}).map(([name, result]) => result.ok
    ? `${name}: ${result.unchanged ? "内容未变" : result.cleared ? "空快照已同步" : `${result.cookieCount} 个 Cookie 已同步`}（推送不验证登录）`
    : `${name}: ${result.code || result.error}${result.queued ? "（已排队重试）" : ""}`).join("\n") || "没有已授权来源，或任务已在队列中", Object.values(response.results || {}).some(r => !r.ok));
  if (doc) await refreshStatuses();
}
function button(text, fn) {
  const button = document.createElement("button");
  button.type = "button"; button.textContent = text; button.addEventListener("click", action(fn));
  return button;
}
function render() {
  $("sender-context").textContent = `当前发送端标签：${connection.senderTag || "default"}。本页配置、快照与状态均属于此标签。`;
  statusCells.clear();
  $("sources").replaceChildren();
  for (const [name, spec] of Object.entries(doc.sources)) {
    const row = document.createElement("tr");
    const cells = Array.from({ length: 4 }, () => document.createElement("td"));
    cells[0].textContent = `${spec.label} (${name})`;
    cells[1].textContent = `${spec.domains.join(", ")}\n${spec.target_url || "未配置目标 URL；消费者必须传入 URL"}`;
    cells[2].textContent = "正在读取授权、同步与登录反馈…";
    cells[2].className = "source-status";
    statusCells.set(name, cells[2]);
    cells[3].append(
      button("编辑", () => {
        $("source-name").value = name; $("source-name").readOnly = true;
        $("source-label").value = spec.label; $("source-domains").value = spec.domains.join("\n");
        $("source-url").value = spec.target_url; $("source-enabled").checked = spec.enabled;
      }),
      button("仅授权此来源", async () => {
        requireDoc();
        const origins = spec.enabled ? sourceOrigins(spec) : [];
        if (!spec.enabled) throw new Error("来源已暂停，请先编辑并启用");
        if (origins.length && !await chrome.permissions.request({ origins })) throw new Error("网站权限未授予");
        await approveSources(doc, [name]); await refreshStatuses();
        show(`${name} 已授权；没有改动配置或清空接收端快照。`);
      }),
      button("撤销本机授权", async () => {
        if (!confirm(`停止本机同步 ${name}？保留浏览器 Cookie 和接收端副本；已经发出的请求不能撤回。`)) return;
        const result = await revokeLocalSource(name, connection.connectionId);
        await refreshStatuses();
        const cleanup = {
          removed: "已移除不再共用的浏览器域名权限。",
          shared: "保留其他来源或接收端仍需使用的共用权限。",
          failed: "本机同步已停止，但浏览器权限清理失败，可到扩展设置中检查。",
          deferred: "配置同时发生变化，未移除浏览器权限。",
          none: "该来源原本就未授权。",
        };
        show(`${name} 本机授权已撤销。${cleanup[result.permissionCleanup] || ""}`);
      }),
      button("推送", () => push(name)),
      button("清空并暂停", async () => {
        if (!confirm(`清空 ${name} 的接收端副本并暂停同步？不会删除浏览器 Cookie。`)) return;
        await checkConnection();
        const result = await receiverRequest(connection, `/v2/cookies/${name}`, {
          method: "DELETE", headers: { "If-Match": doc.revision },
        });
        doc = wrapped(result); await approveSources(doc, [name]); render(); await refreshStatuses();
        show(`${name} 已清空并暂停。重新启用和推送需要显式操作。`);
      }),
      button("删除来源", async () => {
        if (!confirm(`删除 ${name} 并清空其接收端副本？`)) return;
        const sources = { ...doc.sources }; delete sources[name];
        await saveConfig(sources, [name]);
      }),
    );
    if (doc.offline) {
      for (const control of cells[3].children) {
        if (control.textContent !== "撤销本机授权") control.disabled = true;
      }
    }
    row.append(...cells); $("sources").append(row);
  }
}
$("connection-form").addEventListener("submit", action(async () => {
  const endpoint = endpointURL($("endpoint").value.trim());
  const patch = { endpoint, senderTag: $("sender-tag").value.trim(), token: $("token").value.trim(), autoPushMinutes: Number($("autoPushMinutes").value), syncOnChange: $("syncOnChange").checked };
  const origins = [`${new URL(endpoint).protocol}//${new URL(endpoint).hostname}/*`];
  if (!await chrome.permissions.request({ origins })) throw new Error("未授权接收端地址");
  await saveSettings(patch);
  const result = await chrome.runtime.sendMessage({ type: "settings-saved" });
  if (!result?.ok) throw new Error(result?.error || "无法更新定时任务");
  await reload();
}));
$("reload").addEventListener("click", action(reload));
$("approve").addEventListener("click", action(async () => {
  requireDoc(); const origins = originsFor(doc.sources);
  if (origins.length && !await chrome.permissions.request({ origins })) throw new Error("网站权限未授予");
  await approveSources(doc); await refreshStatuses(); show("已授权当前配置。现在可以推送。");
}));
$("push-all").addEventListener("click", action(() => push()));
$("source-form").addEventListener("reset", () => { $("source-name").readOnly = false; });
$("source-form").addEventListener("submit", action(async () => {
  requireDoc(); const name = $("source-name").value.trim();
  const sources = normalizeSources({ ...doc.sources, [name]: {
    label: $("source-label").value.trim(), domains: $("source-domains").value.split(/[,\s]+/).filter(Boolean),
    target_url: $("source-url").value.trim(), enabled: $("source-enabled").checked,
  } });
  const origins = sources[name].enabled ? sourceOrigins(sources[name]) : [];
  if (origins.length && !await chrome.permissions.request({ origins })) throw new Error("网站权限未授予，配置未保存");
  await saveConfig(sources, [name]); $("source-form").reset();
}));
$("export").addEventListener("click", action(() => {
  requireDoc();
  const url = URL.createObjectURL(new Blob([JSON.stringify(configExport(doc.sources), null, 2)], { type: "application/json" }));
  const a = document.createElement("a"); a.href = url; a.download = "cookie-seeder-sources.json"; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}));
$("import-file").addEventListener("change", action(async () => {
  imported = null; $("apply-import").disabled = true; $("import-preview").textContent = "";
  const file = $("import-file").files[0];
  if (!file || file.size > 128 * 1024) throw new Error("请选择不超过 128 KiB 的 JSON 配置");
  imported = configImport(JSON.parse(await file.text()));
  $("import-preview").textContent = JSON.stringify(configExport(imported), null, 2);
  $("apply-import").disabled = false;
}));
$("apply-import").addEventListener("click", action(async () => {
  requireDoc(); if (!imported) throw new Error("先选择配置文件");
  if (!confirm("用预览配置替换全部来源？被删除或修改的来源会清空旧快照。")) return;
  const origins = originsFor(imported);
  if (origins.length && !await chrome.permissions.request({ origins })) throw new Error("网站权限未授予");
  await saveConfig(imported, Object.keys(imported));
  imported = null; $("apply-import").disabled = true; $("import-preview").textContent = "";
}));
loadSettings().then(settings => {
  $("sender-tag").value = settings.senderTag;
  $("endpoint").value = settings.endpoint; $("token").value = settings.token;
  $("autoPushMinutes").value = settings.autoPushMinutes;
  $("syncOnChange").checked = settings.syncOnChange;
  if (settings.token && settings.connectionId) return reload();
}).catch(error => show(error.message, true));

$("diagnose").addEventListener("click", action(async () => {
  const settings = await loadSettings();
  const queue = await chrome.runtime.sendMessage({ type: "sync-status" });
  let remote;
  try { remote = await receiverRequest(settings, "/v1/status"); }
  catch (error) { remote = { ok: false, code: error.code || "connection_failed" }; }
  const permissions = {};
  for (const [name, spec] of Object.entries(settings.approvedSources || {})) {
    permissions[name] = await chrome.permissions.contains({ origins: sourceOrigins(spec) });
  }
  // Do not include the settings object: it contains the receiver token.
  $("diagnostics").textContent = `当地时间（${localTimeZone()}，YYYY-MM-DD HH:mm:ss）\n` +
    JSON.stringify(displayTimes({
      sender_tag: settings.senderTag, connection: remote.ok ? "authenticated" : remote.code,
      permissions, queue: queue?.queue?.jobs || {}, sources: remote.sources || {},
    }), null, 2);
}));

async function checkConnection() {
  requireDoc();
  if ((await loadSettings()).connectionId !== connection.connectionId) {
    throw new Error("连接已在其他页面变更，请重新加载配置");
  }
}

async function refreshStatuses() {
  requireDoc(false);
  const generation = ++statusGeneration, requestedDoc = doc;
  const settings = await loadSettings();
  if (settings.connectionId !== connection.connectionId) {
    for (const cell of statusCells.values()) cell.textContent = "连接已变更，请重新加载配置";
    return;
  }
  for (const cell of statusCells.values()) cell.textContent = "正在刷新状态…";
  const [queue, remote, permissions] = await Promise.all([
    chrome.runtime.sendMessage({ type: "sync-status" }).catch(() => ({ ok: false })),
    receiverRequest(settings, "/v1/status").catch(() => ({ ok: false })),
    Promise.all(Object.entries(requestedDoc.sources).map(async ([name, spec]) => {
      let granted = false;
      try { granted = await chrome.permissions.contains({ origins: sourceOrigins(spec) }); } catch { /* unavailable */ }
      return [name, granted];
    })),
  ]);
  const current = await loadSettings();
  if (generation !== statusGeneration || doc !== requestedDoc) return;
  if (current.connectionId !== settings.connectionId) {
    for (const cell of statusCells.values()) cell.textContent = "连接已变更，请重新加载配置";
    return;
  }
  const grants = Object.fromEntries(permissions);
  const changed = remote.ok && remote.config_revision !== requestedDoc.revision;
  for (const [name, spec] of Object.entries(requestedDoc.sources)) {
    const summary = sourceStatus(spec, {
      approved: sourceApproved(current, name, spec), permission: grants[name],
      job: queue?.queue?.jobs?.[name], queueAvailable: queue?.ok === true,
      remote: remote?.sources?.[name], remoteAvailable: remote.ok === true && !changed,
    });
    const cell = statusCells.get(name);
    if (!cell) continue;
    cell.textContent = changed ? "接收端配置已变更，请重新加载配置\n" + summary.text : summary.text;
    cell.className = `source-status ${summary.warning || changed ? "attention" : "ok"}`;
  }
  $("status-updated").textContent = `状态查询时间：${formatLocalTime(Date.now())}（${localTimeZone()}）。登录有效性仅代表爬虫最近一次报告。`;
}
$("refresh-sources").addEventListener("click", action(refreshStatuses));

/** Shared Options/Popup presentation and explicit operator gestures. */
import { formatLocalTime, localTimeZone } from "./time.js";
const phase = { idle: "尚未推送", pending: "等待同步", syncing: "正在同步", retrying: "等待重试", succeeded: "最近同步成功", exhausted: "重试已耗尽", blocked: "需要处理", paused: "已暂停", probing: "正在探测恢复" };
const validation = { unavailable: "无法读取（不会沿用旧结果）", unverified: "未验证", valid: "爬虫最近报告有效", invalid: "爬虫报告登录失效", error: "爬虫验证异常", expired: "验证结果已过期" };
const freshness = { missing: "尚无快照", cleared: "快照已清空", unknown: "新鲜度未知", fresh: "最近收到快照", stale: "快照已过旧" };
const kind = { login_invalid: "登录失效", validation_expired: "验证过期", snapshot_stale: "长期未同步" };
const state = { open: "待处理", acknowledged: "已确认，仍待恢复", snoozed: "已静默，仍待恢复", awaiting_validation: "等待新版本爬虫验证", resolved: "已恢复" };
const actions = { push: "推送", open_login: "打开登录页面", acknowledge: "确认", snooze: "静默 1 小时", reopen: "重新提醒" };
const known = (map, key, fallback) => Object.hasOwn(map, key) ? map[key] : fallback;

export function sourceText(row) {
  const sync = row.sync || {}, snapshot = row.snapshot || {};
  const lines = [`来源：${row.enabled ? "已启用" : "已暂停"}`,
    `本机授权：${row.approved ? row.permission_granted ? "已授权" : "浏览器权限缺失" : "未授权或已撤销"}`,
    `同步：${known(phase, sync.phase, "未知状态")} (${Number.isInteger(sync.attempts) ? sync.attempts : 0}/5)`,
    `快照：${known(freshness, snapshot.freshness, "新鲜度未知")}`,
    `登录反馈：${known(validation, snapshot.validation, "未验证")}`];
  if (sync.nextAt != null) lines.push(`计划重试：${formatLocalTime(sync.nextAt)}`);
  if (sync.probeAt != null) lines.push(`恢复探测：${formatLocalTime(sync.probeAt)}`);
  if (sync.lastSuccessAt != null) lines.push(`上次成功：${formatLocalTime(sync.lastSuccessAt)}`);
  for (const incident of row.incidents || []) {
    lines.push(`${known(kind, incident.kind, "事件")}：${known(state, incident.status, "需要检查")}`);
    if (incident.status === "snoozed") lines.push(`静默至：${formatLocalTime(incident.snoozed_until)}`);
  }
  return lines.join("\n");
}

export class DashboardView {
  constructor({ document, root, send = message => chrome.runtime.sendMessage(message), onRender = () => {} }) {
    Object.assign(this, { document, root, send, onRender }); this.generation = 0;
  }
  async refresh() {
    const generation = ++this.generation;
    let result;
    try { result = await this.send({ type: "get-dashboard" }); }
    catch { result = null; }
    if (generation !== this.generation || result?.code === "superseded") return;
    this.render(result);
    return result;
  }
  render(result) {
    this.root.replaceChildren();
    const summary = this.document.createElement("p");
    const states = { recent: "最近收到上报", overdue: "上报已超期", not_reported: "尚未上报", unknown: "未知" };
    summary.textContent = `${result?.connection === "connected" ? "接收端已连接" : "接收端不可用"}。` +
      `客户端健康：${known(states, result?.clientHealth?.state, "未知")}` +
      (Number.isFinite(result?.clientHealth?.ageSeconds) ? `（${Math.floor(result.clientHealth.ageSeconds)} 秒前）` : "") +
      `。最近上报不代表浏览器当前在线。时间按 ${localTimeZone()} 显示。`;
    if (result?.ok && !result.capabilities?.includes("client_health")) summary.textContent += " 健康上报需升级接收端至 0.5.0。";
    if (result?.ok && !result.capabilities?.includes("source_incidents")) summary.textContent += " 事件处置需升级接收端至 0.5.0。";
    this.root.append(summary);
    for (const [source, row] of Object.entries(result?.sources || {})) {
      if (!/^[a-z][a-z0-9_-]{0,31}$/.test(source)) continue;
      const group = this.document.createElement("div"), text = this.document.createElement("pre");
      text.textContent = `${source}\n${sourceText(row)}`; group.append(text);
      for (const action of row.actions || []) {
        if (!["push", "open_login"].includes(action)) continue;
        this.button(group, actions[action], { type: action === "push" ? "push-now" : "source-action", source, action });
      }
      for (const incident of row.incidents || []) {
        if (!/^[a-f0-9]{32}$/.test(incident.incident_id || "")) continue;
        for (const action of ["acknowledge", "snooze", "reopen"]) {
          if (!row.actions?.includes(action)) continue;
          this.button(group, `${known(kind, incident.kind, "事件")} · ${actions[action]}`, {
            type: "source-action", source, action, incident_id: incident.incident_id,
            ...(action === "snooze" ? { duration_seconds: 3600 } : {}),
          });
        }
      }
      this.root.append(group);
    }
    this.onRender(result);
  }
  button(group, text, message) {
    const button = this.document.createElement("button");
    button.type = "button"; button.textContent = text;
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        const result = await this.send(message);
        if (!result?.ok) throw new Error("operation_failed");
        await this.refresh();
      } catch { button.textContent = "操作失败，请刷新后检查连接和配置"; }
      finally { button.disabled = false; }
    });
    group.append(button);
  }
}

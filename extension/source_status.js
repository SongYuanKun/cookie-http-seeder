/** Display-only summaries: no credentials, raw receiver errors or HTML rendering. */
import { formatLocalTime } from "./time.js";

const PHASES = {
  pending: "等待同步", syncing: "正在同步", retrying: "等待重试", succeeded: "最近同步成功",
  blocked: "需要处理", exhausted: "重试已耗尽", paused: "已暂停",
};
const FRESHNESS = { missing: "尚无快照", cleared: "快照已清空", unknown: "新鲜度未知",
  fresh: "最近收到快照", stale: "快照已过旧" };
const VALIDATION = { unverified: "未验证", valid: "爬虫最近报告有效", invalid: "爬虫报告登录失效",
  error: "爬虫验证异常", expired: "验证结果已过期" };
const REASONS = { logged_in: "已登录", login_required: "需要登录", session_expired: "会话过期",
  account_mismatch: "账号不匹配", network_error: "网络异常", rate_limited: "受到限流",
  unexpected_response: "响应异常", manual_reset: "手动重置" };
const known = (map, key, fallback) => Object.hasOwn(map, key) ? map[key] : fallback;

export function sourceStatus(spec, {
  approved = false, permission = false, job = null, remote = null,
  remoteAvailable = false, queueAvailable = true,
} = {}) {
  const lines = [spec.enabled ? "来源：已启用" : "来源：已暂停"];
  lines.push(`本机授权：${approved ? permission === true ? "已授权" : "浏览器权限缺失" : "未授权或已撤销"}`);
  const phase = job && typeof job === "object" ? job.phase : null;
  lines.push(`同步：${!approved ? "不会采集" : !queueAvailable ? "无法读取队列" :
    phase ? known(PHASES, phase, "未知状态") : "尚未推送"}`);
  if (job && typeof job.nextAt === "number" && ["pending", "retrying"].includes(phase)) {
    lines.push(`下次尝试：${formatLocalTime(job.nextAt)}`);
  }
  let warning = !approved || permission !== true || !queueAvailable ||
    ["blocked", "exhausted"].includes(phase);
  if (!remoteAvailable || !remote || typeof remote !== "object") {
    lines.push("接收端：状态不可用", "登录反馈：无法读取（不会沿用旧结果）");
    return { text: lines.join("\n"), warning: true };
  }
  if (remote.error || remote.syncError) {
    lines.push("接收端：状态文件异常，请运行 doctor"); warning = true;
  }
  lines.push(`快照：${known(FRESHNESS, remote.freshness, "新鲜度未知")}`);
  if (Number.isInteger(remote.cookieCount) && remote.cookieCount >= 0) {
    lines.push(`Cookie 数量：${remote.cookieCount}`);
  }
  lines.push(`最近接收：${formatLocalTime(typeof remote.lastSeenAt === "string" ? remote.lastSeenAt : null)}`);
  lines.push(`内容更新：${formatLocalTime(typeof remote.updatedAt === "string" ? remote.updatedAt : null)}`);
  // "valid" is a bounded crawler observation, not a claim made from file presence.
  const validation = remote.validationDetail?.origin === "crawler_report" &&
    remote.present === true && !remote.cleared ? remote.validation : "unverified";
  lines.push(`登录反馈：${known(VALIDATION, validation, "未验证")}`);
  if (validation !== "unverified") {
    const reason = known(REASONS, remote.validationDetail?.reasonCode, null);
    if (reason) lines.push(`反馈原因：${reason}`);
  }
  warning ||= remote.freshness !== "fresh" || ["invalid", "error", "expired"].includes(validation);
  return { text: lines.join("\n"), warning };
}

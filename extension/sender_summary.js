/** Read-only metadata overview. Never render arbitrary receiver fields. */
import { senderTag } from "./senders.js";
import { formatLocalTime } from "./time.js";

const FRESHNESS = { fresh: "最近同步", stale: "快照过旧", missing: "尚无快照",
  cleared: "已清空", unknown: "新鲜度未知" };
const VALIDATION = { valid: "爬虫最近报告有效", invalid: "爬虫报告登录失效",
  expired: "验证已过期", error: "验证异常", unverified: "未验证" };
export function senderSummary(document) {
  const lines = [];
  for (const [tag, sender] of Object.entries(document?.senders || {}).slice(0, 101)) {
    try { senderTag(tag); } catch { continue; }
    lines.push(`发送端 ${tag}`);
    if (!sender || sender.error || !sender.sources || typeof sender.sources !== "object") {
      lines.push("  状态不可读，请检查此标签配置"); continue;
    }
    for (const [source, status] of Object.entries(sender.sources).slice(0, 100)) {
      if (!/^[a-z][a-z0-9_-]{0,31}$/.test(source) || !status || typeof status !== "object") continue;
      const freshness = FRESHNESS[status.freshness] || FRESHNESS.unknown;
      const verified = status.present === true && !status.cleared &&
        status.validationDetail?.origin === "crawler_report";
      const validation = verified ? VALIDATION[status.validation] || VALIDATION.unverified : VALIDATION.unverified;
      lines.push(`  ${source}：${freshness}；${validation}；最近接收 ${formatLocalTime(status.lastSeenAt)}`);
    }
  }
  return lines.join("\n") || "尚无可读取的发送端状态";
}

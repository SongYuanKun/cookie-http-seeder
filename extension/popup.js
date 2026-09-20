import { formatLocalTime, localTimeZone } from "./time.js";

const status = document.getElementById("status");
const labels = { pending: "等待同步", syncing: "正在同步", retrying: "等待重试", succeeded: "已同步",
  blocked: "需要处理", exhausted: "重试已耗尽", paused: "已暂停" };
async function refresh() {
  const response = await chrome.runtime.sendMessage({ type: "sync-status" });
  if (!response?.ok) throw new Error("无法读取同步队列");
  const jobs = response.queue?.jobs || {};
  status.textContent = Object.entries(jobs).map(([source, job]) => {
    const retry = job.nextAt != null ? `\n  计划重试：${formatLocalTime(job.nextAt)}` : "";
    const success = job.lastSuccessAt != null ? `\n  上次成功：${formatLocalTime(job.lastSuccessAt)}` : "";
    return `${source}: ${labels[job.phase] || job.phase} (${job.attempts}/5)${retry}${success}` +
      (job.errorCode ? `\n  ${job.errorCode}` : "");
  }).join("\n") || "没有待处理任务。请先管理网站并授权。";
  if (Object.keys(jobs).length) status.textContent += `\n时间按 ${localTimeZone()} 显示`;
  status.className = Object.values(jobs).some(j => ["blocked", "exhausted"].includes(j.phase)) ? "error" : "ok";
}
document.getElementById("manage").addEventListener("click", () => chrome.runtime.openOptionsPage());
document.getElementById("refresh").addEventListener("click", () => refresh().catch(() => { status.textContent = "读取失败"; }));
document.getElementById("push").addEventListener("click", async event => {
  event.target.disabled = true; status.textContent = "正在同步；临时故障会排队重试…";
  try {
    const response = await chrome.runtime.sendMessage({ type: "push-now" });
    if (!response?.ok) throw new Error(response?.error || "推送失败");
    await refresh();
  } catch (error) { status.textContent = error.message; status.className = "error"; }
  finally { event.target.disabled = false; }
});
chrome.storage.onChanged.addListener((_changes, area) => {
  if (area === "local") refresh().catch(() => {});
});
refresh().catch(() => { status.textContent = "无法读取扩展状态"; });

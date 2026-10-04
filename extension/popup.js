import { DashboardView } from "./dashboard_view.js";
const status = document.getElementById("status");
const view = new DashboardView({ document, root: document.getElementById("health-dashboard") });
async function refresh() { await view.refresh(); status.textContent = ""; }
document.getElementById("manage").addEventListener("click", () => chrome.runtime.openOptionsPage());
document.getElementById("refresh").addEventListener("click", () => refresh());
document.getElementById("push").addEventListener("click", async event => {
  event.target.disabled = true; status.textContent = "正在同步…";
  try {
    const result = await chrome.runtime.sendMessage({ type: "push-now" });
    if (!result?.ok) throw new Error("operation_failed");
    await refresh();
  } catch { status.textContent = "推送失败，请检查连接并刷新状态"; }
  finally { event.target.disabled = false; }
});
chrome.storage.onChanged.addListener((_changes, area) => { if (area === "local") refresh(); });
refresh();

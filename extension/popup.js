import { loadSettings, saveSettings } from "./shared.js";

const endpointEl = document.getElementById("endpoint");
const tokenEl = document.getElementById("token");
const autoEl = document.getElementById("autoPushMinutes");
const statusEl = document.getElementById("status");
const saveBtn = document.getElementById("save");
const pushBtn = document.getElementById("push");

function setStatus(text, kind = "") {
  statusEl.textContent = text;
  statusEl.className = kind;
}

async function refreshForm() {
  const settings = await loadSettings();
  endpointEl.value = settings.endpoint || "";
  tokenEl.value = settings.token || "";
  autoEl.value = String(settings.autoPushMinutes ?? 0);
  const stored = await chrome.storage.local.get(["lastPushAt", "lastPushResults"]);
  if (stored.lastPushAt) {
    const lines = [`Last push: ${stored.lastPushAt}`];
    const results = stored.lastPushResults || {};
    for (const [source, result] of Object.entries(results)) {
      lines.push(
        result.ok
          ? `✓ ${source} (${result.cookieCount || "?"} cookies)`
          : `✗ ${source}: ${result.error}`,
      );
    }
    setStatus(
      lines.join("\n"),
      Object.values(results).every((item) => item.ok) ? "ok" : "error",
    );
  }
}

saveBtn.addEventListener("click", async () => {
  const autoPushMinutes = Math.max(0, Number(autoEl.value) || 0);
  await saveSettings({
    endpoint: endpointEl.value.trim(),
    token: tokenEl.value.trim(),
    autoPushMinutes,
  });
  const response = await chrome.runtime.sendMessage({ type: "settings-saved" });
  setStatus(response?.ok ? "Saved" : response?.error || "Save failed", response?.ok ? "ok" : "error");
});

pushBtn.addEventListener("click", async () => {
  pushBtn.disabled = true;
  setStatus("Pushing…");
  try {
    await saveSettings({
      endpoint: endpointEl.value.trim(),
      token: tokenEl.value.trim(),
      autoPushMinutes: Math.max(0, Number(autoEl.value) || 0),
    });
    const response = await chrome.runtime.sendMessage({ type: "push-now" });
    if (!response?.ok) {
      setStatus(response?.error || "Push failed", "error");
      return;
    }
    const lines = [];
    for (const [source, result] of Object.entries(response.results || {})) {
      lines.push(
        result.ok
          ? `✓ ${source} @ ${result.updatedAt || "?"}`
          : `✗ ${source}: ${result.error}`,
      );
    }
    const allOk = Object.values(response.results || {}).every((item) => item.ok);
    setStatus(lines.join("\n") || "No result", allOk ? "ok" : "error");
  } catch (error) {
    setStatus(String(error?.message || error), "error");
  } finally {
    pushBtn.disabled = false;
  }
});

refreshForm();

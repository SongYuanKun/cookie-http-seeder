/** Headed Windows Chrome acceptance using the released, unmodified extension. */
import { spawn, execFileSync } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { isDeepStrictEqual } from 'node:util';

const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const report = {
  platform: process.platform, profile_isolated: true, real_site_login_tested: false,
  credentials: 'synthetic receiver token only', headless: false, passed: false,
};
let browser, socket, server, rpc, profile;
let sequence = 0;
const pending = new Map();
let optionsSession;
function requireTrue(value, code) {
  if (value !== true) throw new Error(code);
}
async function waitFor(check, code, milliseconds = 10000) {
  const deadline = Date.now() + milliseconds;
  while (Date.now() < deadline) {
    if (await check()) return true;
    await delay(100);
  }
  throw new Error(code);
}
async function evaluate(sessionId, expression) {
  const result = await rpc('Runtime.evaluate', {
    expression, awaitPromise: true, returnByValue: true,
  }, sessionId);
  if (result.exceptionDetails) throw new Error('extension_evaluation_failed');
  return result.result.value;
}
async function attach(url) {
  const { targetId } = await rpc('Target.createTarget', { url, newWindow: true, width: 1200, height: 900 });
  const { sessionId } = await rpc('Target.attachToTarget', { targetId, flatten: true });
  await rpc('Page.enable', {}, sessionId);
  return { targetId, sessionId };
}
async function click(sessionId, expression) {
  const position = await evaluate(sessionId, `(()=>{const b=${expression};if(!b||b.disabled)throw Error('button unavailable');b.scrollIntoView({block:'center'});const r=b.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()`);
  await rpc('Input.dispatchMouseEvent', { type: 'mousePressed', button: 'left', clickCount: 1, ...position }, sessionId);
  await rpc('Input.dispatchMouseEvent', { type: 'mouseReleased', button: 'left', clickCount: 1, ...position }, sessionId);
}
const sourceButton = text => `[...document.querySelectorAll('#sources button')].find(b=>b.textContent===${JSON.stringify(text)})`;
const pattern = '*://*.example.com/*';
const contains = `chrome.permissions.contains({origins:[${JSON.stringify(pattern)}]})`;

try {
  report.stage = 'preflight';
  if (process.platform !== 'win32') throw new Error('windows_required');
  const extension = path.resolve(process.env.NATIVE_EXTENSION_PATH || '');
  const manifest = JSON.parse(fs.readFileSync(path.join(extension, 'manifest.json'), 'utf8'));
  if (manifest.version !== '0.5.0') throw new Error('release_version_mismatch');
  const chrome = path.join(process.env.ProgramFiles, 'Google', 'Chrome', 'Application', 'chrome.exe');
  if (!fs.existsSync(chrome)) throw new Error('official_chrome_missing');
  profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cookie-native-windows-'));
  const revision = 'a'.repeat(64);
  const syntheticToken = 'synthetic-native-token-123456';
  const sources = { native_example: { label: 'Native acceptance', domains: ['example.com'], target_url: 'https://example.com/', enabled: true } };
  server = http.createServer((request, response) => {
    if (request.headers.authorization !== `Bearer ${syntheticToken}`) {
      report.unexpected_receiver_authentication = true;
      response.writeHead(401, { 'Content-Type': 'application/json' }).end(JSON.stringify({ ok: false, error: 'unauthorized' }));
      return;
    }
    // No mutations or real-site traffic are part of this acceptance fixture.
    if (request.method !== 'GET') {
      report.unexpected_receiver_mutation = true;
      response.writeHead(405).end(); return;
    }
    const url = new URL(request.url, 'http://127.0.0.1');
    let body;
    if (url.pathname === '/v1/sources') body = { ok: true, protocol_version: 2, revision, capabilities: ['conditional_snapshots'], sources };
    else if (url.pathname === '/v1/status') body = { ok: true, config_revision: revision, sources: { native_example: { present: false, validation: 'unverified', freshness: 'missing' } } };
    else { response.writeHead(404).end(); return; }
    response.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify(body));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const endpoint = `http://127.0.0.1:${server.address().port}`;
  browser = spawn(chrome, [
    `--user-data-dir=${profile}`, '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1',
    '--enable-unsafe-extension-debugging', '--force-renderer-accessibility', '--lang=en-US',
    '--no-first-run', '--no-default-browser-check', 'about:blank',
  ], { stdio: 'ignore' });
  let launchFailed = false;
  browser.on('error', () => { launchFailed = true; });
  const activePort = path.join(profile, 'DevToolsActivePort');
  report.stage = 'native_browser_launch';
  await waitFor(() => {
    if (launchFailed) throw new Error('chrome_start_failed');
    return fs.existsSync(activePort);
  }, 'debugging_endpoint_unavailable', 20000);
  const [port, route] = fs.readFileSync(activePort, 'utf8').trim().split(/\r?\n/);
  if (!/^\d+$/.test(port) || +port < 1 || +port > 65535 || !/^\/devtools\/browser\/[a-z0-9-]+$/i.test(route)) throw new Error('invalid_owned_debugging_endpoint');
  socket = new WebSocket(`ws://127.0.0.1:${port}${route}`);
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true });
    socket.addEventListener('error', () => reject(new Error('debugging_connection_failed')), { once: true });
  });
  rpc = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error('debugging_command_timeout')); }, 10000);
    pending.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });
  socket.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      const request = pending.get(message.id); pending.delete(message.id); clearTimeout(request.timer);
      if (message.error) request.reject(new Error('debugging_command_rejected'));
      else request.resolve(message.result);
    }
    if (message.method === 'Page.javascriptDialogOpening') {
      const expected = report.stage === 'extension_ui_revoke' && message.sessionId === optionsSession &&
        message.params.type === 'confirm' && message.params.message.startsWith('停止本机同步 native_example？');
      report.revocation_confirmed = expected;
      rpc('Page.handleJavaScriptDialog', { accept: expected }, message.sessionId).catch(() => {});
    }
  });
  report.chrome = (await rpc('Browser.getVersion')).product;
  report.os_version = os.release();
  report.stage = 'developer_mode';
  const manager = await attach('chrome://extensions/');
  const toggle = `document.querySelector('extensions-manager')?.shadowRoot?.querySelector('extensions-toolbar')?.shadowRoot?.querySelector('#devMode')`;
  await waitFor(() => evaluate(manager.sessionId, `Boolean(${toggle})`), 'developer_mode_control_missing');
  await click(manager.sessionId, toggle);
  report.developer_mode_enabled = await evaluate(manager.sessionId, `${toggle}.checked===true`);
  requireTrue(report.developer_mode_enabled, 'developer_mode_not_enabled');
  report.stage = 'release_installation';
  const { id } = await rpc('Extensions.loadUnpacked', { path: extension });
  const options = await attach(`chrome-extension://${id}/options.html`);
  optionsSession = options.sessionId;
  await waitFor(() => evaluate(optionsSession, 'typeof chrome.runtime?.getManifest==="function"').catch(() => false), 'options_context_unavailable');
  report.runtime_version = await evaluate(optionsSession, 'chrome.runtime.getManifest().version');
  report.initial_permission_denied = !(await evaluate(optionsSession, contains));
  requireTrue(report.runtime_version === '0.5.0' && report.initial_permission_denied, 'initial_installation_gate_failed');
  await evaluate(optionsSession, `(async()=>{const {saveSettings}=await import('./settings.js');await saveSettings({endpoint:${JSON.stringify(endpoint)},token:${JSON.stringify(syntheticToken)},senderTag:'default',autoPushMinutes:0,syncOnChange:false,recoveryProbeMinutes:0,loginPollMinutes:0,healthReportMinutes:0});await chrome.storage.local.set({nativeAcceptanceMarker:'synthetic-owned-profile'});return true;})()`);
  // Reload the page to initialize its actual receiver/source UI from those settings.
  await rpc('Page.reload', {}, optionsSession);
  const ready = s => waitFor(() => evaluate(s, `document.getElementById('status')?.textContent.startsWith('已加载配置')===true`).catch(() => false), 'source_ui_not_ready');
  await ready(optionsSession);
  report.stage = 'extension_ui_permission_request';
  await rpc('Target.activateTarget', { targetId: options.targetId });
  await evaluate(optionsSession, `document.addEventListener('click',e=>{window.nativeAcceptanceTrusted=e.isTrusted;},{once:true,capture:true});true`);
  await click(optionsSession, sourceButton('仅授权此来源'));
  report.trusted_extension_click = await evaluate(optionsSession, 'window.nativeAcceptanceTrusted===true');
  requireTrue(report.trusted_extension_click, 'untrusted_permission_click');
  report.stage = 'native_permission_consent';
  const helper = fileURLToPath(new URL('./windows_native_consent.ps1', import.meta.url));
  const native = JSON.parse(execFileSync('powershell.exe', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', helper, '-ChromeProcessId', String(browser.pid)], { encoding: 'utf8', timeout: 20000 }).trim());
  report.native_consent = native;
  requireTrue(native.invoked, 'native_consent_not_invoked');
  await waitFor(() => evaluate(optionsSession, contains), 'native_permission_not_granted');
  report.permission_granted = true;
  const approved = s => evaluate(s, `(async()=>{const {loadSettings,sourceApproved}=await import('./settings.js');return sourceApproved(await loadSettings(),'native_example',${JSON.stringify(sources.native_example)});})()`);
  await waitFor(() => approved(optionsSession), 'source_approval_not_saved');
  report.approval_saved = true;
  await waitFor(() => evaluate(optionsSession, `document.getElementById('status')?.textContent.startsWith('native_example 已授权')===true`), 'source_approval_not_finished');
  const settingsSnapshot = `(async()=>{const {DEFAULTS}=await import('./settings.js');return chrome.storage.local.get([...Object.keys(DEFAULTS),'nativeAcceptanceMarker']);})()`;
  const beforeReload = await evaluate(optionsSession, settingsSnapshot);
  report.stage = 'native_manager_reload';
  const locate = `(()=>{const walk=root=>{for(const e of root.querySelectorAll('*')){if(e.tagName==='EXTENSIONS-ITEM'&&(e.data?.id==='${id}'||e.id==='${id}'))return e;if(e.shadowRoot){const found=walk(e.shadowRoot);if(found)return found;}}};return walk(document);})()`;
  await waitFor(() => evaluate(manager.sessionId, `Boolean(${locate}?.shadowRoot?.querySelector('#dev-reload-button'))`), 'native_reload_control_missing');
  await click(manager.sessionId, `${locate}.shadowRoot.querySelector('#dev-reload-button')`);
  report.native_reload_clicked = true;
  await waitFor(() => evaluate(optionsSession, 'typeof chrome.runtime.getManifest!=="function"').catch(() => true), 'old_context_not_invalidated');
  await waitFor(() => evaluate(manager.sessionId, `(()=>{const d=${locate}?.data;return d?.state==='ENABLED'&&d.disableReasons?.reloading!==true;})()`), 'native_reload_not_finished');
  // The manager must finish before opening the replacement options page.
  const next = await attach(`chrome-extension://${id}/options.html`);
  optionsSession = next.sessionId;
  await ready(optionsSession);
  report.reload_version = await evaluate(optionsSession, 'chrome.runtime.getManifest().version');
  report.settings_preserved = isDeepStrictEqual(beforeReload, await evaluate(optionsSession, settingsSnapshot));
  report.permission_preserved = await evaluate(optionsSession, contains);
  report.approval_preserved = await approved(optionsSession);
  requireTrue(report.reload_version === '0.5.0' && report.settings_preserved && report.permission_preserved && report.approval_preserved, 'reload_preservation_gate_failed');
  report.stage = 'extension_ui_revoke';
  await rpc('Target.activateTarget', { targetId: next.targetId });
  await click(optionsSession, sourceButton('撤销本机授权'));
  await waitFor(async () => !(await evaluate(optionsSession, contains)), 'permission_not_revoked');
  report.permission_revoked = true;
  report.approval_removed = await evaluate(optionsSession, `(async()=>{const v=await chrome.storage.local.get('approvedSources');return !Object.hasOwn(v.approvedSources||{},'native_example');})()`);
  requireTrue(report.revocation_confirmed && report.approval_removed && !report.unexpected_receiver_mutation && !report.unexpected_receiver_authentication, 'revocation_gate_failed');
  report.stage = 'complete';
  report.passed = true;
} catch (error) {
  report.error_code = /^[a-z_]+$/.test(error.message) ? error.message : 'acceptance_gate_failed';
} finally {
  if (rpc && socket?.readyState === WebSocket.OPEN) {
    try { await rpc('Browser.close'); } catch { /* The owned browser may close before replying. */ }
  }
  socket?.close();
  for (const request of pending.values()) { clearTimeout(request.timer); request.reject(new Error('owned_browser_closed')); }
  pending.clear();
  try {
    if (browser?.pid) {
      const exited = () => browser.exitCode !== null || browser.signalCode !== null;
      const deadline = Date.now() + 2000;
      while (!exited() && Date.now() < deadline) await delay(100);
      if (!exited()) {
        execFileSync('taskkill.exe', ['/PID', String(browser.pid), '/T', '/F'], { stdio: 'ignore', timeout: 5000 });
        await waitFor(exited, 'owned_browser_cleanup_failed', 5000);
      }
    }
    if (profile) fs.rmSync(profile, { recursive: true, force: true, maxRetries: 3, retryDelay: 200 });
    report.owned_profile_cleaned = true;
  } catch {
    report.owned_profile_cleaned = false;
    report.passed = false;
    report.error_code ||= 'owned_browser_cleanup_failed';
  }
  if (server) { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
  const encoded = JSON.stringify(report, null, 2);
  if (process.env.NATIVE_REPORT_PATH) fs.writeFileSync(process.env.NATIVE_REPORT_PATH, encoded + '\n');
  if (process.env.GITHUB_STEP_SUMMARY) fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, `## Windows native Chrome acceptance\n\n\`\`\`json\n${encoded}\n\`\`\`\n`);
  console.log(encoded);
  process.exitCode = report.passed ? 0 : 1;
}

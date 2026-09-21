/** Serialize settings read-modify-write across extension pages/workers. */
let pending = Promise.resolve();
export function withSettingsLock(action) {
  const locks = globalThis.navigator?.locks;
  if (locks?.request) return locks.request("cookie-http-seeder-settings", action);
  // Node's unit-test environment has no Web Locks. Chrome 120+ uses the branch above.
  const result = pending.then(action);
  pending = result.catch(() => {});
  return result;
}

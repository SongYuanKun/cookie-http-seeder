import { allowedDomain, domainMatches, domainName, sourceOrigins } from "./settings.js";
export async function collectSourceCookies(spec) {
  const origins = sourceOrigins(spec);
  if (!await chrome.permissions.contains({ origins })) throw new Error("Site permission missing; authorize in Manage sites");
  const byIdentity = new Map();
  // Query each root once to preserve Chrome creation order within overlapping scopes.
  const roots = spec.domains.filter(d => !spec.domains.some(p => d !== p && domainMatches(d, p)));
  for (const domain of roots) {
    const batch = await chrome.cookies.getAll({ domain }); // current store, non-partitioned only
    for (const cookie of batch) {
      const host = domainName(cookie.domain);
      if (!allowedDomain(host, spec.domains)) throw new Error("Cookie outside allow-list");
      if (cookie.partitionKey != null) throw new Error("Partitioned cookies are unsupported in phase 1");
      const key = JSON.stringify([host, cookie.path, cookie.name, cookie.storeId]);
      const previous = byIdentity.get(key);
      if (previous && JSON.stringify(previous) !== JSON.stringify(cookie)) throw new Error("Conflicting duplicate cookie identity");
      byIdentity.set(key, cookie);
    }
  }
  if (!await chrome.permissions.contains({ origins })) throw new Error("Site permission changed during collection");
  const cookies = [...byIdentity.values()];
  if (new Set(cookies.map(c => c.storeId)).size > 1) throw new Error("Mixed stores are unsupported in phase 1");
  return cookies;
}

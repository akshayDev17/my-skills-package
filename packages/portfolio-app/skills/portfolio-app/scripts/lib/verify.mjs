// The login check: does a path answer with a redirect to Cloudflare Access?
//
// It needs no token on purpose. A deploy job runs it before anything else, with no
// secrets, so a sub-app whose path is not behind Access is refused before it ships.
// Both the bare prefix and a path under it are probed, because the Access app has
// two destinations (`<prefix>` and `<prefix>/*`) and either one can be missing.

const PROBE_SUBPATH = "portfolio-app-verify";

export function probeUrls(domain, prefix) {
  return [`https://${domain}${prefix}`, `https://${domain}${prefix}/${PROBE_SUBPATH}`];
}

/** One request, no redirect following: `{ url, ok, status, reason }`. */
export async function checkAccessRedirect({ fetch, url, team }) {
  let res;
  try {
    res = await fetch(url, { method: "GET", redirect: "manual" });
  } catch (err) {
    return { url, ok: false, status: null, reason: `request failed: ${err.message}` };
  }
  const status = res.status;
  const location = res.headers.get("location");
  if (status < 300 || status >= 400 || !location) {
    return { url, ok: false, status, reason: `answered ${status} — not behind Access` };
  }
  let target;
  try {
    target = new URL(location, url);
  } catch {
    return { url, ok: false, status, reason: `redirects to an unparseable location` };
  }
  const host = target.hostname;
  const expectedHost = team ? `${team}.cloudflareaccess.com` : null;
  const isAccessHost = expectedHost ? host === expectedHost : host.endsWith(".cloudflareaccess.com");
  if (!isAccessHost || !target.pathname.startsWith("/cdn-cgi/access/login/")) {
    const want = expectedHost ?? "<team>.cloudflareaccess.com";
    return { url, ok: false, status, reason: `redirects to ${host}${target.pathname}, not ${want}/cdn-cgi/access/login/` };
  }
  return { url, ok: true, status, reason: `${status} → ${host}` };
}

/** Every probe URL for the prefix must redirect to Access. */
export async function verifyPath({ fetch, domain, prefix, team }) {
  const results = [];
  for (const url of probeUrls(domain, prefix)) results.push(await checkAccessRedirect({ fetch, url, team }));
  return { ok: results.every((r) => r.ok), results };
}

/** Access takes a few seconds to start answering for a new app; poll before calling it a failure. */
export async function waitForAccess({ fetch, domain, prefix, team, attempts = 12, delayMs = 5000, sleep }) {
  let last;
  for (let attempt = 1; attempt <= attempts; attempt++) {
    last = await verifyPath({ fetch, domain, prefix, team });
    if (last.ok || attempt === attempts) return last;
    await sleep(delayMs);
  }
  return last;
}

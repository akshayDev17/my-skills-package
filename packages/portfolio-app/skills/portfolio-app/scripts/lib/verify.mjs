// The login check: does a path answer with a redirect to Cloudflare Access?
//
// It needs no token on purpose. A deploy job runs it before anything else, with no
// secrets, so a sub-app whose path is not behind Access is refused before it ships.
// Both the bare prefix and a path under it are probed, because the Access app has
// two destinations (`<prefix>` and `<prefix>/*`) and either one can be missing.
//
// A mixed app (signed in by default, with named public paths) is probed both ways:
// every private probe must redirect to the Access login, and every listed public path
// must answer signed out. Its private probes add `<prefix>/api/…`, since an app's API
// is the path a too-wide public entry would most likely expose, and a child of every
// public path listed without a `/*`: Cloudflare documents that a sub-path with no rule
// of its own inherits its parent's, so only a probe shows whether the children of an
// exact public path still ask for sign-in.

import { pathCovers } from "./prefix.mjs";

const PROBE_SUBPATH = "portfolio-app-verify";

export function probeUrls(domain, prefix) {
  return [`https://${domain}${prefix}`, `https://${domain}${prefix}/${PROBE_SUBPATH}`];
}

/**
 * A mixed app's probes, each `{ url, expect }` with `expect` "access" or "public". The
 * private ones are the bare prefix, a child, an API child and a child of each public
 * path listed without a `/*`, less any a public path covers; each public path is probed
 * as itself, or a child of it for a `/*` one.
 */
export function mixedProbes(domain, prefix, publicPaths) {
  const exactChildren = publicPaths.filter((p) => !p.endsWith("/*")).map((p) => `${p}/${PROBE_SUBPATH}`);
  const privatePaths = [...new Set([prefix, `${prefix}/${PROBE_SUBPATH}`, `${prefix}/api/${PROBE_SUBPATH}`, ...exactChildren])]
    .filter((path) => !publicPaths.some((p) => pathCovers(p, path)));
  const publicProbes = publicPaths.map((p) => (p.endsWith("/*") ? `${p.slice(0, -1)}${PROBE_SUBPATH}` : p));
  return [
    ...privatePaths.map((path) => ({ url: `https://${domain}${path}`, expect: "access" })),
    ...publicProbes.map((path) => ({ url: `https://${domain}${path}`, expect: "public" })),
  ];
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

/**
 * The opposite check, for a public path: it must answer without any Access login
 * redirect (of any team). A bare 401 or 403 also fails: it may be Access refusing the
 * request, or Cloudflare's bot protection refusing an automated client, and from
 * here the two look the same — so that path is reported for a check in a browser.
 */
export async function checkSignedOutAnswer({ fetch, url }) {
  const access = await checkAccessRedirect({ fetch, url });
  if (access.status === null) return { ...access, expect: "public" };
  if (access.ok) return { url, ok: false, status: access.status, expect: "public", reason: `${access.reason} — public path still asks for sign-in` };
  if (access.status === 401 || access.status === 403) {
    return { url, ok: false, status: access.status, expect: "public",
      reason: `answered a bare ${access.status} — Access or Cloudflare's bot protection refused a signed-out request; open it in a private browser window to tell which` };
  }
  return { url, ok: true, status: access.status, expect: "public", reason: `answered ${access.status} signed out — no Access redirect` };
}

/**
 * Every probe URL for the prefix must redirect to Access. With `publicPaths` (a mixed
 * app), the private probes must redirect and the public ones must answer signed out.
 */
export async function verifyPath({ fetch, domain, prefix, team, publicPaths = [] }) {
  const results = [];
  if (publicPaths.length === 0) {
    for (const url of probeUrls(domain, prefix)) results.push(await checkAccessRedirect({ fetch, url, team }));
    return { ok: results.every((r) => r.ok), results };
  }
  for (const probe of mixedProbes(domain, prefix, publicPaths)) {
    results.push(probe.expect === "access"
      ? { ...(await checkAccessRedirect({ fetch, url: probe.url, team })), expect: "access" }
      : await checkSignedOutAnswer({ fetch, url: probe.url }));
  }
  const privateProbes = results.filter((r) => r.expect === "access").length;
  return { ok: privateProbes > 0 && results.every((r) => r.ok), results };
}

/**
 * Access takes a few seconds to start answering for a new app; poll before calling it a
 * failure. A mixed app has two apps that start answering at different moments, so a
 * round can pass on private answers taken before the Bypass app was live. Its public
 * answers come after its private ones, so by the end of a passing round both apps are
 * answering, and a mixed app passes only when a second round, run at once, passes too.
 * A second round that fails is one more failed attempt: polling goes on.
 */
export async function waitForAccess({ fetch, domain, prefix, team, publicPaths = [], attempts = 12, delayMs = 5000, sleep }) {
  const round = () => verifyPath({ fetch, domain, prefix, team, publicPaths });
  let last;
  for (let attempt = 1; attempt <= attempts; attempt++) {
    last = await round();
    if (last.ok && publicPaths.length > 0) last = await round();
    if (last.ok || attempt === attempts) return last;
    await sleep(delayMs);
  }
  return last;
}

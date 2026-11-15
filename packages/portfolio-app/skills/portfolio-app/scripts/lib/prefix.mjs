// The one place that decides which path prefixes a sub-app may claim.
//
// Every sub-app of the site lives under its own prefix, and its Access app guards
// exactly `<domain>/<prefix>` and `<domain>/<prefix>/*` (a public app's Worker route is
// the one `<domain>/<prefix>*`, see `workerRoute`). A prefix that is empty or
// `/` would put the whole site behind one sub-app's login; one under
// ai-chat-archive's site-wide `/api`, `/_next` or `/vendors` would lock the archive's
// own assets and API behind another app's policy.

const RESERVED_FIRST_SEGMENTS = ["api", "_next", "vendors"];
const SEGMENT = "[a-z0-9]+(?:[-_][a-z0-9]+)*";
const PREFIX_SHAPE = new RegExp(`^/${SEGMENT}(?:/${SEGMENT})*$`);

/** Returns the prefix as `/<segment>[/<segment>…]`, or throws saying why it is refused. */
export function normalizePrefix(raw) {
  const input = String(raw ?? "").trim();
  if (input === "" || /^\/+$/.test(input)) {
    throw new Error(`refusing path "${input}": a sub-app needs its own prefix, not the site root`);
  }
  const prefix = (input.startsWith("/") ? input : `/${input}`).replace(/\/$/, "");
  const first = prefix.split("/")[1];
  if (RESERVED_FIRST_SEGMENTS.includes(first)) {
    throw new Error(
      `refusing path "${prefix}": /${first} is ai-chat-archive's site-wide route (reserved: ${RESERVED_FIRST_SEGMENTS.map((s) => `/${s}`).join(", ")})`,
    );
  }
  if (!PREFIX_SHAPE.test(prefix)) {
    throw new Error(
      `refusing path "${prefix}": use lowercase letters, digits, "-" or "_" in each segment — no wildcards, dots, queries or empty segments`,
    );
  }
  return prefix;
}

/** The exact two Access destinations a sub-app at `prefix` gets — nothing wider. */
export function accessDestinations(domain, prefix) {
  return [`${domain}${prefix}`, `${domain}${prefix}/*`];
}

const PUBLIC_SEGMENT = /^[A-Za-z0-9._~-]+$/;

/**
 * A mixed app's public path, as `/<prefix>`, `/<prefix>/`, `/<prefix>/<path>` or
 * `/<prefix>/<path>/*`, or throws saying why it is refused. Public paths are always named,
 * never inferred, and may not reach outside the app's own prefix or cover all of it (that
 * is `--public`). `/<prefix>/` (the prefix's trailing-slash path, exactly) is the one path
 * with an empty segment; it is an exact path like the others.
 */
export function normalizePublicPath(raw, prefix) {
  const input = String(raw ?? "").trim();
  const path = `/${input.replace(/^\/+/, "")}`;
  if (path === `${prefix}/`) return path;
  const refuse = (why) => new Error(`refusing public path "${input}": ${why}`);
  const wildcard = path.endsWith("/*");
  const body = wildcard ? path.slice(0, -2) : path;
  const segments = body.split("/").slice(1);
  if (path.includes("*") && (!wildcard || body.includes("*"))) {
    throw refuse(`the only wildcard allowed is a final "/*"`);
  }
  if (segments.some((s) => !PUBLIC_SEGMENT.test(s) || s === "." || s === "..")) {
    throw refuse(`use letters, digits, ".", "_", "~" or "-" in each segment — no empty, "." or ".." segments, queries or spaces (the one trailing "/" allowed is ${prefix}/ itself)`);
  }
  if (body !== prefix && !body.startsWith(`${prefix}/`)) {
    throw refuse(`it is outside the app's prefix ${prefix}`);
  }
  if (wildcard && body === prefix) {
    throw refuse(`${prefix}/* is the whole app; an app anyone can reach is --public, not a public path`);
  }
  return path;
}

/** Whether a destination path (`/a/b` or `/a/b/*`) covers the request path `path`. */
export function pathCovers(destination, path) {
  return destination.endsWith("/*") ? path.startsWith(destination.slice(0, -1)) : path === destination;
}

/**
 * A mixed app's base Allow destinations: the usual two, less any that is itself listed as
 * public (`/time-track` public leaves `<domain>/time-track/*` behind sign-in).
 */
export function privateDestinations(domain, prefix, publicPaths) {
  return accessDestinations(domain, prefix).filter((uri) => !publicPaths.some((p) => `${domain}${p}` === uri));
}

/**
 * A mixed app's narrowing destinations: `<path>/*` for each public path listed without a
 * `/*`, so the paths under it ask for sign-in. Cloudflare lets a sub-path inherit its
 * parent's rule, so without one an exact Bypass destination opens its children too (seen
 * on the live account, 2026-11-15). None for the bare prefix or `<prefix>/`, whose
 * children are already the Allow app's `<prefix>/*` (for `<prefix>/` that would be the
 * narrowing destination itself), nor for a path whose children a listed wildcard makes public.
 */
export function narrowingDestinations(domain, prefix, publicPaths) {
  return publicPaths
    .filter((p) => !p.endsWith("/*") && p !== prefix && p !== `${prefix}/`)
    .filter((p) => !publicPaths.some((w) => w.endsWith("/*") && pathCovers(w, `${p}/x`)))
    .map((p) => `${domain}${p}/*`);
}

/** Everything a mixed app's Allow app holds: the base destinations, then the narrowing ones. */
export function allowDestinations(domain, prefix, publicPaths) {
  return [...privateDestinations(domain, prefix, publicPaths), ...narrowingDestinations(domain, prefix, publicPaths)];
}

export function publicDestinations(domain, publicPaths) {
  return publicPaths.map((p) => `${domain}${p}`);
}

/**
 * The one Worker route a public app's Worker gets: `<domain><prefix>*`. Cloudflare matches a
 * route against the whole URL, query string included, and a pattern cannot hold a query, so
 * only a pattern ending in `*` matches `/<prefix>?<query>`; the old exact `<domain><prefix>`
 * never did, and those requests fell through to the zone's origin. `<prefix>*` also matches
 * siblings such as `/<prefix>foo`, so the app's Worker must hand every path that is neither
 * `/<prefix>` nor under `/<prefix>/` back to the origin with `fetch(req)`.
 * Access is not widened with it: its destinations stay `accessDestinations`.
 */
export function workerRoute(domain, prefix) {
  return `${domain}${prefix}*`;
}

/**
 * How an existing Worker route pattern relates to the paths an app at `prefix` claims
 * (`/<prefix>` and everything under it), on `domain`:
 *   "exact"     the app's own `<domain><prefix>*` (see `workerRoute`)
 *   "pair"      `<domain><prefix>` or `<domain><prefix>/*`, the two routes an app got before
 *               `<prefix>*`, which replaces them
 *   "inside"    matches only paths under the prefix
 *   "broader"   a wildcard ending above the prefix (such as the home `/*`); the app's
 *               longer pattern is more specific, so it wins under the prefix
 *   "straddles" matches paths both under the prefix and outside it, at its level
 *   "sibling"   matches only paths outside the prefix that `<prefix>*` also matches
 *               (`/<prefix>foo`, `/<prefix>-old/*`); it is more specific, so it keeps them
 *   null        never matches a path the app's route matches (or another host)
 * A route pattern is `[scheme://]host/path`, with an optional `*` at the start of the
 * host and at the end of the path; nothing else is a wildcard.
 */
export function classifyRoute(pattern, domain, prefix) {
  const bare = pattern.replace(/^https?:\/\//, "");
  const slash = bare.indexOf("/");
  const host = slash === -1 ? bare : bare.slice(0, slash);
  const path = slash === -1 ? "/" : bare.slice(slash);
  if (host !== domain && !(host.startsWith("*") && domain.endsWith(host.slice(1)))) return null;

  const claimed = (p) => p === prefix || p.startsWith(`${prefix}/`);
  if (!path.endsWith("*")) {
    if (!claimed(path)) return path.startsWith(prefix) ? "sibling" : null;
    return host === domain && path === prefix ? "pair" : "inside";
  }
  const base = path.slice(0, -1);
  if (host === domain && base === prefix) return "exact";
  if (host === domain && base === `${prefix}/`) return "pair";
  if (base.startsWith(`${prefix}/`)) return "inside";
  if (prefix.startsWith(base)) return base.length < prefix.length ? "broader" : "straddles";
  if (base.startsWith(prefix)) return "sibling";
  return null;
}

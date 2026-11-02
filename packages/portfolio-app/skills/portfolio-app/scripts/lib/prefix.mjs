// The one place that decides which path prefixes a sub-app may claim.
//
// Every sub-app of the site lives under its own prefix, and its Access app guards
// exactly `<domain>/<prefix>` and `<domain>/<prefix>/*`. A prefix that is empty or
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

/**
 * How an existing Worker route pattern relates to the paths an app at `prefix` claims
 * (`/<prefix>` and everything under it), on `domain`:
 *   "exact"    one of the app's own two patterns
 *   "inside"   matches only paths under the prefix
 *   "broader"  a wildcard ending above the prefix (such as the home `/*`); the app's
 *              longer patterns are more specific, so they win under the prefix
 *   "straddles" matches paths both under the prefix and outside it, at its level
 *   null       never matches a path the app claims (or another host)
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
    if (!claimed(path)) return null;
    return host === domain && path === prefix ? "exact" : "inside";
  }
  const base = path.slice(0, -1);
  if (host === domain && base === `${prefix}/`) return "exact";
  if (base.startsWith(`${prefix}/`)) return "inside";
  if (prefix.startsWith(base)) return base.length < prefix.length ? "broader" : "straddles";
  return null;
}

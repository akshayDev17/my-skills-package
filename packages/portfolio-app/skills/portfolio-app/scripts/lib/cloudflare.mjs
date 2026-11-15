// Cloudflare v4 API calls for the resources bootstrap manages: Access (reusable
// policies + self-hosted apps), D1 databases, R2 buckets and Worker routes. Every
// function takes `cf` —
// `{ fetch, token, accountId }` — explicitly, so tests stub `fetch` and nothing else.
//
// The token goes into the Authorization header and nowhere else: errors carry the
// method, path and Cloudflare's own error messages, never request headers.
//
// API shape (read 2026-10-31, see README "Research notes"):
//   reusable policies  GET/POST /accounts/{id}/access/policies — `decision` is allow or
//                      bypass here; Everyone is the include rule `{ everyone: {} }`
//   applications       GET/POST /accounts/{id}/access/apps  — `destinations` supersedes
//                      the deprecated `self_hosted_domains`; `policies: [{ id, precedence }]`
//                      links reusable policies, and reusable + inline are mutually exclusive.
//                      GET/PUT /accounts/{id}/access/apps/{app_id} reads one app and
//                      replaces it: PUT takes the app's full settings, not a patch.
//   D1                 GET/POST /accounts/{id}/d1/database
//   R2                 GET /accounts/{id}/r2/buckets/{name}, POST /accounts/{id}/r2/buckets
//   zones              GET /zones?name=<domain>
//   Worker routes      GET/POST /zones/{zone_id}/workers/routes — `{ pattern, script }`
//   Worker scripts     GET /accounts/{id}/workers/scripts — a route can only name a
//                      deployed script (creating one for a missing script is refused)

const API = "https://api.cloudflare.com/client/v4";

export async function cfRequest(cf, method, path, body) {
  const res = await cf.fetch(`${API}${path}`, {
    method,
    headers: { Authorization: `Bearer ${cf.token}`, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let json = null;
  try {
    json = await res.json();
  } catch {
    // A non-JSON body is reported by status below.
  }
  if (!res.ok || !json?.success) {
    const detail = json?.errors?.map((e) => `${e.code}: ${e.message}`).join("; ") || `HTTP ${res.status}`;
    // `status` lets callers tell "not found" and "not allowed" apart from other failures.
    throw Object.assign(new Error(`Cloudflare ${method} ${path.split("?")[0]} failed — ${detail}`), { status: res.status });
  }
  return json;
}

/** Every page of a list endpoint. Stops on the last page or an empty one. */
export async function cfListAll(cf, path, params = {}) {
  const items = [];
  for (let page = 1; ; page++) {
    const qs = new URLSearchParams({ ...params, page: String(page) });
    const json = await cfRequest(cf, "GET", `${path}?${qs}`);
    const batch = json.result ?? [];
    items.push(...batch);
    const info = json.result_info ?? {};
    const totalPages =
      info.total_pages ?? (info.total_count && info.per_page ? Math.ceil(info.total_count / info.per_page) : page);
    if (batch.length === 0 || page >= totalPages) return items;
  }
}

export const policyEmails = (policy) =>
  (policy.include ?? []).filter((rule) => rule.email?.email).map((rule) => rule.email.email);

export const listAccessPolicies = (cf) => cfListAll(cf, `/accounts/${cf.accountId}/access/policies`, { per_page: "1000" });
export const listAccessApps = (cf) => cfListAll(cf, `/accounts/${cf.accountId}/access/apps`, { per_page: "1000" });

export const pickReusablePolicy = (policies, name) => policies.find((p) => p.name === name && p.reusable !== false) ?? null;

/** `include` is a list of Access rules, such as `{ email: { email } }` or `{ everyone: {} }`. */
export async function createReusablePolicy(cf, { name, decision, include }) {
  const json = await cfRequest(cf, "POST", `/accounts/${cf.accountId}/access/policies`, { name, decision, include });
  return json.result;
}

/** A policy that lets anyone through without signing in: Bypass, Everyone, nothing narrower. */
export function isBypassEveryone(policy) {
  return policy?.decision === "bypass" &&
    (policy.include ?? []).some((rule) => rule.everyone) &&
    (policy.require ?? []).length === 0 && (policy.exclude ?? []).length === 0;
}

/**
 * An app's policies as full objects. The apps list carries them inline; a reference
 * with only an id is looked up in the account's policy list.
 */
export function appPolicies(app, policies) {
  return (app.policies ?? []).map((ref) => (ref.decision ? ref : policies.find((p) => p.id === ref.id) ?? ref));
}

/** The URIs an app guards, whichever field it was created with. */
export function appUris(app) {
  const fromDestinations = (app.destinations ?? []).map((d) => d.uri).filter(Boolean);
  if (fromDestinations.length > 0) return fromDestinations;
  return app.self_hosted_domains?.length ? app.self_hosted_domains : app.domain ? [app.domain] : [];
}

/** Apps guarding any of `uris`, so an exact match and a partial overlap are both visible. */
export async function findAppsTouching(cf, uris) {
  const apps = await listAccessApps(cf);
  return apps.filter((app) => appUris(app).some((uri) => uris.includes(uri)));
}

export async function createSelfHostedApp(cf, { name, uris, policyId }) {
  const json = await cfRequest(cf, "POST", `/accounts/${cf.accountId}/access/apps`, {
    name,
    type: "self_hosted",
    domain: uris[0],
    destinations: uris.map((uri) => ({ type: "public", uri })),
    policies: [{ id: policyId, precedence: 1 }],
    session_duration: "24h",
  });
  return json.result;
}

export async function getAccessApp(cf, appId) {
  return (await cfRequest(cf, "GET", `/accounts/${cf.accountId}/access/apps/${encodeURIComponent(appId)}`)).result;
}

/** PUT replaces the whole app, so `app` is its full current settings with the change applied. */
export async function updateAccessApp(cf, appId, app) {
  return (await cfRequest(cf, "PUT", `/accounts/${cf.accountId}/access/apps/${encodeURIComponent(appId)}`, app)).result;
}

/**
 * `app`'s full settings with `uris` appended to its destinations, every other field as it
 * was. Its policies are sent as `{ id, precedence }` links, keeping each id and precedence.
 */
export function withAddedDestinations(app, uris) {
  return {
    ...app,
    destinations: [...app.destinations, ...uris.map((uri) => ({ type: "public", uri }))],
    policies: (app.policies ?? []).map((p, i) => ({ id: p.id, precedence: p.precedence ?? i + 1 })),
  };
}

export async function findD1(cf, name) {
  const dbs = await cfListAll(cf, `/accounts/${cf.accountId}/d1/database`, { name, per_page: "100" });
  return dbs.find((db) => db.name === name) ?? null;
}

export async function createD1(cf, name) {
  const json = await cfRequest(cf, "POST", `/accounts/${cf.accountId}/d1/database`, { name });
  return json.result;
}

/** The bucket, or null when it does not exist (Cloudflare answers 404). */
export async function findR2Bucket(cf, name) {
  try {
    return (await cfRequest(cf, "GET", `/accounts/${cf.accountId}/r2/buckets/${encodeURIComponent(name)}`)).result;
  } catch (err) {
    if (err.status === 404) return null;
    throw err;
  }
}

export async function createR2Bucket(cf, name) {
  const json = await cfRequest(cf, "POST", `/accounts/${cf.accountId}/r2/buckets`, { name });
  return json.result;
}

/** The zone for `domain`, or null when the token cannot see it (the list is filtered, not refused). */
export async function findZone(cf, domain) {
  const zones = await cfListAll(cf, "/zones", { name: domain, per_page: "50" });
  return zones.find((z) => z.name === domain) ?? null;
}

export async function listWorkerRoutes(cf, zoneId) {
  return (await cfRequest(cf, "GET", `/zones/${zoneId}/workers/routes`)).result ?? [];
}

export async function createWorkerRoute(cf, zoneId, { pattern, script }) {
  const json = await cfRequest(cf, "POST", `/zones/${zoneId}/workers/routes`, { pattern, script });
  return json.result;
}

export async function workerExists(cf, name) {
  const scripts = (await cfRequest(cf, "GET", `/accounts/${cf.accountId}/workers/scripts`)).result ?? [];
  return scripts.some((s) => s.id === name);
}

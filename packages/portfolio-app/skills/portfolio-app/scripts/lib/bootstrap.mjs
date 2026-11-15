// bootstrap = plan, then (only with --apply) carry the plan out.
//
// Planning makes read calls only: Cloudflare GETs, `gh api` reads, `git remote`, and a
// keychain existence check. The plan is plain data — one action per thing that exists
// or must be created — so the same records drive the printout, the conflict refusal and
// the apply loop. Apply refuses outright while any conflict stands, and then creates
// only the actions marked "create", which is what makes a re-run a no-op.
//
// A private app (the default) gets its Access app first and nothing else is touched
// until the path answers with the Access login. A public app (`--public`, never
// inferred) skips Access entirely and gets Worker routes for its prefix instead; it
// refuses a path that already has Access protection rather than removing it.
//
// A mixed app (`--public-path`, repeatable, never inferred) is a private app plus one
// more Access app: "<repo name> (public)", a Bypass-for-Everyone app holding exactly the
// listed public paths. Its Allow app is the private one, less any listed destination,
// plus a narrowing `<path>/*` for each exact public path but the bare prefix, so the
// children of that path ask for sign-in rather than inherit its Bypass. Apply waits until
// the private paths (a child of each exact public path among them) redirect to the login
// and the public ones answer signed out. It changes an existing app in two ways only, both
// by adding destinations: missing narrowing ones to the Allow app, which only shrinks what
// is public, and newly listed public paths to the public app. It never removes or alters
// a destination that was there before the run, or moves one out of an existing app, and
// anything else in the way is a conflict. A failed check leaves the apps the run created
// in place for inspection.
//
// The prefix's trailing-slash path `/<prefix>/` is an exact public path like any other,
// but it is the parent of every page under the prefix: a destination covers its subpaths,
// and the docs do not say whether `<prefix>/` or the Allow app's `<prefix>/*` wins for
// `<prefix>/<child>`. No narrowing destination can help (it would be `<prefix>/*` itself),
// so the child probes are what show the children still ask for sign-in. A run that added
// `<domain>/<prefix>/` and then fails its check is the one case in which apply takes
// something out again: that one destination, from the Bypass app the run put it in. It
// then checks once more without it. Nothing else is removed, and no app is deleted.
// `/<prefix>/` is planned only alongside `/<prefix>`, so that app always keeps the bare
// prefix; `/<prefix>/` listed without it is a conflict.

import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import {
  accessDestinations, allowDestinations, classifyRoute, narrowingDestinations, privateDestinations, publicDestinations,
} from "./prefix.mjs";
import { verifyPath, waitForAccess } from "./verify.mjs";
import {
  appPolicies,
  appUris,
  createD1,
  createR2Bucket,
  createReusablePolicy,
  createSelfHostedApp,
  createWorkerRoute,
  findAppsTouching,
  findD1,
  findR2Bucket,
  findZone,
  getAccessApp,
  isBypassEveryone,
  listAccessApps,
  listAccessPolicies,
  listWorkerRoutes,
  pickReusablePolicy,
  policyEmails,
  updateAccessApp,
  withAddedDestinations,
  withoutDestination,
  workerExists,
} from "./cloudflare.mjs";
import { D1_PLACEHOLDER, d1Target, parseJsonc, r2Target, replacePlaceholder } from "./wrangler-config.mjs";
import {
  DEPLOY_TOKEN_ACCOUNT,
  GITHUB_ENVIRONMENT,
  KEYCHAIN_SERVICE,
  POLICY_NAME,
  PUBLIC_POLICY_NAME,
  cloneRepo,
  ghCreateEnvironment,
  ghEnvironmentExists,
  ghSecretNames,
  ghSetSecret,
  keychainHas,
  keychainRead,
} from "./local.mjs";

const sameSet = (a, b) => a.length === b.length && a.every((x) => b.includes(x));

/** Setup-token permissions beyond Access and D1, named as the Cloudflare dashboard names them. */
export const SCOPES = {
  r2: "Account · Workers R2 Storage · Edit",
  zone: "Zone · Zone · Read",
  routes: "Zone · Workers Routes · Edit",
  scripts: "Account · Workers Scripts · Read",
};

/** Runs a read; a 401/403 becomes a sentence naming the permission the setup token lacks. */
async function readWithScope(scope, what, read) {
  try {
    return { value: await read() };
  } catch (err) {
    if (err.status !== 401 && err.status !== 403) throw err;
    return { denied: `the setup token may not ${what} (${err.message}) — add "${scope}" to it; see the README's keychain table` };
  }
}

/**
 * Why `app` cannot take more destinations by a plain PUT, or null if it can: a self-hosted
 * app whose paths are all public `destinations`.
 */
function putRefusal(app) {
  if ((app.type ?? "self_hosted") !== "self_hosted") return `it is a ${app.type} app, not self-hosted`;
  if (!(app.destinations ?? []).length) return "its paths are in the deprecated self_hosted_domains, not destinations";
  if (app.destinations.some((d) => (d.type ?? "public") !== "public")) return "it holds a destination that is not a public hostname";
  return null;
}

/** Why the Allow app cannot take the narrowing destinations, or null: as `putRefusal`, and no Bypass policy. */
function narrowingRefusal(app, policies) {
  const bypass = appPolicies(app, policies).some((p) => p.decision === "bypass");
  return putRefusal(app) ?? (bypass ? "it has a Bypass policy, so a destination added to it would not ask for sign-in" : null);
}

/**
 * The Allow app. For a mixed app it holds the base destinations plus a narrowing
 * destination per exact public path (see prefix.mjs). An existing app holding the base
 * ones and only some of the narrowing ones gets the rest added: the one change the tool
 * makes to an existing Allow app, and it only shrinks what is public.
 */
async function planAccess({ cf }, { repo, prefix, config, publicPaths = [] }) {
  const actions = [];
  const base = privateDestinations(config.domain, prefix, publicPaths);
  const uris = allowDestinations(config.domain, prefix, publicPaths);

  const policies = await listAccessPolicies(cf);
  const policy = pickReusablePolicy(policies, POLICY_NAME);
  if (policy) {
    actions.push({ step: "access", kind: "policy", state: "ok", policyId: policy.id,
      summary: `reusable policy "${policy.name}" exists` });
    const emails = policyEmails(policy);
    if (!sameSet(emails, config.allowedEmails)) {
      actions.push({ step: "access", kind: "policy-emails", state: "warn",
        summary: `policy "${policy.name}" allows [${emails.join(", ")}], config says [${config.allowedEmails.join(", ")}] — left unchanged; edit it in the Zero Trust dashboard if that is intended` });
    }
  } else {
    actions.push({ step: "access", kind: "policy", state: "create", name: POLICY_NAME, emails: config.allowedEmails,
      summary: `create reusable policy "${POLICY_NAME}" (allow: ${config.allowedEmails.join(", ")})` });
  }

  const touching = await findAppsTouching(cf, uris);
  // Holds every base destination and nothing beyond the narrowing ones: the Allow app.
  const fits = (app) => base.every((uri) => appUris(app).includes(uri)) && appUris(app).every((uri) => uris.includes(uri));
  const exact = touching.filter(fits);
  const partial = touching.filter((app) => !exact.includes(app));
  if (partial.length > 0) {
    for (const app of partial) {
      actions.push({ step: "access", kind: "app", state: "conflict",
        summary: `Access app "${app.name}" guards [${appUris(app).join(", ")}], which overlaps [${uris.join(", ")}] without matching it; fix or remove it by hand` });
    }
  } else if (exact.length > 0) {
    const app = exact[0];
    const held = appUris(app);
    const missing = uris.filter((uri) => !held.includes(uri));
    actions.push({ step: "access", kind: "app", state: "ok", name: app.name, summary: `Access app "${app.name}" guards ${held.join(" + ")}` });
    const refusal = missing.length > 0 ? narrowingRefusal(app, policies) : null;
    if (refusal) {
      actions.push({ step: "access", kind: "app-narrowing", state: "conflict",
        summary: `Access app "${app.name}" needs narrowing destination(s) ${missing.join(" + ")}, but ${refusal}; add them by hand` });
    } else if (missing.length > 0) {
      actions.push({ step: "access", kind: "app-narrowing", state: "create", name: app.name, appId: app.id, held, uris: missing,
        summary: `add narrowing destination(s) ${missing.join(" + ")} to "${app.name}" — the paths under each exact public path ask for sign-in` });
    }
    const attached = (app.policies ?? []).some((p) => policy && p.id === policy.id);
    if (!attached) {
      actions.push({ step: "access", kind: "app-policy", state: "warn",
        summary: `Access app "${app.name}" is not attached to policy "${POLICY_NAME}" — left unchanged` });
    }
  } else {
    const name = `${repo.split("/")[1]} (${prefix})`;
    actions.push({ step: "access", kind: "app", state: "create", name, uris,
      summary: `create self-hosted Access app "${name}" — destinations ${uris.join(" + ")}, policy "${POLICY_NAME}"` });
  }
  return actions;
}

/** What the site answers right now; with `publicPaths`, both ways (see verify.mjs). */
async function planAccessCheck({ fetch }, { prefix, config, publicPaths = [] }) {
  const now = await verifyPath({ fetch, domain: config.domain, prefix, team: config.team, publicPaths });
  const except = publicPaths.length > 0 ? `, and its public paths answer signed out` : "";
  return [{ step: "access", kind: "check", state: now.ok ? "ok" : "info",
    summary: now.ok ? `${prefix} already redirects to ${config.team}.cloudflareaccess.com${except}`
      : `now: ${now.results.map((r) => `${r.url} ${r.reason}`).join("; ")}` }];
}

/** Whether a planned action puts `slash` (`<domain>/<prefix>/`) into a Bypass app: what apply takes out again if its check fails. */
const addsSlashDestination = (action, slash) =>
  action.state === "create" && (action.kind === "bypass-app" || action.kind === "bypass-app-add") && action.uris.includes(slash);

const NEVER_MOVE = "a destination stays with the app that holds it — the tool never removes, alters or moves one out of an existing app; fix it by hand";

/**
 * Mixed apps only: the Bypass-for-Everyone app on exactly the listed public paths, and
 * the reusable Bypass policy it links. An app holding exactly those destinations is
 * reused if every policy on it is Bypass for Everyone. The public app (by its name)
 * holding only some of them, Bypass for Everyone and open to a plain PUT, gets the rest
 * added: a re-run with one more public path adds only that destination. Conflicts: any
 * other app holding a listed destination, an app with the public app's name holding
 * anything else, and a Bypass app that opens more of the prefix than the list. A Bypass
 * app above the prefix (say `<domain>/*`) is left alone: the app's own destinations are
 * more specific. `/<prefix>/` listed without `/<prefix>` is a conflict before anything is
 * read: the Bypass app must keep the bare prefix if `<domain>/<prefix>/` is taken out again.
 */
async function planBypass({ cf }, { repo, prefix, config, publicPaths }) {
  const app = (state, summary, extra = {}) => ({ step: "access", kind: "bypass-app", state, summary, ...extra });
  if (publicPaths.includes(`${prefix}/`) && !publicPaths.includes(prefix)) {
    return [app("conflict", `public path ${prefix}/ is only accepted alongside ${prefix}: if the check fails, apply takes ${config.domain}${prefix}/ out of the Bypass app again, and the app must have ${config.domain}${prefix} left to keep; add --public-path ${prefix}, or drop --public-path ${prefix}/`)];
  }
  const uris = publicDestinations(config.domain, publicPaths);
  const name = `${repo.split("/")[1]} (public)`;
  const policies = await listAccessPolicies(cf);
  const apps = await listAccessApps(cf);
  // An Access destination with no path covers the whole host, as a route `<host>/*` would.
  const relations = (a) => appUris(a).map((uri) => classifyRoute(uri.includes("/") ? uri : `${uri}/*`, config.domain, prefix));
  const bypasses = (a) => appPolicies(a, policies).some((p) => p.decision === "bypass");
  const everyoneOnly = (a) => appPolicies(a, policies).length > 0 && appPolicies(a, policies).every(isBypassEveryone);

  const exact = apps.find((a) => sameSet(appUris(a), uris));
  const appActions = [];
  for (const a of apps) {
    const held = appUris(a);
    const shared = held.filter((uri) => uris.includes(uri));
    if (a === exact) {
      appActions.push(everyoneOnly(a)
        ? app("ok", `Bypass app "${a.name}" lets anyone reach ${uris.join(" + ")}`, { name: a.name })
        : app("conflict", `Access app "${a.name}" holds exactly the public paths but is not Bypass for Everyone; ${NEVER_MOVE}`));
    } else if (a.name === name && !exact && held.length > 0 && held.every((uri) => uris.includes(uri))) {
      const missing = uris.filter((uri) => !held.includes(uri));
      const refusal = everyoneOnly(a) ? putRefusal(a) : "it is not Bypass for Everyone";
      appActions.push(refusal
        ? app("conflict", `Access app "${a.name}" holds [${held.join(", ")}], some of the public paths, but ${refusal}, so the tool cannot add ${missing.join(" + ")}; add them by hand`)
        : app("ok", `Bypass app "${a.name}" lets anyone reach ${held.join(" + ")}`, { name: a.name }));
      if (!refusal) {
        appActions.push({ step: "access", kind: "bypass-app-add", state: "create", name: a.name, appId: a.id, held, uris: missing,
          summary: `add public destination(s) ${missing.join(" + ")} to Bypass app "${a.name}" (anyone, no sign-in)` });
      }
    } else if (a.name === name) {
      appActions.push(app("conflict", `Access app "${a.name}" holds [${held.join(", ")}], not exactly the public paths [${uris.join(", ")}]; ${NEVER_MOVE}`));
    } else if (shared.length > 0) {
      appActions.push(app("conflict", `${shared.join(" + ")} already belong(s) to Access app "${a.name}" [${held.join(", ")}]; ${NEVER_MOVE}`));
    } else if (bypasses(a) && relations(a).some((r) => r === "exact" || r === "inside" || r === "straddles")) {
      appActions.push(app("conflict", `Bypass app "${a.name}" lets anyone reach [${held.join(", ")}], more of ${prefix} than the public paths [${uris.join(", ")}]; narrow or remove it by hand`));
    } else if (bypasses(a) && relations(a).includes("broader")) {
      appActions.push(app("info", `Bypass app "${a.name}" on [${held.join(", ")}] is broader than ${prefix}; the app's own destinations are more specific and win under it — left alone`));
    }
  }
  if (exact || appActions.some((x) => x.state === "conflict" || x.kind === "bypass-app-add")) return appActions;

  const policy = pickReusablePolicy(policies, PUBLIC_POLICY_NAME);
  const policyAction = !policy
    ? { step: "access", kind: "bypass-policy", state: "create", name: PUBLIC_POLICY_NAME,
      summary: `create reusable policy "${PUBLIC_POLICY_NAME}" (bypass: everyone)` }
    : isBypassEveryone(policy)
      ? { step: "access", kind: "bypass-policy", state: "ok", policyId: policy.id, summary: `reusable policy "${policy.name}" exists (bypass: everyone)` }
      : { step: "access", kind: "bypass-policy", state: "conflict",
        summary: `reusable policy "${policy.name}" exists but is not Bypass for Everyone — left unchanged; fix it in the Zero Trust dashboard` };
  if (policyAction.state === "conflict") return [policyAction, ...appActions];
  return [
    policyAction,
    ...appActions,
    app("create", `create self-hosted Access app "${name}" — destinations ${uris.join(" + ")}, policy "${PUBLIC_POLICY_NAME}" (anyone, no sign-in)`, { name, uris }),
  ];
}

/**
 * Mixed apps only: which app answers each listed destination, and the rest of the prefix.
 * A public `<prefix>/` gets one more line for the paths under it: they are meant for the
 * Allow app's `<prefix>/*`, but a destination covers its subpaths too, and the docs do not
 * say whether `<prefix>/` or `<prefix>/*` is the more specific, so apply checks a child.
 * The line promises to take `<domain>/<prefix>/` out again only when this plan adds it.
 */
function planCoverage(actions, { prefix, config, publicPaths }) {
  const line = (summary) => ({ step: "access", kind: "coverage", state: "info", summary });
  if (actions.some((a) => a.step === "access" && a.state === "conflict")) {
    return [line(`which app answers what is known once the conflicts are resolved`)];
  }
  const allow = actions.find((a) => a.kind === "app")?.name;
  const bypass = actions.find((a) => a.kind === "bypass-app" && a.state !== "info")?.name;
  const rest = `${config.domain}${prefix}/*`;
  const slash = `${config.domain}${prefix}/`;
  const onExposedChild = actions.some((a) => addsSlashDestination(a, slash))
    ? `takes ${slash} out again if one answers signed out`
    : `reports one that answers signed out; this run does not add ${slash}, so it removes nothing`;
  const base = privateDestinations(config.domain, prefix, publicPaths);
  const allowLine = (uri, note = "") => line(`  ${uri}${note} → "${allow}" — Allow, sign-in`);
  return [
    line(`who answers (the app with the most specific matching destination wins; Bypass lets anyone in, Allow asks for sign-in):`),
    ...publicDestinations(config.domain, publicPaths).map((uri) => line(`  ${uri} → "${bypass}" — Bypass, anyone`)),
    ...base.filter((uri) => uri !== rest).map((uri) => allowLine(uri)),
    ...narrowingDestinations(config.domain, prefix, publicPaths).map((uri) => allowLine(uri, " (narrowing)")),
    ...base.filter((uri) => uri === rest).map((uri) => allowLine(uri, " (the rest)")),
    ...(publicPaths.includes(`${prefix}/`)
      ? [line(`  ${config.domain}${prefix}/<child> → "${allow}" (by ${rest}) — Allow, sign-in; ${slash} covers its subpaths too and the docs do not rank it against ${rest}, so apply checks a child and ${onExposedChild}`)]
      : []),
  ];
}

async function planPublicAccess({ cf, fetch }, { prefix, config }) {
  const uris = accessDestinations(config.domain, prefix);
  const refuse = "--public refuses a path that already has Access protection; removing it is a decision for the Zero Trust dashboard, not for a re-run";
  const actions = [{ step: "access", kind: "public", state: "warn",
    summary: `public app: ${uris.join(" + ")} will be reachable by anyone — the Access sign-in step is skipped` }];

  const touching = await findAppsTouching(cf, uris);
  for (const app of touching) {
    actions.push({ step: "access", kind: "app", state: "conflict",
      summary: `Access app "${app.name}" guards [${appUris(app).join(", ")}]; ${refuse}` });
  }
  if (touching.length > 0) return actions;

  // An Access app with wider destinations (say `<domain>/work/*`) is not in the list
  // above, but it still answers for the prefix, so ask the site itself.
  const now = await verifyPath({ fetch, domain: config.domain, prefix, team: config.team });
  const guarded = now.results.filter((r) => r.ok);
  actions.push(guarded.length > 0
    ? { step: "access", kind: "check", state: "conflict",
      summary: `${guarded.map((r) => r.url).join(" and ")} already redirect(s) to Cloudflare Access; ${refuse}` }
    : { step: "access", kind: "check", state: "ok", summary: `no Access app guards ${uris.join(" + ")}` });
  return actions;
}

/** The clone's backend/wrangler.jsonc, parsed — or the reason it cannot be used. Reads only. */
function readClone({ run }, { repo, cloneDir }) {
  const wranglerPath = join(cloneDir, "backend", "wrangler.jsonc");
  const cloned = cloneRepo(run, cloneDir);
  if (cloned?.toLowerCase() !== repo.toLowerCase()) {
    return { wranglerPath, problem: `${cloneDir} is a clone of ${cloned ?? "nothing GitHub knows"}, not ${repo}; pass --clone <path to a clone of ${repo}>` };
  }
  if (!existsSync(wranglerPath)) return { wranglerPath, problem: `${wranglerPath} does not exist` };
  try {
    return { wranglerPath, wrangler: parseJsonc(readFileSync(wranglerPath, "utf8")) };
  } catch (err) {
    return { wranglerPath, problem: `${wranglerPath}: ${err.message}` };
  }
}

const CLONE_PROBLEM = "needs the clone's backend/wrangler.jsonc — see the D1 conflict above";

async function planD1({ cf }, { d1Flag }, clone) {
  const { wranglerPath } = clone;
  const conflict = (summary) => [{ step: "d1", kind: "d1", state: "conflict", summary }];
  if (clone.problem) return conflict(clone.problem);

  const target = d1Target(clone.wrangler, d1Flag);
  if (target.state === "conflict") return conflict(`${wranglerPath}: ${target.reason}`);
  if (target.state === "none") return [{ step: "d1", kind: "d1", state: "info", summary: "no d1_databases binding — nothing to do" }];
  if (target.state === "present") {
    return [{ step: "d1", kind: "d1", state: "ok", summary: `wrangler.jsonc already carries the database_id for ${target.name}` }];
  }

  const existing = await findD1(cf, target.name);
  const configAction = { step: "d1", kind: "d1-config", state: "create", path: wranglerPath, databaseId: existing?.uuid ?? null,
    summary: `write database_id into ${wranglerPath} (replaces ${D1_PLACEHOLDER}); commit it through the normal delivery path` };
  if (existing) {
    return [
      { step: "d1", kind: "d1-database", state: "ok", databaseId: existing.uuid, summary: `D1 database "${target.name}" exists (${existing.uuid})` },
      configAction,
    ];
  }
  return [
    { step: "d1", kind: "d1-database", state: "create", name: target.name, summary: `create D1 database "${target.name}"` },
    configAction,
  ];
}

/** Public apps only: the single bucket the clone's `r2_buckets` names. */
async function planR2({ cf }, clone) {
  const action = (state, summary, extra = {}) => ({ step: "r2", kind: "r2-bucket", state, summary, ...extra });
  if (clone.problem) return [action("info", CLONE_PROBLEM)];

  const target = r2Target(clone.wrangler);
  if (target.state === "conflict") return [action("conflict", `${clone.wranglerPath}: ${target.reason}`)];
  if (target.state === "none") return [action("info", "no r2_buckets binding — nothing to do")];

  const found = await readWithScope(SCOPES.r2, "read R2 buckets", () => findR2Bucket(cf, target.name));
  if (found.denied) return [action("conflict", found.denied)];
  return found.value
    ? [action("ok", `R2 bucket "${target.name}" exists`)]
    : [action("create", `create R2 bucket "${target.name}"`, { name: target.name })];
}

/**
 * Public apps only: the Worker named in the clone's wrangler.jsonc gets exactly
 * `<domain>/<prefix>` and `<domain>/<prefix>/*`. Any other route that reaches into the
 * prefix — owned by another Worker, by none, or by this Worker but spilling outside
 * the prefix — is a conflict. Broader routes of other Workers (the home `/*`) are left
 * alone: the app's longer patterns are more specific, so they win under the prefix.
 */
async function planRoutes({ cf }, { prefix, config }, clone) {
  const action = (state, summary, extra = {}) => ({ step: "route", kind: "route", state, summary, ...extra });
  if (clone.problem) return [action("info", CLONE_PROBLEM)];
  const script = clone.wrangler.name;
  if (typeof script !== "string" || script === "") {
    return [action("conflict", `${clone.wranglerPath} has no "name"; the routes need the Worker's name`)];
  }
  const patterns = accessDestinations(config.domain, prefix);

  const zone = await readWithScope(SCOPES.zone, `read zone ${config.domain}`, () => findZone(cf, config.domain));
  if (zone.denied) return [action("conflict", zone.denied)];
  if (!zone.value) {
    return [action("conflict", `the setup token cannot see zone ${config.domain} — give it "${SCOPES.zone}" and "${SCOPES.routes}" on that zone; see the README's keychain table`)];
  }
  const zoneId = zone.value.id;
  const routes = await readWithScope(SCOPES.routes, `read the Worker routes of ${config.domain}`, () => listWorkerRoutes(cf, zoneId));
  if (routes.denied) return [action("conflict", routes.denied)];

  const actions = [];
  const present = [];
  for (const route of routes.value) {
    const relation = classifyRoute(route.pattern, config.domain, prefix);
    if (!relation) continue;
    const ours = route.script === script;
    const target = route.script ? `Worker "${route.script}"` : "no Worker";
    if (relation === "exact" && ours) {
      present.push(route.pattern.endsWith("*") ? patterns[1] : patterns[0]);
      actions.push(action("ok", `route ${route.pattern} → ${target}`));
    } else if (relation === "inside" && ours) {
      actions.push(action("ok", `route ${route.pattern} → ${target} sits inside ${prefix}`));
    } else if (relation === "broader" && !ours) {
      actions.push(action("info", `route ${route.pattern} → ${target} is broader; the app's routes are more specific and win under ${prefix} — left alone`));
    } else if (ours) {
      actions.push(action("conflict", `route ${route.pattern} → ${target} reaches outside ${prefix}, but an app claims only ${patterns.join(" + ")}; fix it by hand`));
    } else {
      actions.push(action("conflict", `route ${route.pattern} → ${target} overlaps ${patterns.join(" + ")}; fix or remove it by hand`));
    }
  }

  const missing = patterns.filter((p) => !present.includes(p));
  if (missing.length === 0) return actions;
  const deployed = await readWithScope(SCOPES.scripts, "list Worker scripts", () => workerExists(cf, script));
  if (deployed.denied) return [...actions, action("conflict", deployed.denied)];
  if (!deployed.value) {
    return [...actions, action("info",
      `Worker "${script}" is not deployed yet, and a route can only name a deployed Worker — re-run with --public after its first deploy to add ${missing.join(" + ")}`,
      { later: true })];
  }
  return [...actions, ...missing.map((pattern) =>
    action("create", `create route ${pattern} → Worker "${script}" (publicly reachable)`, { pattern, script, zoneId }))];
}

function planGithub({ run }, { repo }) {
  const actions = [];
  const envExists = ghEnvironmentExists(run, repo);
  actions.push(envExists
    ? { step: "github", kind: "gh-environment", state: "ok", summary: `environment ${GITHUB_ENVIRONMENT} exists` }
    : { step: "github", kind: "gh-environment", state: "create", summary: `create environment ${GITHUB_ENVIRONMENT}` });

  const present = envExists ? ghSecretNames(run, repo) : [];
  const secrets = [
    { name: "CLOUDFLARE_API_TOKEN", source: `keychain ${KEYCHAIN_SERVICE}/${DEPLOY_TOKEN_ACCOUNT} (the narrow deploy token)` },
    { name: "CLOUDFLARE_ACCOUNT_ID", source: "config accountId" },
  ];
  for (const secret of secrets) {
    if (present.includes(secret.name)) {
      actions.push({ step: "github", kind: "gh-secret", state: "ok", name: secret.name, summary: `secret ${secret.name} is set` });
    } else if (secret.name === "CLOUDFLARE_API_TOKEN" && !keychainHas(run, DEPLOY_TOKEN_ACCOUNT)) {
      actions.push({ step: "github", kind: "gh-secret", state: "conflict", name: secret.name,
        summary: `no keychain entry service="${KEYCHAIN_SERVICE}" account="${DEPLOY_TOKEN_ACCOUNT}" to set ${secret.name} from` });
    } else {
      actions.push({ step: "github", kind: "gh-secret", state: "create", name: secret.name,
        summary: `set secret ${secret.name} from ${secret.source}` });
    }
  }
  return actions;
}

/** Private and mixed apps: the Allow app, then (mixed) the Bypass app, then the site's answer. */
async function planPrivateAccess(deps, opts) {
  const allow = await planAccess(deps, opts);
  if (!opts.publicPaths?.length) return [...allow, ...(await planAccessCheck(deps, opts))];
  const both = [...allow, ...(await planBypass(deps, opts))];
  return [...both, ...planCoverage(both, opts), ...(await planAccessCheck(deps, opts))];
}

/**
 * Read-only. `deps = { cf, run, fetch }`;
 * `opts = { repo, prefix, cloneDir, d1Flag, public, publicPaths, config }`.
 */
export async function planBootstrap(deps, opts) {
  const clone = readClone(deps, opts);
  return [
    ...(opts.public ? await planPublicAccess(deps, opts) : await planPrivateAccess(deps, opts)),
    ...(await planD1(deps, opts, clone)),
    ...(opts.public ? await planR2(deps, clone) : []),
    ...(opts.public ? await planRoutes(deps, opts, clone) : []),
    ...planGithub(deps, opts),
  ];
}

const MARK = { ok: "✓", create: "+", warn: "!", conflict: "✗", info: "·" };
const HEADING = { access: "Cloudflare Access", d1: "D1", r2: "R2", route: "Worker routes", github: "GitHub" };
const OPTIONAL_STEPS = ["r2", "route"];

export function formatPlan(actions, { repo, prefix, apply, public: isPublic, publicPaths = [] }) {
  const mode = isPublic ? " (public: no Access sign-in)"
    : publicPaths.length > 0 ? ` (mixed: sign-in except ${publicPaths.join(", ")})` : "";
  const lines = [`portfolio-app bootstrap ${repo} ${prefix}${mode} — ${apply ? "applying" : "plan only; nothing changes without --apply"}`];
  for (const step of ["access", "d1", "r2", "route", "github"]) {
    const stepActions = actions.filter((x) => x.step === step);
    if (stepActions.length === 0 && OPTIONAL_STEPS.includes(step)) continue;
    lines.push(step === "github" ? `${HEADING[step]} ${repo}` : HEADING[step]);
    for (const a of stepActions) lines.push(`  ${MARK[a.state]} ${a.summary}`);
  }
  const creates = actions.filter((a) => a.state === "create").length;
  const conflicts = actions.filter((a) => a.state === "conflict").length;
  const later = actions.some((a) => a.later);
  lines.push(conflicts > 0 ? `${conflicts} conflict(s) — resolve them before --apply`
    : creates > 0 ? `${creates} change(s) planned`
    : later ? "nothing to create until the Worker's first deploy — then re-run"
    : "nothing to create — already bootstrapped");
  return lines.join("\n");
}

/**
 * Adds the planned destinations (narrowing ones to the Allow app, public ones to the
 * Bypass app) to the existing app. The app is read again first, and an app whose paths
 * changed since the plan is refused, since PUT replaces the whole app with what is sent.
 */
async function addDestinations(cf, { appId, name, held, uris }) {
  const current = await getAccessApp(cf, appId);
  if (!sameSet(appUris(current), held)) {
    throw new Error(`Access app "${name}" now guards [${appUris(current).join(", ")}], not [${held.join(", ")}] as planned; re-run the plan`);
  }
  const updated = await updateAccessApp(cf, appId, withAddedDestinations(current, uris));
  const lost = [...held, ...uris].filter((uri) => !appUris(updated ?? {}).includes(uri));
  if (lost.length > 0) throw new Error(`Access app "${name}" was updated but does not guard ${lost.join(" + ")}; check it in the Zero Trust dashboard`);
}

/**
 * Takes `slash` (`<domain>/<prefix>/`) out of the Bypass app this run added it to, after
 * the run's check failed. The app is read again and sent back with only that destination
 * removed, so everything else it holds stays; the plan made sure that includes the bare
 * prefix. Returns one sentence saying what was removed.
 */
async function removeSlashDestination(cf, { appId, name }, slash) {
  const current = await getAccessApp(cf, appId);
  const kept = appUris(current).filter((uri) => uri !== slash);
  const updated = await updateAccessApp(cf, appId, withoutDestination(current, slash));
  if (!sameSet(appUris(updated ?? {}), kept)) {
    return `removed ${slash} from Bypass app "${name}", but it now guards [${appUris(updated ?? {}).join(", ")}], not [${kept.join(", ")}]; check it in the Zero Trust dashboard now`;
  }
  return `removed ${slash} from Bypass app "${name}", which keeps ${kept.join(" + ")}`;
}

/**
 * Carries out the "create" actions in plan order. Refuses while any conflict stands.
 * Private: Access first, and nothing else until the path redirects to the Access login.
 * Mixed: the Allow app (or the narrowing destinations added to the existing one), then
 * the Bypass app (or the public destinations added to the existing one), and nothing else
 * until the private paths redirect to the login and the public ones answer signed out. A
 * failed check leaves what the run created in place. Only a run that added
 * `<domain>/<prefix>/` does more: a private path that asked for sign-in before that
 * destination was added and answers signed out after it stops the wait at once, and on
 * any failed check that one destination is taken out again (see `removeSlashDestination`)
 * and the check is run once more without it.
 * Public: no Access step and no login wait; Cloudflare resources, then GitHub.
 */
export async function applyBootstrap({ cf, run, fetch, sleep, log }, actions, { prefix, repo, config, public: isPublic, publicPaths = [] }) {
  const conflicts = actions.filter((a) => a.state === "conflict");
  if (conflicts.length > 0) throw new Error(`refusing to apply: ${conflicts.length} conflict(s) in the plan`);

  let policyId = actions.find((a) => a.kind === "policy")?.policyId ?? null;
  let bypassPolicyId = actions.find((a) => a.kind === "bypass-policy")?.policyId ?? null;
  let databaseId = actions.find((a) => a.kind === "d1-database" || a.kind === "d1-config")?.databaseId ?? null;

  if (!isPublic) {
    const slash = `${config.domain}${prefix}/`;
    const slashApps = []; // the Bypass app(s) this run adds `slash` to: [{ appId, name }]
    let mustStayPrivate = [];
    for (const action of actions.filter((a) => a.step === "access" && a.state === "create")) {
      const addsSlash = addsSlashDestination(action, slash);
      if (addsSlash && slashApps.length === 0) {
        // What asks for sign-in before `slash` is public; the wait stops at once if one stops asking.
        const before = await verifyPath({ fetch, domain: config.domain, prefix, team: config.team, publicPaths });
        mustStayPrivate = before.results.filter((r) => r.expect === "access" && r.ok).map((r) => r.url);
      }
      if (action.kind === "policy") {
        const include = action.emails.map((email) => ({ email: { email } }));
        policyId = (await createReusablePolicy(cf, { name: action.name, decision: "allow", include })).id;
        log(`  + created reusable policy "${action.name}"`);
      } else if (action.kind === "app") {
        await createSelfHostedApp(cf, { name: action.name, uris: action.uris, policyId });
        log(`  + created Access app "${action.name}"`);
      } else if (action.kind === "app-narrowing") {
        await addDestinations(cf, action);
        log(`  + added narrowing destination(s) ${action.uris.join(" + ")} to Access app "${action.name}"`);
      } else if (action.kind === "bypass-policy") {
        bypassPolicyId = (await createReusablePolicy(cf, { name: action.name, decision: "bypass", include: [{ everyone: {} }] })).id;
        log(`  + created reusable policy "${action.name}" (bypass: everyone)`);
      } else if (action.kind === "bypass-app") {
        const app = await createSelfHostedApp(cf, { name: action.name, uris: action.uris, policyId: bypassPolicyId });
        if (addsSlash) slashApps.push({ appId: app.id, name: action.name });
        log(`  + created Bypass app "${action.name}"`);
      } else if (action.kind === "bypass-app-add") {
        await addDestinations(cf, action);
        if (addsSlash) slashApps.push({ appId: action.appId, name: action.name });
        log(`  + added public destination(s) ${action.uris.join(" + ")} to Bypass app "${action.name}"`);
      }
    }
    const failures = (result) => result.results.filter((r) => !r.ok).map((r) => `${r.url} ${r.reason}`).join("; ");
    const check = await waitForAccess({ fetch, domain: config.domain, prefix, team: config.team, publicPaths, mustStayPrivate, sleep });
    if (!check.ok) {
      const failed = `Access is not answering for ${prefix}: ${failures(check)}`;
      if (slashApps.length === 0) throw new Error(failed);
      const why = check.exposed
        ? `${check.exposed.join(", ")} asked for sign-in before this run and answer(s) signed out now, so the wait stopped at once`
        : `the check failed after this run made ${slash} public`;
      log(`  ! ${why}; taking ${slash} out again`);
      const done = [];
      try {
        for (const app of slashApps) done.push(await removeSlashDestination(cf, app, slash));
      } catch (err) {
        throw new Error(`${failed}. Taking ${slash} out again failed (${err.message}); check ${slashApps.map((a) => `"${a.name}"`).join(", ")} in the Zero Trust dashboard now`);
      }
      for (const step of done) log(`  - ${step}`);
      const recheck = await waitForAccess({ fetch, domain: config.domain, prefix, team: config.team,
        publicPaths: publicPaths.filter((p) => p !== `${prefix}/`), sleep });
      const after = recheck.ok ? `the site passes the check without ${prefix}/`
        : `the site still fails the check without ${prefix}/: ${failures(recheck)}`;
      throw new Error(`${failed}. Rolled back: ${done.join("; ")}. After the rollback, ${after}`);
    }
    log(publicPaths.length > 0
      ? `  ✓ ${prefix} redirects to ${config.team}.cloudflareaccess.com, and its ${publicPaths.length} public path(s) answer signed out`
      : `  ✓ ${prefix} redirects to ${config.team}.cloudflareaccess.com`);
  }

  for (const action of actions.filter((a) => a.step !== "access" && a.state === "create")) {
    if (action.kind === "d1-database") {
      databaseId = (await createD1(cf, action.name)).uuid;
      log(`  + created D1 database "${action.name}" (${databaseId})`);
    } else if (action.kind === "d1-config") {
      writeFileSync(action.path, replacePlaceholder(readFileSync(action.path, "utf8"), databaseId));
      log(`  + wrote database_id into ${action.path} — commit it through the normal delivery path`);
    } else if (action.kind === "r2-bucket") {
      await createR2Bucket(cf, action.name);
      log(`  + created R2 bucket "${action.name}"`);
    } else if (action.kind === "route") {
      await createWorkerRoute(cf, action.zoneId, { pattern: action.pattern, script: action.script });
      log(`  + created route ${action.pattern} → Worker "${action.script}"`);
    } else if (action.kind === "gh-environment") {
      ghCreateEnvironment(run, repo);
      log(`  + created environment ${GITHUB_ENVIRONMENT} on ${repo}`);
    } else if (action.kind === "gh-secret") {
      const value = action.name === "CLOUDFLARE_API_TOKEN" ? keychainRead(run, DEPLOY_TOKEN_ACCOUNT) : config.accountId;
      ghSetSecret(run, repo, action.name, value);
      log(`  + set secret ${action.name} on ${repo}/${GITHUB_ENVIRONMENT}`);
    }
  }
}

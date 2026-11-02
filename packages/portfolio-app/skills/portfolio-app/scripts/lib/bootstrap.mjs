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

import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { accessDestinations, classifyRoute } from "./prefix.mjs";
import { verifyPath, waitForAccess } from "./verify.mjs";
import {
  appUris,
  createD1,
  createR2Bucket,
  createReusablePolicy,
  createSelfHostedApp,
  createWorkerRoute,
  findAppsTouching,
  findD1,
  findR2Bucket,
  findReusablePolicy,
  findZone,
  listWorkerRoutes,
  policyEmails,
  workerExists,
} from "./cloudflare.mjs";
import { D1_PLACEHOLDER, d1Target, parseJsonc, r2Target, replacePlaceholder } from "./wrangler-config.mjs";
import {
  DEPLOY_TOKEN_ACCOUNT,
  GITHUB_ENVIRONMENT,
  KEYCHAIN_SERVICE,
  POLICY_NAME,
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

async function planAccess({ cf, fetch }, { repo, prefix, config }) {
  const actions = [];
  const uris = accessDestinations(config.domain, prefix);

  const policy = await findReusablePolicy(cf, POLICY_NAME);
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
  const exact = touching.filter((app) => sameSet(appUris(app), uris));
  const partial = touching.filter((app) => !exact.includes(app));
  if (partial.length > 0) {
    for (const app of partial) {
      actions.push({ step: "access", kind: "app", state: "conflict",
        summary: `Access app "${app.name}" guards [${appUris(app).join(", ")}], which overlaps [${uris.join(", ")}] without matching it; fix or remove it by hand` });
    }
  } else if (exact.length > 0) {
    const app = exact[0];
    actions.push({ step: "access", kind: "app", state: "ok", summary: `Access app "${app.name}" guards ${uris.join(" + ")}` });
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

  const now = await verifyPath({ fetch, domain: config.domain, prefix, team: config.team });
  actions.push({ step: "access", kind: "check", state: now.ok ? "ok" : "info",
    summary: now.ok ? `${prefix} already redirects to ${config.team}.cloudflareaccess.com`
      : `now: ${now.results.map((r) => `${r.url} ${r.reason}`).join("; ")}` });
  return actions;
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

/**
 * Read-only. `deps = { cf, run, fetch }`;
 * `opts = { repo, prefix, cloneDir, d1Flag, public, config }`.
 */
export async function planBootstrap(deps, opts) {
  const clone = readClone(deps, opts);
  return [
    ...(opts.public ? await planPublicAccess(deps, opts) : await planAccess(deps, opts)),
    ...(await planD1(deps, opts, clone)),
    ...(opts.public ? await planR2(deps, clone) : []),
    ...(opts.public ? await planRoutes(deps, opts, clone) : []),
    ...planGithub(deps, opts),
  ];
}

const MARK = { ok: "✓", create: "+", warn: "!", conflict: "✗", info: "·" };
const HEADING = { access: "Cloudflare Access", d1: "D1", r2: "R2", route: "Worker routes", github: "GitHub" };
const OPTIONAL_STEPS = ["r2", "route"];

export function formatPlan(actions, { repo, prefix, apply, public: isPublic }) {
  const mode = isPublic ? " (public: no Access sign-in)" : "";
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
 * Carries out the "create" actions in plan order. Refuses while any conflict stands.
 * Private: Access first, and nothing else until the path redirects to the Access login.
 * Public: no Access step and no login wait; Cloudflare resources, then GitHub.
 */
export async function applyBootstrap({ cf, run, fetch, sleep, log }, actions, { prefix, repo, config, public: isPublic }) {
  const conflicts = actions.filter((a) => a.state === "conflict");
  if (conflicts.length > 0) throw new Error(`refusing to apply: ${conflicts.length} conflict(s) in the plan`);

  let policyId = actions.find((a) => a.kind === "policy")?.policyId ?? null;
  let databaseId = actions.find((a) => a.kind === "d1-database" || a.kind === "d1-config")?.databaseId ?? null;

  if (!isPublic) {
    for (const action of actions.filter((a) => a.step === "access" && a.state === "create")) {
      if (action.kind === "policy") {
        policyId = (await createReusablePolicy(cf, { name: action.name, emails: action.emails })).id;
        log(`  + created reusable policy "${action.name}"`);
      } else if (action.kind === "app") {
        await createSelfHostedApp(cf, { name: action.name, uris: action.uris, policyId });
        log(`  + created Access app "${action.name}"`);
      }
    }
    const check = await waitForAccess({ fetch, domain: config.domain, prefix, team: config.team, sleep });
    if (!check.ok) {
      throw new Error(`Access is not answering for ${prefix}: ${check.results.filter((r) => !r.ok).map((r) => `${r.url} ${r.reason}`).join("; ")}`);
    }
    log(`  ✓ ${prefix} redirects to ${config.team}.cloudflareaccess.com`);
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

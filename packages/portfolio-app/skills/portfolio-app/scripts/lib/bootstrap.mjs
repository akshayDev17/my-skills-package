// bootstrap = plan, then (only with --apply) carry the plan out.
//
// Planning makes read calls only: Cloudflare GETs, `gh api` reads, `git remote`, and a
// keychain existence check. The plan is plain data — one action per thing that exists
// or must be created — so the same records drive the printout, the conflict refusal and
// the apply loop. Apply refuses outright while any conflict stands, and then creates
// only the actions marked "create", which is what makes a re-run a no-op.

import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { accessDestinations } from "./prefix.mjs";
import { verifyPath, waitForAccess } from "./verify.mjs";
import {
  appUris,
  createD1,
  createReusablePolicy,
  createSelfHostedApp,
  findAppsTouching,
  findD1,
  findReusablePolicy,
  policyEmails,
} from "./cloudflare.mjs";
import { D1_PLACEHOLDER, d1Target, parseJsonc, replacePlaceholder } from "./wrangler-config.mjs";
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

async function planD1({ cf, run }, { repo, cloneDir, d1Flag }) {
  const wranglerPath = join(cloneDir, "backend", "wrangler.jsonc");
  const conflict = (summary) => [{ step: "d1", kind: "d1", state: "conflict", summary }];

  const cloned = cloneRepo(run, cloneDir);
  if (cloned?.toLowerCase() !== repo.toLowerCase()) {
    return conflict(`${cloneDir} is a clone of ${cloned ?? "nothing GitHub knows"}, not ${repo}; pass --clone <path to a clone of ${repo}>`);
  }
  if (!existsSync(wranglerPath)) return conflict(`${wranglerPath} does not exist`);

  let target;
  try {
    target = d1Target(parseJsonc(readFileSync(wranglerPath, "utf8")), d1Flag);
  } catch (err) {
    return conflict(`${wranglerPath}: ${err.message}`);
  }
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

/** Read-only. `deps = { cf, run, fetch }`; `opts = { repo, prefix, cloneDir, d1Flag, config }`. */
export async function planBootstrap(deps, opts) {
  return [
    ...(await planAccess(deps, opts)),
    ...(await planD1(deps, opts)),
    ...planGithub(deps, opts),
  ];
}

const MARK = { ok: "✓", create: "+", warn: "!", conflict: "✗", info: "·" };
const HEADING = { access: "Cloudflare Access", d1: "D1", github: "GitHub" };

export function formatPlan(actions, { repo, prefix, apply }) {
  const lines = [`portfolio-app bootstrap ${repo} ${prefix} — ${apply ? "applying" : "plan only; nothing changes without --apply"}`];
  for (const step of ["access", "d1", "github"]) {
    lines.push(step === "github" ? `${HEADING[step]} ${repo}` : HEADING[step]);
    for (const a of actions.filter((x) => x.step === step)) lines.push(`  ${MARK[a.state]} ${a.summary}`);
  }
  const creates = actions.filter((a) => a.state === "create").length;
  const conflicts = actions.filter((a) => a.state === "conflict").length;
  lines.push(conflicts > 0 ? `${conflicts} conflict(s) — resolve them before --apply`
    : creates === 0 ? "nothing to create — already bootstrapped" : `${creates} change(s) planned`);
  return lines.join("\n");
}

/** Carries out the "create" actions in order. Refuses while any conflict stands. */
export async function applyBootstrap({ cf, run, fetch, sleep, log }, actions, { prefix, repo, config }) {
  const conflicts = actions.filter((a) => a.state === "conflict");
  if (conflicts.length > 0) throw new Error(`refusing to apply: ${conflicts.length} conflict(s) in the plan`);

  let policyId = actions.find((a) => a.kind === "policy")?.policyId ?? null;
  let databaseId = actions.find((a) => a.kind === "d1-database" || a.kind === "d1-config")?.databaseId ?? null;

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

  for (const action of actions.filter((a) => a.step !== "access" && a.state === "create")) {
    if (action.kind === "d1-database") {
      databaseId = (await createD1(cf, action.name)).uuid;
      log(`  + created D1 database "${action.name}" (${databaseId})`);
    } else if (action.kind === "d1-config") {
      writeFileSync(action.path, replacePlaceholder(readFileSync(action.path, "utf8"), databaseId));
      log(`  + wrote database_id into ${action.path} — commit it through the normal delivery path`);
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

// Stubs for the two layers bootstrap talks through: HTTP (`fetch`) and processes
// (`run`). Both record every call, so a test can assert what was — and was not — sent.

import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

export const ACCOUNT = "0123456789abcdef0123456789abcdef";
export const DOMAIN = "example.com";
export const TEAM = "test-team";
export const SETUP_TOKEN = "setup-token-value-not-for-output";
export const DEPLOY_TOKEN = "deploy-token-value-not-for-output";

export const config = {
  domain: DOMAIN,
  accountId: ACCOUNT,
  team: TEAM,
  allowedEmails: ["owner@example.com"],
};

const jsonResponse = (status, body) => ({
  ok: status >= 200 && status < 300,
  status,
  headers: new Map(),
  json: async () => body,
});

const redirect = (location) => ({ ok: false, status: 302, headers: new Map([["location", location]]) });
const plain = (status) => ({ ok: status < 300, status, headers: new Map() });

export const ACCESS_LOGIN = `https://${TEAM}.cloudflareaccess.com/cdn-cgi/access/login/${DOMAIN}?kid=abc`;

/**
 * A fake Cloudflare account plus the public site. `state` is mutated by POSTs so an
 * apply followed by a plan sees what apply created.
 */
export function fakeFetch(state) {
  const calls = [];
  const fetch = async (url, init = {}) => {
    const method = init.method ?? "GET";
    calls.push({ url, method, body: init.body ? JSON.parse(init.body) : undefined, headers: init.headers });
    const u = new URL(url);

    if (u.hostname !== "api.cloudflare.com") {
      const protectedPaths = state.protected ?? [];
      return protectedPaths.some((p) => u.pathname === p || u.pathname.startsWith(`${p}/`))
        ? redirect(ACCESS_LOGIN)
        : plain(200);
    }
    if (init.headers?.Authorization !== `Bearer ${SETUP_TOKEN}`) {
      return jsonResponse(403, { success: false, errors: [{ code: 10000, message: "Authentication error" }] });
    }
    const path = u.pathname.replace("/client/v4", "");
    const base = `/accounts/${ACCOUNT}`;
    const list = (items) => jsonResponse(200, { success: true, result: items, result_info: { page: 1, total_pages: 1 } });

    if (path === `${base}/access/policies` && method === "GET") return list(state.policies);
    if (path === `${base}/access/policies` && method === "POST") {
      const policy = { id: `pol-${state.policies.length + 1}`, reusable: true, ...JSON.parse(init.body) };
      state.policies.push(policy);
      return jsonResponse(200, { success: true, result: policy });
    }
    if (path === `${base}/access/apps` && method === "GET") return list(state.apps);
    if (path === `${base}/access/apps` && method === "POST") {
      const app = { id: `app-${state.apps.length + 1}`, ...JSON.parse(init.body) };
      state.apps.push(app);
      state.protected = [...(state.protected ?? []), new URL(`https://${app.domain}`).pathname];
      return jsonResponse(200, { success: true, result: app });
    }
    if (path === `${base}/d1/database` && method === "GET") {
      const name = u.searchParams.get("name");
      return list(state.d1.filter((db) => !name || db.name.includes(name)));
    }
    if (path === `${base}/d1/database` && method === "POST") {
      const db = { uuid: `00000000-0000-4000-8000-00000000000${state.d1.length + 1}`, ...JSON.parse(init.body) };
      state.d1.push(db);
      return jsonResponse(200, { success: true, result: db });
    }
    return jsonResponse(404, { success: false, errors: [{ code: 7003, message: `no route ${method} ${path}` }] });
  };
  return { fetch, calls };
}

/** A fake `run` for security, git and gh, backed by the same kind of mutable state. */
export function fakeRun(state) {
  const calls = [];
  const run = (cmd, args, opts = {}) => {
    calls.push({ cmd, args, input: opts.input });
    const ok = (stdout = "") => ({ code: 0, stdout, stderr: "" });
    const fail = (stderr) => ({ code: 1, stdout: "", stderr });

    if (cmd === "security") {
      const account = args[args.indexOf("-a") + 1];
      const value = state.keychain?.[account];
      if (!value) return fail("security: SecKeychainSearchCopyNext: The specified item could not be found in the keychain.");
      return args.includes("-w") ? ok(`${value}\n`) : ok(`keychain: "login.keychain-db"\nclass: "genp"\n`);
    }
    if (cmd === "git") {
      if (args.includes("rev-parse")) return state.configInRepo ? ok("true\n") : fail("fatal: not a git repository");
      if (args.includes("remote")) return state.remote ? ok(`${state.remote}\n`) : fail("error: No such remote 'origin'");
    }
    if (cmd === "gh") {
      const env = state.github;
      if (args[0] === "api" && args[1] === "-X" && args[2] === "PUT") {
        env.environment = true;
        return ok("{}");
      }
      if (args[0] === "api") return env.environment ? ok("{}") : fail("gh: Not Found (HTTP 404)");
      if (args[0] === "secret" && args[1] === "list") {
        return ok(JSON.stringify(env.secrets.map((name) => ({ name }))));
      }
      if (args[0] === "secret" && args[1] === "set") {
        env.secrets.push(args[2]);
        env.values = { ...env.values, [args[2]]: opts.input };
        return ok("");
      }
    }
    return fail(`unexpected command: ${cmd} ${args.join(" ")}`);
  };
  return { run, calls };
}

export const WRANGLER_WITH_PLACEHOLDER = `{
\t"name": "graph-notes-backend",
\t// Under /graph/ on purpose: ai-chat-archive owns the site-wide /api/*.
\t"routes": [{ "pattern": "example.com/graph/api/*", "zone_name": "example.com" },],
\t"d1_databases": [
\t\t{
\t\t\t"binding": "DB",
\t\t\t"database_name": "graph-notes",
\t\t\t/* CI refuses to deploy while "REPLACE_AFTER_d1_create" is here. */
\t\t\t"database_id": "REPLACE_AFTER_d1_create"
\t\t}
\t]
}
`;

/** A directory standing in for a local clone, with backend/wrangler.jsonc. */
export function makeClone(wranglerText = WRANGLER_WITH_PLACEHOLDER) {
  const dir = mkdtempSync(join(tmpdir(), "portfolio-app-test-"));
  mkdirSync(join(dir, "backend"));
  writeFileSync(join(dir, "backend", "wrangler.jsonc"), wranglerText);
  return dir;
}

/** A fresh account with nothing set up, and a clone of the repo. */
export function freshWorld() {
  return {
    site: { policies: [], apps: [], d1: [], protected: [] },
    local: {
      keychain: { "cloudflare-setup-token": SETUP_TOKEN, "cloudflare-deploy-token": DEPLOY_TOKEN },
      remote: "git@github.com:owner/graph-notes.git",
      github: { environment: false, secrets: [] },
    },
  };
}

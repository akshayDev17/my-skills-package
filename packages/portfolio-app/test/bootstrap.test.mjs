import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { applyBootstrap, formatPlan, planBootstrap } from "../skills/portfolio-app/scripts/lib/bootstrap.mjs";
import { POLICY_NAME } from "../skills/portfolio-app/scripts/lib/local.mjs";
import { main } from "../skills/portfolio-app/scripts/portfolio-app.mjs";
import {
  ACCOUNT, DEPLOY_TOKEN, DOMAIN, SETUP_TOKEN, TEAM, WRANGLER_WITH_PLACEHOLDER,
  config, fakeFetch, fakeRun, freshWorld, makeClone,
} from "./helpers.mjs";

const REPO = "owner/graph-notes";
const PREFIX = "/graph";

function harness(world) {
  const http = fakeFetch(world.site);
  const proc = fakeRun(world.local);
  const cf = { fetch: http.fetch, token: SETUP_TOKEN, accountId: ACCOUNT };
  const logs = [];
  return { http, proc, cf, logs, deps: { cf, run: proc.run, fetch: http.fetch, sleep: async () => {}, log: (l) => logs.push(l) } };
}

const isWriteRun = ({ cmd, args }) =>
  (cmd === "gh" && (args.includes("-X") || (args[0] === "secret" && args[1] === "set"))) ||
  (cmd === "security" && args.includes("-w") && args.includes("cloudflare-deploy-token"));

test("plan on a fresh account plans every step and makes no write call", async () => {
  const world = freshWorld();
  const cloneDir = makeClone();
  const h = harness(world);
  const actions = await planBootstrap(h.deps, { repo: REPO, prefix: PREFIX, cloneDir, config });

  assert.deepEqual(
    actions.filter((a) => a.state === "create").map((a) => `${a.kind}${a.name ? `:${a.name}` : ""}`),
    [
      "policy:portfolio-app sign-in",
      "app:graph-notes (/graph)",
      "d1-database:graph-notes",
      "d1-config",
      "gh-environment",
      "gh-secret:CLOUDFLARE_API_TOKEN",
      "gh-secret:CLOUDFLARE_ACCOUNT_ID",
    ],
  );
  assert.deepEqual(h.http.calls.filter((c) => c.method !== "GET"), [], "no Cloudflare write in plan mode");
  assert.deepEqual(h.proc.calls.filter(isWriteRun), [], "no gh write and no deploy-token read in plan mode");
  assert.equal(readFileSync(join(cloneDir, "backend", "wrangler.jsonc"), "utf8"), WRANGLER_WITH_PLACEHOLDER, "clone untouched");
  assert.deepEqual(world.site.apps, []);
  assert.deepEqual(world.local.github, { environment: false, secrets: [] });
});

test("the plan's Access app guards exactly <domain>/<prefix> and <domain>/<prefix>/*", async () => {
  const world = freshWorld();
  const h = harness(world);
  const actions = await planBootstrap(h.deps, { repo: REPO, prefix: PREFIX, cloneDir: makeClone(), config });
  assert.deepEqual(actions.find((a) => a.kind === "app").uris, [`${DOMAIN}/graph`, `${DOMAIN}/graph/*`]);
});

test("apply creates everything, wires the policy and D1 id through, and a re-plan is a no-op", async () => {
  const world = freshWorld();
  const cloneDir = makeClone();
  const h = harness(world);
  const opts = { repo: REPO, prefix: PREFIX, cloneDir, config };
  await applyBootstrap(h.deps, await planBootstrap(h.deps, opts), opts);

  const [policy] = world.site.policies;
  assert.equal(policy.decision, "allow");
  assert.deepEqual(policy.include, [{ email: { email: "owner@example.com" } }]);

  const [app] = world.site.apps;
  assert.equal(app.type, "self_hosted");
  assert.deepEqual(app.destinations, [
    { type: "public", uri: `${DOMAIN}/graph` },
    { type: "public", uri: `${DOMAIN}/graph/*` },
  ]);
  assert.deepEqual(app.policies, [{ id: policy.id, precedence: 1 }]);
  assert.equal(app.self_hosted_domains, undefined, "the deprecated field is not sent");

  const [db] = world.site.d1;
  const written = readFileSync(join(cloneDir, "backend", "wrangler.jsonc"), "utf8");
  assert.match(written, new RegExp(`"database_id": "${db.uuid}"`));
  assert.match(written, /\/\/ Under \/graph\/ on purpose/, "comments survive the write");

  assert.equal(world.local.github.environment, true);
  assert.deepEqual(world.local.github.values, { CLOUDFLARE_API_TOKEN: DEPLOY_TOKEN, CLOUDFLARE_ACCOUNT_ID: ACCOUNT });
  assert.ok(h.logs.some((l) => l.includes(`redirects to ${TEAM}.cloudflareaccess.com`)), "Access confirmed after apply");

  const again = harness(world);
  const replan = await planBootstrap(again.deps, opts);
  assert.deepEqual(replan.filter((a) => a.state === "create"), [], "second run has nothing to create");
  assert.match(formatPlan(replan, { ...opts, apply: false }), /nothing to create — already bootstrapped/);
});

test("the setup token goes to Cloudflare only, and only the deploy token goes to GitHub", async () => {
  const world = freshWorld();
  const h = harness(world);
  const opts = { repo: REPO, prefix: PREFIX, cloneDir: makeClone(), config };
  await applyBootstrap(h.deps, await planBootstrap(h.deps, opts), opts);

  for (const call of h.http.calls) {
    const toCloudflare = new URL(call.url).hostname === "api.cloudflare.com";
    assert.equal(JSON.stringify(call.headers ?? {}).includes(SETUP_TOKEN), toCloudflare, call.url);
  }
  const ghInputs = h.proc.calls.filter((c) => c.cmd === "gh").map((c) => c.input).filter(Boolean);
  assert.ok(!ghInputs.includes(SETUP_TOKEN), "the broad setup token never reaches gh");
  for (const call of h.proc.calls) {
    for (const token of [SETUP_TOKEN, DEPLOY_TOKEN]) assert.ok(!call.args.includes(token), "no secret in argv");
  }
  for (const line of h.logs) {
    for (const token of [SETUP_TOKEN, DEPLOY_TOKEN]) assert.ok(!line.includes(token), "no secret in output");
  }
});

test("existing pieces are reused, not recreated", async () => {
  const world = freshWorld();
  world.site.policies.push({ id: "pol-x", name: POLICY_NAME, reusable: true, include: [{ email: { email: "owner@example.com" } }] });
  world.site.apps.push({ id: "app-x", name: "graph", destinations: [{ uri: `${DOMAIN}/graph` }, { uri: `${DOMAIN}/graph/*` }], policies: [{ id: "pol-x" }] });
  world.site.protected.push("/graph");
  world.site.d1.push({ uuid: "existing-db-uuid", name: "graph-notes" });
  world.local.github = { environment: true, secrets: ["CLOUDFLARE_ACCOUNT_ID"] };
  const cloneDir = makeClone();
  const h = harness(world);
  const opts = { repo: REPO, prefix: PREFIX, cloneDir, config };
  const actions = await planBootstrap(h.deps, opts);

  assert.deepEqual(actions.filter((a) => a.state === "create").map((a) => a.kind), ["d1-config", "gh-secret"]);
  assert.deepEqual(actions.filter((a) => a.state === "warn"), []);
  await applyBootstrap(h.deps, actions, opts);
  assert.equal(world.site.policies.length, 1);
  assert.equal(world.site.apps.length, 1);
  assert.equal(world.site.d1.length, 1);
  assert.match(readFileSync(join(cloneDir, "backend", "wrangler.jsonc"), "utf8"), /"database_id": "existing-db-uuid"/);
  assert.deepEqual(world.local.github.secrets, ["CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN"]);
});

test("a policy whose emails drifted from the config is reported, never rewritten", async () => {
  const world = freshWorld();
  world.site.policies.push({ id: "pol-x", name: POLICY_NAME, include: [{ email: { email: "someone@else.com" } }] });
  const h = harness(world);
  const actions = await planBootstrap(h.deps, { repo: REPO, prefix: PREFIX, cloneDir: makeClone(), config });
  const warn = actions.find((a) => a.kind === "policy-emails");
  assert.equal(warn.state, "warn");
  assert.match(warn.summary, /left unchanged/);
});

test("an Access app that overlaps the prefix without matching it is a conflict, and apply refuses", async () => {
  const world = freshWorld();
  world.site.apps.push({ id: "app-x", name: "half", destinations: [{ uri: `${DOMAIN}/graph` }] });
  const h = harness(world);
  const opts = { repo: REPO, prefix: PREFIX, cloneDir: makeClone(), config };
  const actions = await planBootstrap(h.deps, opts);
  assert.equal(actions.find((a) => a.kind === "app").state, "conflict");
  await assert.rejects(applyBootstrap(h.deps, actions, opts), /refusing to apply/);
  assert.deepEqual(h.http.calls.filter((c) => c.method !== "GET"), []);
});

test("a clone of a different repo is a conflict rather than a write into the wrong file", async () => {
  const world = freshWorld();
  world.local.remote = "https://github.com/owner/quiz-app.git";
  const h = harness(world);
  const actions = await planBootstrap(h.deps, { repo: REPO, prefix: PREFIX, cloneDir: makeClone(), config });
  assert.match(actions.find((a) => a.step === "d1").summary, /clone of owner\/quiz-app, not owner\/graph-notes/);
});

test("a missing deploy-token keychain entry is a conflict in the plan", async () => {
  const world = freshWorld();
  delete world.local.keychain["cloudflare-deploy-token"];
  const h = harness(world);
  const actions = await planBootstrap(h.deps, { repo: REPO, prefix: PREFIX, cloneDir: makeClone(), config });
  const secret = actions.find((a) => a.name === "CLOUDFLARE_API_TOKEN");
  assert.equal(secret.state, "conflict");
  assert.match(secret.summary, /account="cloudflare-deploy-token"/);
});

test("apply fails loudly when the path never starts redirecting to Access", async () => {
  const world = freshWorld();
  const h = harness(world);
  const opts = { repo: REPO, prefix: PREFIX, cloneDir: makeClone(), config };
  const actions = await planBootstrap(h.deps, opts);
  const neverProtected = async (url, init) =>
    new URL(url).hostname === "api.cloudflare.com" ? h.http.fetch(url, init) : { status: 200, headers: new Map() };
  await assert.rejects(
    applyBootstrap({ ...h.deps, fetch: neverProtected }, actions, opts),
    /Access is not answering for \/graph/,
  );
  assert.deepEqual(world.local.github.secrets, [], "GitHub is not given a deploy token for an unprotected path");
});

// --- CLI ---------------------------------------------------------------------------

function cli(world, argv) {
  const http = fakeFetch(world.site);
  const proc = fakeRun(world.local);
  const out = [];
  const err = [];
  return main(argv, { fetch: http.fetch, run: proc.run, log: (l) => out.push(l), error: (l) => err.push(l) })
    .then((code) => ({ code, out: out.join("\n"), err: err.join("\n"), http, proc }));
}

function writeConfig() {
  const dir = mkdtempSync(join(tmpdir(), "portfolio-app-config-"));
  const path = join(dir, "config.json");
  writeFileSync(path, JSON.stringify(config));
  return path;
}

test("cli bootstrap without --apply prints the plan, exits 0 and writes nothing", async () => {
  const world = freshWorld();
  const cloneDir = makeClone();
  const r = await cli(world, ["bootstrap", "--repo", REPO, "--path", PREFIX, "--clone", cloneDir, "--config", writeConfig()]);
  assert.equal(r.code, 0, r.err);
  assert.match(r.out, /plan only; nothing changes without --apply/);
  assert.match(r.out, /\+ create self-hosted Access app "graph-notes \(\/graph\)"/);
  assert.deepEqual(r.http.calls.filter((c) => c.method !== "GET"), []);
  assert.deepEqual(r.proc.calls.filter(isWriteRun), []);
  assert.ok(!r.out.includes(SETUP_TOKEN) && !r.out.includes(DEPLOY_TOKEN));
});

test("cli bootstrap --apply carries the plan out", async () => {
  const world = freshWorld();
  const r = await cli(world, ["bootstrap", "--repo", REPO, "--path", PREFIX, "--clone", makeClone(), "--config", writeConfig(), "--apply"]);
  assert.equal(r.code, 0, r.err);
  assert.equal(world.site.apps.length, 1);
  assert.ok(!r.out.includes(SETUP_TOKEN) && !r.out.includes(DEPLOY_TOKEN));
});

test("cli refuses a config file kept inside a git repo", async () => {
  const world = freshWorld();
  world.local.configInRepo = true;
  const r = await cli(world, ["bootstrap", "--repo", REPO, "--path", PREFIX, "--config", writeConfig()]);
  assert.equal(r.code, 2);
  assert.match(r.err, /inside a git work tree/);
});

test("cli refuses reserved prefixes before touching anything", async () => {
  const world = freshWorld();
  for (const path of ["/", "/api", "/_next", "/vendors"]) {
    const r = await cli(world, ["bootstrap", "--repo", REPO, "--path", path, "--config", writeConfig(), "--apply"]);
    assert.equal(r.code, 2, path);
    assert.match(r.err, /refusing path/);
    assert.deepEqual(r.http.calls, []);
  }
});

test("cli verify needs no token or config and exits by the redirect", async () => {
  const world = freshWorld();
  world.site.protected.push("/quiz");
  const pass = await cli(world, ["verify", "--path", "/quiz", "--domain", DOMAIN, "--team", TEAM]);
  assert.equal(pass.code, 0, pass.err);
  assert.deepEqual(pass.proc.calls, [], "no keychain, git or gh");

  const fail = await cli(world, ["verify", "--path", "/chat-archives", "--domain", DOMAIN]);
  assert.equal(fail.code, 1);
  assert.match(fail.out, /NOT behind Cloudflare Access/);
});

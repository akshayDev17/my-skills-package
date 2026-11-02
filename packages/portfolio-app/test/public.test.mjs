// --public: no Access step; D1, R2, Worker routes and GitHub secrets instead. Every test
// here also checks the opposite of what --public removes: no Access write, ever.

import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { applyBootstrap, formatPlan, planBootstrap } from "../skills/portfolio-app/scripts/lib/bootstrap.mjs";
import { main } from "../skills/portfolio-app/scripts/portfolio-app.mjs";
import {
  ACCOUNT, DEPLOY_TOKEN, DOMAIN, SETUP_TOKEN, WRANGLER_PUBLIC,
  config, fakeFetch, fakeRun, freshPublicWorld, makeClone,
} from "./helpers.mjs";

const REPO = "owner/election-atlas";
const PREFIX = "/work/election-atlas";
const ROUTES = [`${DOMAIN}/work/election-atlas`, `${DOMAIN}/work/election-atlas/*`];

function harness(world) {
  const http = fakeFetch(world.site);
  const proc = fakeRun(world.local);
  const cf = { fetch: http.fetch, token: SETUP_TOKEN, accountId: ACCOUNT };
  const logs = [];
  return { http, proc, logs, deps: { cf, run: proc.run, fetch: http.fetch, sleep: async () => {}, log: (l) => logs.push(l) } };
}

const opts = (cloneDir, extra = {}) => ({ repo: REPO, prefix: PREFIX, cloneDir, config, public: true, ...extra });
const creates = (actions) => actions.filter((a) => a.state === "create").map((a) => `${a.kind}${a.name || a.pattern ? `:${a.name ?? a.pattern}` : ""}`);
const writes = (calls) => calls.filter((c) => c.method !== "GET");
const accessCalls = (calls) => calls.filter((c) => c.url.includes("/access/"));

function deployedWorld() {
  const world = freshPublicWorld();
  world.site.scripts.push("election-atlas");
  return world;
}

test("public plan: says the path will be public, plans D1, R2, both routes and GitHub, and writes nothing", async () => {
  const world = deployedWorld();
  const cloneDir = makeClone(WRANGLER_PUBLIC);
  const h = harness(world);
  const actions = await planBootstrap(h.deps, opts(cloneDir));

  assert.deepEqual(creates(actions), [
    "d1-database:election-atlas",
    "d1-config",
    "r2-bucket:election-atlas-bills",
    `route:${ROUTES[0]}`,
    `route:${ROUTES[1]}`,
    "gh-environment",
    "gh-secret:CLOUDFLARE_API_TOKEN",
    "gh-secret:CLOUDFLARE_ACCOUNT_ID",
  ]);
  assert.ok(!actions.some((a) => a.kind === "policy"), "no sign-in policy is planned");

  const text = formatPlan(actions, { ...opts(cloneDir), apply: false });
  assert.match(text, /^portfolio-app bootstrap owner\/election-atlas \/work\/election-atlas \(public: no Access sign-in\) — plan only/);
  assert.match(text, /! public app: example\.com\/work\/election-atlas \+ example\.com\/work\/election-atlas\/\* will be reachable by anyone — the Access sign-in step is skipped/);
  assert.match(text, /\n {2}· route example\.com\/\* → Worker "home-site" is broader; the app's routes are more specific and win under \/work\/election-atlas — left alone/);
  assert.match(text, /\nR2\n {2}\+ create R2 bucket "election-atlas-bills"\nWorker routes\n/);

  assert.deepEqual(writes(h.http.calls), [], "no Cloudflare write in plan mode");
  assert.ok(!h.http.calls.some((c) => c.url.includes("/access/policies")), "public plan does not even read policies");
  assert.equal(readFileSync(join(cloneDir, "backend", "wrangler.jsonc"), "utf8"), WRANGLER_PUBLIC);
});

test("public apply: Cloudflare resources first (D1, R2, routes), then GitHub; no Access call or login wait; re-run is a no-op", async () => {
  const world = deployedWorld();
  const cloneDir = makeClone(WRANGLER_PUBLIC);
  const h = harness(world);
  const actions = await planBootstrap(h.deps, opts(cloneDir));
  const planCalls = h.http.calls.length;
  await applyBootstrap(h.deps, actions, opts(cloneDir));

  const applyCalls = h.http.calls.slice(planCalls);
  assert.deepEqual(applyCalls.map((c) => `${c.method} ${new URL(c.url).pathname.replace(`/client/v4/accounts/${ACCOUNT}`, "")}`), [
    "POST /d1/database",
    "POST /r2/buckets",
    "POST /client/v4/zones/zone-1/workers/routes",
    "POST /client/v4/zones/zone-1/workers/routes",
  ], "no Access write, and no probe of the site (no login wait)");
  assert.deepEqual(accessCalls(writes(h.http.calls)), []);
  assert.deepEqual(h.logs.map((l) => l.trim().split(" ").slice(0, 4).join(" ")), [
    "+ created D1 database",
    "+ wrote database_id into",
    `+ created R2 bucket`,
    `+ created route ${ROUTES[0]}`,
    `+ created route ${ROUTES[1]}`,
    "+ created environment production",
    "+ set secret CLOUDFLARE_API_TOKEN",
    "+ set secret CLOUDFLARE_ACCOUNT_ID",
  ]);

  assert.deepEqual(world.site.routes.filter((r) => r.script === "election-atlas").map((r) => r.pattern), ROUTES);
  assert.deepEqual(world.site.r2.map((b) => b.name), ["election-atlas-bills"]);
  assert.deepEqual(world.site.apps, [], "no Access app");
  assert.deepEqual(world.site.policies, [], "no policy");
  assert.match(readFileSync(join(cloneDir, "backend", "wrangler.jsonc"), "utf8"), /"database_id": "00000000-0000-4000-8000-000000000001"/);
  assert.deepEqual(world.local.github.values, { CLOUDFLARE_API_TOKEN: DEPLOY_TOKEN, CLOUDFLARE_ACCOUNT_ID: ACCOUNT });

  const again = harness(world);
  const replan = await planBootstrap(again.deps, opts(cloneDir));
  assert.deepEqual(creates(replan), [], "second run has nothing to create");
  assert.match(formatPlan(replan, { ...opts(cloneDir), apply: false }), /nothing to create — already bootstrapped$/);
});

test("public, before the Worker's first deploy: routes wait for a re-run, everything else is created", async () => {
  const world = freshPublicWorld();
  const cloneDir = makeClone(WRANGLER_PUBLIC);
  const h = harness(world);
  const actions = await planBootstrap(h.deps, opts(cloneDir));
  const pending = actions.find((a) => a.step === "route" && a.later);
  assert.equal(pending.state, "info");
  assert.match(pending.summary, /Worker "election-atlas" is not deployed yet.*re-run with --public after its first deploy/);
  assert.ok(!creates(actions).some((c) => c.startsWith("route")));

  await applyBootstrap(h.deps, actions, opts(cloneDir));
  assert.equal(world.site.routes.length, 1, "only the home route");
  const waiting = await planBootstrap(harness(world).deps, opts(cloneDir));
  assert.match(formatPlan(waiting, { ...opts(cloneDir), apply: false }), /nothing to create until the Worker's first deploy — then re-run$/);

  world.site.scripts.push("election-atlas");
  const afterDeploy = await planBootstrap(harness(world).deps, opts(cloneDir));
  assert.deepEqual(creates(afterDeploy), ROUTES.map((r) => `route:${r}`));
});

test("public refuses a path that already has an Access app, and applies nothing", async () => {
  const world = deployedWorld();
  world.site.apps.push({ id: "app-x", name: "atlas", destinations: [{ uri: ROUTES[0] }, { uri: ROUTES[1] }] });
  world.site.protected.push(PREFIX);
  const h = harness(world);
  const actions = await planBootstrap(h.deps, opts(makeClone(WRANGLER_PUBLIC)));
  const conflict = actions.find((a) => a.step === "access" && a.state === "conflict");
  assert.match(conflict.summary, /Access app "atlas" guards .*--public refuses a path that already has Access protection/);
  await assert.rejects(applyBootstrap(h.deps, actions, opts(makeClone(WRANGLER_PUBLIC))), /refusing to apply/);
  assert.deepEqual(writes(h.http.calls), []);
  assert.equal(world.site.apps.length, 1, "the Access app is left in place");
});

test("public refuses a path a wider Access app already guards (seen by the redirect)", async () => {
  const world = deployedWorld();
  world.site.protected.push("/work");
  const h = harness(world);
  const actions = await planBootstrap(h.deps, opts(makeClone(WRANGLER_PUBLIC)));
  const conflict = actions.find((a) => a.kind === "check");
  assert.equal(conflict.state, "conflict");
  assert.match(conflict.summary, /already redirect\(s\) to Cloudflare Access/);
});

test("public refuses routes that overlap the prefix unless they are the app's own and inside it", async () => {
  const cases = [
    [{ pattern: `${DOMAIN}/work/election-atlas/*`, script: "old-atlas" }, "conflict", /→ Worker "old-atlas" overlaps/],
    [{ pattern: `${DOMAIN}/work/election-atlas/api/*`, script: "someone-else" }, "conflict", /overlaps/],
    [{ pattern: `${DOMAIN}/work/election-atlas*`, script: "election-atlas" }, "conflict", /reaches outside \/work\/election-atlas/],
    [{ pattern: `${DOMAIN}/work/*`, script: "election-atlas" }, "conflict", /reaches outside/],
    [{ pattern: `${DOMAIN}/work/election-atlas` }, "conflict", /→ no Worker overlaps/],
    [{ pattern: `*${DOMAIN}/work/election-atlas/*`, script: "someone-else" }, "conflict", /overlaps/],
    [{ pattern: `${DOMAIN}/work/election-atlas/api/*`, script: "election-atlas" }, "ok", /sits inside/],
    [{ pattern: `${DOMAIN}/work/*`, script: "portfolio" }, "info", /is broader/],
    [{ pattern: `${DOMAIN}/work/election-atlas-old/*`, script: "someone-else" }, null, null],
    [{ pattern: `other.com/work/election-atlas/*`, script: "someone-else" }, null, null],
  ];
  for (const [route, state, summary] of cases) {
    const world = deployedWorld();
    world.site.routes.push({ id: "route-x", ...route });
    const h = harness(world);
    const actions = await planBootstrap(h.deps, opts(makeClone(WRANGLER_PUBLIC)));
    const found = actions.find((a) => a.step === "route" && a.summary.startsWith(`route ${route.pattern} `));
    if (state === null) {
      assert.equal(found, undefined, route.pattern);
      continue;
    }
    assert.equal(found?.state, state, route.pattern);
    assert.match(found.summary, summary, route.pattern);
    if (state === "conflict") {
      await assert.rejects(applyBootstrap(h.deps, actions, opts(makeClone(WRANGLER_PUBLIC))), /refusing to apply/);
      assert.deepEqual(writes(h.http.calls), [], route.pattern);
    }
  }
});

test("a setup token missing a permission gets a plan conflict naming it", async () => {
  const cases = [
    ["r2", "r2", /may not read R2 buckets .* add "Account · Workers R2 Storage · Edit"/],
    ["zone", "route", /cannot see zone example\.com — give it "Zone · Zone · Read" and "Zone · Workers Routes · Edit"/],
    ["routes", "route", /may not read the Worker routes of example\.com .* add "Zone · Workers Routes · Edit"/],
    ["scripts", "route", /may not list Worker scripts .* add "Account · Workers Scripts · Read"/],
  ];
  for (const [area, step, summary] of cases) {
    const world = freshPublicWorld();
    world.site.deny = [area];
    const h = harness(world);
    const actions = await planBootstrap(h.deps, opts(makeClone(WRANGLER_PUBLIC)));
    const conflict = actions.find((a) => a.step === step && a.state === "conflict");
    assert.ok(conflict, area);
    assert.match(conflict.summary, summary, area);
    assert.ok(!conflict.summary.includes(SETUP_TOKEN));
  }
});

test("--r2 picks one of several buckets; without it several are a conflict", async () => {
  const two = WRANGLER_PUBLIC.replace(
    `[{ "binding": "BILLS", "bucket_name": "election-atlas-bills" }]`,
    `[{ "binding": "BILLS", "bucket_name": "election-atlas-bills" }, { "binding": "MAPS", "bucket_name": "election-atlas-maps" }]`,
  );
  const world = deployedWorld();
  const h = harness(world);
  const ambiguous = await planBootstrap(h.deps, opts(makeClone(two)));
  assert.match(ambiguous.find((a) => a.step === "r2").summary, /several buckets .* pass --r2 <name>/);
  const picked = await planBootstrap(h.deps, opts(makeClone(two), { r2Flag: "election-atlas-maps" }));
  assert.deepEqual(creates(picked).filter((c) => c.startsWith("r2")), ["r2-bucket:election-atlas-maps"]);
});

test("private apps get an R2 step only with --r2, and it runs after the Access check", async () => {
  const world = freshPublicWorld();
  const cloneDir = makeClone(WRANGLER_PUBLIC);
  const h = harness(world);
  const privateOpts = { repo: REPO, prefix: PREFIX, cloneDir, config };

  const without = await planBootstrap(h.deps, privateOpts);
  assert.ok(!without.some((a) => a.step === "r2" || a.step === "route"), "no R2 or route step by default");
  assert.ok(!h.http.calls.some((c) => /\/r2\/|\/zones|\/workers\//.test(c.url)), "and no R2, zone or route read");

  const withR2 = { ...privateOpts, r2Flag: "election-atlas-bills" };
  const actions = await planBootstrap(h.deps, withR2);
  assert.ok(!actions.some((a) => a.step === "route"), "routes stay public-only");
  await applyBootstrap(h.deps, actions, withR2);
  const order = h.logs.map((l) => l.trim().split(" ").slice(0, 3).join(" "));
  assert.ok(order.indexOf("✓ /work/election-atlas redirects") < order.indexOf("+ created R2"), order.join(" | "));
  assert.equal(world.site.apps.length, 1, "the private app still got its Access app");
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
  const path = join(mkdtempSync(join(tmpdir(), "portfolio-app-config-")), "config.json");
  writeFileSync(path, JSON.stringify(config));
  return path;
}

test("cli --public plans a multi-segment prefix, exits 0 and writes nothing", async () => {
  const world = deployedWorld();
  const r = await cli(world, ["bootstrap", "--public", "--repo", REPO, "--path", PREFIX, "--clone", makeClone(WRANGLER_PUBLIC), "--config", writeConfig()]);
  assert.equal(r.code, 0, r.err);
  assert.match(r.out, /\(public: no Access sign-in\) — plan only/);
  assert.match(r.out, /will be reachable by anyone/);
  assert.deepEqual(writes(r.http.calls), []);
  assert.ok(!r.out.includes(SETUP_TOKEN) && !r.out.includes(DEPLOY_TOKEN));
});

test("cli --public --apply carries the plan out and never sends the setup token to GitHub", async () => {
  const world = deployedWorld();
  const r = await cli(world, ["bootstrap", "--public", "--repo", REPO, "--path", PREFIX, "--clone", makeClone(WRANGLER_PUBLIC), "--config", writeConfig(), "--apply"]);
  assert.equal(r.code, 0, r.err);
  assert.match(r.out, /— applying/);
  assert.equal(world.site.routes.length, 3);
  assert.deepEqual(accessCalls(writes(r.http.calls)), []);
  assert.ok(!r.proc.calls.some((c) => c.input === SETUP_TOKEN || c.args.includes(SETUP_TOKEN)));
});

test("cli --public with an Access app on the path exits 2 and applies nothing", async () => {
  const world = deployedWorld();
  world.site.apps.push({ id: "app-x", name: "atlas", destinations: [{ uri: ROUTES[0] }, { uri: ROUTES[1] }] });
  const r = await cli(world, ["bootstrap", "--public", "--repo", REPO, "--path", PREFIX, "--clone", makeClone(WRANGLER_PUBLIC), "--config", writeConfig(), "--apply"]);
  assert.equal(r.code, 2);
  assert.match(r.out, /✗ Access app "atlas"/);
  assert.deepEqual(writes(r.http.calls), []);
});

test("cli refuses an invalid --r2 name and keeps every prefix refusal in public mode", async () => {
  const world = deployedWorld();
  const bad = await cli(world, ["bootstrap", "--public", "--repo", REPO, "--path", PREFIX, "--r2", "Bad_Name", "--config", writeConfig()]);
  assert.equal(bad.code, 2);
  assert.match(bad.err, /not a valid R2 bucket name/);
  for (const path of ["", "/", "/api", "/_next/x", "/vendors", "/work/*", "/work/a.b", "/work?x=1", "/Work"]) {
    const r = await cli(world, ["bootstrap", "--public", "--repo", REPO, "--path", path, "--config", writeConfig(), "--apply"]);
    assert.equal(r.code, 2, path);
    assert.deepEqual(r.http.calls, [], path);
  }
});

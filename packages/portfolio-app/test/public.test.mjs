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
// The app's one Worker route, and the exact pair apps got before it (also the Access destinations).
const ROUTE = `${DOMAIN}/work/election-atlas*`;
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

/** The app's Worker is deployed and, as it must, passes every path outside its prefix to the origin. */
function deployedWorld() {
  const world = freshPublicWorld();
  world.site.scripts.push("election-atlas");
  world.site.workerPrefixes = { "election-atlas": PREFIX };
  return world;
}

const apiCalls = (calls) => calls.filter((c) => c.url.startsWith("https://api.cloudflare.com/"))
  .map((c) => `${c.method} ${new URL(c.url).pathname.replace(`/client/v4/accounts/${ACCOUNT}`, "").replace("/client/v4", "")}`);
const siteProbes = (calls) => calls.filter((c) => !c.url.startsWith("https://api.cloudflare.com/")).map((c) => c.url);
const ROUTE_PROBES = [
  `https://${DOMAIN}/work/election-atlas`,
  `https://${DOMAIN}/work/election-atlas?portfolio-app-verify=1`,
  `https://${DOMAIN}/work/election-atlasportfolio-app-verify`,
];

test("public plan: says the path will be public, plans D1, R2, the one <prefix>* route and GitHub, and writes nothing", async () => {
  const world = deployedWorld();
  const cloneDir = makeClone(WRANGLER_PUBLIC);
  const h = harness(world);
  const actions = await planBootstrap(h.deps, opts(cloneDir));

  assert.deepEqual(creates(actions), [
    "d1-database:election-atlas",
    "d1-config",
    "r2-bucket:election-atlas-bills",
    `route:${ROUTE}`,
    "gh-environment",
    "gh-secret:CLOUDFLARE_API_TOKEN",
    "gh-secret:CLOUDFLARE_ACCOUNT_ID",
  ]);
  assert.ok(!actions.some((a) => a.kind === "policy"), "no sign-in policy is planned");
  assert.ok(!actions.some((a) => a.pattern === ROUTES[0] || a.pattern === ROUTES[1]), "the exact pair is no longer created");

  const text = formatPlan(actions, { ...opts(cloneDir), apply: false });
  assert.match(text, /^portfolio-app bootstrap owner\/election-atlas \/work\/election-atlas \(public: no Access sign-in\) — plan only/);
  assert.match(text, /! public app: example\.com\/work\/election-atlas \+ example\.com\/work\/election-atlas\/\* will be reachable by anyone — the Access sign-in step is skipped/);
  assert.match(text, /\n {2}· route example\.com\/\* → Worker "home-site" is broader; the app's route is more specific and wins under \/work\/election-atlas — left alone/);
  assert.match(text, /\nR2\n {2}\+ create R2 bucket "election-atlas-bills"\nWorker routes\n/);
  assert.match(text, /\n {2}\+ create route example\.com\/work\/election-atlas\* → Worker "election-atlas" \(publicly reachable; the Worker must pass paths outside \/work\/election-atlas to the origin\)\n/);

  assert.deepEqual(writes(h.http.calls), [], "no Cloudflare write in plan mode");
  assert.ok(!h.http.calls.some((c) => c.url.includes("/access/policies")), "public plan does not even read policies");
  assert.equal(readFileSync(join(cloneDir, "backend", "wrangler.jsonc"), "utf8"), WRANGLER_PUBLIC);
});

test("public apply: D1, R2, the route and its check, then GitHub; no Access call or login wait; re-run is a no-op", async () => {
  const world = deployedWorld();
  const cloneDir = makeClone(WRANGLER_PUBLIC);
  const h = harness(world);
  const actions = await planBootstrap(h.deps, opts(cloneDir));
  const planCalls = h.http.calls.length;
  await applyBootstrap(h.deps, actions, opts(cloneDir));

  const applyCalls = h.http.calls.slice(planCalls);
  assert.deepEqual(apiCalls(applyCalls), [
    "POST /d1/database",
    "POST /r2/buckets",
    "POST /zones/zone-1/workers/routes",
  ], "no Access write");
  assert.deepEqual(siteProbes(applyCalls), ROUTE_PROBES, "the only probes are the route check's, once: no login wait");
  assert.deepEqual(accessCalls(writes(h.http.calls)), []);
  assert.deepEqual(h.logs.map((l) => l.trim().split(" ").slice(0, 4).join(" ")), [
    "+ created D1 database",
    "+ wrote database_id into",
    `+ created R2 bucket`,
    `+ created route ${ROUTE}`,
    "✓ /work/election-atlas?<query> reaches the",
    "+ created environment production",
    "+ set secret CLOUDFLARE_API_TOKEN",
    "+ set secret CLOUDFLARE_ACCOUNT_ID",
  ]);

  assert.deepEqual(world.site.routes.filter((r) => r.script === "election-atlas").map((r) => r.pattern), [ROUTE]);
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
  assert.deepEqual(creates(afterDeploy), [`route:${ROUTE}`]);
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
    [{ pattern: `${DOMAIN}/work/election-atlas*`, script: "election-atlas" }, "ok", /^route \S+ → Worker "election-atlas"$/],
    [{ pattern: `${DOMAIN}/work/election-atlas*`, script: "someone-else" }, "conflict", /→ Worker "someone-else" overlaps example\.com\/work\/election-atlas\*/],
    [{ pattern: `${DOMAIN}/work/election-atlas`, script: "someone-else" }, "conflict", /overlaps/],
    [{ pattern: `*${DOMAIN}/work/election-atlas*`, script: "election-atlas" }, "conflict", /reaches outside \/work\/election-atlas, but an app claims only example\.com\/work\/election-atlas\*/],
    [{ pattern: `${DOMAIN}/work/*`, script: "election-atlas" }, "conflict", /reaches outside/],
    [{ pattern: `${DOMAIN}/work/election-atlas` }, "conflict", /→ no Worker overlaps/],
    [{ pattern: `*${DOMAIN}/work/election-atlas/*`, script: "someone-else" }, "conflict", /overlaps/],
    [{ pattern: `${DOMAIN}/work/election-atlas/api/*`, script: "election-atlas" }, "ok", /sits inside/],
    [{ pattern: `${DOMAIN}/work/*`, script: "portfolio" }, "info", /is broader/],
    [{ pattern: `${DOMAIN}/work/election-atlas-old/*`, script: "someone-else" }, "info", /outside \/work\/election-atlas that example\.com\/work\/election-atlas\* also matches; it is more specific and keeps that path — left alone/],
    [{ pattern: `${DOMAIN}/work/election-atlasfoo`, script: "someone-else" }, "info", /is more specific and keeps that path/],
    [{ pattern: `${DOMAIN}/work/election-atlas2` }, "info", /→ no Worker is on a path outside/],
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

test("several r2_buckets names are a plan conflict", async () => {
  const two = WRANGLER_PUBLIC.replace(
    `[{ "binding": "BILLS", "bucket_name": "election-atlas-bills" }]`,
    `[{ "binding": "BILLS", "bucket_name": "election-atlas-bills" }, { "binding": "MAPS", "bucket_name": "election-atlas-maps" }]`,
  );
  const world = deployedWorld();
  const h = harness(world);
  const ambiguous = await planBootstrap(h.deps, opts(makeClone(two)));
  const r2 = ambiguous.find((a) => a.step === "r2");
  assert.equal(r2.state, "conflict");
  assert.match(r2.summary, /several buckets \(election-atlas-bills, election-atlas-maps\)/);
});

/** An app bootstrapped before `<prefix>*`: the Worker holds the exact pair. */
function pairWorld() {
  const world = deployedWorld();
  world.site.routes.push(
    { id: "route-exact", pattern: ROUTES[0], script: "election-atlas" },
    { id: "route-slash", pattern: ROUTES[1], script: "election-atlas" },
  );
  return world;
}

test("public, the Worker's old exact pair: the plan creates <prefix>* and deletes the pair once it answers", async () => {
  const world = pairWorld();
  const cloneDir = makeClone(WRANGLER_PUBLIC);
  const h = harness(world);
  const actions = await planBootstrap(h.deps, opts(cloneDir));
  const route = actions.filter((a) => a.step === "route" && a.state !== "info").map((a) => `${a.state} ${a.kind}:${a.pattern}`);
  assert.deepEqual(route, [`create route:${ROUTE}`, `delete route-delete:${ROUTES[0]}`, `delete route-delete:${ROUTES[1]}`]);
  assert.ok(!actions.some((a) => a.state === "conflict"), "the app's own old routes are not a conflict");
  const text = formatPlan(actions, { ...opts(cloneDir), apply: false });
  assert.match(text, /\n {2}- delete route example\.com\/work\/election-atlas → Worker "election-atlas" once example\.com\/work\/election-atlas\* answers: .* misses \/work\/election-atlas\?<query>\)\n/);
  assert.match(text, /\n {2}- delete route example\.com\/work\/election-atlas\/\* → Worker "election-atlas" once/);
  assert.deepEqual(writes(h.http.calls), [], "plan mode writes nothing");

  const planCalls = h.http.calls.length;
  await applyBootstrap(h.deps, actions, opts(cloneDir));
  const applyCalls = apiCalls(h.http.calls.slice(planCalls)).filter((c) => c.includes("/workers/routes"));
  assert.deepEqual(applyCalls, [
    "POST /zones/zone-1/workers/routes",
    "DELETE /zones/zone-1/workers/routes/route-exact",
    "DELETE /zones/zone-1/workers/routes/route-slash",
  ], "create first, delete after");
  assert.deepEqual(world.site.routes.map((r) => r.pattern), [`${DOMAIN}/*`, ROUTE]);
  assert.ok(h.logs.some((l) => l.trim() === `- deleted route ${ROUTES[0]}, replaced by ${ROUTE}`));

  const again = await planBootstrap(harness(world).deps, opts(cloneDir));
  assert.deepEqual(creates(again), []);
  assert.ok(!again.some((a) => a.state === "delete"));
});

test("public, <prefix>* already there beside the old pair: the plan only deletes the pair", async () => {
  const world = pairWorld();
  world.site.routes.push({ id: "route-new", pattern: ROUTE, script: "election-atlas" });
  const actions = await planBootstrap(harness(world).deps, opts(makeClone(WRANGLER_PUBLIC)));
  const route = actions.filter((a) => a.step === "route").map((a) => `${a.state} ${a.pattern ?? a.summary}`);
  assert.deepEqual(route.filter((r) => !r.startsWith("info")), [
    `ok route ${ROUTE} → Worker "election-atlas"`,
    `delete ${ROUTES[0]}`,
    `delete ${ROUTES[1]}`,
  ]);
  assert.match(formatPlan(actions, { ...opts(makeClone(WRANGLER_PUBLIC)), apply: false }), /\d change\(s\) planned$/);
});

test("public apply stops before deleting anything or touching GitHub when the Worker serves paths outside its prefix", async () => {
  const world = pairWorld();
  delete world.site.workerPrefixes; // a Worker without the pass-through guard
  const cloneDir = makeClone(WRANGLER_PUBLIC);
  const h = harness(world);
  const actions = await planBootstrap(h.deps, opts(cloneDir));
  await assert.rejects(applyBootstrap(h.deps, actions, opts(cloneDir)),
    /the Worker route example\.com\/work\/election-atlas\* does not answer as it should: https:\/\/example\.com\/work\/election-atlasportfolio-app-verify answered 200 without x-github-request-id — .*fetch\(req\)\. Nothing was deleted: example\.com\/work\/election-atlas \+ example\.com\/work\/election-atlas\/\* still point at the same Worker/);
  assert.deepEqual(world.site.routes.map((r) => r.pattern), [`${DOMAIN}/*`, ROUTES[0], ROUTES[1], ROUTE]);
  assert.equal(world.local.github.environment, false, "GitHub is not touched");
});

test("public apply keeps checking while the new route is not answering yet, then goes on", async () => {
  const world = deployedWorld();
  world.site.routes = []; // no home route: unrouted paths reach the origin, as on the real zone
  const cloneDir = makeClone(WRANGLER_PUBLIC);
  const h = harness(world);
  const actions = await planBootstrap(h.deps, opts(cloneDir));
  // The route reaches the edge only after two rounds of probes.
  const created = [];
  let probes = 0;
  const fetch = async (url, init) => {
    if (!url.startsWith("https://api.cloudflare.com/") && ++probes <= 6) {
      const saved = world.site.routes;
      world.site.routes = saved.filter((r) => !created.includes(r));
      try { return await h.http.fetch(url, init); } finally { world.site.routes = saved; }
    }
    const res = await h.http.fetch(url, init);
    if (init?.method === "POST" && url.endsWith("/workers/routes")) created.push(world.site.routes.at(-1));
    return res;
  };
  const sleeps = [];
  await applyBootstrap({ ...h.deps, cf: { ...h.deps.cf, fetch }, fetch, sleep: async (ms) => sleeps.push(ms) }, actions, opts(cloneDir));
  assert.equal(sleeps.length, 2);
  assert.equal(world.local.github.environment, true);
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
  assert.equal(world.site.routes.length, 2, "the home route and the app's one route");
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

test("cli keeps every prefix refusal in public mode", async () => {
  const world = deployedWorld();
  for (const path of ["", "/", "/api", "/_next/x", "/vendors", "/work/*", "/work/a.b", "/work?x=1", "/Work"]) {
    const r = await cli(world, ["bootstrap", "--public", "--repo", REPO, "--path", path, "--config", writeConfig(), "--apply"]);
    assert.equal(r.code, 2, path);
    assert.deepEqual(r.http.calls, [], path);
  }
});

test("cli verify --routes: exits 0 for <prefix>* with the pass-through, 1 for the old exact pair, and needs no token", async () => {
  const good = deployedWorld();
  good.site.routes = [{ id: "r1", pattern: ROUTE, script: "election-atlas" }];
  const pass = await cli(good, ["verify", "--routes", "--path", PREFIX, "--domain", DOMAIN]);
  assert.equal(pass.code, 0, pass.out);
  assert.match(pass.out, /✓ https:\/\/example\.com\/work\/election-atlas\?portfolio-app-verify=1 answered 200, as \/work\/election-atlas does/);
  assert.match(pass.out, /✓ https:\/\/example\.com\/work\/election-atlasportfolio-app-verify answered 404 from the origin, as a path outside \/work\/election-atlas should/);
  assert.deepEqual(pass.proc.calls, [], "no keychain, git or gh");
  assert.ok(pass.http.calls.every((c) => !c.url.startsWith("https://api.cloudflare.com/")), "no API call");

  const old = deployedWorld();
  old.site.routes = [{ id: "r1", pattern: ROUTES[0], script: "election-atlas" }, { id: "r2", pattern: ROUTES[1], script: "election-atlas" }];
  const fail = await cli(old, ["verify", "--routes", "--path", PREFIX, "--domain", DOMAIN]);
  assert.equal(fail.code, 1);
  assert.match(fail.out, /✗ https:\/\/example\.com\/work\/election-atlas\?portfolio-app-verify=1 answered 200 from the origin .* misses \/work\/election-atlas\?<query>/);

  const mixed = await cli(good, ["verify", "--routes", "--path", PREFIX, "--domain", DOMAIN, "--public-path", PREFIX]);
  assert.equal(mixed.code, 2);
});

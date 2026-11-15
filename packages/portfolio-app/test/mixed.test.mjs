// Mixed apps (--public-path): signed in by default, plus a Bypass-for-Everyone app on
// exactly the listed public paths. Two shapes from real use: a fresh app (time-track,
// nothing set up yet) and an app that already has its Allow app and D1 (graph-notes,
// where only the public app is new). Every conflict test also checks that nothing is
// written, and no existing Access app is ever edited.

import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { applyBootstrap, formatPlan, planBootstrap } from "../skills/portfolio-app/scripts/lib/bootstrap.mjs";
import { POLICY_NAME, PUBLIC_POLICY_NAME } from "../skills/portfolio-app/scripts/lib/local.mjs";
import { main } from "../skills/portfolio-app/scripts/portfolio-app.mjs";
import {
  ACCESS_LOGIN, ACCOUNT, DEPLOY_TOKEN, DOMAIN, SETUP_TOKEN, TEAM, WRANGLER_WITH_PLACEHOLDER,
  config, fakeFetch, fakeRun, freshWorld, makeClone,
} from "./helpers.mjs";

const writes = (calls) => calls.filter((c) => c.method !== "GET");
const creates = (actions) => actions.filter((a) => a.state === "create").map((a) => `${a.kind}${a.name ? `:${a.name}` : ""}`);
const conflicts = (actions) => actions.filter((a) => a.state === "conflict");
const api = (calls) => calls.map((c) => `${c.method} ${c.url.replace(`https://api.cloudflare.com/client/v4/accounts/${ACCOUNT}`, "")}`);

function harness(world) {
  const http = fakeFetch(world.site);
  const proc = fakeRun(world.local);
  const cf = { fetch: http.fetch, token: SETUP_TOKEN, accountId: ACCOUNT };
  const logs = [];
  return { http, proc, logs, deps: { cf, run: proc.run, fetch: http.fetch, sleep: async () => {}, log: (l) => logs.push(l) } };
}

// --- time-track: a fresh mixed app ---------------------------------------------------

const TT = { repo: "owner/time-track", prefix: "/time-track", publicPaths: ["/time-track", "/time-track/_next/*"] };
const WRANGLER_TT = WRANGLER_WITH_PLACEHOLDER.replaceAll("graph-notes", "time-track").replaceAll("/graph/", "/time-track/");

function timeTrackWorld() {
  const world = freshWorld();
  world.local.remote = "git@github.com:owner/time-track.git";
  return world;
}
const ttOpts = (cloneDir) => ({ ...TT, cloneDir, config });

test("mixed, fresh: plans the Allow app on the private paths, the Bypass app on the listed ones, D1 and GitHub, and writes nothing", async () => {
  const world = timeTrackWorld();
  const cloneDir = makeClone(WRANGLER_TT);
  const h = harness(world);
  const actions = await planBootstrap(h.deps, ttOpts(cloneDir));

  assert.deepEqual(creates(actions), [
    `policy:${POLICY_NAME}`,
    "app:time-track (/time-track)",
    `bypass-policy:${PUBLIC_POLICY_NAME}`,
    "bypass-app:time-track (public)",
    "d1-database:time-track",
    "d1-config",
    "gh-environment",
    "gh-secret:CLOUDFLARE_API_TOKEN",
    "gh-secret:CLOUDFLARE_ACCOUNT_ID",
  ]);
  assert.deepEqual(actions.find((a) => a.kind === "app").uris, [`${DOMAIN}/time-track/*`],
    "the bare prefix is public, so the Allow app keeps only <prefix>/*");
  assert.deepEqual(actions.find((a) => a.kind === "bypass-app").uris, [`${DOMAIN}/time-track`, `${DOMAIN}/time-track/_next/*`]);

  const text = formatPlan(actions, { ...ttOpts(cloneDir), apply: false }).replaceAll(cloneDir, "<CLONE>");
  const access = text.split("\nD1\n")[0];
  assert.equal(access, [
    "portfolio-app bootstrap owner/time-track /time-track (mixed: sign-in except /time-track, /time-track/_next/*) — plan only; nothing changes without --apply",
    "Cloudflare Access",
    `  + create reusable policy "portfolio-app sign-in" (allow: owner@example.com)`,
    `  + create self-hosted Access app "time-track (/time-track)" — destinations example.com/time-track/*, policy "portfolio-app sign-in"`,
    `  + create reusable policy "portfolio-app public" (bypass: everyone)`,
    `  + create self-hosted Access app "time-track (public)" — destinations example.com/time-track + example.com/time-track/_next/*, policy "portfolio-app public" (anyone, no sign-in)`,
    "  · who answers (the app with the most specific matching destination wins; Bypass lets anyone in, Allow asks for sign-in):",
    `  ·   example.com/time-track → "time-track (public)" — Bypass, anyone`,
    `  ·   example.com/time-track/_next/* → "time-track (public)" — Bypass, anyone`,
    `  ·   example.com/time-track/* (the rest) → "time-track (/time-track)" — Allow, sign-in`,
    "  · now: https://example.com/time-track/portfolio-app-verify answered 200 — not behind Access; " +
      "https://example.com/time-track/api/portfolio-app-verify answered 200 — not behind Access; " +
      "https://example.com/time-track answered 200 signed out — no Access redirect; " +
      "https://example.com/time-track/_next/portfolio-app-verify answered 200 signed out — no Access redirect",
  ].join("\n"));
  assert.match(text, /9 change\(s\) planned$/);

  assert.deepEqual(writes(h.http.calls), [], "no Cloudflare write in plan mode");
  assert.equal(readFileSync(join(cloneDir, "backend", "wrangler.jsonc"), "utf8"), WRANGLER_TT);
});

test("mixed, fresh apply: Allow then Bypass, waits for both answers, then D1 and GitHub; a re-run is a no-op", async () => {
  const world = timeTrackWorld();
  const cloneDir = makeClone(WRANGLER_TT);
  const h = harness(world);
  const actions = await planBootstrap(h.deps, ttOpts(cloneDir));
  const planCalls = h.http.calls.length;
  await applyBootstrap(h.deps, actions, ttOpts(cloneDir));

  assert.deepEqual(api(h.http.calls.slice(planCalls)), [
    "POST /access/policies",
    "POST /access/apps",
    "POST /access/policies",
    "POST /access/apps",
    `GET https://${DOMAIN}/time-track/portfolio-app-verify`,
    `GET https://${DOMAIN}/time-track/api/portfolio-app-verify`,
    `GET https://${DOMAIN}/time-track`,
    `GET https://${DOMAIN}/time-track/_next/portfolio-app-verify`,
    "POST /d1/database",
  ]);
  const [allowPolicy, bypassPolicy] = world.site.policies;
  assert.deepEqual([allowPolicy.decision, allowPolicy.include], ["allow", [{ email: { email: "owner@example.com" } }]]);
  assert.deepEqual([bypassPolicy.name, bypassPolicy.decision, bypassPolicy.include], [PUBLIC_POLICY_NAME, "bypass", [{ everyone: {} }]]);
  const [allowApp, bypassApp] = world.site.apps;
  assert.deepEqual(allowApp.destinations, [{ type: "public", uri: `${DOMAIN}/time-track/*` }]);
  assert.deepEqual(allowApp.policies, [{ id: allowPolicy.id, precedence: 1 }]);
  assert.equal(bypassApp.name, "time-track (public)");
  assert.deepEqual(bypassApp.destinations.map((d) => d.uri), [`${DOMAIN}/time-track`, `${DOMAIN}/time-track/_next/*`]);
  assert.deepEqual(bypassApp.policies, [{ id: bypassPolicy.id, precedence: 1 }]);

  assert.deepEqual(h.logs.map((l) => l.replaceAll(cloneDir, "<CLONE>")), [
    `  + created reusable policy "portfolio-app sign-in"`,
    `  + created Access app "time-track (/time-track)"`,
    `  + created reusable policy "portfolio-app public" (bypass: everyone)`,
    `  + created Bypass app "time-track (public)"`,
    `  ✓ /time-track redirects to ${TEAM}.cloudflareaccess.com, and its 2 public path(s) answer signed out`,
    `  + created D1 database "time-track" (00000000-0000-4000-8000-000000000001)`,
    "  + wrote database_id into <CLONE>/backend/wrangler.jsonc — commit it through the normal delivery path",
    "  + created environment production on owner/time-track",
    "  + set secret CLOUDFLARE_API_TOKEN on owner/time-track/production",
    "  + set secret CLOUDFLARE_ACCOUNT_ID on owner/time-track/production",
  ]);
  assert.deepEqual(world.local.github.values, { CLOUDFLARE_API_TOKEN: DEPLOY_TOKEN, CLOUDFLARE_ACCOUNT_ID: ACCOUNT });

  const replan = await planBootstrap(harness(world).deps, ttOpts(cloneDir));
  assert.deepEqual(creates(replan), [], "second run has nothing to create");
  assert.match(replan.find((a) => a.kind === "check").summary, /already redirects .*, and its public paths answer signed out/);
  assert.match(formatPlan(replan, { ...ttOpts(cloneDir), apply: false }), /nothing to create — already bootstrapped$/);
});

// --- graph-notes: an existing Allow app and D1, a new public app -------------------------

const GN = {
  repo: "owner/graph-notes",
  prefix: "/graph",
  publicPaths: ["/graph/public", "/graph/public.txt", "/graph/_next/*", "/graph/icons/*", "/graph/manifest.webmanifest", "/graph/api/public/*"],
};
const GN_PUBLIC = GN.publicPaths.map((p) => `${DOMAIN}${p}`);
const WRANGLER_GN_DONE = WRANGLER_WITH_PLACEHOLDER.replace(`"database_id": "REPLACE_AFTER_d1_create"`, `"database_id": "11111111-2222-4333-8444-555555555555"`);
const ALLOW_APP = {
  id: "app-graph", name: "graph-notes (/graph)", type: "self_hosted", domain: `${DOMAIN}/graph`,
  destinations: [{ type: "public", uri: `${DOMAIN}/graph` }, { type: "public", uri: `${DOMAIN}/graph/*` }],
  policies: [{ id: "pol-signin", precedence: 1 }],
};

function graphNotesWorld() {
  const world = freshWorld();
  world.site.policies.push({ id: "pol-signin", name: POLICY_NAME, reusable: true, decision: "allow", include: [{ email: { email: "owner@example.com" } }] });
  world.site.apps.push(structuredClone(ALLOW_APP));
  world.site.d1.push({ uuid: "11111111-2222-4333-8444-555555555555", name: "graph-notes" });
  world.local.github = { environment: true, secrets: ["CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"] };
  return world;
}
const gnOpts = (cloneDir, extra = {}) => ({ ...GN, cloneDir, config, ...extra });

test("mixed, existing Allow app and D1: only the Bypass policy and app are new; the Allow app is left exactly as it was", async () => {
  const world = graphNotesWorld();
  const cloneDir = makeClone(WRANGLER_GN_DONE);
  const h = harness(world);
  const actions = await planBootstrap(h.deps, gnOpts(cloneDir));

  assert.deepEqual(creates(actions), [`bypass-policy:${PUBLIC_POLICY_NAME}`, "bypass-app:graph-notes (public)"]);
  assert.deepEqual(conflicts(actions), []);
  assert.deepEqual(actions.filter((a) => a.state === "warn"), []);
  const text = formatPlan(actions, { ...gnOpts(cloneDir), apply: false });
  assert.match(text, /✓ Access app "graph-notes \(\/graph\)" guards example\.com\/graph \+ example\.com\/graph\/\*/);
  assert.match(text, /· {3}example\.com\/graph\/api\/public\/\* → "graph-notes \(public\)" — Bypass, anyone/);
  assert.match(text, /· {3}example\.com\/graph\/\* \(the rest\) → "graph-notes \(\/graph\)" — Allow, sign-in/);
  assert.match(text, /\nD1\n {2}✓ wrangler\.jsonc already carries the database_id for graph-notes\n/);
  assert.match(text, /\nGitHub owner\/graph-notes\n {2}✓ environment production exists\n {2}✓ secret CLOUDFLARE_API_TOKEN is set\n {2}✓ secret CLOUDFLARE_ACCOUNT_ID is set\n/);
  assert.match(text, /2 change\(s\) planned$/);

  const planCalls = h.http.calls.length;
  await applyBootstrap(h.deps, actions, gnOpts(cloneDir));
  assert.deepEqual(api(writes(h.http.calls.slice(planCalls))), ["POST /access/policies", "POST /access/apps"], "no D1 write");
  assert.deepEqual(world.site.apps[0], ALLOW_APP, "the Allow app is untouched");
  assert.deepEqual(world.site.apps[1].destinations.map((d) => d.uri), GN_PUBLIC);
  assert.equal(world.site.apps[1].name, "graph-notes (public)");
  assert.deepEqual(h.proc.calls.filter((c) => c.cmd === "gh" && (c.args.includes("-X") || c.args[1] === "set")), [], "no GitHub write");
  assert.equal(readFileSync(join(cloneDir, "backend", "wrangler.jsonc"), "utf8"), WRANGLER_GN_DONE);
  assert.match(h.logs.join("\n"), /✓ \/graph redirects to test-team\.cloudflareaccess\.com, and its 6 public path\(s\) answer signed out/);

  const replan = await planBootstrap(harness(world).deps, gnOpts(cloneDir));
  assert.deepEqual(creates(replan), []);
  assert.equal(replan.find((a) => a.kind === "bypass-app").state, "ok");
});

test("mixed reuses an existing Bypass app holding exactly the public paths, with policies by id or inline", async () => {
  for (const inline of [false, true]) {
    const world = graphNotesWorld();
    const bypass = { id: "pol-public", name: PUBLIC_POLICY_NAME, reusable: true, decision: "bypass", include: [{ everyone: {} }] };
    if (!inline) world.site.policies.push(bypass);
    world.site.apps.push({ id: "app-pub", name: "graph-notes (public)", destinations: GN_PUBLIC.map((uri) => ({ type: "public", uri })),
      policies: [inline ? { ...bypass, precedence: 1 } : { id: bypass.id, precedence: 1 }] });
    const h = harness(world);
    const actions = await planBootstrap(h.deps, gnOpts(makeClone(WRANGLER_GN_DONE)));
    assert.deepEqual(creates(actions), [], `inline=${inline}`);
    assert.deepEqual(conflicts(actions), [], `inline=${inline}`);
    assert.match(actions.find((a) => a.kind === "bypass-app").summary, /^Bypass app "graph-notes \(public\)" lets anyone reach /);
    assert.ok(!actions.some((a) => a.kind === "bypass-policy"), "no policy step when the app already exists");
    assert.equal(actions.find((a) => a.kind === "check").state, "ok");
  }
});

test("mixed conflicts: the plan names each one, apply refuses, and nothing is written", async () => {
  const bypassPolicy = { id: "pol-public", name: PUBLIC_POLICY_NAME, reusable: true, decision: "bypass", include: [{ everyone: {} }] };
  const bypassApp = (name, uris) => ({ id: `app-${name}`, name, destinations: uris.map((uri) => ({ uri })), policies: [{ id: "pol-public" }] });
  const cases = [
    ["an extra destination in the public app",
      (w) => w.site.apps.push(bypassApp("graph-notes (public)", [...GN_PUBLIC, `${DOMAIN}/graph/admin`])),
      /Access app "graph-notes \(public\)" holds \[.*example\.com\/graph\/admin\], not exactly the public paths/],
    ["the public app's name on other destinations",
      (w) => w.site.apps.push(bypassApp("graph-notes (public)", [`${DOMAIN}/graph/old`])),
      /Access app "graph-notes \(public\)" holds \[example\.com\/graph\/old\], not exactly the public paths/],
    ["a wider Bypass app under the prefix",
      (w) => w.site.apps.push(bypassApp("open api", [`${DOMAIN}/graph/api/*`])),
      /Bypass app "open api" lets anyone reach \[example\.com\/graph\/api\/\*\], more of \/graph than the public paths/],
    ["a wider Bypass app straddling the prefix",
      (w) => w.site.apps.push(bypassApp("open graph", [`${DOMAIN}/graph*`])),
      /Bypass app "open graph" lets anyone reach/],
    ["a public path already guarded by another app",
      (w) => w.site.apps.push({ id: "app-old", name: "notes viewer", destinations: [{ uri: `${DOMAIN}/graph/public` }], policies: [{ id: "pol-signin" }] }),
      /example\.com\/graph\/public already belong\(s\) to Access app "notes viewer" .*never edits, narrows or moves one/],
    ["exactly the public paths, but on an Allow app",
      (w) => w.site.apps.push({ id: "app-y", name: "notes viewer", destinations: GN_PUBLIC.map((uri) => ({ uri })), policies: [{ id: "pol-signin" }] }),
      /Access app "notes viewer" holds exactly the public paths but is not Bypass for Everyone/],
    [`a "${PUBLIC_POLICY_NAME}" policy that is not Bypass for Everyone`,
      (w) => w.site.policies.push({ ...bypassPolicy, include: [{ email: { email: "owner@example.com" } }] }),
      /reusable policy "portfolio-app public" exists but is not Bypass for Everyone — left unchanged/],
  ];
  for (const [name, setup, summary] of cases) {
    const world = graphNotesWorld();
    if (!name.startsWith(`a "${PUBLIC_POLICY_NAME}"`)) world.site.policies.push(bypassPolicy);
    setup(world);
    const before = structuredClone(world.site.apps);
    const h = harness(world);
    const opts = gnOpts(makeClone(WRANGLER_GN_DONE));
    const actions = await planBootstrap(h.deps, opts);
    assert.ok(conflicts(actions).some((a) => summary.test(a.summary)), `${name}: ${conflicts(actions).map((a) => a.summary).join(" | ")}`);
    assert.deepEqual(creates(actions).filter((c) => c.startsWith("bypass")), [], name);
    assert.match(formatPlan(actions, { ...opts, apply: false }), /which app answers what is known once the conflicts are resolved/, name);
    await assert.rejects(applyBootstrap(h.deps, actions, opts), /refusing to apply/, name);
    assert.deepEqual(writes(h.http.calls), [], name);
    assert.deepEqual(world.site.apps, before, `${name}: no app changed`);
  }
});

test("mixed refuses to move a destination out of an existing private app (time-track already fully private)", async () => {
  const world = timeTrackWorld();
  world.site.apps.push({ id: "app-tt", name: "time-track (/time-track)",
    destinations: [{ uri: `${DOMAIN}/time-track` }, { uri: `${DOMAIN}/time-track/*` }], policies: [] });
  const h = harness(world);
  const actions = await planBootstrap(h.deps, ttOpts(makeClone(WRANGLER_TT)));
  const found = conflicts(actions).map((a) => a.summary);
  assert.ok(found.some((s) => /overlaps \[example\.com\/time-track\/\*\] without matching it/.test(s)), found.join(" | "));
  assert.ok(found.some((s) => /example\.com\/time-track already belong\(s\) to Access app "time-track \(\/time-track\)"/.test(s)), found.join(" | "));
  assert.ok(!creates(actions).some((c) => c.startsWith("app") || c.startsWith("bypass")));
});

test("mixed leaves a Bypass app above the prefix alone, as information", async () => {
  const world = graphNotesWorld();
  world.site.policies.push({ id: "pol-public", name: PUBLIC_POLICY_NAME, reusable: true, decision: "bypass", include: [{ everyone: {} }] });
  world.site.apps.push({ id: "app-home", name: "home", destinations: [{ uri: DOMAIN }], policies: [{ id: "pol-public" }] });
  const actions = await planBootstrap(harness(world).deps, gnOpts(makeClone(WRANGLER_GN_DONE)));
  assert.deepEqual(conflicts(actions), []);
  assert.match(actions.find((a) => a.kind === "bypass-app" && a.state === "info").summary, /Bypass app "home" on \[example\.com\] is broader than \/graph.*left alone/);
  assert.deepEqual(creates(actions), ["bypass-app:graph-notes (public)"], "the existing Bypass policy is reused");
});

test("mixed apply stops before D1 and GitHub when a public path still asks for sign-in, or a private one answers", async () => {
  const cases = [
    ["public path still redirects", (path) => path.startsWith("/time-track/_next/"), "access",
      /time-track\/_next\/portfolio-app-verify .*public path still asks for sign-in/],
    ["the API answers signed out", (path) => path === "/time-track/api/portfolio-app-verify", "open",
      /time-track\/api\/portfolio-app-verify answered 200 — not behind Access/],
    ["a public path answers a bare 403", (path) => path === "/time-track", "forbidden",
      /https:\/\/example\.com\/time-track answered a bare 403 — Access or Cloudflare's bot protection/],
  ];
  for (const [name, hit, answer, reason] of cases) {
    const world = timeTrackWorld();
    const cloneDir = makeClone(WRANGLER_TT);
    const h = harness(world);
    const opts = ttOpts(cloneDir);
    const actions = await planBootstrap(h.deps, opts);
    const broken = async (url, init) => {
      const u = new URL(url);
      if (u.hostname === "api.cloudflare.com" || !hit(u.pathname)) return h.http.fetch(url, init);
      if (answer === "access") return { status: 302, headers: new Map([["location", ACCESS_LOGIN]]) };
      return { status: answer === "open" ? 200 : 403, headers: new Map() };
    };
    await assert.rejects(applyBootstrap({ ...h.deps, fetch: broken }, actions, opts), (err) => {
      assert.match(err.message, /^Access is not answering for \/time-track: /, name);
      assert.match(err.message, reason, name);
      return true;
    });
    assert.equal(world.site.apps.length, 2, `${name}: both Access apps were created`);
    assert.deepEqual(world.site.d1, [], `${name}: no D1 database`);
    assert.deepEqual(world.local.github, { environment: false, secrets: [] }, `${name}: GitHub untouched`);
    assert.equal(readFileSync(join(cloneDir, "backend", "wrangler.jsonc"), "utf8"), WRANGLER_TT, name);
  }
});

test("mixed apply stops before D1 and GitHub when the children of an exact public path inherit its Bypass", async () => {
  const world = freshWorld();
  world.site.inheritExact = true;
  const cloneDir = makeClone();
  const h = harness(world);
  const opts = gnOpts(cloneDir);
  const actions = await planBootstrap(h.deps, opts);
  await assert.rejects(applyBootstrap(h.deps, actions, opts), (err) => {
    assert.match(err.message, /^Access is not answering for \/graph: /);
    for (const child of ["/graph/public/portfolio-app-verify", "/graph/public.txt/portfolio-app-verify", "/graph/manifest.webmanifest/portfolio-app-verify"]) {
      assert.ok(err.message.includes(`https://${DOMAIN}${child} answered 200 — not behind Access`), `${child}: ${err.message}`);
    }
    assert.doesNotMatch(err.message, /public path still asks for sign-in|graph\/api\/portfolio-app-verify/, "only the inherited children fail");
    return true;
  });
  assert.deepEqual(world.site.apps.map((a) => a.name), ["graph-notes (/graph)", "graph-notes (public)"]);
  assert.deepEqual(world.site.d1, [], "no D1 database");
  assert.deepEqual(world.local.github, { environment: false, secrets: [] }, "GitHub untouched");
  assert.equal(readFileSync(join(cloneDir, "backend", "wrangler.jsonc"), "utf8"), WRANGLER_WITH_PLACEHOLDER);
});

test("mixed apply passes under inheritance when the only exact public path is the prefix, whose children the Allow app's /* answers", async () => {
  const world = timeTrackWorld();
  world.site.inheritExact = true;
  const cloneDir = makeClone(WRANGLER_TT);
  const h = harness(world);
  const actions = await planBootstrap(h.deps, ttOpts(cloneDir));
  await applyBootstrap(h.deps, actions, ttOpts(cloneDir));
  assert.match(h.logs.join("\n"), /✓ \/time-track redirects to test-team\.cloudflareaccess\.com, and its 2 public path\(s\) answer signed out/);
  assert.equal(world.site.d1.length, 1);
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

const ttArgs = (...extra) => ["bootstrap", "--repo", TT.repo, "--path", TT.prefix,
  "--public-path", "time-track", "--public-path", "/time-track/_next/*", ...extra];

test("cli --public-path plans a mixed app (repeatable flag, leading slash optional), exits 0 and writes nothing", async () => {
  const world = timeTrackWorld();
  const r = await cli(world, ttArgs("--clone", makeClone(WRANGLER_TT), "--config", writeConfig()));
  assert.equal(r.code, 0, r.err);
  assert.match(r.out, /\(mixed: sign-in except \/time-track, \/time-track\/_next\/\*\) — plan only/);
  assert.match(r.out, /\+ create self-hosted Access app "time-track \(public\)"/);
  assert.deepEqual(writes(r.http.calls), []);
  assert.ok(!r.out.includes(SETUP_TOKEN) && !r.out.includes(DEPLOY_TOKEN));
});

test("cli --public-path --apply carries a mixed plan out and keeps secrets out of argv and output", async () => {
  const world = timeTrackWorld();
  const r = await cli(world, ttArgs("--clone", makeClone(WRANGLER_TT), "--config", writeConfig(), "--apply"));
  assert.equal(r.code, 0, r.err);
  assert.match(r.out, /— applying/);
  assert.deepEqual(world.site.apps.map((a) => a.name), ["time-track (/time-track)", "time-track (public)"]);
  for (const token of [SETUP_TOKEN, DEPLOY_TOKEN]) {
    assert.ok(!r.out.includes(token) && !r.err.includes(token));
    assert.ok(!r.proc.calls.some((c) => c.args.includes(token)));
  }
  assert.ok(!r.proc.calls.some((c) => c.input === SETUP_TOKEN), "the setup token never reaches gh");
});

test("cli --public-path with a conflict exits 2 and applies nothing", async () => {
  const world = graphNotesWorld();
  world.site.apps.push({ id: "app-old", name: "notes viewer", destinations: [{ uri: `${DOMAIN}/graph/public` }] });
  const r = await cli(world, ["bootstrap", "--repo", GN.repo, "--path", GN.prefix, ...GN.publicPaths.flatMap((p) => ["--public-path", p]),
    "--clone", makeClone(WRANGLER_GN_DONE), "--config", writeConfig(), "--apply"]);
  assert.equal(r.code, 2);
  assert.match(r.out, /✗ example\.com\/graph\/public already belong\(s\) to Access app "notes viewer"/);
  assert.doesNotMatch(r.out, /— applying/);
  assert.deepEqual(writes(r.http.calls), []);
});

test("cli refuses public paths outside the prefix, the whole prefix, odd shapes, and --public with --public-path, before any call", async () => {
  const world = graphNotesWorld();
  const bad = [
    ["/time-track", /outside the app's prefix \/graph/],
    ["/graphs/public", /outside the app's prefix \/graph/],
    ["/", /refusing public path/],
    ["/graph/*", /is the whole app; an app anyone can reach is --public/],
    ["/graph/*/x", /the only wildcard allowed is a final/],
    ["/graph/pub*", /the only wildcard allowed is a final/],
    ["/graph/../api", /no empty, "\." or "\.\." segments/],
    ["/graph//x", /no empty/],
    ["/graph/x?y=1", /refusing public path/],
  ];
  for (const [path, message] of bad) {
    for (const command of ["bootstrap", "verify"]) {
      const argv = command === "bootstrap"
        ? ["bootstrap", "--repo", GN.repo, "--path", "/graph", "--public-path", path, "--config", writeConfig(), "--apply"]
        : ["verify", "--path", "/graph", "--public-path", path, "--domain", DOMAIN];
      const r = await cli(world, argv);
      assert.equal(r.code, 2, `${command} ${path}`);
      assert.match(r.err, message, `${command} ${path}`);
      assert.deepEqual(r.http.calls, [], `${command} ${path}`);
    }
  }
  const both = await cli(world, ["bootstrap", "--public", "--repo", GN.repo, "--path", "/graph", "--public-path", "/graph/public", "--config", writeConfig()]);
  assert.equal(both.code, 2);
  assert.match(both.err, /--public and --public-path do not mix/);
  assert.deepEqual(both.http.calls, []);
});

test("cli verify --public-path checks both ways; without it verify is unchanged", async () => {
  const world = graphNotesWorld();
  const args = ["verify", "--path", "/graph", "--domain", DOMAIN, "--team", TEAM, ...GN.publicPaths.flatMap((p) => ["--public-path", p])];

  const before = await cli(world, args);
  assert.equal(before.code, 1, "public paths still ask for sign-in");
  assert.match(before.out, /NOT set up as a mixed app \(sign-in except \/graph\/public, .*\) — refusing/);
  assert.match(before.out, /✗ https:\/\/example\.com\/graph\/public .*public path still asks for sign-in/);

  world.site.policies.push({ id: "pol-public", name: PUBLIC_POLICY_NAME, reusable: true, decision: "bypass", include: [{ everyone: {} }] });
  world.site.apps.push({ id: "app-pub", name: "graph-notes (public)", destinations: GN_PUBLIC.map((uri) => ({ uri })), policies: [{ id: "pol-public" }] });
  const after = await cli(world, args);
  assert.equal(after.code, 0, after.out);
  assert.deepEqual(after.http.calls.map((c) => new URL(c.url).pathname), [
    "/graph", "/graph/portfolio-app-verify", "/graph/api/portfolio-app-verify",
    "/graph/public/portfolio-app-verify", "/graph/public.txt/portfolio-app-verify", "/graph/manifest.webmanifest/portfolio-app-verify",
    "/graph/public", "/graph/public.txt", "/graph/_next/portfolio-app-verify", "/graph/icons/portfolio-app-verify",
    "/graph/manifest.webmanifest", "/graph/api/public/portfolio-app-verify",
  ]);
  assert.match(after.out, /example\.com\/graph is behind Cloudflare Access, and its 6 public path\(s\) answer signed out$/);
  assert.deepEqual(after.proc.calls, [], "still no keychain, git or gh");

  world.site.inheritExact = true;
  const inherited = await cli(world, args);
  assert.equal(inherited.code, 1, "a child of an exact public path answers signed out");
  assert.match(inherited.out, /✗ https:\/\/example\.com\/graph\/public\/portfolio-app-verify answered 200 — not behind Access/);
  assert.match(inherited.out, /NOT set up as a mixed app/);

  const plain = await cli(world, ["verify", "--path", "/graph", "--domain", DOMAIN, "--team", TEAM]);
  assert.equal(plain.code, 0);
  assert.deepEqual(plain.http.calls.map((c) => new URL(c.url).pathname), ["/graph", "/graph/portfolio-app-verify"]);
  assert.match(plain.out, /example\.com\/graph is behind Cloudflare Access$/);
});

// Mixed apps (--public-path): signed in by default, plus a Bypass-for-Everyone app on
// exactly the listed public paths, and a narrowing `<path>/*` in the Allow app for each
// exact public path. Two shapes from real use: a fresh app (time-track, nothing set up
// yet) and an app that already has its Allow app and D1 (graph-notes, where the public
// app is new and the Allow app gains its narrowing destinations, the one edit the tool
// makes to an existing Allow app). Every conflict test also checks that nothing is written.
// The fake site lets the children of an exact destination inherit it, as Cloudflare does.
// time-track's re-run with its trailing-slash path `/time-track/` adds that one destination
// to its public app, the one edit the tool makes to an existing public app; with the fake's
// `slashOutranksWildcard` the children inherit it, and apply takes exactly that destination
// out again. A failed check on a run that did not add `<prefix>/` leaves both apps in place.

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
const GN_NARROWING = ["/graph/public/*", "/graph/public.txt/*", "/graph/manifest.webmanifest/*"].map((p) => `${DOMAIN}${p}`);
const WRANGLER_GN_DONE = WRANGLER_WITH_PLACEHOLDER.replace(`"database_id": "REPLACE_AFTER_d1_create"`, `"database_id": "11111111-2222-4333-8444-555555555555"`);
// As the apps list returns it: policies inline, and settings the tool never set itself.
const ALLOW_APP = {
  id: "app-graph", aud: "aud-graph", name: "graph-notes (/graph)", type: "self_hosted", domain: `${DOMAIN}/graph`,
  destinations: [{ type: "public", uri: `${DOMAIN}/graph` }, { type: "public", uri: `${DOMAIN}/graph/*` }],
  policies: [{ id: "pol-signin", precedence: 1, name: POLICY_NAME, decision: "allow", include: [{ email: { email: "owner@example.com" } }] }],
  session_duration: "12h", app_launcher_visible: false, auto_redirect_to_identity: true, tags: ["portfolio"],
};
// graph-notes as it stands today: the owner approved adding the narrowing paths by hand.
const ALLOW_APP_NARROWED = { ...ALLOW_APP, destinations: [...ALLOW_APP.destinations, ...GN_NARROWING.map((uri) => ({ type: "public", uri }))] };
const BYPASS_POLICY = { id: "pol-public", name: PUBLIC_POLICY_NAME, reusable: true, decision: "bypass", include: [{ everyone: {} }] };

function graphNotesWorld({ narrowed = false } = {}) {
  const world = freshWorld();
  world.site.policies.push({ id: "pol-signin", name: POLICY_NAME, reusable: true, decision: "allow", include: [{ email: { email: "owner@example.com" } }] });
  world.site.apps.push(structuredClone(narrowed ? ALLOW_APP_NARROWED : ALLOW_APP));
  world.site.d1.push({ uuid: "11111111-2222-4333-8444-555555555555", name: "graph-notes" });
  world.local.github = { environment: true, secrets: ["CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"] };
  return world;
}
const gnOpts = (cloneDir, extra = {}) => ({ ...GN, cloneDir, config, ...extra });

test("mixed, existing Allow app without narrowing: exactly an add to it, then the Bypass policy and app; the PUT keeps every other setting", async () => {
  const world = graphNotesWorld();
  const cloneDir = makeClone(WRANGLER_GN_DONE);
  const h = harness(world);
  const actions = await planBootstrap(h.deps, gnOpts(cloneDir));

  assert.deepEqual(creates(actions), ["app-narrowing:graph-notes (/graph)", `bypass-policy:${PUBLIC_POLICY_NAME}`, "bypass-app:graph-notes (public)"]);
  assert.deepEqual(actions.find((a) => a.kind === "app-narrowing").uris, GN_NARROWING);
  assert.deepEqual(conflicts(actions), []);
  assert.deepEqual(actions.filter((a) => a.state === "warn"), []);
  const text = formatPlan(actions, { ...gnOpts(cloneDir), apply: false });
  assert.match(text, /✓ Access app "graph-notes \(\/graph\)" guards example\.com\/graph \+ example\.com\/graph\/\*\n/);
  assert.ok(text.includes(`  + add narrowing destination(s) ${GN_NARROWING.join(" + ")} to "graph-notes (/graph)" — the paths under each exact public path ask for sign-in\n`), text);
  assert.ok(text.includes([
    `  ·   example.com/graph/api/public/* → "graph-notes (public)" — Bypass, anyone`,
    `  ·   example.com/graph → "graph-notes (/graph)" — Allow, sign-in`,
    `  ·   example.com/graph/public/* (narrowing) → "graph-notes (/graph)" — Allow, sign-in`,
    `  ·   example.com/graph/public.txt/* (narrowing) → "graph-notes (/graph)" — Allow, sign-in`,
    `  ·   example.com/graph/manifest.webmanifest/* (narrowing) → "graph-notes (/graph)" — Allow, sign-in`,
    `  ·   example.com/graph/* (the rest) → "graph-notes (/graph)" — Allow, sign-in`,
  ].join("\n")), text);
  assert.match(text, /\nD1\n {2}✓ wrangler\.jsonc already carries the database_id for graph-notes\n/);
  assert.match(text, /\nGitHub owner\/graph-notes\n {2}✓ environment production exists\n {2}✓ secret CLOUDFLARE_API_TOKEN is set\n {2}✓ secret CLOUDFLARE_ACCOUNT_ID is set\n/);
  assert.match(text, /3 change\(s\) planned$/);
  assert.deepEqual(writes(h.http.calls), [], "no Cloudflare write in plan mode");

  const planCalls = h.http.calls.length;
  await applyBootstrap(h.deps, actions, gnOpts(cloneDir));
  const sent = writes(h.http.calls.slice(planCalls));
  assert.deepEqual(api(sent), ["PUT /access/apps/app-graph", "POST /access/policies", "POST /access/apps"],
    "the narrowing comes first, so no child is ever public; no D1 write");
  const expected = { ...ALLOW_APP_NARROWED, policies: [{ id: "pol-signin", precedence: 1 }] };
  assert.deepEqual(sent[0].body, expected, "the full current settings, with only the narrowing destinations added");
  assert.deepEqual(world.site.apps[0], expected);
  assert.deepEqual(world.site.apps[1].destinations.map((d) => d.uri), GN_PUBLIC);
  assert.equal(world.site.apps[1].name, "graph-notes (public)");
  assert.deepEqual(h.proc.calls.filter((c) => c.cmd === "gh" && (c.args.includes("-X") || c.args[1] === "set")), [], "no GitHub write");
  assert.equal(readFileSync(join(cloneDir, "backend", "wrangler.jsonc"), "utf8"), WRANGLER_GN_DONE);
  assert.equal(h.logs[0], `  + added narrowing destination(s) ${GN_NARROWING.join(" + ")} to Access app "graph-notes (/graph)"`);
  assert.match(h.logs.join("\n"), /✓ \/graph redirects to test-team\.cloudflareaccess\.com, and its 6 public path\(s\) answer signed out/,
    "the child probes pass once the narrowing destinations are in");

  const replan = await planBootstrap(harness(world).deps, gnOpts(cloneDir));
  assert.deepEqual(creates(replan), []);
  assert.equal(replan.find((a) => a.kind === "bypass-app").state, "ok");
});

test("mixed, graph-notes as it stands today (narrowing paths and public app in place) plans zero changes, with policies by id or inline", async () => {
  for (const inline of [false, true]) {
    const world = graphNotesWorld({ narrowed: true });
    if (!inline) world.site.policies.push(BYPASS_POLICY);
    world.site.apps.push({ id: "app-pub", name: "graph-notes (public)", destinations: GN_PUBLIC.map((uri) => ({ type: "public", uri })),
      policies: [inline ? { ...BYPASS_POLICY, precedence: 1 } : { id: BYPASS_POLICY.id, precedence: 1 }] });
    const h = harness(world);
    const opts = gnOpts(makeClone(WRANGLER_GN_DONE));
    const actions = await planBootstrap(h.deps, opts);
    assert.deepEqual(creates(actions), [], `inline=${inline}`);
    assert.deepEqual(conflicts(actions), [], `inline=${inline}`);
    assert.ok(!actions.some((a) => a.kind === "app-narrowing"), "nothing to add to the Allow app");
    assert.match(actions.find((a) => a.kind === "app").summary, new RegExp(`guards .*${GN_NARROWING.map((u) => u.replaceAll(".", "\\.").replaceAll("*", "\\*")).join(" \\+ ")}$`));
    assert.match(actions.find((a) => a.kind === "bypass-app").summary, /^Bypass app "graph-notes \(public\)" lets anyone reach /);
    assert.ok(!actions.some((a) => a.kind === "bypass-policy"), "no policy step when the app already exists");
    assert.equal(actions.find((a) => a.kind === "check").state, "ok", "every child probe already asks for sign-in");
    assert.match(formatPlan(actions, { ...opts, apply: false }), /nothing to create — already bootstrapped$/);
    assert.deepEqual(writes(h.http.calls), []);
  }
});

test("mixed conflicts: the plan names each one, apply refuses, and nothing is written", async () => {
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
      /example\.com\/graph\/public already belong\(s\) to Access app "notes viewer" .*never removes, alters or moves one/],
    ["exactly the public paths, but on an Allow app",
      (w) => w.site.apps.push({ id: "app-y", name: "notes viewer", destinations: GN_PUBLIC.map((uri) => ({ uri })), policies: [{ id: "pol-signin" }] }),
      /Access app "notes viewer" holds exactly the public paths but is not Bypass for Everyone/],
    [`a "${PUBLIC_POLICY_NAME}" policy that is not Bypass for Everyone`,
      (w) => w.site.policies.push({ ...BYPASS_POLICY, include: [{ email: { email: "owner@example.com" } }] }),
      /reusable policy "portfolio-app public" exists but is not Bypass for Everyone — left unchanged/],
  ];
  for (const [name, setup, summary] of cases) {
    const world = graphNotesWorld({ narrowed: true });
    if (!name.startsWith(`a "${PUBLIC_POLICY_NAME}"`)) world.site.policies.push(BYPASS_POLICY);
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

test("mixed refuses to add narrowing destinations to an Allow app it cannot change unambiguously, and writes nothing", async () => {
  const cases = [
    ["an unexpected destination in the Allow app",
      (app) => app.destinations.push({ type: "public", uri: `${DOMAIN}/graph/admin/*` }),
      /Access app "graph-notes \(\/graph\)" guards \[.*example\.com\/graph\/admin\/\*\], which overlaps .* without matching it/],
    ["a narrowing path for a public path no longer listed",
      (app) => app.destinations.push({ type: "public", uri: `${DOMAIN}/graph/old.txt/*` }),
      /Access app "graph-notes \(\/graph\)" guards \[.*example\.com\/graph\/old\.txt\/\*\], which overlaps/],
    ["paths in the deprecated self_hosted_domains only",
      (app) => { app.self_hosted_domains = app.destinations.map((d) => d.uri); delete app.destinations; },
      /needs narrowing destination\(s\) .*, but its paths are in the deprecated self_hosted_domains, not destinations; add them by hand/],
    ["a destination that is not a public hostname",
      (app) => app.destinations.push({ type: "private", cidr: "10.0.0.0/8" }),
      /but it holds a destination that is not a public hostname; add them by hand/],
    ["a Bypass policy on the Allow app",
      (app) => app.policies.push({ id: "pol-public", precedence: 2, decision: "bypass", include: [{ everyone: {} }] }),
      /but it has a Bypass policy, so a destination added to it would not ask for sign-in; add them by hand/],
    ["an app that is not self-hosted",
      (app) => { app.type = "saas"; },
      /but it is a saas app, not self-hosted; add them by hand/],
  ];
  for (const [name, change, summary] of cases) {
    const world = graphNotesWorld();
    change(world.site.apps[0]);
    const before = structuredClone(world.site.apps);
    const h = harness(world);
    const opts = gnOpts(makeClone(WRANGLER_GN_DONE));
    const actions = await planBootstrap(h.deps, opts);
    assert.ok(conflicts(actions).some((a) => summary.test(a.summary)), `${name}: ${conflicts(actions).map((a) => a.summary).join(" | ")}`);
    assert.ok(!creates(actions).some((c) => c.startsWith("app")), name);
    await assert.rejects(applyBootstrap(h.deps, actions, opts), /refusing to apply/, name);
    assert.deepEqual(writes(h.http.calls), [], name);
    assert.deepEqual(world.site.apps, before, `${name}: no app changed`);
  }
});

test("mixed apply refuses to PUT an Allow app whose paths changed after the plan", async () => {
  const world = graphNotesWorld();
  const h = harness(world);
  const opts = gnOpts(makeClone(WRANGLER_GN_DONE));
  const actions = await planBootstrap(h.deps, opts);
  world.site.apps[0].destinations.push({ type: "public", uri: `${DOMAIN}/graph/admin/*` });
  const before = structuredClone(world.site.apps);
  const planCalls = h.http.calls.length;
  await assert.rejects(applyBootstrap(h.deps, actions, opts), /Access app "graph-notes \(\/graph\)" now guards \[.*graph\/admin\/\*\], not .* as planned; re-run the plan/);
  assert.deepEqual(writes(h.http.calls.slice(planCalls)), []);
  assert.deepEqual(world.site.apps, before);
});

test("mixed, fresh graph-notes: the Allow app is created with a narrowing destination per exact public path, and apply's child probes pass", async () => {
  const world = freshWorld();
  const cloneDir = makeClone();
  const h = harness(world);
  const opts = gnOpts(cloneDir);
  const actions = await planBootstrap(h.deps, opts);
  const allow = actions.find((a) => a.kind === "app");
  assert.equal(allow.state, "create");
  assert.deepEqual(allow.uris, [`${DOMAIN}/graph`, `${DOMAIN}/graph/*`, ...GN_NARROWING]);
  assert.match(allow.summary, new RegExp(`destinations example\\.com/graph \\+ example\\.com/graph/\\* \\+ example\\.com/graph/public/\\* \\+ `));
  assert.ok(!actions.some((a) => a.kind === "app-narrowing"), "a new app gets them at creation, not as a later add");

  await applyBootstrap(h.deps, actions, opts);
  assert.deepEqual(world.site.apps[0].destinations.map((d) => d.uri), [`${DOMAIN}/graph`, `${DOMAIN}/graph/*`, ...GN_NARROWING]);
  assert.match(h.logs.join("\n"), /✓ \/graph redirects to test-team\.cloudflareaccess\.com, and its 6 public path\(s\) answer signed out/);
  const probed = h.http.calls.filter((c) => c.url.includes("/graph/public/portfolio-app-verify"));
  assert.ok(probed.length > 0, "the child probe still runs");
  assert.equal(world.site.d1.length, 1);

  const replan = await planBootstrap(harness(world).deps, opts);
  assert.deepEqual(creates(replan).filter((c) => !c.startsWith("d1-config")), [], "a re-run plans nothing in Access");
});

test("mixed narrowing skips the bare prefix and any path whose children a listed wildcard makes public", async () => {
  const world = freshWorld();
  const opts = { repo: "owner/graph-notes", prefix: "/graph", cloneDir: makeClone(), config,
    publicPaths: ["/graph", "/graph/docs/*", "/graph/docs/readme", "/graph/about", "/graph/about/*", "/graph/terms"] };
  const actions = await planBootstrap(harness(world).deps, opts);
  assert.deepEqual(actions.find((a) => a.kind === "app").uris, [`${DOMAIN}/graph/*`, `${DOMAIN}/graph/terms/*`],
    "only /graph/terms needs one: /graph is the prefix, /graph/docs/readme sits under /graph/docs/*, and /graph/about/* is listed");
  assert.deepEqual(conflicts(actions), []);
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
  const world = graphNotesWorld({ narrowed: true });
  world.site.policies.push(BYPASS_POLICY);
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

test("mixed apply still stops before D1 and GitHub when a child of an exact public path answers signed out despite the narrowing", async () => {
  const world = freshWorld();
  const cloneDir = makeClone();
  const h = harness(world);
  const opts = gnOpts(cloneDir);
  const actions = await planBootstrap(h.deps, opts);
  const children = ["/graph/public/portfolio-app-verify", "/graph/public.txt/portfolio-app-verify", "/graph/manifest.webmanifest/portfolio-app-verify"];
  const narrowingIgnored = async (url, init) => (children.includes(new URL(url).pathname) ? { status: 200, headers: new Map() } : h.http.fetch(url, init));
  await assert.rejects(applyBootstrap({ ...h.deps, fetch: narrowingIgnored }, actions, opts), (err) => {
    assert.match(err.message, /^Access is not answering for \/graph: /);
    for (const child of children) {
      assert.ok(err.message.includes(`https://${DOMAIN}${child} answered 200 — not behind Access`), `${child}: ${err.message}`);
    }
    assert.doesNotMatch(err.message, /public path still asks for sign-in|graph\/api\/portfolio-app-verify/, "only the children fail");
    return true;
  });
  assert.deepEqual(world.site.apps.map((a) => a.name), ["graph-notes (/graph)", "graph-notes (public)"]);
  assert.deepEqual(world.site.d1, [], "no D1 database");
  assert.deepEqual(world.local.github, { environment: false, secrets: [] }, "GitHub untouched");
  assert.equal(readFileSync(join(cloneDir, "backend", "wrangler.jsonc"), "utf8"), WRANGLER_WITH_PLACEHOLDER);
});

test("mixed apply stops when the Bypass app goes live after a round's private probes were answered", async () => {
  const world = graphNotesWorld({ narrowed: true });
  const h = harness(world);
  const opts = gnOpts(makeClone(WRANGLER_GN_DONE));
  const actions = await planBootstrap(h.deps, opts);
  const planCalls = h.http.calls.length;
  let bypassLive = false;
  const probed = [];
  // Once the Bypass app is live, a child of /graph/public answers signed out: it went
  // public with the Bypass app, after the round had already asked it.
  const propagating = async (url, init) => {
    const u = new URL(url);
    if (u.hostname === "api.cloudflare.com") return h.http.fetch(url, init);
    probed.push(u.pathname);
    if (u.pathname === "/graph/public") bypassLive = true;
    if (!bypassLive) return { status: 302, headers: new Map([["location", ACCESS_LOGIN]]) };
    return u.pathname === "/graph/public/portfolio-app-verify" ? { status: 200, headers: new Map() } : h.http.fetch(url, init);
  };
  let sleeps = 0;
  await assert.rejects(applyBootstrap({ ...h.deps, fetch: propagating, sleep: async () => { sleeps += 1; } }, actions, opts), (err) => {
    assert.match(err.message, /^Access is not answering for \/graph: /);
    assert.ok(err.message.includes(`https://${DOMAIN}/graph/public/portfolio-app-verify answered 200 — not behind Access`), err.message);
    return true;
  });
  assert.equal(probed.indexOf("/graph/public/portfolio-app-verify") < probed.indexOf("/graph/public"), true,
    "the child was first answered before the Bypass app was live");
  assert.equal(sleeps, 11, "the failed second round is one more attempt; polling runs to its last attempt");
  assert.equal(probed.filter((p) => p === "/graph/public/portfolio-app-verify").length, 13,
    "asked again in the second round, then once in each remaining attempt");
  assert.ok(!h.logs.some((l) => l.includes("answer signed out")), "no success line");
  assert.deepEqual(api(writes(h.http.calls.slice(planCalls))), ["POST /access/policies", "POST /access/apps"]);
  assert.deepEqual(world.site.apps[0], ALLOW_APP_NARROWED, "the Allow app is untouched");
});

test("mixed apply keeps polling when the second round fails for a passing reason, and goes on once two rounds in a row pass", async () => {
  const world = graphNotesWorld({ narrowed: true });
  const h = harness(world);
  const opts = gnOpts(makeClone(WRANGLER_GN_DONE));
  const actions = await planBootstrap(h.deps, opts);
  let publicRequests = 0;
  const flaky = async (url, init) => {
    if (new URL(url).pathname === "/graph/public" && ++publicRequests === 2) throw new Error("socket hang up");
    return h.http.fetch(url, init);
  };
  let sleeps = 0;
  await applyBootstrap({ ...h.deps, fetch: flaky, sleep: async () => { sleeps += 1; } }, actions, opts);
  assert.equal(publicRequests, 4, "a passing round, its failed second round, then a passing round and its second round");
  assert.equal(sleeps, 1, "one wait, after the failed second round");
  assert.match(h.logs.join("\n"), /✓ \/graph redirects to test-team\.cloudflareaccess\.com, and its 6 public path\(s\) answer signed out/);
});

test("mixed apply passes when the only exact public path is the prefix, whose children the Allow app's /* answers, with no narrowing destination", async () => {
  const world = timeTrackWorld();
  const cloneDir = makeClone(WRANGLER_TT);
  const h = harness(world);
  const actions = await planBootstrap(h.deps, ttOpts(cloneDir));
  assert.ok(!actions.some((a) => a.kind === "app-narrowing"));
  await applyBootstrap(h.deps, actions, ttOpts(cloneDir));
  assert.deepEqual(world.site.apps[0].destinations.map((d) => d.uri), [`${DOMAIN}/time-track/*`]);
  assert.match(h.logs.join("\n"), /✓ \/time-track redirects to test-team\.cloudflareaccess\.com, and its 2 public path\(s\) answer signed out/);
  assert.equal(world.site.d1.length, 1);
});

// time-track as it stands on the live account (2026-11-15): the Allow app on time-track/*,
// the public app on exactly time-track and time-track/_next/*, D1 and secrets in place.
const TT_PUBLIC_APP = {
  id: "app-tt-pub", name: "time-track (public)", type: "self_hosted", domain: `${DOMAIN}/time-track`, session_duration: "24h",
  destinations: TT.publicPaths.map((p) => ({ type: "public", uri: `${DOMAIN}${p}` })), policies: [{ id: "pol-public", precedence: 1 }],
};
const TT_ALLOW_APP = {
  id: "app-tt", name: "time-track (/time-track)", type: "self_hosted",
  destinations: [{ type: "public", uri: `${DOMAIN}/time-track/*` }], policies: [{ id: "pol-signin", precedence: 1 }],
};
const TT_DB = "22222222-2222-4333-8444-555555555555";
const WRANGLER_TT_DONE = WRANGLER_TT.replace(`"database_id": "REPLACE_AFTER_d1_create"`, `"database_id": "${TT_DB}"`);

function timeTrackTodayWorld() {
  const world = timeTrackWorld();
  world.site.policies.push({ id: "pol-signin", name: POLICY_NAME, reusable: true, decision: "allow", include: [{ email: { email: "owner@example.com" } }] }, BYPASS_POLICY);
  world.site.apps.push(structuredClone(TT_ALLOW_APP), structuredClone(TT_PUBLIC_APP));
  world.site.d1.push({ uuid: TT_DB, name: "time-track" });
  world.local.github = { environment: true, secrets: ["CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"] };
  return world;
}
// The owner's fix: the same run with the trailing-slash path added.
const TT_SLASH = [...TT.publicPaths, "/time-track/"];
const ttSlashOpts = (cloneDir) => ({ ...TT, publicPaths: TT_SLASH, cloneDir, config });

test("mixed, time-track as it stands today plans zero changes", async () => {
  const world = timeTrackTodayWorld();
  const cloneDir = makeClone(WRANGLER_TT_DONE);
  const h = harness(world);
  const actions = await planBootstrap(h.deps, ttOpts(cloneDir));
  assert.deepEqual(creates(actions), []);
  assert.deepEqual(conflicts(actions), []);
  assert.match(formatPlan(actions, { ...ttOpts(cloneDir), apply: false }), /nothing to create — already bootstrapped$/);
  assert.deepEqual(writes(h.http.calls), []);
});

test("mixed, time-track re-run with /time-track/: the plan adds only that destination to the public app, and says who answers it and its children", async () => {
  const world = timeTrackTodayWorld();
  const cloneDir = makeClone(WRANGLER_TT_DONE);
  const h = harness(world);
  const actions = await planBootstrap(h.deps, ttSlashOpts(cloneDir));

  assert.deepEqual(creates(actions), ["bypass-app-add:time-track (public)"]);
  assert.deepEqual(conflicts(actions), []);
  const add = actions.find((a) => a.kind === "bypass-app-add");
  assert.deepEqual([add.appId, add.held, add.uris], ["app-tt-pub", [`${DOMAIN}/time-track`, `${DOMAIN}/time-track/_next/*`], [`${DOMAIN}/time-track/`]]);
  assert.ok(!actions.some((a) => a.kind === "app-narrowing" || a.kind === "bypass-policy"), "no change to the Allow app, no policy step");

  const text = formatPlan(actions, { ...ttSlashOpts(cloneDir), apply: false });
  assert.equal(text.split("\nD1\n")[0], [
    "portfolio-app bootstrap owner/time-track /time-track (mixed: sign-in except /time-track, /time-track/_next/*, /time-track/) — plan only; nothing changes without --apply",
    "Cloudflare Access",
    `  ✓ reusable policy "portfolio-app sign-in" exists`,
    `  ✓ Access app "time-track (/time-track)" guards example.com/time-track/*`,
    `  ✓ Bypass app "time-track (public)" lets anyone reach example.com/time-track + example.com/time-track/_next/*`,
    `  + add public destination(s) example.com/time-track/ to Bypass app "time-track (public)" (anyone, no sign-in)`,
    "  · who answers (the app with the most specific matching destination wins; Bypass lets anyone in, Allow asks for sign-in):",
    `  ·   example.com/time-track → "time-track (public)" — Bypass, anyone`,
    `  ·   example.com/time-track/_next/* → "time-track (public)" — Bypass, anyone`,
    `  ·   example.com/time-track/ → "time-track (public)" — Bypass, anyone`,
    `  ·   example.com/time-track/* (the rest) → "time-track (/time-track)" — Allow, sign-in`,
    `  ·   example.com/time-track/<child> → "time-track (/time-track)" (by example.com/time-track/*) — Allow, sign-in; ` +
      "example.com/time-track/ covers its subpaths too and the docs do not rank it against example.com/time-track/*, " +
      "so apply checks a child and takes example.com/time-track/ out again if one answers signed out",
    "  · now: https://example.com/time-track/portfolio-app-verify 302 → test-team.cloudflareaccess.com; " +
      "https://example.com/time-track/api/portfolio-app-verify 302 → test-team.cloudflareaccess.com; " +
      "https://example.com/time-track answered 200 signed out — no Access redirect; " +
      "https://example.com/time-track/_next/portfolio-app-verify answered 200 signed out — no Access redirect; " +
      "https://example.com/time-track/ 302 → test-team.cloudflareaccess.com — public path still asks for sign-in",
  ].join("\n"));
  assert.match(text, /1 change\(s\) planned$/);
  assert.deepEqual(writes(h.http.calls), [], "no Cloudflare write in plan mode");
});

test("mixed, time-track re-run with /time-track/ applied: one PUT adds it, the children still ask for sign-in, and a re-run is a no-op", async () => {
  const world = timeTrackTodayWorld();
  const cloneDir = makeClone(WRANGLER_TT_DONE);
  const h = harness(world);
  const opts = ttSlashOpts(cloneDir);
  const actions = await planBootstrap(h.deps, opts);
  const planCalls = h.http.calls.length;
  await applyBootstrap(h.deps, actions, opts);

  const sent = writes(h.http.calls.slice(planCalls));
  assert.deepEqual(api(sent), ["PUT /access/apps/app-tt-pub"], "one write: the public app, nothing else");
  const expected = { ...TT_PUBLIC_APP, destinations: [...TT_PUBLIC_APP.destinations, { type: "public", uri: `${DOMAIN}/time-track/` }] };
  assert.deepEqual(sent[0].body, expected, "the full current settings, with only time-track/ added");
  assert.deepEqual(world.site.apps, [TT_ALLOW_APP, expected], "the Allow app is untouched");
  const probes = h.http.calls.slice(planCalls).filter((c) => !c.url.startsWith("https://api.")).map((c) => new URL(c.url).pathname);
  for (const child of ["/time-track/portfolio-app-verify", "/time-track/api/portfolio-app-verify"]) {
    assert.ok(probes.includes(child), `${child} is probed`);
  }
  assert.deepEqual(h.logs, [
    `  + added public destination(s) ${DOMAIN}/time-track/ to Bypass app "time-track (public)"`,
    `  ✓ /time-track redirects to ${TEAM}.cloudflareaccess.com, and its 3 public path(s) answer signed out`,
  ]);

  const replan = await planBootstrap(harness(world).deps, opts);
  assert.deepEqual(creates(replan), []);
  assert.match(replan.find((a) => a.kind === "bypass-app").summary, /lets anyone reach example\.com\/time-track \+ example\.com\/time-track\/_next\/\* \+ example\.com\/time-track\/$/);
  assert.equal(replan.find((a) => a.kind === "check").state, "ok");
});

test("mixed, time-track re-run with /time-track/ when its children inherit it: apply takes exactly time-track/ out again at once and leaves the rest", async () => {
  const world = timeTrackTodayWorld();
  world.site.slashOutranksWildcard = true;
  const before = structuredClone(world.site.apps);
  const cloneDir = makeClone(WRANGLER_TT_DONE);
  const h = harness(world);
  const opts = ttSlashOpts(cloneDir);
  const actions = await planBootstrap(h.deps, opts);
  const planCalls = h.http.calls.length;
  let sleeps = 0;
  await assert.rejects(applyBootstrap({ ...h.deps, sleep: async () => { sleeps += 1; } }, actions, opts), (err) => {
    assert.match(err.message, /^Access is not answering for \/time-track: https:\/\/example\.com\/time-track\/portfolio-app-verify answered 200 — not behind Access; https:\/\/example\.com\/time-track\/api\/portfolio-app-verify answered 200 — not behind Access\. /);
    assert.match(err.message, /Rolled back: removed example\.com\/time-track\/ from Bypass app "time-track \(public\)", which keeps example\.com\/time-track \+ example\.com\/time-track\/_next\/\*\. After the rollback, the site passes the check without \/time-track\/$/);
    return true;
  });
  assert.equal(sleeps, 0, "the children asked for sign-in before the run, so their first signed-out answer ends the wait");
  assert.deepEqual(world.site.apps, before.map((a) => ({ ...a, policies: a.policies.map(({ id, precedence }) => ({ id, precedence })) })),
    "both apps hold exactly what they held before the run");
  assert.deepEqual(api(writes(h.http.calls.slice(planCalls))), ["PUT /access/apps/app-tt-pub", "PUT /access/apps/app-tt-pub"]);
  assert.deepEqual(writes(h.http.calls.slice(planCalls))[1].body, TT_PUBLIC_APP, "the second PUT is the app as it was");
  assert.deepEqual(h.logs, [
    `  + added public destination(s) ${DOMAIN}/time-track/ to Bypass app "time-track (public)"`,
    `  ! https://${DOMAIN}/time-track/portfolio-app-verify, https://${DOMAIN}/time-track/api/portfolio-app-verify asked for sign-in before this run and answer(s) signed out now, so the wait stopped at once; taking ${DOMAIN}/time-track/ out again`,
    `  - removed ${DOMAIN}/time-track/ from Bypass app "time-track (public)", which keeps ${DOMAIN}/time-track + ${DOMAIN}/time-track/_next/*`,
  ]);
  const recheck = h.http.calls.slice(planCalls).filter((c) => !c.url.startsWith("https://api.")).slice(-4).map((c) => new URL(c.url).pathname);
  assert.deepEqual(recheck, ["/time-track/portfolio-app-verify", "/time-track/api/portfolio-app-verify", "/time-track", "/time-track/_next/portfolio-app-verify"],
    "re-checked with the public paths that were there before the run");
});

test("mixed, time-track re-run with /time-track/ that never answers signed out: apply waits the attempts out, then takes time-track/ out again", async () => {
  const world = timeTrackTodayWorld();
  const h = harness(world);
  const opts = ttSlashOpts(makeClone(WRANGLER_TT_DONE));
  const actions = await planBootstrap(h.deps, opts);
  const planCalls = h.http.calls.length;
  const stillAsking = async (url, init) => (new URL(url).pathname === "/time-track/"
    ? { status: 302, headers: new Map([["location", ACCESS_LOGIN]]) } : h.http.fetch(url, init));
  let sleeps = 0;
  await assert.rejects(applyBootstrap({ ...h.deps, fetch: stillAsking, sleep: async () => { sleeps += 1; } }, actions, opts), (err) => {
    assert.match(err.message, /^Access is not answering for \/time-track: https:\/\/example\.com\/time-track\/ 302 .*public path still asks for sign-in\. Rolled back: removed example\.com\/time-track\/ from Bypass app "time-track \(public\)", which keeps example\.com\/time-track \+ example\.com\/time-track\/_next\/\*\. After the rollback, the site passes the check without \/time-track\/$/);
    return true;
  });
  assert.equal(sleeps, 11, "no private path stopped asking for sign-in, so the wait runs to its last attempt");
  assert.ok(h.logs.includes(`  ! the check failed after this run made ${DOMAIN}/time-track/ public; taking ${DOMAIN}/time-track/ out again`), h.logs.join("\n"));
  assert.deepEqual(api(writes(h.http.calls.slice(planCalls))), ["PUT /access/apps/app-tt-pub", "PUT /access/apps/app-tt-pub"]);
  assert.deepEqual(world.site.apps, [TT_ALLOW_APP, TT_PUBLIC_APP], "both apps hold exactly what they held before the run");
  assert.deepEqual(world.site.d1.length, 1, "no second D1 database");
});

test("mixed, fresh time-track with /time-track/ when its children inherit it: the Bypass app this run created loses only time-track/, and both apps stay", async () => {
  const world = timeTrackWorld();
  world.site.slashOutranksWildcard = true;
  const cloneDir = makeClone(WRANGLER_TT);
  const h = harness(world);
  const opts = ttSlashOpts(cloneDir);
  const actions = await planBootstrap(h.deps, opts);
  const planCalls = h.http.calls.length;
  assert.deepEqual(actions.find((a) => a.kind === "bypass-app").uris, [`${DOMAIN}/time-track`, `${DOMAIN}/time-track/_next/*`, `${DOMAIN}/time-track/`]);
  let sleeps = 0;
  await assert.rejects(applyBootstrap({ ...h.deps, sleep: async () => { sleeps += 1; } }, actions, opts), (err) => {
    assert.match(err.message, /^Access is not answering for \/time-track: .*time-track\/portfolio-app-verify answered 200 — not behind Access/);
    assert.match(err.message, /Rolled back: removed example\.com\/time-track\/ from Bypass app "time-track \(public\)", which keeps example\.com\/time-track \+ example\.com\/time-track\/_next\/\*\. After the rollback, the site passes the check without \/time-track\/$/);
    return true;
  });
  assert.equal(sleeps, 0, "the children asked for sign-in before time-track/ was public, so the wait stops at once");
  assert.deepEqual(world.site.apps.map((a) => [a.name, a.destinations.map((d) => d.uri)]), [
    ["time-track (/time-track)", [`${DOMAIN}/time-track/*`]],
    ["time-track (public)", [`${DOMAIN}/time-track`, `${DOMAIN}/time-track/_next/*`]],
  ]);
  const bypassId = world.site.apps[1].id;
  assert.deepEqual(api(writes(h.http.calls.slice(planCalls))),
    ["POST /access/policies", "POST /access/apps", "POST /access/policies", "POST /access/apps", `PUT /access/apps/${bypassId}`], "no app is deleted");
  assert.deepEqual(world.site.policies.map((p) => p.name), [POLICY_NAME, PUBLIC_POLICY_NAME]);
  assert.deepEqual(world.site.d1, [], "no D1 database");
  assert.deepEqual(world.local.github, { environment: false, secrets: [] }, "GitHub untouched");
});

test("mixed rollback takes only <domain>/<prefix>/ out of an app that changed after the run wrote it", async () => {
  const world = timeTrackWorld();
  world.site.slashOutranksWildcard = true;
  const h = harness(world);
  const opts = ttSlashOpts(makeClone(WRANGLER_TT));
  const actions = await planBootstrap(h.deps, opts);
  const meddling = async (url, init) => {
    const created = world.site.apps.find((a) => a.name === "time-track (public)");
    if (created && !created.destinations.some((d) => d.uri === `${DOMAIN}/time-track/other`)) created.destinations.push({ type: "public", uri: `${DOMAIN}/time-track/other` });
    return h.http.fetch(url, init);
  };
  await assert.rejects(applyBootstrap({ ...h.deps, fetch: meddling }, actions, opts),
    /Rolled back: removed example\.com\/time-track\/ from Bypass app "time-track \(public\)", which keeps example\.com\/time-track \+ example\.com\/time-track\/_next\/\* \+ example\.com\/time-track\/other\. After the rollback, /);
  assert.deepEqual(world.site.apps.map((a) => a.name), ["time-track (/time-track)", "time-track (public)"]);
  assert.deepEqual(world.site.apps[1].destinations.map((d) => d.uri), [`${DOMAIN}/time-track`, `${DOMAIN}/time-track/_next/*`, `${DOMAIN}/time-track/other`]);
});

test("mixed rollback leaves a Bypass app whose only destination is <domain>/<prefix>/ in place and says to remove it by hand", async () => {
  const world = timeTrackWorld();
  world.site.slashOutranksWildcard = true;
  const h = harness(world);
  const opts = { ...ttOpts(makeClone(WRANGLER_TT)), publicPaths: ["/time-track/"] };
  const actions = await planBootstrap(h.deps, opts);
  const planCalls = h.http.calls.length;
  await assert.rejects(applyBootstrap(h.deps, actions, opts), (err) => {
    assert.match(err.message, /Rolled back: left Bypass app "time-track \(public\)" in place: example\.com\/time-track\/ is its only destination, and the tool does not delete an app; remove it in the Zero Trust dashboard now\. /);
    assert.match(err.message, /After the rollback, the site still fails the check without \/time-track\/: https:\/\/example\.com\/time-track\/portfolio-app-verify answered 200 — not behind Access$/);
    return true;
  });
  assert.deepEqual(api(writes(h.http.calls.slice(planCalls))), ["POST /access/policies", "POST /access/apps", "POST /access/policies", "POST /access/apps"],
    "nothing is written after the apps are created");
  assert.deepEqual(world.site.apps.map((a) => [a.name, a.destinations.map((d) => d.uri)]), [
    ["time-track (/time-track)", [`${DOMAIN}/time-track`, `${DOMAIN}/time-track/*`]],
    ["time-track (public)", [`${DOMAIN}/time-track/`]],
  ]);
});

test("mixed refuses to add a public path to a public app it cannot change unambiguously, and writes nothing", async () => {
  const cases = [
    ["a public app that is not Bypass for Everyone", (app) => { app.policies = [{ id: "pol-signin", precedence: 1 }]; },
      /Access app "time-track \(public\)" holds \[example\.com\/time-track, example\.com\/time-track\/_next\/\*\], some of the public paths, but it is not Bypass for Everyone, so the tool cannot add example\.com\/time-track\/; add them by hand/],
    ["a public app that is not self-hosted", (app) => { app.type = "saas"; }, /but it is a saas app, not self-hosted, so the tool cannot add/],
    ["a public app in self_hosted_domains", (app) => { app.self_hosted_domains = app.destinations.map((d) => d.uri); delete app.destinations; },
      /but its paths are in the deprecated self_hosted_domains, not destinations, so the tool cannot add/],
  ];
  for (const [name, change, summary] of cases) {
    const world = timeTrackTodayWorld();
    change(world.site.apps[1]);
    const before = structuredClone(world.site.apps);
    const h = harness(world);
    const opts = ttSlashOpts(makeClone(WRANGLER_TT_DONE));
    const actions = await planBootstrap(h.deps, opts);
    assert.ok(conflicts(actions).some((a) => summary.test(a.summary)), `${name}: ${conflicts(actions).map((a) => a.summary).join(" | ")}`);
    assert.deepEqual(creates(actions), [], name);
    await assert.rejects(applyBootstrap(h.deps, actions, opts), /refusing to apply/, name);
    assert.deepEqual(writes(h.http.calls), [], name);
    assert.deepEqual(world.site.apps, before, `${name}: no app changed`);
  }
});

test("mixed apply refuses to PUT the public app when its paths changed after the plan", async () => {
  const world = timeTrackTodayWorld();
  const h = harness(world);
  const opts = ttSlashOpts(makeClone(WRANGLER_TT_DONE));
  const actions = await planBootstrap(h.deps, opts);
  world.site.apps[1].destinations.push({ type: "public", uri: `${DOMAIN}/time-track/about` });
  const before = structuredClone(world.site.apps);
  const planCalls = h.http.calls.length;
  await assert.rejects(applyBootstrap(h.deps, actions, opts), /^Error: Access app "time-track \(public\)" now guards \[.*time-track\/about\], not .* as planned; re-run the plan$/);
  assert.deepEqual(writes(h.http.calls.slice(planCalls)), []);
  assert.deepEqual(world.site.apps, before);
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
    ["/graph//", /no empty/],
    ["/graph/public/", /no empty.*the one trailing "\/" allowed is \/graph\/ itself/],
    ["/graph/_next/", /no empty/],
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

test("cli accepts --public-path /time-track/ for bootstrap and verify, and verify probes the children as private", async () => {
  const world = timeTrackTodayWorld();
  const plan = await cli(world, ["bootstrap", "--repo", TT.repo, "--path", TT.prefix, ...TT_SLASH.flatMap((p) => ["--public-path", p]),
    "--clone", makeClone(WRANGLER_TT_DONE), "--config", writeConfig()]);
  assert.equal(plan.code, 0, plan.err);
  assert.match(plan.out, /\+ add public destination\(s\) example\.com\/time-track\/ to Bypass app "time-track \(public\)"/);
  assert.deepEqual(writes(plan.http.calls), []);

  const args = ["verify", "--path", "/time-track", "--domain", DOMAIN, "--team", TEAM, ...TT_SLASH.flatMap((p) => ["--public-path", p])];
  const before = await cli(world, args);
  assert.equal(before.code, 1, "time-track/ still asks for sign-in");
  assert.match(before.out, /✗ https:\/\/example\.com\/time-track\/ 302 .*public path still asks for sign-in/);

  world.site.apps[1].destinations.push({ type: "public", uri: `${DOMAIN}/time-track/` });
  const after = await cli(world, args);
  assert.equal(after.code, 0, after.out);
  assert.deepEqual(after.http.calls.map((c) => new URL(c.url).pathname), [
    "/time-track/portfolio-app-verify", "/time-track/api/portfolio-app-verify",
    "/time-track", "/time-track/_next/portfolio-app-verify", "/time-track/",
  ]);
  assert.match(after.out, /example\.com\/time-track is behind Cloudflare Access, and its 3 public path\(s\) answer signed out$/);

  world.site.slashOutranksWildcard = true;
  const inherited = await cli(world, args);
  assert.equal(inherited.code, 1, "children that inherit time-track/ fail the check");
  assert.match(inherited.out, /✗ https:\/\/example\.com\/time-track\/api\/portfolio-app-verify answered 200 — not behind Access/);
});

test("cli verify --public-path checks both ways; without it verify is unchanged", async () => {
  const world = graphNotesWorld();
  const args = ["verify", "--path", "/graph", "--domain", DOMAIN, "--team", TEAM, ...GN.publicPaths.flatMap((p) => ["--public-path", p])];

  const before = await cli(world, args);
  assert.equal(before.code, 1, "public paths still ask for sign-in");
  assert.match(before.out, /NOT set up as a mixed app \(sign-in except \/graph\/public, .*\) — refusing/);
  assert.match(before.out, /✗ https:\/\/example\.com\/graph\/public .*public path still asks for sign-in/);

  world.site.policies.push(BYPASS_POLICY);
  world.site.apps.push({ id: "app-pub", name: "graph-notes (public)", destinations: GN_PUBLIC.map((uri) => ({ uri })), policies: [{ id: "pol-public" }] });
  const inherited = await cli(world, args);
  assert.equal(inherited.code, 1, "without narrowing, a child of an exact public path inherits its Bypass");
  assert.match(inherited.out, /✗ https:\/\/example\.com\/graph\/public\/portfolio-app-verify answered 200 — not behind Access/);
  assert.match(inherited.out, /NOT set up as a mixed app/);

  world.site.apps[0] = structuredClone(ALLOW_APP_NARROWED);
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

  const plain = await cli(world, ["verify", "--path", "/graph", "--domain", DOMAIN, "--team", TEAM]);
  assert.equal(plain.code, 0);
  assert.deepEqual(plain.http.calls.map((c) => new URL(c.url).pathname), ["/graph", "/graph/portfolio-app-verify"]);
  assert.match(plain.out, /example\.com\/graph is behind Cloudflare Access$/);
});

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  accessDestinations, allowDestinations, classifyRoute, narrowingDestinations, normalizePrefix, normalizePublicPath, pathCovers,
  privateDestinations, publicDestinations, workerRoute,
} from "../skills/portfolio-app/scripts/lib/prefix.mjs";
import { checkAccessRedirect, checkRoutes, checkSignedOutAnswer, mixedProbes, verifyPath, waitForAccess } from "../skills/portfolio-app/scripts/lib/verify.mjs";
import { appPolicies, isBypassEveryone, withAddedDestinations, withoutDestination } from "../skills/portfolio-app/scripts/lib/cloudflare.mjs";
import { D1_PLACEHOLDER, d1Target, parseJsonc, r2Target, replacePlaceholder } from "../skills/portfolio-app/scripts/lib/wrangler-config.mjs";
import { parseConfig, repoFromRemote } from "../skills/portfolio-app/scripts/lib/local.mjs";
import { ACCESS_LOGIN, DOMAIN, TEAM, WRANGLER_WITH_PLACEHOLDER, fakeFetch } from "./helpers.mjs";

test("normalizePrefix accepts sub-app prefixes and normalizes the slashes", () => {
  assert.equal(normalizePrefix("/quiz"), "/quiz");
  assert.equal(normalizePrefix("quiz/"), "/quiz");
  assert.equal(normalizePrefix("/chat-archives"), "/chat-archives");
  assert.equal(normalizePrefix("/tools/graph_notes"), "/tools/graph_notes");
  assert.equal(normalizePrefix("/work/election-atlas"), "/work/election-atlas");
  assert.equal(normalizePrefix("work/election-atlas/"), "/work/election-atlas");
});

test("normalizePrefix refuses the site root and ai-chat-archive's site-wide routes", () => {
  for (const bad of ["", "   ", "/", "//", "/api", "/api/x", "/_next", "/_next/static", "/vendors", "vendors/"]) {
    assert.throws(() => normalizePrefix(bad), /refusing path/, `should refuse ${JSON.stringify(bad)}`);
  }
});

test("normalizePrefix refuses wildcards, dots, queries and odd characters", () => {
  for (const bad of ["/quiz/*", "/*", "/a/../b", "/Quiz", "/a b", "/a?x=1", "/a//b", "/.well-known"]) {
    assert.throws(() => normalizePrefix(bad), /refusing path/, `should refuse ${JSON.stringify(bad)}`);
  }
});

test("accessDestinations is exactly the prefix and everything under it", () => {
  assert.deepEqual(accessDestinations("example.com", "/graph"), ["example.com/graph", "example.com/graph/*"]);
});

test("checkAccessRedirect passes only a redirect to the team's Access login", async () => {
  const respond = (status, location) => async () => ({ status, headers: new Map(location ? [["location", location]] : []) });
  const url = `https://${DOMAIN}/quiz`;
  assert.equal((await checkAccessRedirect({ fetch: respond(302, ACCESS_LOGIN), url, team: TEAM })).ok, true);
  assert.equal((await checkAccessRedirect({ fetch: respond(302, ACCESS_LOGIN), url })).ok, true, "any team when none is pinned");
  assert.equal((await checkAccessRedirect({ fetch: respond(302, ACCESS_LOGIN), url, team: "other" })).ok, false);
  assert.equal((await checkAccessRedirect({ fetch: respond(200), url })).ok, false);
  assert.equal((await checkAccessRedirect({ fetch: respond(302, "https://example.com/login"), url })).ok, false);
  assert.equal((await checkAccessRedirect({ fetch: respond(302, "https://evil.example/cdn-cgi/access/login/x"), url })).ok, false);
  assert.equal(
    (await checkAccessRedirect({ fetch: respond(302, `https://${TEAM}.cloudflareaccess.com/other`), url })).ok,
    false,
    "an Access host is not enough; it must be the login path",
  );
  const thrown = await checkAccessRedirect({ fetch: async () => { throw new Error("ENOTFOUND"); }, url });
  assert.equal(thrown.ok, false);
  assert.match(thrown.reason, /ENOTFOUND/);
});

test("checkAccessRedirect never follows the redirect", async () => {
  const { fetch, calls } = fakeFetch({ protected: ["/quiz"] });
  await checkAccessRedirect({ fetch, url: `https://${DOMAIN}/quiz` });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, `https://${DOMAIN}/quiz`);
});

test("verifyPath needs both the bare prefix and a path under it behind Access", async () => {
  const bareOnly = async (url) =>
    new URL(url).pathname === "/quiz"
      ? { status: 302, headers: new Map([["location", ACCESS_LOGIN]]) }
      : { status: 200, headers: new Map() };
  const result = await verifyPath({ fetch: bareOnly, domain: DOMAIN, prefix: "/quiz", team: TEAM });
  assert.equal(result.ok, false);
  assert.deepEqual(result.results.map((r) => r.ok), [true, false]);

  const { fetch } = fakeFetch({ protected: ["/quiz"] });
  assert.equal((await verifyPath({ fetch, domain: DOMAIN, prefix: "/quiz", team: TEAM })).ok, true);
});

test("parseJsonc reads comments, block comments and trailing commas", () => {
  const parsed = parseJsonc(WRANGLER_WITH_PLACEHOLDER);
  assert.equal(parsed.d1_databases[0].database_id, D1_PLACEHOLDER);
  assert.equal(parsed.routes[0].pattern, "example.com/graph/api/*");
  assert.deepEqual(parseJsonc('{ "url": "https://x//y", "s": "a /* b */ c" }'), { url: "https://x//y", s: "a /* b */ c" });
});

test("replacePlaceholder edits only the string value, leaving comments byte-for-byte", () => {
  const id = "11111111-2222-4333-8444-555555555555";
  const out = replacePlaceholder(WRANGLER_WITH_PLACEHOLDER, id);
  assert.match(out, new RegExp(`"database_id": "${id}"`));
  assert.match(out, /\/\* CI refuses to deploy while "REPLACE_AFTER_d1_create" is here\. \*\//, "comment untouched");
  assert.match(out, /\/\/ Under \/graph\/ on purpose/);
  assert.equal(out.length, WRANGLER_WITH_PLACEHOLDER.length + id.length - D1_PLACEHOLDER.length);
  assert.equal(replacePlaceholder(out, "other"), out, "idempotent once the placeholder is gone");
});

test("d1Target decides one database per app, and says why when it cannot", () => {
  const entry = (name, id = D1_PLACEHOLDER) => ({ binding: "DB", database_name: name, database_id: id });
  assert.deepEqual(d1Target({}, undefined), { state: "none" });
  assert.deepEqual(d1Target({ d1_databases: [entry("g")] }, undefined), { state: "missing", name: "g" });
  assert.deepEqual(d1Target({ d1_databases: [entry("g"), entry("g")] }, "g"), { state: "missing", name: "g" });
  assert.equal(d1Target({ d1_databases: [entry("g")] }, "other").state, "conflict");
  assert.equal(d1Target({ d1_databases: [entry("a"), entry("b")] }, undefined).state, "conflict");
  assert.equal(d1Target({ d1_databases: [{ binding: "DB", database_id: D1_PLACEHOLDER }] }, undefined).state, "conflict");
  assert.deepEqual(d1Target({ d1_databases: [entry("g", "real-id")] }, undefined), { state: "present", name: "g" });
  assert.deepEqual(d1Target({ d1_databases: [entry("g", "real-id")] }, "g"), { state: "present", name: "g" });
  assert.equal(d1Target({ d1_databases: [entry("g", "real-id")] }, "nope").state, "conflict");
});

test("parseConfig names every problem at once", () => {
  const good = { domain: "example.com", accountId: "0".repeat(32), team: "t", allowedEmails: ["a@b.c"] };
  assert.deepEqual(parseConfig(JSON.stringify(good), "c.json"), good);
  assert.throws(() => parseConfig("{", "c.json"), /not valid JSON/);
  assert.throws(
    () => parseConfig(JSON.stringify({ domain: "https://x", accountId: "nope", allowedEmails: [] }), "c.json"),
    (err) => ["domain", "accountId", "team", "allowedEmails"].every((f) => err.message.includes(`"${f}"`)),
  );
});

test("repoFromRemote reads https and ssh GitHub remotes", () => {
  assert.equal(repoFromRemote("git@github.com:owner/graph-notes.git"), "owner/graph-notes");
  assert.equal(repoFromRemote("https://github.com/owner/graph-notes.git\n"), "owner/graph-notes");
  assert.equal(repoFromRemote("https://github.com/owner/graph-notes"), "owner/graph-notes");
  assert.equal(repoFromRemote("https://gitlab.com/owner/x.git"), null);
});

test("classifyRoute places a route relative to the app's prefix", () => {
  const at = (pattern) => classifyRoute(pattern, "example.com", "/work/election-atlas");
  assert.equal(at("example.com/work/election-atlas*"), "exact", "the app's own route");
  assert.equal(at("https://example.com/work/election-atlas*"), "exact");
  assert.equal(at("example.com/work/election-atlas"), "pair", "the old exact route <prefix>* replaces");
  assert.equal(at("example.com/work/election-atlas/*"), "pair");
  assert.equal(at("https://example.com/work/election-atlas/*"), "pair");
  assert.equal(at("example.com/work/election-atlas/api/*"), "inside");
  assert.equal(at("example.com/work/election-atlas/data.json"), "inside");
  assert.equal(at("*example.com/work/election-atlas/*"), "inside", "a wildcard host is never the app's own pattern");
  assert.equal(at("example.com/*"), "broader");
  assert.equal(at("*example.com/*"), "broader");
  assert.equal(at("example.com/work/*"), "broader");
  assert.equal(at("*example.com/work/election-atlas*"), "straddles", "a wildcard host is never the app's own pattern");
  assert.equal(at("example.com/work/election*"), "broader", "shorter than the app's own route, which wins under the prefix");
  assert.equal(at("example.com/work/election-atlas-old/*"), "sibling");
  assert.equal(at("example.com/work/election-atlasfoo"), "sibling");
  assert.equal(at("example.com/work/election-atlas-x*"), "sibling");
  assert.equal(at("example.com/work"), null);
  assert.equal(at("example.com/quiz/*"), null);
  assert.equal(at("*.example.com/*"), null, "a subdomain wildcard does not cover the apex");
  assert.equal(at("other.com/*"), null);
});

test("r2Target picks one bucket per app, and says why when it cannot", () => {
  const cfg = (...names) => ({ r2_buckets: names.map((bucket_name) => ({ binding: "B", bucket_name })) });
  assert.deepEqual(r2Target({}), { state: "none" });
  assert.deepEqual(r2Target(cfg("bills")), { state: "wanted", name: "bills" });
  assert.deepEqual(r2Target(cfg("bills", "bills")), { state: "wanted", name: "bills" });
  assert.equal(r2Target(cfg("a-1", "b-2")).state, "conflict");
  assert.equal(r2Target({ r2_buckets: [{ binding: "B" }] }).state, "conflict");
  assert.equal(r2Target(cfg("Bad_Name")).state, "conflict");
});

test("normalizePublicPath keeps a public path inside the app's prefix, exact or with a final /*", () => {
  assert.equal(normalizePublicPath("time-track", "/time-track"), "/time-track");
  assert.equal(normalizePublicPath("/time-track/_next/*", "/time-track"), "/time-track/_next/*");
  assert.equal(normalizePublicPath("graph/public.txt", "/graph"), "/graph/public.txt");
  assert.equal(normalizePublicPath("/graph/manifest.webmanifest", "/graph"), "/graph/manifest.webmanifest");
  assert.equal(normalizePublicPath("/work/atlas/data/*", "/work/atlas"), "/work/atlas/data/*");
  for (const bad of ["", "/", "/graphs", "/graphx/public", "/quiz/public", "/graph/*", "/*", "/graph/a*", "/graph/*/b",
    "/graph/..", "/graph/./x", "/graph//x", "/graph/a b", "/graph/x?y", "/graph/x#y"]) {
    assert.throws(() => normalizePublicPath(bad, "/graph"), /refusing public path/, `should refuse ${JSON.stringify(bad)}`);
  }
});

test("normalizePublicPath accepts the prefix's trailing-slash path exactly, and no other empty segment", () => {
  assert.equal(normalizePublicPath("/time-track/", "/time-track"), "/time-track/");
  assert.equal(normalizePublicPath("time-track/", "/time-track"), "/time-track/", "the leading slash stays optional");
  assert.equal(normalizePublicPath("/work/atlas/", "/work/atlas"), "/work/atlas/");
  for (const bad of ["/time-track//", "/time-track///", "/time-track//x", "/time-track/x/", "/time-track/_next/", "/time-track/x//y",
    "/time-track/*/", "/work/", "/time-trac/", "/time-track-old/"]) {
    assert.throws(() => normalizePublicPath(bad, bad.startsWith("/work") ? "/work/atlas" : "/time-track"),
      /refusing public path/, `should refuse ${JSON.stringify(bad)}`);
  }
  assert.throws(() => normalizePublicPath("/time-track/x/", "/time-track"), /no empty, "\." or "\.\." segments.*the one trailing "\/" allowed is \/time-track\/ itself/);
});

test("a public <prefix>/ is an exact destination with no narrowing, and its child is the usual private probe", () => {
  const paths = ["/time-track", "/time-track/_next/*", "/time-track/"];
  assert.deepEqual(publicDestinations("example.com", paths), ["example.com/time-track", "example.com/time-track/_next/*", "example.com/time-track/"]);
  assert.deepEqual(privateDestinations("example.com", "/time-track", paths), ["example.com/time-track/*"]);
  assert.deepEqual(narrowingDestinations("example.com", "/time-track", paths), [], "its narrowing destination would be <prefix>/* itself");
  assert.equal(pathCovers("/time-track/", "/time-track/"), true);
  assert.equal(pathCovers("/time-track/", "/time-track/x"), false, "an exact public path, not a directory");
  assert.deepEqual(mixedProbes("example.com", "/time-track", paths), [
    { url: "https://example.com/time-track/portfolio-app-verify", expect: "access" },
    { url: "https://example.com/time-track/api/portfolio-app-verify", expect: "access" },
    { url: "https://example.com/time-track", expect: "public" },
    { url: "https://example.com/time-track/_next/portfolio-app-verify", expect: "public" },
    { url: "https://example.com/time-track/", expect: "public" },
  ]);
});

test("waitForAccess stops at once when a probe that asked for sign-in before stops asking", async () => {
  const login = { status: 302, headers: new Map([["location", `https://${TEAM}.cloudflareaccess.com/cdn-cgi/access/login/x`]]) };
  const fetch = async (url) => (url.endsWith("/q/portfolio-app-verify") ? { status: 200, headers: new Map() } : login);
  const exposedUrl = `https://${DOMAIN}/q/portfolio-app-verify`;
  let sleeps = 0;
  const sleep = async () => { sleeps += 1; };
  const watched = await waitForAccess({ fetch, domain: DOMAIN, prefix: "/q", team: TEAM, mustStayPrivate: [exposedUrl], sleep, attempts: 5 });
  assert.equal(watched.ok, false);
  assert.deepEqual(watched.exposed, [exposedUrl]);
  assert.equal(sleeps, 0);
  const plain = await waitForAccess({ fetch, domain: DOMAIN, prefix: "/q", team: TEAM, sleep, attempts: 5 });
  assert.equal(plain.exposed, undefined);
  assert.equal(sleeps, 4, "without the list, a failing probe is waited out to the last attempt");
  const unreachable = await waitForAccess({ fetch: async () => { throw new Error("socket hang up"); }, domain: DOMAIN, prefix: "/q", team: TEAM,
    mustStayPrivate: [exposedUrl], sleep, attempts: 2 });
  assert.equal(unreachable.exposed, undefined, "a request that failed is not an answer");
});

test("withoutDestination takes out exactly the named destination and keeps every other setting", () => {
  const app = {
    id: "a1", name: "x (public)", type: "self_hosted", session_duration: "24h",
    destinations: [{ type: "public", uri: "example.com/x" }, { type: "public", uri: "example.com/x/_next/*" }, { type: "public", uri: "example.com/x/" }],
    policies: [{ id: "p1", precedence: 1, decision: "bypass" }],
  };
  const kept = { ...app, destinations: [{ type: "public", uri: "example.com/x" }, { type: "public", uri: "example.com/x/_next/*" }], policies: [{ id: "p1", precedence: 1 }] };
  assert.deepEqual(withoutDestination(app, "example.com/x/"), kept);
  assert.deepEqual(withoutDestination({ ...app, domain: "example.com/x" }, "example.com/x/"), { ...kept, domain: "example.com/x" });
  assert.deepEqual(withoutDestination({ ...app, domain: "example.com/x/" }, "example.com/x/"), { ...kept, domain: "example.com/x" },
    "a domain that named the removed destination names the first that stays");
});

test("pathCovers and privateDestinations: a public bare prefix leaves only <prefix>/* behind sign-in", () => {
  assert.equal(pathCovers("/graph/_next/*", "/graph/_next/a.js"), true);
  assert.equal(pathCovers("/graph/_next/*", "/graph/_next"), false, "a /* destination does not cover its own base");
  assert.equal(pathCovers("/graph/public", "/graph/public/x"), false);
  assert.deepEqual(privateDestinations("example.com", "/time-track", ["/time-track", "/time-track/_next/*"]), ["example.com/time-track/*"]);
  assert.deepEqual(privateDestinations("example.com", "/graph", ["/graph/public"]), ["example.com/graph", "example.com/graph/*"]);
  assert.deepEqual(privateDestinations("example.com", "/graph", []), accessDestinations("example.com", "/graph"));
});

test("narrowingDestinations: <path>/* for each exact public path, except the bare prefix and paths a listed wildcard opens", () => {
  const graph = ["/graph/public", "/graph/public.txt", "/graph/_next/*", "/graph/icons/*", "/graph/manifest.webmanifest", "/graph/api/public/*"];
  assert.deepEqual(narrowingDestinations("example.com", "/graph", graph),
    ["example.com/graph/public/*", "example.com/graph/public.txt/*", "example.com/graph/manifest.webmanifest/*"]);
  assert.deepEqual(narrowingDestinations("example.com", "/time-track", ["/time-track", "/time-track/_next/*"]), [],
    "the bare prefix's children are the Allow app's own <prefix>/*");
  assert.deepEqual(narrowingDestinations("example.com", "/graph", ["/graph/docs/*", "/graph/docs/readme"]), [],
    "a path under a listed wildcard is public with its children");
  assert.deepEqual(narrowingDestinations("example.com", "/graph", ["/graph/about", "/graph/about/*"]), [],
    "listing <path>/* makes its children public on purpose");
  assert.deepEqual(narrowingDestinations("example.com", "/graph", ["/graph/abo/*", "/graph/about"]), ["example.com/graph/about/*"],
    "a wildcard covers by path segment, not by string prefix");
  assert.deepEqual(narrowingDestinations("example.com", "/graph", []), []);
  assert.deepEqual(allowDestinations("example.com", "/graph", ["/graph/public"]),
    ["example.com/graph", "example.com/graph/*", "example.com/graph/public/*"]);
  assert.deepEqual(allowDestinations("example.com", "/graph", []), accessDestinations("example.com", "/graph"));
});

test("withAddedDestinations keeps every setting and sends policies as id + precedence links", () => {
  const app = {
    id: "a1", name: "x", type: "self_hosted", domain: "example.com/x", session_duration: "12h", tags: ["t"],
    destinations: [{ type: "public", uri: "example.com/x" }],
    policies: [{ id: "p2", precedence: 2, decision: "allow", include: [] }, { id: "p1", precedence: 1 }, { id: "p3" }],
  };
  const before = structuredClone(app);
  assert.deepEqual(withAddedDestinations(app, ["example.com/x/a/*"]), {
    ...app,
    destinations: [{ type: "public", uri: "example.com/x" }, { type: "public", uri: "example.com/x/a/*" }],
    policies: [{ id: "p2", precedence: 2 }, { id: "p1", precedence: 1 }, { id: "p3", precedence: 3 }],
  });
  assert.deepEqual(app, before, "the input is not changed");
});

test("mixedProbes: private probes include the API and a child of each exact public path, and drop any a public path covers", () => {
  assert.deepEqual(mixedProbes("example.com", "/time-track", ["/time-track", "/time-track/_next/*"]), [
    { url: "https://example.com/time-track/portfolio-app-verify", expect: "access" },
    { url: "https://example.com/time-track/api/portfolio-app-verify", expect: "access" },
    { url: "https://example.com/time-track", expect: "public" },
    { url: "https://example.com/time-track/_next/portfolio-app-verify", expect: "public" },
  ]);
  const privateUrls = (publicPaths) => mixedProbes("example.com", "/graph", publicPaths).filter((p) => p.expect === "access").map((p) => p.url);
  assert.deepEqual(privateUrls(["/graph/api/*"]), ["https://example.com/graph", "https://example.com/graph/portfolio-app-verify"]);
  assert.deepEqual(privateUrls(["/graph/public", "/graph/public.txt", "/graph/_next/*"]), [
    "https://example.com/graph",
    "https://example.com/graph/portfolio-app-verify",
    "https://example.com/graph/api/portfolio-app-verify",
    "https://example.com/graph/public/portfolio-app-verify",
    "https://example.com/graph/public.txt/portfolio-app-verify",
  ]);
  assert.deepEqual(privateUrls(["/graph/public", "/graph/public/*"]), [
    "https://example.com/graph",
    "https://example.com/graph/portfolio-app-verify",
    "https://example.com/graph/api/portfolio-app-verify",
  ], "a child that another listed path makes public is not probed as private");
});

test("verifyPath with public paths fails when no private probe is left", async () => {
  const { fetch } = fakeFetch({ protected: [] });
  const all = ["/q", "/q/portfolio-app-verify", "/q/portfolio-app-verify/*", "/q/api/*"];
  const result = await verifyPath({ fetch, domain: DOMAIN, prefix: "/q", team: TEAM, publicPaths: all });
  assert.equal(result.results.every((r) => r.expect === "public" && r.ok), true);
  assert.equal(result.ok, false, "a mixed check with nothing private is never a pass");
});

test("checkSignedOutAnswer passes any non-Access answer except a bare 401/403", async () => {
  const respond = (status, location) => async () => ({ status, headers: new Map(location ? [["location", location]] : []) });
  const url = `https://${DOMAIN}/graph/public`;
  assert.equal((await checkSignedOutAnswer({ fetch: respond(200), url })).ok, true);
  assert.equal((await checkSignedOutAnswer({ fetch: respond(404), url })).ok, true, "the app's own 404 is still not Access");
  assert.equal((await checkSignedOutAnswer({ fetch: respond(301, `https://${DOMAIN}/graph/public/`), url })).ok, true);
  assert.equal((await checkSignedOutAnswer({ fetch: respond(302, ACCESS_LOGIN), url })).ok, false);
  const otherTeam = ACCESS_LOGIN.replace(TEAM, "someone-else");
  assert.equal((await checkSignedOutAnswer({ fetch: respond(302, otherTeam), url })).ok, false, "any team's login fails a public path");
  for (const status of [401, 403]) {
    const r = await checkSignedOutAnswer({ fetch: respond(status), url });
    assert.equal(r.ok, false);
    assert.match(r.reason, new RegExp(`bare ${status} — Access or Cloudflare's bot protection`));
  }
  assert.equal((await checkSignedOutAnswer({ fetch: async () => { throw new Error("ENOTFOUND"); }, url })).ok, false);
});

test("isBypassEveryone and appPolicies read policies inline or by id", () => {
  const everyone = { id: "p1", decision: "bypass", include: [{ everyone: {} }] };
  assert.equal(isBypassEveryone(everyone), true);
  assert.equal(isBypassEveryone({ ...everyone, decision: "allow" }), false);
  assert.equal(isBypassEveryone({ ...everyone, include: [{ email: { email: "a@b.c" } }] }), false);
  assert.equal(isBypassEveryone({ ...everyone, require: [{ country: { country_code: "IN" } }] }), false);
  assert.equal(isBypassEveryone({ ...everyone, exclude: [{ email: { email: "a@b.c" } }] }), false);
  assert.deepEqual(appPolicies({ policies: [{ id: "p1", precedence: 1 }] }, [everyone]), [everyone]);
  assert.deepEqual(appPolicies({ policies: [{ ...everyone, precedence: 1 }] }, []), [{ ...everyone, precedence: 1 }]);
  assert.deepEqual(appPolicies({}, [everyone]), []);
});

test("workerRoute is the one pattern <domain><prefix>*, which matches /<prefix>?<query>", () => {
  assert.equal(workerRoute("example.com", "/graph"), "example.com/graph*");
  assert.equal(workerRoute("example.com", "/work/election-atlas"), "example.com/work/election-atlas*");
  assert.deepEqual(accessDestinations("example.com", "/graph"), ["example.com/graph", "example.com/graph/*"], "Access is not widened with it");
});

/** A fetch answering each probe path (pathname + search) from `answers`; the origin marks its answers. */
function stubSite(answers) {
  return async (url) => {
    const u = new URL(url);
    const a = answers[`${u.pathname}${u.search}`] ?? { status: 404, origin: true };
    const headers = new Map([...(a.location ? [["location", a.location]] : []), ...(a.origin ? [["x-github-request-id", "X"]] : [])]);
    return { status: a.status, headers };
  };
}

test("checkRoutes: /<prefix>?<query> must answer like /<prefix> (or redirect into it), the sibling from the origin", async () => {
  const run = (answers) => checkRoutes({ fetch: stubSite(answers), domain: "example.com", prefix: "/graph" });
  const app = { "/graph": { status: 200 }, "/graphportfolio-app-verify": { status: 404, origin: true } };

  const served = await run({ ...app, "/graph?portfolio-app-verify=1": { status: 200 } });
  assert.equal(served.ok, true, JSON.stringify(served.results));
  assert.deepEqual(served.results.map((r) => r.url), [
    "https://example.com/graph?portfolio-app-verify=1", "https://example.com/graphportfolio-app-verify",
  ]);

  const redirected = await run({ ...app, "/graph?portfolio-app-verify=1": { status: 308, location: "/graph/?portfolio-app-verify=1" } });
  assert.equal(redirected.ok, true);
  assert.match(redirected.results[0].reason, /308 → \/graph\/, inside \/graph/);

  const missed = await run(app);
  assert.equal(missed.ok, false);
  assert.match(missed.results[0].reason, /from the origin .* the Worker route misses \/graph\?<query>/);

  const elsewhere = await run({ ...app, "/graph?portfolio-app-verify=1": { status: 308, location: "/graphs/" } });
  assert.equal(elsewhere.ok, false, "a redirect outside the prefix is not the app's answer");

  const caught = await run({ ...app, "/graph?portfolio-app-verify=1": { status: 200 }, "/graphportfolio-app-verify": { status: 200 } });
  assert.equal(caught.ok, false);
  assert.match(caught.results[1].reason, /serves a path outside \/graph instead of passing it to the origin with fetch\(req\)/);

  const unrouted = await run({ "/graph": { status: 404, origin: true } });
  assert.equal(unrouted.ok, false);

  const login = { status: 302, location: "https://team.cloudflareaccess.com/cdn-cgi/access/login/example.com" };
  const behindAccess = await run({ ...app, "/graph": login, "/graph?portfolio-app-verify=1": login });
  assert.equal(behindAccess.ok, false, "the same Access redirect on both is no proof the route matches the query");
  assert.match(behindAccess.results[0].reason, /Cloudflare Access answered .* cannot be checked signed out/);
});

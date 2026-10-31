import { test } from "node:test";
import assert from "node:assert/strict";
import { accessDestinations, normalizePrefix } from "../skills/portfolio-app/scripts/lib/prefix.mjs";
import { checkAccessRedirect, verifyPath } from "../skills/portfolio-app/scripts/lib/verify.mjs";
import { D1_PLACEHOLDER, d1Target, parseJsonc, replacePlaceholder } from "../skills/portfolio-app/scripts/lib/wrangler-config.mjs";
import { parseConfig, repoFromRemote } from "../skills/portfolio-app/scripts/lib/local.mjs";
import { ACCESS_LOGIN, DOMAIN, TEAM, WRANGLER_WITH_PLACEHOLDER, fakeFetch } from "./helpers.mjs";

test("normalizePrefix accepts sub-app prefixes and normalizes the slashes", () => {
  assert.equal(normalizePrefix("/quiz"), "/quiz");
  assert.equal(normalizePrefix("quiz/"), "/quiz");
  assert.equal(normalizePrefix("/chat-archives"), "/chat-archives");
  assert.equal(normalizePrefix("/tools/graph_notes"), "/tools/graph_notes");
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

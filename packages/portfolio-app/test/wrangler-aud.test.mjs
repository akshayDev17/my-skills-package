// --wrangler <path> (the D1 step's Worker config), the two D1 placeholders, and the AUD tags
// printed for the Access apps a run creates or finds. Everything runs the real code against
// the same Cloudflare/process stand-ins as the other tests; nothing here touches a real account.

import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { planBootstrap } from "../skills/portfolio-app/scripts/lib/bootstrap.mjs";
import { d1Target, replacePlaceholder, resolveWranglerPath } from "../skills/portfolio-app/scripts/lib/wrangler-config.mjs";
import { main } from "../skills/portfolio-app/scripts/portfolio-app.mjs";
import {
  ACCOUNT, DEPLOY_TOKEN, DOMAIN, SETUP_TOKEN,
  config, fakeFetch, fakeRun, freshPublicWorld, freshWorld,
} from "./helpers.mjs";

const ZERO = "00000000-0000-0000-0000-000000000000";
const REPO = "owner/chief-of-staff";
const PREFIX = "/chief-of-staff";

// A Worker config shaped like chief-of-staff's shell/wrangler.jsonc: comments, a zero-UUID
// placeholder for the D1 id, and another binding that legitimately holds an all-zero id.
const SHELL_WRANGLER = `{
\t"name": "chief-of-staff-shell",
\t// The D1 id below is a placeholder until bootstrap writes the real one (${ZERO}).
\t"d1_databases": [
\t\t{
\t\t\t"binding": "DB",
\t\t\t"database_name": "chief-of-staff",
\t\t\t/* CI refuses to deploy while "${ZERO}" is here. */
\t\t\t"database_id": "${ZERO}"
\t\t}
\t],
\t"kv_namespaces": [{ "binding": "CACHE", "id": "${ZERO}" }],
}
`;

/** A clone with `text` at `relPath` (any nested directory) and nothing at backend/wrangler.jsonc. */
function cloneWith(relPath, text) {
  const dir = mkdtempSync(join(tmpdir(), "portfolio-app-wrangler-"));
  const file = join(dir, relPath);
  mkdirSync(join(file, ".."), { recursive: true });
  writeFileSync(file, text);
  return { dir, file };
}

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

const worldFor = (repo = REPO) => {
  const world = freshWorld();
  world.local.remote = `git@github.com:${repo}.git`;
  return world;
};
const run = (world, clone, extra = []) =>
  cli(world, ["bootstrap", "--repo", REPO, "--path", PREFIX, "--clone", clone, "--config", writeConfig(), ...extra]);

const changedLines = (before, after) => {
  const a = before.split("\n");
  const b = after.split("\n");
  assert.equal(a.length, b.length, "no line was added or removed");
  return b.map((line, i) => (line === a[i] ? null : { before: a[i], after: line })).filter(Boolean);
};

// --- --wrangler ----------------------------------------------------------------------

test("--wrangler: apply writes the database id into a nested file, as a one-line diff that keeps every comment", async () => {
  const world = worldFor();
  const { dir, file } = cloneWith("shell/wrangler.jsonc", SHELL_WRANGLER);
  const r = await run(world, dir, ["--wrangler", "shell/wrangler.jsonc", "--apply"]);
  assert.equal(r.code, 0, r.err);

  const after = readFileSync(file, "utf8");
  const [only, ...rest] = changedLines(SHELL_WRANGLER, after);
  assert.deepEqual(rest, [], "exactly one line changed");
  assert.equal(only.before, `\t\t\t"database_id": "${ZERO}"`);
  assert.equal(only.after, `\t\t\t"database_id": "${world.site.d1[0].uuid}"`);
  assert.ok(after.includes(`// The D1 id below is a placeholder until bootstrap writes the real one (${ZERO}).`), "line comment kept");
  assert.ok(after.includes(`/* CI refuses to deploy while "${ZERO}" is here. */`), "block comment kept");
  assert.ok(after.includes(`"kv_namespaces": [{ "binding": "CACHE", "id": "${ZERO}" }]`), "an all-zero id outside database_id is not touched");

  assert.match(r.out, new RegExp(`\\+ write database_id into ${file.replaceAll(".", "\\.")} \\(replaces ${ZERO}\\)`), "the plan names the file given");
  assert.match(r.out, new RegExp(`\\+ wrote database_id into ${file.replaceAll(".", "\\.")} — commit`), "apply names the file given");
  assert.ok(!r.out.includes("backend/wrangler.jsonc"), "no message names the default file");
  assert.ok(!r.out.includes(SETUP_TOKEN) && !r.out.includes(DEPLOY_TOKEN));
});

test("--wrangler: plan only reads the file and writes nothing; without the flag the same clone still stops on backend/wrangler.jsonc", async () => {
  const world = worldFor();
  const { dir, file } = cloneWith("shell/wrangler.jsonc", SHELL_WRANGLER);
  const planned = await run(world, dir, ["--wrangler", "shell/wrangler.jsonc"]);
  assert.equal(planned.code, 0, planned.err);
  assert.equal(readFileSync(file, "utf8"), SHELL_WRANGLER);
  assert.deepEqual(planned.http.calls.filter((c) => c.method !== "GET"), []);

  const plain = await run(worldFor(), dir);
  assert.equal(plain.code, 2);
  assert.match(plain.out, /backend\/wrangler\.jsonc does not exist/);
});

test("--wrangler: a missing file is a conflict that names it", async () => {
  const { dir } = cloneWith("shell/wrangler.jsonc", SHELL_WRANGLER);
  const r = await run(worldFor(), dir, ["--wrangler", "shell/missing.jsonc"]);
  assert.equal(r.code, 2);
  assert.match(r.out, /shell\/missing\.jsonc does not exist/);
});

test("--wrangler: a path that leaves the clone is refused before any call", async () => {
  const { dir } = cloneWith("shell/wrangler.jsonc", SHELL_WRANGLER);
  for (const bad of ["../elsewhere/wrangler.jsonc", "shell/../../elsewhere/wrangler.jsonc", join(dir, "shell", "wrangler.jsonc"), "/etc/hosts", ".", ""]) {
    const r = await run(worldFor(), dir, ["--wrangler", bad, "--apply"]);
    assert.equal(r.code, 2, `"${bad}"`);
    assert.match(r.err, /--wrangler .* must be a file path inside the clone/, `"${bad}"`);
    assert.deepEqual(r.http.calls, [], `"${bad}": no Cloudflare call`);
    assert.deepEqual(r.proc.calls, [], `"${bad}": no process call`);
  }
  assert.throws(() => resolveWranglerPath(dir, "../x"), /inside the clone/);
  assert.equal(resolveWranglerPath(dir, "shell/./wrangler.jsonc"), join(dir, "shell", "wrangler.jsonc"));
});

test("--wrangler: the library refuses an escaping path too, as a conflict rather than a read outside the clone", async () => {
  const { dir } = cloneWith("shell/wrangler.jsonc", SHELL_WRANGLER);
  const world = worldFor();
  const http = fakeFetch(world.site);
  const proc = fakeRun(world.local);
  const cf = { fetch: http.fetch, token: SETUP_TOKEN, accountId: ACCOUNT };
  const actions = await planBootstrap({ cf, run: proc.run, fetch: http.fetch },
    { repo: REPO, prefix: PREFIX, cloneDir: dir, wrangler: "../outside.jsonc", config });
  const d1 = actions.filter((a) => a.step === "d1");
  assert.deepEqual(d1.map((a) => a.state), ["conflict"]);
  assert.match(d1[0].summary, /must be a file path inside the clone/);
});

test("--wrangler: an already-set id is reported against the file given", async () => {
  const text = SHELL_WRANGLER.replace(`"database_id": "${ZERO}"`, `"database_id": "11111111-2222-4333-8444-555555555555"`);
  const { dir } = cloneWith("shell/wrangler.jsonc", text);
  const r = await run(worldFor(), dir, ["--wrangler", "shell/wrangler.jsonc"]);
  assert.equal(r.code, 0, r.err);
  assert.match(r.out, /\nD1\n {2}✓ shell\/wrangler\.jsonc already carries the database_id for chief-of-staff\n/);
});

test("--wrangler: a public app reads its R2 bucket and Worker name from the same file", async () => {
  const world = freshPublicWorld();
  world.local.remote = "git@github.com:owner/chief-of-staff.git";
  const text = `{
\t"name": "chief-of-staff-shell",
\t"d1_databases": [{ "binding": "DB", "database_name": "chief-of-staff", "database_id": "${ZERO}" }],
\t"r2_buckets": [{ "binding": "FILES", "bucket_name": "chief-of-staff-files" }]
}
`;
  const { dir } = cloneWith("shell/wrangler.jsonc", text);
  const r = await run(world, dir, ["--public", "--wrangler", "shell/wrangler.jsonc"]);
  assert.equal(r.code, 0, r.err);
  assert.match(r.out, /\+ create R2 bucket "chief-of-staff-files"/);
  assert.match(r.out, /Worker "chief-of-staff-shell" is not deployed yet/);
  assert.ok(!r.out.includes("backend/wrangler.jsonc"));
});

// --- the two placeholders --------------------------------------------------------------

test("both placeholders are replaced in backend/wrangler.jsonc, and in --wrangler's file", async () => {
  for (const [placeholder, flag] of [[ZERO, []], ["REPLACE_AFTER_d1_create", ["--wrangler", "shell/wrangler.jsonc"]]]) {
    const world = worldFor();
    const text = SHELL_WRANGLER.replaceAll(ZERO, placeholder).replace(`"id": "${placeholder}"`, `"id": "${ZERO}"`);
    const rel = flag.length ? "shell/wrangler.jsonc" : "backend/wrangler.jsonc";
    const { dir, file } = cloneWith(rel, text);
    const r = await run(world, dir, [...flag, "--apply"]);
    assert.equal(r.code, 0, r.err);
    assert.match(r.out, new RegExp(`\\(replaces ${placeholder}\\)`));
    const lines = changedLines(text, readFileSync(file, "utf8"));
    assert.equal(lines.length, 1, placeholder);
    assert.equal(lines[0].after, `\t\t\t"database_id": "${world.site.d1[0].uuid}"`);
  }
});

test("d1Target: entries holding either placeholder for one database are one target; several databases stay ambiguous", () => {
  const entry = (name, id) => ({ binding: "DB", database_name: name, database_id: id });
  assert.deepEqual(d1Target({ d1_databases: [entry("g", ZERO)] }, undefined), { state: "missing", name: "g", placeholders: [ZERO] });
  assert.deepEqual(
    d1Target({ d1_databases: [entry("g", ZERO), entry("g", "REPLACE_AFTER_d1_create")] }, undefined),
    { state: "missing", name: "g", placeholders: [ZERO, "REPLACE_AFTER_d1_create"] },
  );
  const ambiguous = d1Target({ d1_databases: [entry("a", ZERO), entry("b", "REPLACE_AFTER_d1_create")] }, undefined);
  assert.equal(ambiguous.state, "conflict");
  assert.match(ambiguous.reason, /several databases \(a, b\)/);
  assert.equal(d1Target({ d1_databases: [{ binding: "DB", database_id: ZERO }] }, undefined).state, "conflict", "no database_name");
  assert.equal(d1Target({ d1_databases: [entry("g", "real-id")] }, undefined).state, "present");
});

test("two placeholder entries naming different databases are a conflict in the plan, whichever placeholder each holds", async () => {
  const text = `{ "name": "w", "d1_databases": [
  { "binding": "A", "database_name": "a", "database_id": "${ZERO}" },
  { "binding": "B", "database_name": "b", "database_id": "REPLACE_AFTER_d1_create" } ] }`;
  const { dir, file } = cloneWith("shell/wrangler.jsonc", text);
  const r = await run(worldFor(), dir, ["--wrangler", "shell/wrangler.jsonc", "--apply"]);
  assert.equal(r.code, 2);
  assert.match(r.out, /shell\/wrangler\.jsonc: placeholder entries name several databases \(a, b\)/);
  assert.equal(readFileSync(file, "utf8"), text, "nothing written");
  assert.deepEqual(r.http.calls.filter((c) => c.method !== "GET"), []);
});

test("replacePlaceholder only touches a database_id value: comments, other keys and other strings stay", () => {
  const text = `{ // "database_id": "${ZERO}"
  "x": "${ZERO}", "preview_database_id": "${ZERO}", "d1_databases": [{ "database_id" /* c */ : "${ZERO}" }] }`;
  assert.equal(replacePlaceholder(text, "real"), text.replace(`"database_id" /* c */ : "${ZERO}"`, `"database_id" /* c */ : "real"`));
});

test("only the top-level d1_databases entry is replaced; an all-zero id in env.preview.d1_databases stays", async () => {
  const world = worldFor();
  const text = `{
\t"name": "chief-of-staff-shell",
\t"d1_databases": [
\t\t{ "binding": "DB", "database_name": "chief-of-staff", "database_id": "${ZERO}" }
\t],
\t"env": {
\t\t"preview": {
\t\t\t"d1_databases": [
\t\t\t\t{ "binding": "DB", "database_name": "chief-of-staff-preview", "database_id": "${ZERO}" }
\t\t\t]
\t\t}
\t}
}
`;
  const { dir, file } = cloneWith("shell/wrangler.jsonc", text);
  const r = await run(world, dir, ["--wrangler", "shell/wrangler.jsonc", "--apply"]);
  assert.equal(r.code, 0, r.err);
  const lines = changedLines(text, readFileSync(file, "utf8"));
  assert.equal(lines.length, 1);
  assert.equal(lines[0].after, `\t\t{ "binding": "DB", "database_name": "chief-of-staff", "database_id": "${world.site.d1[0].uuid}" }`);
  assert.deepEqual(world.site.d1.map((d) => d.name), ["chief-of-staff"]);
});

// --- AUD tags ---------------------------------------------------------------------------

test("AUD: plan only prints the tag of an Access app that exists, and nothing for one not yet created", async () => {
  const world = worldFor("owner/graph-notes");
  const { dir } = cloneWith("backend/wrangler.jsonc", SHELL_WRANGLER);

  const fresh = await cli(world, ["bootstrap", "--repo", "owner/graph-notes", "--path", "/graph", "--clone", dir, "--config", writeConfig()]);
  assert.equal(fresh.code, 0, fresh.err);
  assert.doesNotMatch(fresh.out, /AUD/, "an app that does not exist has no tag to show");

  world.site.policies.push({ id: "pol-1", name: "portfolio-app sign-in", reusable: true, decision: "allow", include: [{ email: { email: "owner@example.com" } }] });
  world.site.apps.push({ id: "app-1", aud: "f".repeat(64), name: "graph-notes (/graph)", type: "self_hosted",
    destinations: [`${DOMAIN}/graph`, `${DOMAIN}/graph/*`].map((uri) => ({ type: "public", uri })), policies: [{ id: "pol-1", precedence: 1 }] });
  const found = await cli(world, ["bootstrap", "--repo", "owner/graph-notes", "--path", "/graph", "--clone", dir, "--config", writeConfig()]);
  assert.equal(found.code, 0, found.err);
  assert.match(found.out, new RegExp(`✓ Access app "graph-notes \\(/graph\\)" guards ${DOMAIN}/graph \\+ ${DOMAIN}/graph/\\* \\(AUD ${"f".repeat(64)}\\)`));
});

test("AUD: apply prints a created app's tag as Cloudflare returned it, and a re-run prints the found one; no token is printed", async () => {
  const world = worldFor();
  const { dir } = cloneWith("shell/wrangler.jsonc", SHELL_WRANGLER);
  const first = await run(world, dir, ["--wrangler", "shell/wrangler.jsonc", "--apply"]);
  assert.equal(first.code, 0, first.err);
  const minted = world.site.apps[0].aud;
  assert.ok(minted, "the stand-in mints a tag on create, as Cloudflare does");
  assert.match(first.out, new RegExp(`\n {2}AUD tag of Access app "chief-of-staff \\(/chief-of-staff\\)" \\(created\\): ${minted}\n`));

  const again = await run(world, dir, ["--wrangler", "shell/wrangler.jsonc", "--apply"]);
  assert.equal(again.code, 0, again.err);
  assert.match(again.out, /nothing to create — already bootstrapped/);
  assert.match(again.out, new RegExp(`\\(AUD ${minted}\\)`), "the plan shows it");
  assert.match(again.out, new RegExp(`AUD tag of Access app "chief-of-staff \\(/chief-of-staff\\)" \\(found\\): ${minted}`), "apply shows it too");
  for (const r of [first, again]) assert.ok(!r.out.includes(SETUP_TOKEN) && !r.out.includes(DEPLOY_TOKEN));
});

test("AUD: a mixed app prints the sign-in app's tag and the Bypass app's", async () => {
  const world = worldFor();
  const { dir } = cloneWith("shell/wrangler.jsonc", SHELL_WRANGLER);
  const r = await run(world, dir, ["--wrangler", "shell/wrangler.jsonc", "--public-path", `${PREFIX}/public`, "--apply"]);
  assert.equal(r.code, 0, r.err);
  const [allow, bypass] = world.site.apps;
  assert.match(r.out, new RegExp(`AUD tag of Access app "chief-of-staff \\(/chief-of-staff\\)" \\(created\\): ${allow.aud}`));
  assert.match(r.out, new RegExp(`AUD tag of Access app "chief-of-staff \\(public\\)" \\(created\\): ${bypass.aud}`));
  assert.notEqual(allow.aud, bypass.aud);
});

test("AUD: an answer without a tag says so rather than inventing one", async () => {
  const world = worldFor();
  const { dir } = cloneWith("shell/wrangler.jsonc", SHELL_WRANGLER);
  const http = fakeFetch(world.site);
  const noAud = async (url, init) => {
    const res = await http.fetch(url, init);
    if (init?.method !== "POST" || !url.endsWith("/access/apps")) return res;
    const body = await res.json();
    delete body.result.aud;
    return { ...res, json: async () => body };
  };
  const proc = fakeRun(world.local);
  const out = [];
  const code = await main(["bootstrap", "--repo", REPO, "--path", PREFIX, "--clone", dir, "--config", writeConfig(), "--wrangler", "shell/wrangler.jsonc", "--apply"],
    { fetch: noAud, run: proc.run, log: (l) => out.push(l), error: (l) => out.push(l) });
  assert.equal(code, 0, out.join("\n"));
  assert.match(out.join("\n"), /AUD tag of Access app "chief-of-staff \(\/chief-of-staff\)" \(created\): not in Cloudflare's answer; read it from the app in the Zero Trust dashboard/);
});

// --- --access-vars ------------------------------------------------------------------------

const VARS = ["--wrangler", "shell/wrangler.jsonc", "--access-vars"];
const varCalls = (r) => r.proc.calls.filter((c) => c.cmd === "gh" && c.args[0] === "variable" && c.args[1] === "set");

test("--access-vars: off by default, nothing about variables is planned, read or set", async () => {
  const world = worldFor();
  const { dir } = cloneWith("shell/wrangler.jsonc", SHELL_WRANGLER);
  const r = await run(world, dir, ["--wrangler", "shell/wrangler.jsonc", "--apply"]);
  assert.equal(r.code, 0, r.err);
  assert.doesNotMatch(r.out, /variable/);
  assert.deepEqual(r.proc.calls.filter((c) => c.args[0] === "variable"), []);
  assert.equal(world.local.github.variables, undefined);
});

test("--access-vars: the plan shows both lines before --apply, sets nothing, and an AUD not yet minted is not invented", async () => {
  const world = worldFor();
  const { dir } = cloneWith("shell/wrangler.jsonc", SHELL_WRANGLER);
  const r = await run(world, dir, VARS);
  assert.equal(r.code, 0, r.err);
  assert.match(r.out, /\+ set variable ACCESS_AUD to the AUD tag of the sign-in Access app "chief-of-staff \(\/chief-of-staff\)" \(known once the app is created\)/);
  assert.match(r.out, new RegExp(`\\+ set variable ACCESS_TEAM_DOMAIN to test-team\\.cloudflareaccess\\.com`));
  assert.deepEqual(varCalls(r), []);
  assert.deepEqual(r.http.calls.filter((c) => c.method !== "GET"), []);
});

test("--access-vars: apply sets ACCESS_AUD to the created sign-in app's tag and ACCESS_TEAM_DOMAIN from the config; a re-run changes nothing", async () => {
  const world = worldFor();
  const { dir } = cloneWith("shell/wrangler.jsonc", SHELL_WRANGLER);
  const first = await run(world, dir, [...VARS, "--apply"]);
  assert.equal(first.code, 0, first.err);
  const aud = world.site.apps[0].aud;
  assert.deepEqual(world.local.github.variables, { ACCESS_AUD: aud, ACCESS_TEAM_DOMAIN: "test-team.cloudflareaccess.com" });
  assert.match(first.out, new RegExp(`\\+ set variable ACCESS_AUD on ${REPO}/production to ${aud}`));
  const secretsBefore = [...world.local.github.secrets];

  const again = await run(world, dir, [...VARS, "--apply"]);
  assert.equal(again.code, 0, again.err);
  assert.match(again.out, new RegExp(`✓ variable ACCESS_AUD is already ${aud}`));
  assert.match(again.out, /✓ variable ACCESS_TEAM_DOMAIN is already test-team\.cloudflareaccess\.com/);
  assert.match(again.out, /nothing to create — already bootstrapped/);
  assert.deepEqual(varCalls(again), [], "equal values are skipped, not re-set");
  assert.deepEqual(world.local.github.secrets, secretsBefore);
  for (const r of [first, again]) assert.ok(!r.out.includes(SETUP_TOKEN) && !r.out.includes(DEPLOY_TOKEN));
});

test("--access-vars: a variable holding another value is updated, and the plan says what it is now", async () => {
  const world = worldFor();
  const { dir } = cloneWith("shell/wrangler.jsonc", SHELL_WRANGLER);
  await run(world, dir, [...VARS, "--apply"]);
  world.local.github.variables.ACCESS_TEAM_DOMAIN = "old.cloudflareaccess.com";
  const aud = world.site.apps[0].aud;
  const r = await run(world, dir, [...VARS, "--apply"]);
  assert.match(r.out, /\+ set variable ACCESS_TEAM_DOMAIN to test-team\.cloudflareaccess\.com \(now old\.cloudflareaccess\.com\)/);
  assert.deepEqual(varCalls(r).map((c) => c.args[2]), ["ACCESS_TEAM_DOMAIN"], "only the one that differs");
  assert.deepEqual(world.local.github.variables, { ACCESS_AUD: aud, ACCESS_TEAM_DOMAIN: "test-team.cloudflareaccess.com" });
});

test("--access-vars: on an existing app the plan shows the real AUD; the sign-in app's, never the Bypass app's", async () => {
  const world = worldFor();
  const { dir } = cloneWith("shell/wrangler.jsonc", SHELL_WRANGLER);
  const mixed = [...VARS, "--public-path", `${PREFIX}/public`];
  await run(world, dir, [...mixed, "--apply"]);
  const [allow, bypass] = world.site.apps;
  world.local.github.variables = {};
  const planOnly = await run(world, dir, mixed);
  assert.match(planOnly.out, new RegExp(`\\+ set variable ACCESS_AUD to ${allow.aud}\n`));
  assert.ok(!planOnly.out.includes(`set variable ACCESS_AUD to ${bypass.aud}`));
  assert.deepEqual(varCalls(planOnly), []);
  await run(world, dir, [...mixed, "--apply"]);
  assert.equal(world.local.github.variables.ACCESS_AUD, allow.aud);
});

test("--access-vars: an existing sign-in app whose answer has no AUD is a conflict, not a guess", async () => {
  const world = worldFor();
  const { dir } = cloneWith("shell/wrangler.jsonc", SHELL_WRANGLER);
  await run(world, dir, ["--wrangler", "shell/wrangler.jsonc", "--apply"]);
  delete world.site.apps[0].aud;
  const r = await run(world, dir, [...VARS, "--apply"]);
  assert.equal(r.code, 2);
  assert.match(r.out, /✗ Access app "chief-of-staff \(\/chief-of-staff\)" has no AUD tag in Cloudflare's answer, so ACCESS_AUD cannot be set/);
  assert.deepEqual(varCalls(r), []);
});

test("--access-vars with --public is a usage error: there is no Access app", async () => {
  const { dir } = cloneWith("shell/wrangler.jsonc", SHELL_WRANGLER);
  const r = await run(worldFor(), dir, [...VARS, "--public"]);
  assert.equal(r.code, 2);
  assert.match(r.err, /--access-vars needs an Access app/);
  assert.deepEqual(r.http.calls, []);
});

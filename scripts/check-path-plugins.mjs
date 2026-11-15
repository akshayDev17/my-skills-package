#!/usr/bin/env node
// Checks the Claude-only plugins under plugins/, which the Claude catalogue delivers from
// a path inside this repo instead of from npm.
//
// packages/ holds the npm packages every host installs; their checks are
// check-manifests.mjs and check-marketplace-pins.mjs. A plugin under plugins/ ships no
// npm package, so nothing else reads it. This fails when one of them:
// - is missing from .claude-plugin/marketplace.json, or listed at another path, or listed
//   in the Codex/Antigravity catalogue (which cannot install a Claude plugin from a path);
// - has a manifest whose name is not its directory, or a skill whose frontmatter does not
//   name it;
// - has an .mcp.json that is not JSON or points outside the plugin (an absolute path works
//   on one machine only);
// - bundles a copy of code from elsewhere (SERVER_SOURCE) that differs from the copy that
//   file records, or whose README does not name the commit it was copied from.
//
// Usage: node scripts/check-path-plugins.mjs
import { createHash } from "node:crypto";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join, relative, resolve } from "node:path";

const ROOT = resolve(import.meta.dirname, "..");
const CLAUDE = ".claude-plugin/marketplace.json";
const AGENTS = ".agents/plugins/marketplace.json";
// Made by running the copy (uv, Python), never part of it.
const BUILT = new Set([".venv", "__pycache__", ".pytest_cache"]);

const readJson = (path) => JSON.parse(readFileSync(join(ROOT, path), "utf8"));
const claude = readJson(CLAUDE).plugins ?? [];
const agents = readJson(AGENTS).plugins ?? [];

let failures = 0;
const fail = (message) => {
  failures += 1;
  console.error(`  ✗ ${message}`);
};

function files(dir, out = []) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (BUILT.has(entry.name)) continue;
    const path = join(dir, entry.name);
    if (entry.isDirectory()) files(path, out);
    else if (entry.isFile()) out.push(path);
  }
  return out;
}

function frontmatterName(text) {
  const block = /^---\r?\n([\s\S]*?)\r?\n---/.exec(text);
  return block && /^name:\s*(.+)$/m.exec(block[1])?.[1].trim();
}

function checkCopy(dir, label) {
  const lines = readFileSync(join(dir, "SERVER_SOURCE"), "utf8").split("\n").filter((l) => l && !l.startsWith("#"));
  const commit = /^commit ([0-9a-f]{40})$/.exec(lines.shift() ?? "")?.[1];
  if (!commit) return fail(`${label}/SERVER_SOURCE has no "commit <sha>" line`);
  const recorded = new Map(lines.map((l) => l.split(/ {2}/)).map(([sum, file]) => [file, sum]));
  const server = join(dir, "server");
  const onDisk = existsSync(server) ? files(server).map((f) => relative(server, f).split("\\").join("/")) : [];
  for (const file of onDisk) {
    const sum = createHash("sha256").update(readFileSync(join(server, file))).digest("hex");
    if (!recorded.has(file)) fail(`${label}/server/${file} is not in SERVER_SOURCE: the copy was edited by hand`);
    else if (recorded.get(file) !== sum) fail(`${label}/server/${file} differs from the copy of ${commit.slice(0, 7)}`);
  }
  for (const file of recorded.keys()) {
    if (!onDisk.includes(file)) fail(`${label}/server/${file} is in SERVER_SOURCE but missing`);
  }
  const readme = join(dir, "README.md");
  if (!existsSync(readme) || !readFileSync(readme, "utf8").includes(commit)) {
    fail(`${label}/README.md does not name the commit the server was copied from (${commit})`);
  }
}

const dirs = existsSync(join(ROOT, "plugins"))
  ? readdirSync(join(ROOT, "plugins"), { withFileTypes: true }).filter((e) => e.isDirectory()).map((e) => e.name).sort()
  : [];

for (const name of dirs) {
  const label = `plugins/${name}`;
  const dir = join(ROOT, label);
  const before = failures;

  const manifest = join(dir, ".claude-plugin/plugin.json");
  if (!existsSync(manifest)) {
    fail(`${label} has no .claude-plugin/plugin.json`);
    continue;
  }
  const plugin = JSON.parse(readFileSync(manifest, "utf8"));
  if (plugin.name !== name) fail(`${label}/.claude-plugin/plugin.json is named "${plugin.name}"`);
  if (!plugin.description) fail(`${label}/.claude-plugin/plugin.json has no description`);

  const entry = claude.find((p) => p.name === name);
  if (!entry) fail(`${CLAUDE} does not list ${name} — Claude Code cannot install it`);
  else if (entry.source !== `./${label}`) fail(`${CLAUDE} delivers ${name} from ${JSON.stringify(entry.source)}, not "./${label}"`);
  if (agents.some((p) => p.name === name)) fail(`${AGENTS} lists ${name}, a Claude-only plugin`);

  const skills = join(dir, "skills");
  for (const skill of existsSync(skills) ? readdirSync(skills, { withFileTypes: true }).filter((e) => e.isDirectory()) : []) {
    const file = join(skills, skill.name, "SKILL.md");
    const declared = existsSync(file) ? frontmatterName(readFileSync(file, "utf8")) : null;
    if (declared !== skill.name) fail(`${label}/skills/${skill.name}/SKILL.md declares name: ${declared ?? "(none)"}`);
  }

  const mcp = join(dir, ".mcp.json");
  if (existsSync(mcp)) {
    let servers = {};
    try {
      servers = JSON.parse(readFileSync(mcp, "utf8")).mcpServers ?? {};
    } catch (err) {
      fail(`${label}/.mcp.json: ${err.message}`);
    }
    for (const [server, spec] of Object.entries(servers)) {
      for (const arg of [spec.command, ...(spec.args ?? [])]) {
        if (typeof arg === "string" && /^(\/|~|[A-Za-z]:\\)/.test(arg)) {
          fail(`${label}/.mcp.json: ${server} names ${arg}; use \${CLAUDE_PLUGIN_ROOT} for the plugin's own files`);
        }
      }
    }
  }

  if (existsSync(join(dir, "SERVER_SOURCE"))) checkCopy(dir, label);
  if (failures === before) console.log(`✓ ${label}@${plugin.version}`);
}

for (const entry of claude) {
  if (typeof entry.source === "string" && !dirs.some((name) => entry.source === `./plugins/${name}`)) {
    fail(`${CLAUDE}: ${entry.name} is delivered from ${entry.source}, which is not a plugin under plugins/`);
  }
}

if (failures > 0) {
  console.error(`\n${failures} path-plugin problem(s).`);
  process.exit(1);
}

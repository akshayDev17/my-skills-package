#!/usr/bin/env node
// Fails when one package's four host-facing manifests disagree with each other.
//
// Each host reads a different file: DSH reads package.json + cordis.patch.yml,
// Codex/Gemini read plugin.json, Claude reads .claude-plugin/plugin.json. A
// version or name that drifts in one of them ships a package whose advertised
// identity depends on which host asked — invisible in every test that only reads
// package.json. scripts/bump-package.mjs writes all three versions together, so
// drift here means something else wrote a manifest by hand.
//
// Usage: node scripts/check-manifests.mjs

import { readdir, readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join, resolve } from "node:path";

const ROOT = resolve(import.meta.dirname, "..");

const readJson = async (path) => JSON.parse(await readFile(path, "utf8"));
// The npm name may carry a scope; the plugin manifests carry the bare skill name.
const unscoped = (name) => name?.replace(/^@[^/]+\//, "");

async function packageDirs() {
  const entries = await readdir(join(ROOT, "packages"), { withFileTypes: true });
  return entries
    .filter((e) => e.isDirectory())
    .map((e) => join(ROOT, "packages", e.name))
    .sort();
}

function frontmatter(text) {
  const match = /^---\r?\n([\s\S]*?)\r?\n---/.exec(text);
  if (!match) return null;
  const fields = {};
  for (const line of match[1].split(/\r?\n/)) {
    const kv = /^([A-Za-z0-9_-]+):\s*(.*)$/.exec(line);
    if (kv) fields[kv[1]] = kv[2].trim();
  }
  return fields;
}

let failures = 0;

const makeFail = (pkg) => (message) => {
  failures += 1;
  console.error(`  ✗ ${pkg}: ${message}`);
};

for (const dir of await packageDirs()) {
  const pkg = dir.slice(ROOT.length + 1);
  const pkgJson = await readJson(join(dir, "package.json"));
  const before = failures;
  const fail = makeFail(pkg);

  // Both host manifests are optional per package, but whatever exists must agree.
  const manifests = [{ file: "package.json", json: pkgJson }];
  for (const file of ["plugin.json", ".claude-plugin/plugin.json"]) {
    const path = join(dir, file);
    if (existsSync(path)) manifests.push({ file, json: await readJson(path) });
  }

  const versions = new Set(manifests.map((m) => m.json.version));
  const names = new Set(manifests.map((m) => unscoped(m.json.name)));
  if (versions.size > 1) {
    fail(`version drift: ${manifests.map((m) => `${m.file}=${m.json.version}`).join(", ")}`);
  }
  if (names.size > 1) {
    fail(`name drift: ${manifests.map((m) => `${m.file}=${m.json.name}`).join(", ")}`);
  }
  if (!pkgJson.description) fail("package.json has no description");

  // An entry point that resolves only after install is an entry point that is
  // missing from the tarball; check the declared paths against the tree.
  for (const [field, rel] of [
    ["main", pkgJson.main],
    ["dsh.bundle.patch", pkgJson.dsh?.bundle?.patch],
  ]) {
    if (rel && !existsSync(join(dir, rel))) fail(`${field} points at ${rel}, which does not exist`);
  }
  if (!pkgJson.dsh?.bundle?.patch) fail("no dsh.bundle.patch — DSH cannot mount this package");

  // Every skill directory must declare the identity the filesystem provider
  // registers it under; a mismatch silently mounts a skill nobody can invoke.
  const skillsDir = join(dir, "skills");
  if (!existsSync(skillsDir)) {
    fail("no skills/ directory — this package ships no skill");
    continue;
  }
  const skills = (await readdir(skillsDir, { withFileTypes: true })).filter((e) => e.isDirectory());
  if (skills.length === 0) fail("skills/ contains no skill");

  for (const skill of skills) {
    const skillFile = join(skillsDir, skill.name, "SKILL.md");
    if (!existsSync(skillFile)) {
      fail(`skills/${skill.name}/ has no SKILL.md`);
      continue;
    }
    const fields = frontmatter(await readFile(skillFile, "utf8"));
    if (!fields) {
      fail(`skills/${skill.name}/SKILL.md has no frontmatter`);
      continue;
    }
    if (fields.name !== skill.name) {
      fail(`skills/${skill.name}/SKILL.md declares name: ${fields.name ?? "(none)"}`);
    }
    if (!fields.description) fail(`skills/${skill.name}/SKILL.md has no description`);
  }

  if (failures === before) console.log(`✓ ${pkg}@${pkgJson.version}`);
}

if (failures > 0) {
  console.error(`\n${failures} manifest problem(s).`);
  process.exit(1);
}

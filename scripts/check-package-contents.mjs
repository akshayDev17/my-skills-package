#!/usr/bin/env node
// Fails when a package ships less than what a plugin host can see on disk.
//
// The bug this exists for: `files` in package.json is an allowlist, and npm
// silently drops anything the allowlist does not match — a dotfile such as
// `.claude-plugin/plugin.json` most of all. The package then publishes fine and
// installs fine, and is quietly unusable on one of the four hosts. So the rule
// is mechanical rather than a hand-kept list of manifest paths: everything in a
// package directory is something a host may read, therefore everything must be
// in the tarball.
//
// Usage: node scripts/check-package-contents.mjs [--fix-hint]

import { execFileSync } from "node:child_process";
import { readdir } from "node:fs/promises";
import { join, resolve } from "node:path";

const ROOT = resolve(import.meta.dirname, "..");

// Never shipped on purpose. Keep this list short and justify every entry — it is
// the only way past the gate, so an accidental addition here is the thing to
// look at first when a file goes missing from a published tarball.
const ALLOWED_UNSHIPPED = [
  /(^|\/)node_modules\//,
  /(^|\/)test\//,
  /\.test\.mjs$/,
  /(^|\/)\.DS_Store$/,
];

async function walk(dir, base = dir, out = []) {
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) await walk(path, base, out);
    else if (entry.isFile()) out.push(path.slice(base.length + 1).split("\\").join("/"));
  }
  return out;
}

// Ask npm what it would actually publish. `--dry-run` runs the real packlist
// logic (allowlist + dotfile rules + .npmignore) without touching the registry.
function packedFiles(pkgDir) {
  const raw = execFileSync("npm", ["pack", "--dry-run", "--json", "--ignore-scripts"], {
    cwd: pkgDir,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  });
  const [info] = JSON.parse(raw);
  return { packed: new Set(info.files.map((f) => f.path)), size: info.size, version: info.version };
}

const packageDirs = (await readdir(join(ROOT, "packages"), { withFileTypes: true }))
  .filter((e) => e.isDirectory())
  .map((e) => join(ROOT, "packages", e.name))
  .sort();

if (packageDirs.length === 0) {
  console.error("no packages found under packages/");
  process.exit(1);
}

let failures = 0;

for (const pkgDir of packageDirs) {
  const name = pkgDir.slice(ROOT.length + 1);
  const onDisk = await walk(pkgDir);
  const { packed, size, version } = packedFiles(pkgDir);

  const missing = onDisk.filter((f) => !packed.has(f) && !ALLOWED_UNSHIPPED.some((re) => re.test(f)));
  const label = `${name}@${version}`;

  if (missing.length > 0) {
    failures += 1;
    console.error(`\n✗ ${label}: ${missing.length} file(s) on disk are not in the published tarball`);
    for (const f of missing) console.error(`    ${f}`);
    console.error(`  fix: add them to "files" in ${name}/package.json, then re-run`);
  } else {
    const kb = (size / 1024).toFixed(1);
    console.log(`✓ ${label}: ${onDisk.length} files on disk, ${packed.size} packed (${kb} kB)`);
  }
}

if (failures > 0) {
  console.error(`\n${failures} package(s) would publish incomplete.`);
  process.exit(1);
}

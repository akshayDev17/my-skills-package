#!/usr/bin/env node
// Write a version into one package's manifests, preserving their exact formatting.
//   node scripts/bump-package.mjs <package-dir> <version>
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { resolve, join } from "node:path";

const [dir, version] = process.argv.slice(2);
if (!dir || !version || !/^\d+\.\d+\.\d+/.test(version)) {
  console.error(`bump-package: usage: bump-package.mjs <package-dir> <semver>  (got ${JSON.stringify([dir, version])})`);
  process.exit(1);
}
if (!existsSync(join(dir, "package.json"))) {
  console.error(`bump-package: no package.json in ${dir}`);
  process.exit(1);
}

const MANIFESTS = ["package.json", "plugin.json", ".claude-plugin/plugin.json"];
let touched = 0;
for (const file of MANIFESTS) {
  const path = resolve(join(dir, file));
  if (!existsSync(path)) continue;                    // optional per package
  const before = readFileSync(path, "utf8");
  const after = before.replace(/"version"\s*:\s*"[^"]*"/, `"version": "${version}"`);
  if (after === before) {
    console.error(`bump-package: no "version" field found in ${file}`);
    process.exit(1);
  }
  writeFileSync(path, after);
  console.log(`bump: ${dir}/${file} -> ${version}`);
  touched++;
}
if (!touched) { console.error("bump-package: touched nothing"); process.exit(1); }

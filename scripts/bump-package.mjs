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
const VERSION = /"version"\s*:\s*"([^"]*)"/;
let found = 0;
let touched = 0;
for (const file of MANIFESTS) {
  const path = resolve(join(dir, file));
  if (!existsSync(path)) continue;                    // optional per package
  const before = readFileSync(path, "utf8");
  // "no version field" and "already that version" were the same branch once, so a
  // no-op bump reported a missing field and failed the release re-run it existed to
  // make possible. They are different outcomes, so test for the field itself.
  if (!VERSION.test(before)) {
    console.error(`bump-package: no "version" field in ${file}`);
    process.exit(1);
  }
  found++;
  const after = before.replace(VERSION, `"version": "${version}"`);
  if (after === before) {
    console.log(`bump: ${dir}/${file} already ${version}`);
    continue;
  }
  writeFileSync(path, after);
  console.log(`bump: ${dir}/${file} -> ${version}`);
  touched++;
}
if (!found) { console.error("bump-package: found no manifests to bump"); process.exit(1); }
console.log(`bump-package: ${found} manifest(s), ${touched} changed`);

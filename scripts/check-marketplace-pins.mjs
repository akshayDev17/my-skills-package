#!/usr/bin/env node
// Every package must appear in both marketplace catalogues, delivered from npm, pinned
// to its current version.
//
// The failure this exists for: a catalogue entry pins a version RANGE, nothing read
// that range, and it went stale — the video repo's entries sat at ^0.1.0 while the
// package reached 0.4.0, so Claude, Codex and Gemini installed 0.1.2. A package that is
// simply *absent* from a catalogue is the same failure with no symptom at all.
//
//   node scripts/check-marketplace-pins.mjs
import { readFileSync, existsSync, readdirSync } from "node:fs";
import { join } from "node:path";

const MARKETPLACES = [".claude-plugin/marketplace.json", ".agents/plugins/marketplace.json"];

const marketplaces = MARKETPLACES.map((file) => {
  if (!existsSync(file)) {
    console.error(`✗ ${file} is missing — hosts have no catalogue to read`);
    process.exit(1);
  }
  try {
    return { file, json: JSON.parse(readFileSync(file, "utf8")) };
  } catch (err) {
    console.error(`✗ ${file}: ${err.message}`);
    process.exit(1);
  }
});

const packages = readdirSync("packages", { withFileTypes: true })
  .filter((e) => e.isDirectory())
  .map((e) => {
    const dir = join("packages", e.name);
    return { dir, json: JSON.parse(readFileSync(join(dir, "package.json"), "utf8")) };
  })
  .sort((a, b) => a.json.name.localeCompare(b.json.name));

if (packages.length === 0) {
  console.error("✗ no packages found under packages/");
  process.exit(1);
}

let failures = 0;
const fail = (message) => {
  failures += 1;
  console.error(`  ✗ ${message}`);
};

for (const pkg of packages) {
  const want = `^${pkg.json.version}`;
  let ok = true;
  for (const { file, json } of marketplaces) {
    const entry = (json.plugins ?? []).find((p) => p.source?.package === pkg.json.name);
    if (!entry) {
      fail(`${file}: does not list ${pkg.json.name} — that host cannot install it at all`);
      ok = false;
      continue;
    }
    if (entry.source.source !== "npm") {
      fail(`${file}: delivers ${pkg.json.name} from "${entry.source.source}", not npm`);
      ok = false;
      continue;
    }
    if (entry.source.version !== want) {
      fail(
        `${file}: pins ${pkg.json.name} at "${entry.source.version}", package.json says ${pkg.json.version} — a host reading this installs the wrong release`,
      );
      ok = false;
    }
  }
  if (ok) console.log(`✓ ${pkg.json.name}@${want} in both catalogues`);
}

if (failures > 0) {
  console.error(`\n${failures} marketplace problem(s). fix: node scripts/bump-package.mjs packages/<pkg> <version>`);
  process.exit(1);
}

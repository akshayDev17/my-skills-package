#!/usr/bin/env node
// portfolio-app — set up a new sub-app of the site, and check that it is behind Access.
//
//   portfolio-app bootstrap --repo <owner/name> --path /<prefix> [--d1 <name>] [--clone <dir>] [--apply]
//   portfolio-app verify --path /<prefix> [--domain <host>] [--team <team>]
//
// This file only wires the real world (fetch, child processes, the filesystem) into the
// library under ./lib, where every function takes its dependencies as arguments.

import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, realpathSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { setTimeout as sleep } from "node:timers/promises";
import { parseArgs } from "node:util";
import { normalizePrefix } from "./lib/prefix.mjs";
import { verifyPath } from "./lib/verify.mjs";
import { applyBootstrap, formatPlan, planBootstrap } from "./lib/bootstrap.mjs";
import { SETUP_TOKEN_ACCOUNT, defaultConfigPath, isInsideGitWorkTree, keychainRead, parseConfig } from "./lib/local.mjs";

const USAGE = `usage:
  portfolio-app bootstrap --repo <owner/name> --path /<prefix> [--d1 <name>] [--clone <dir>] [--config <file>] [--apply]
      Plans (default) or applies (--apply) the three setup steps for a sub-app:
      a Cloudflare Access app for the path, its D1 database id in backend/wrangler.jsonc,
      and the GitHub "production" environment secrets.
  portfolio-app verify --path /<prefix> [--domain <host>] [--team <team>] [--config <file>]
      Exits 0 only when the path redirects to Cloudflare Access. Needs no token.

exit codes: 0 ok · 1 verify failed or a step failed · 2 usage error or plan conflict`;

export function runProcess(cmd, args, { input } = {}) {
  const res = spawnSync(cmd, args, { input, encoding: "utf8", stdio: ["pipe", "pipe", "pipe"] });
  if (res.error) return { code: 127, stdout: "", stderr: res.error.message };
  return { code: res.status ?? 1, stdout: res.stdout ?? "", stderr: res.stderr ?? "" };
}

class UsageError extends Error {}

function prefixArg(raw) {
  try {
    return normalizePrefix(raw);
  } catch (err) {
    throw new UsageError(err.message);
  }
}

function loadConfig(path, run) {
  if (!existsSync(path)) {
    throw new UsageError(`no config at ${path} — see the README for its schema (or pass --config)`);
  }
  if (isInsideGitWorkTree(run, path)) {
    throw new UsageError(`${path} is inside a git work tree; keep the config outside any repo so it cannot be committed`);
  }
  return parseConfig(readFileSync(path, "utf8"), path);
}

async function verifyCommand(values, { fetch, run, log }) {
  if (!values.path) throw new UsageError("verify needs --path /<prefix>");
  const prefix = prefixArg(values.path);
  let { domain, team } = values;
  if (!domain) {
    const configPath = values.config ?? defaultConfigPath();
    if (!existsSync(configPath)) throw new UsageError("verify needs --domain <host> (or a config file that names one)");
    const config = loadConfig(configPath, run);
    domain = config.domain;
    team ??= config.team;
  }
  const { ok, results } = await verifyPath({ fetch, domain, prefix, team });
  for (const r of results) log(`${r.ok ? "✓" : "✗"} ${r.url} ${r.reason}`);
  log(ok ? `${domain}${prefix} is behind Cloudflare Access` : `${domain}${prefix} is NOT behind Cloudflare Access — refusing`);
  return ok ? 0 : 1;
}

async function bootstrapCommand(values, { fetch, run, log }) {
  if (!values.repo || !/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(values.repo)) {
    throw new UsageError("bootstrap needs --repo <owner/name>");
  }
  if (!values.path) throw new UsageError("bootstrap needs --path /<prefix>");
  const prefix = prefixArg(values.path);
  const config = loadConfig(values.config ?? defaultConfigPath(), run);
  const cf = { fetch, accountId: config.accountId, token: keychainRead(run, SETUP_TOKEN_ACCOUNT) };
  const opts = { repo: values.repo, prefix, cloneDir: resolve(values.clone ?? "."), d1Flag: values.d1, config };

  const actions = await planBootstrap({ cf, run, fetch }, opts);
  log(formatPlan(actions, { ...opts, apply: values.apply }));
  if (actions.some((a) => a.state === "conflict")) return 2;
  if (!values.apply) return 0;

  log("applying:");
  await applyBootstrap({ cf, run, fetch, sleep, log }, actions, opts);
  log("done");
  return 0;
}

export async function main(argv, deps) {
  const [command, ...rest] = argv;
  try {
    const { values } = parseArgs({
      args: rest,
      options: {
        repo: { type: "string" },
        path: { type: "string" },
        d1: { type: "string" },
        clone: { type: "string" },
        config: { type: "string" },
        domain: { type: "string" },
        team: { type: "string" },
        apply: { type: "boolean", default: false },
        help: { type: "boolean", short: "h", default: false },
      },
    });
    if (values.help || command === "help" || command === "--help" || command === "-h" || !command) {
      deps.log(USAGE);
      return command ? 0 : 2;
    }
    if (command === "verify") return await verifyCommand(values, deps);
    if (command === "bootstrap") return await bootstrapCommand(values, deps);
    throw new UsageError(`unknown command "${command}"`);
  } catch (err) {
    deps.error(`portfolio-app: ${err.message}`);
    if (err instanceof UsageError || err.code?.startsWith("ERR_PARSE_ARGS")) {
      deps.error(USAGE);
      return 2;
    }
    return 1;
  }
}

// Run only when executed (directly or through the npm bin link), not when imported by tests.
const invoked = process.argv[1] && resolve(process.argv[1]);
if (invoked && existsSync(invoked) && realpathSync(invoked) === realpathSync(fileURLToPath(import.meta.url))) {
  const code = await main(process.argv.slice(2), {
    fetch: globalThis.fetch,
    run: runProcess,
    log: (line) => console.log(line),
    error: (line) => console.error(line),
  });
  process.exitCode = code;
}

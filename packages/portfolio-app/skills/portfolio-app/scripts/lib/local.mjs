// Everything bootstrap reads from this machine or drives through a CLI: the config
// file, the macOS keychain, git and gh. Each takes `run(cmd, args, { input })` →
// `{ code, stdout, stderr }`, so tests stub the process layer instead of the tools.
//
// Secret values only ever travel through `input` (stdin) or a return value — never an
// argv, which any local process can read, and never a log line.

import { homedir } from "node:os";
import { dirname, join } from "node:path";

export const KEYCHAIN_SERVICE = "portfolio-app";
export const SETUP_TOKEN_ACCOUNT = "cloudflare-setup-token";
export const DEPLOY_TOKEN_ACCOUNT = "cloudflare-deploy-token";
export const POLICY_NAME = "portfolio-app sign-in";
export const GITHUB_ENVIRONMENT = "production";

export function defaultConfigPath() {
  return join(homedir(), ".config", "portfolio-app", "config.json");
}

/** Parses and checks the config file's text; returns the fields bootstrap uses. */
export function parseConfig(text, path) {
  let raw;
  try {
    raw = JSON.parse(text);
  } catch (err) {
    throw new Error(`${path}: not valid JSON (${err.message})`);
  }
  const problems = [];
  if (typeof raw.domain !== "string" || !/^[a-z0-9.-]+\.[a-z]{2,}$/.test(raw.domain)) {
    problems.push(`"domain" must be a bare hostname such as example.com`);
  }
  if (typeof raw.accountId !== "string" || !/^[0-9a-f]{32}$/.test(raw.accountId)) {
    problems.push(`"accountId" must be the 32-hex-character Cloudflare account id`);
  }
  if (typeof raw.team !== "string" || !/^[a-z0-9-]+$/.test(raw.team)) {
    problems.push(`"team" must be the Zero Trust team name (the <team> in <team>.cloudflareaccess.com)`);
  }
  if (!Array.isArray(raw.allowedEmails) || raw.allowedEmails.length === 0 ||
      !raw.allowedEmails.every((e) => typeof e === "string" && /^[^@\s]+@[^@\s]+$/.test(e))) {
    problems.push(`"allowedEmails" must be a non-empty list of email addresses`);
  }
  if (problems.length > 0) throw new Error(`${path}:\n  - ${problems.join("\n  - ")}`);
  return {
    domain: raw.domain,
    accountId: raw.accountId,
    team: raw.team,
    allowedEmails: raw.allowedEmails,
  };
}

/** The config names an account and who may sign in; it must not sit in a repo where it could be committed. */
export function isInsideGitWorkTree(run, path) {
  return run("git", ["-C", dirname(path), "rev-parse", "--is-inside-work-tree"]).code === 0;
}

/** Existence only — `-w` is what prints the secret, and it is not passed here. */
export function keychainHas(run, account) {
  return run("security", ["find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account]).code === 0;
}

export function keychainRead(run, account) {
  const { code, stdout } = run("security", ["find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account, "-w"]);
  const value = code === 0 ? stdout.trim() : "";
  if (!value) {
    throw new Error(
      `no keychain entry service="${KEYCHAIN_SERVICE}" account="${account}" — create it with: ` +
        `security add-generic-password -s ${KEYCHAIN_SERVICE} -a ${account} -w`,
    );
  }
  return value;
}

/** `owner/name` from a GitHub remote URL (https or ssh), or null. */
export function repoFromRemote(url) {
  const match = /github\.com[:/]([^/\s]+)\/([^/\s]+?)(?:\.git)?\/?$/.exec(url.trim());
  return match ? `${match[1]}/${match[2]}` : null;
}

export function cloneRepo(run, cloneDir) {
  const { code, stdout } = run("git", ["-C", cloneDir, "remote", "get-url", "origin"]);
  return code === 0 ? repoFromRemote(stdout) : null;
}

const notFound = (stderr) => /HTTP 404|Not Found/i.test(stderr);

export function ghEnvironmentExists(run, repo) {
  const { code, stderr } = run("gh", ["api", `repos/${repo}/environments/${GITHUB_ENVIRONMENT}`]);
  if (code === 0) return true;
  if (notFound(stderr)) return false;
  throw new Error(`gh: could not read environment ${GITHUB_ENVIRONMENT} of ${repo}: ${stderr.trim()}`);
}

export function ghSecretNames(run, repo) {
  const { code, stdout, stderr } = run("gh", [
    "secret", "list", "--repo", repo, "--env", GITHUB_ENVIRONMENT, "--json", "name",
  ]);
  if (code !== 0) throw new Error(`gh: could not list ${GITHUB_ENVIRONMENT} secrets of ${repo}: ${stderr.trim()}`);
  return JSON.parse(stdout || "[]").map((s) => s.name);
}

export function ghCreateEnvironment(run, repo) {
  const { code, stderr } = run("gh", ["api", "-X", "PUT", `repos/${repo}/environments/${GITHUB_ENVIRONMENT}`]);
  if (code !== 0) throw new Error(`gh: could not create environment ${GITHUB_ENVIRONMENT} on ${repo}: ${stderr.trim()}`);
}

/** The value goes over stdin: `gh secret set` reads it from there when `--body` is absent. */
export function ghSetSecret(run, repo, name, value) {
  const { code, stderr } = run("gh", ["secret", "set", name, "--repo", repo, "--env", GITHUB_ENVIRONMENT], {
    input: value,
  });
  if (code !== 0) throw new Error(`gh: could not set secret ${name} on ${repo}/${GITHUB_ENVIRONMENT}: ${stderr.trim()}`);
}

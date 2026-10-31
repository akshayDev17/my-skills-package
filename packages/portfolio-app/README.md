# portfolio-app

Set up a new sub-app of the personal site in one command, and check from CI that its path is
behind Cloudflare Access before every deploy.

Ships one skill (`skills/portfolio-app/SKILL.md`) and the script it drives
(`skills/portfolio-app/scripts/portfolio-app.mjs`), which is also the package's `portfolio-app`
npm bin, so it runs with or without an agent. Works with Claude Code, Codex, Gemini and DSH; the
`lib/` + `cordis.patch.yml` pairing is DSH's mounting glue only. Node 20+, no dependencies.

## `bootstrap`

```sh
portfolio-app bootstrap --repo <owner/name> --path /<prefix> [--d1 <name>] [--clone <dir>] [--apply]
```

Three steps, each one only filling in what is missing:

1. **Cloudflare Access.** Find or create one reusable *allow* policy (`policyName` from the
   config) that includes the config's `allowedEmails`. Then find or create a self-hosted Access
   app whose destinations are exactly `<domain>/<prefix>` and `<domain>/<prefix>/*`, attached to
   that policy. With `--apply`, it then waits until the path answers 302 to
   `<team>.cloudflareaccess.com/cdn-cgi/access/login/…`, and stops before touching GitHub if it
   never does.
2. **D1.** Find the database by name or create it, then write its id into the clone's
   `backend/wrangler.jsonc`, replacing the `REPLACE_AFTER_d1_create` placeholder (comments are
   preserved). The commit is left to the repo's normal delivery path. `--clone` defaults to the
   current directory, and its `origin` must be `--repo`. `--d1` defaults to the `database_name`
   of the entry that holds the placeholder.
3. **GitHub.** Create the repo's `production` environment and set `CLOUDFLARE_API_TOKEN` (the
   narrow deploy token) and `CLOUDFLARE_ACCOUNT_ID` through `gh`. Secret values go to `gh` over
   stdin, never in argv.

Without `--apply`, the command prints the plan and changes nothing: planning makes Cloudflare
`GET`s, `gh` reads, `git remote get-url`, and keychain *existence* checks only. Exit codes: `0`
ok, `1` a step failed, `2` usage error or a conflict in the plan.

**What it refuses or leaves alone.**

- **Refused prefixes:** an empty prefix, `/`, and anything under ai-chat-archive's site-wide
  `/api`, `/_next` or `/vendors`. Also refused: wildcards, dots, queries and uppercase.
- **Conflicts (`✗`), where nothing is applied:**
  - an existing Access app that overlaps the two destinations without matching them exactly;
  - a clone of a different repo;
  - placeholder entries that name more than one database;
  - a missing deploy-token keychain entry.
- **Reported but left alone (`!`):**
  - a policy whose emails differ from the config;
  - an exact-match Access app that is not attached to the policy.

  Changing who can sign in is a decision for the Zero Trust dashboard, not for a re-run.

## `verify`: the login check for deploy jobs

```sh
portfolio-app verify --path /<prefix> --domain <host> [--team <team>]
```

Requests `https://<host>/<prefix>` and `https://<host>/<prefix>/portfolio-app-verify` without
following redirects. It exits `0` only when both answer 3xx to
`<team>.cloudflareaccess.com/cdn-cgi/access/login/…`. If `--team` is omitted, any
`*.cloudflareaccess.com` login host is accepted. It needs no token and no config (with `--domain`
given), so it can be the first step of a GitHub Actions deploy job:

```yaml
- name: Refuse to deploy unless the path is behind Cloudflare Access
  run: npx -y portfolio-app verify --domain akshayprabhakant.com --team shiny-moon-6b37 --path /quiz
```

Until the package is published to npm, run the script from a checkout of this repo instead:
`node packages/portfolio-app/skills/portfolio-app/scripts/portfolio-app.mjs verify …`.

## One-time machine setup

### Config file

`~/.config/portfolio-app/config.json` by default; override with `--config <file>` or
`PORTFOLIO_APP_CONFIG`. It must live outside any git work tree, and the script refuses one that
does not. Placeholder values:

```json
{
  "domain": "example.com",
  "accountId": "0123456789abcdef0123456789abcdef",
  "team": "your-team",
  "allowedEmails": ["you@example.com"],
  "policyName": "portfolio-app sign-in"
}
```

| field | meaning |
|---|---|
| `domain` | the site's hostname; Access destinations are `<domain>/<prefix>` and `<domain>/<prefix>/*` |
| `accountId` | the Cloudflare account id (32 hex characters) |
| `team` | the Zero Trust team name: the `<team>` in `<team>.cloudflareaccess.com` |
| `allowedEmails` | who may sign in; becomes the reusable policy's `include` rules |
| `policyName` | optional; the reusable policy to find or create (default `portfolio-app sign-in`) |

### Keychain entries

Both tokens are read at run time with `security find-generic-password -s portfolio-app -a <account> -w`,
and are never written to a file, a log line or the plan output.

| service | account | token | used for |
|---|---|---|---|
| `portfolio-app` | `cloudflare-setup-token` | broad setup token: Access apps and policies edit, D1 edit | Cloudflare API calls from this machine only |
| `portfolio-app` | `cloudflare-deploy-token` | the narrow deploy token the sub-apps' CI deploys with | sent to GitHub as `CLOUDFLARE_API_TOKEN`, nothing else |

Create each once (the `-w` with no value makes `security` prompt for it, so it stays out of
shell history):

```sh
security add-generic-password -s portfolio-app -a cloudflare-setup-token -w
security add-generic-password -s portfolio-app -a cloudflare-deploy-token -w
```

`gh` must be logged in with rights to manage the repo's environments and secrets.

## Research notes (read 2026-10-31)

### Access API: reusable policies, `destinations` vs `self_hosted_domains`

- **Reusable policies** are account-level: `GET`/`POST /accounts/{account_id}/access/policies`
  ("Lists Access reusable policies"). A policy's required fields are `name`, `decision` and
  `include`; an email rule is `{"email": {"email": "…"}}`. List items carry `reusable` and
  `app_count`.
- **Applications:** `GET`/`POST /accounts/{account_id}/access/apps`. An application links a
  reusable policy with `policies: [{ "id", "precedence" }]`, and "reusable and inline policies
  are mutually exclusive". `destinations` ("List of destinations secured by Access. This
  supersedes self_hosted_domains") takes `{ "type": "public", "uri": "<host>/<path>" }` entries
  whose URIs may carry a path and wildcards. If `destinations` is given, `self_hosted_domains` is
  ignored, and `self_hosted_domains` itself was "deprecated in favor of destinations and will be
  supported until November 21, 2025". So bootstrap writes `destinations` only, and reads both
  fields (plus `domain`) when looking for an existing app. The list endpoint filters by `domain`
  / `name` / `exact`, but bootstrap lists all apps and matches destinations client-side, so a
  partial overlap is visible too.
- Sources:
  - [create app](https://developers.cloudflare.com/api/resources/zero_trust/subresources/access/subresources/applications/methods/create/)
  - [list apps](https://developers.cloudflare.com/api/resources/zero_trust/subresources/access/subresources/applications/methods/list/)
  - [create policy](https://developers.cloudflare.com/api/resources/zero_trust/subresources/access/subresources/policies/methods/create/)
  - [list policies](https://developers.cloudflare.com/api/resources/zero_trust/subresources/access/subresources/policies/methods/list/)

### Wrangler 4 automatic provisioning with `pywrangler deploy` + `d1 migrations apply`

**Conclusion: not confirmed without a live deploy, so step 2 stays as designed.** The source
evidence:

- **Provisioning.** Automatic resource provisioning (open beta, wrangler ≥ 4.45.0) creates a D1
  database on `wrangler deploy` for a binding that has no id, and "resource IDs will be
  automatically written back to your Wrangler config file". It is disabled with
  `--no-x-provision`. Wrangler's provisioning tests include "won't prompt to provision if a D1
  database name belongs to an existing database", i.e. a `database_name` with no id is matched
  by name.
  ([changelog 2025-10-24](https://developers.cloudflare.com/changelog/2025-10-24-automatic-resource-provisioning/),
  [provision.test.ts](https://github.com/cloudflare/workers-sdk/blob/main/packages/wrangler/src/__tests__/provision.test.ts))
- **Remote migrations without an id.** Before wrangler 4.102.0 (2026-06-18), `d1 migrations
  apply --remote` failed on a binding with no `database_id`. Since cloudflare/workers-sdk#14275,
  remote D1 subcommands including `d1 migrations apply --remote` resolve the UUID with
  `GET /accounts/:id/d1/database/:name`. With only a `binding`, they use the name auto-provisioning
  would create, `<worker name>-<binding>`. If that database does not exist yet, the command fails
  and says to run `wrangler deploy` first.
  ([d1/utils.ts](https://github.com/cloudflare/workers-sdk/blob/main/packages/wrangler/src/d1/utils.ts),
  [wrangler@4.102.0](https://github.com/cloudflare/workers-sdk/releases/tag/wrangler%404.102.0))
- **pywrangler.** `pywrangler deploy` syncs Python dependencies and then proxies to
  `npx --yes wrangler deploy`, and it requires wrangler ≥ 4.127.1, so both features above are in
  range. ([pywrangler utils.py](https://github.com/cloudflare/workers-py/blob/main/packages/cli/src/pywrangler/utils.py),
  [cli.py](https://github.com/cloudflare/workers-py/blob/main/packages/cli/src/pywrangler/cli.py))
- **What stays unproven:**
  - an end-to-end CI run on a fresh account;
  - ordering: a deploy job that runs `d1 migrations apply --remote` before the first deploy
    would fail, because the database does not exist until `deploy` creates it;
  - in CI, the id write-back lands in the runner's throwaway checkout, not in the repo.

  If a live trial of "`database_name`, no `database_id`, deploy then migrate" succeeds, step 2
  can shrink to removing the placeholder line instead of creating the database.

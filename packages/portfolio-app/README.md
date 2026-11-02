# @akshaydev17/portfolio-app

Set up a new sub-app of the personal site in one command, and check from CI that its path is
behind Cloudflare Access before every deploy. A sub-app meant for everyone can instead be set up
as a public app with `--public`, which skips Access and gives it an R2 bucket and Worker routes.

Ships one skill (`skills/portfolio-app/SKILL.md`) and the script it drives
(`skills/portfolio-app/scripts/portfolio-app.mjs`), which is also the package's `portfolio-app`
bin, so it runs with or without an agent. Works with Claude Code, Codex, Gemini and DSH; the
`lib/` + `cordis.patch.yml` pairing is DSH's mounting glue only. Node 20+, no dependencies.

## `bootstrap`

```sh
portfolio-app bootstrap --repo <owner/name> --path /<prefix> [--public] [--d1 <name>] [--clone <dir>] [--apply]
```

A prefix may have several segments (`/work/election-atlas`). Without `--public`, three steps,
each one only filling in what is missing:

1. **Cloudflare Access.** Find or create one reusable *allow* policy named
   `portfolio-app sign-in` that includes the config's `allowedEmails`. Then find or create a self-hosted Access
   app whose destinations are exactly `<domain>/<prefix>` and `<domain>/<prefix>/*`, attached to
   that policy. With `--apply`, it then waits until the path redirects (3xx) to
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

### `--public`: an app anyone can reach

Only `--public` makes an app public: there is no config default and nothing is inferred from
the repo or the path. The plan's header says `(public: no Access sign-in)` and its first line
says the two destinations will be reachable by anyone. The steps, in apply order:

1. **No Access.** No policy and no Access app are created, and apply does not wait for a
   login redirect. If an Access app already guards either destination, or the path already
   redirects to an Access login (a wider app such as `<domain>/work/*`), the plan is a
   conflict: the tool never removes protection.
2. **D1**, as above.
3. **R2.** Find the bucket by name or create it. The name is the single `bucket_name` in the
   clone's `r2_buckets` (several names are a conflict; none means no R2 step).
4. **Worker routes.** Exactly `<domain>/<prefix>` and `<domain>/<prefix>/*`, pointing at the
   Worker named by `name` in the clone's `backend/wrangler.jsonc`. A route can only name a
   deployed Worker (see the research notes), so before the first deploy the plan says so and
   creates nothing here; re-run after that deploy to add the routes.
5. **GitHub**, as above.

`verify` fails by design on a public path, so a public app's deploy job does not run it.

### Plan, apply, exit codes

Without `--apply`, the command prints the plan and changes nothing: planning makes Cloudflare
`GET`s (with the setup token read from the keychain), `gh` reads, `git remote get-url`, and an
*existence* check on the deploy-token keychain entry only. Exit codes: `0`
ok, `1` a step failed, `2` usage error or a conflict in the plan.

**What it refuses or leaves alone.**

- **Refused prefixes:** an empty prefix, `/`, and anything under ai-chat-archive's site-wide
  `/api`, `/_next` or `/vendors`. Also refused: wildcards, dots, queries and uppercase.
- **Conflicts (`✗`), where nothing is applied:**
  - an existing Access app that overlaps the two destinations without matching them exactly;
  - with `--public`, any Access app on the destinations, or a path that already redirects to
    an Access login;
  - a clone of a different repo;
  - placeholder entries that name more than one database; with `--public`, `r2_buckets` that
    name several buckets;
  - a Worker route that reaches into the prefix and is not the app's own: one owned by another
    Worker or by none, or one of the app's Worker that also matches paths outside the prefix
    (`<domain>/work/election-atlas*` matches `/work/election-atlas-old` too);
  - with `--public`, a setup token that may not read R2 buckets, the zone, its Worker routes
    or the Worker scripts (the line names the permission; see the keychain table);
  - a missing deploy-token keychain entry.
- **Reported but left alone (`!` or `·`):**
  - a policy whose emails differ from the config;
  - an exact-match Access app that is not attached to the policy;
  - a broader route of another Worker, such as the home site's `<domain>/*`: the app's longer
    patterns are more specific, so Cloudflare sends the prefix to the app.

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
  run: npx -y @akshaydev17/portfolio-app@0.1.0 verify --domain akshayprabhakant.com --team shiny-moon-6b37 --path /quiz
```

Until the package is published to npm, run the script from a checkout of this repo instead:
`node packages/portfolio-app/skills/portfolio-app/scripts/portfolio-app.mjs verify …`.

## One-time machine setup

### Config file

`~/.config/portfolio-app/config.json` by default; override with `--config <file>`. It must live outside any git work tree, and the script refuses one that
does not. Placeholder values:

```json
{
  "domain": "example.com",
  "accountId": "0123456789abcdef0123456789abcdef",
  "team": "your-team",
  "allowedEmails": ["you@example.com"]
}
```

| field | meaning |
|---|---|
| `domain` | the site's hostname; Access destinations are `<domain>/<prefix>` and `<domain>/<prefix>/*` |
| `accountId` | the Cloudflare account id (32 hex characters) |
| `team` | the Zero Trust team name: the `<team>` in `<team>.cloudflareaccess.com` |
| `allowedEmails` | who may sign in; becomes the reusable policy's `include` rules |

### Keychain entries

Both tokens are read at run time with `security find-generic-password -s portfolio-app -a <account> -w`,
and are never written to a file, a log line or the plan output.

| service | account | token | used for |
|---|---|---|---|
| `portfolio-app` | `cloudflare-setup-token` | broad setup token; permissions below | Cloudflare API calls from this machine only |
| `portfolio-app` | `cloudflare-deploy-token` | the narrow deploy token the sub-apps' CI deploys with | sent to GitHub as `CLOUDFLARE_API_TOKEN`, nothing else |

The setup token's permissions, as the Cloudflare dashboard names them. Only the reads a
`--public` plan adds name a missing permission: when the token may not read the R2 bucket, the
zone, its Worker routes or the Worker scripts, the plan shows a `✗` line naming the permission
to add. A token missing Access or D1 permission fails the run (exit 1) with Cloudflare's error
instead. Planning only reads, so a token that can read but not edit R2 Storage or Workers Routes
passes the plan and is caught at `--apply`, possibly after D1 or R2 already exist; re-running
after fixing the token resumes safely, since every step only fills in what is missing.

| permission | needed for |
|---|---|
| Account · Access: Apps and Policies · Edit | every run: Access policy and app (a `--public` run only reads the apps) |
| Account · D1 · Edit | every run: the D1 database |
| Account · Workers R2 Storage · Edit | `--public` when the clone binds an R2 bucket |
| Zone · Zone · Read, on the site's zone | `--public`: finding the zone id for the routes |
| Zone · Workers Routes · Edit, on the site's zone | `--public`: listing and creating the routes |
| Account · Workers Scripts · Read | `--public`: checking the Worker is deployed before routing to it |

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

### R2 buckets and Worker routes (read 2026-11-02)

- **R2:** `GET /accounts/{account_id}/r2/buckets/{bucket_name}` returns one bucket;
  `POST /accounts/{account_id}/r2/buckets` with `{ "name" }` creates one and needs
  `Workers R2 Storage Write`. A missing bucket answers 404, which the plan reads as "create".
- **Routes:** `GET`/`POST /zones/{zone_id}/workers/routes`, items `{ id, pattern, script }`,
  needing `Workers Routes Read`/`Write`. The zone id comes from `GET /zones?name=<domain>`,
  which lists only zones the token can read, so an empty answer means a missing Zone Read.
- **Routes need a deployed Worker:** the create docs do not say so, but creating a route for a
  missing script is reported (in a Stack Overflow question, not the docs) to fail with
  `10019: workers.api.error.invalid_route_script_missing`.
  So the plan checks `GET /accounts/{account_id}/workers/scripts` first and defers the routes
  until the Worker exists. **Not confirmed against a live account.**
- **Precedence:** when several route patterns match a request, the most specific one wins,
  which is why a broader route such as the home `<domain>/*` is left alone.
- Sources:
  - [create route](https://developers.cloudflare.com/api/resources/workers/subresources/routes/methods/create/)
  - [list routes](https://developers.cloudflare.com/api/resources/workers/subresources/routes/methods/list/)
  - [get bucket](https://developers.cloudflare.com/api/resources/r2/subresources/buckets/methods/get/)
  - [create bucket](https://developers.cloudflare.com/api/resources/r2/subresources/buckets/methods/create/)
  - [Workers errors (10007, Worker not found)](https://developers.cloudflare.com/workers/observability/errors/)

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

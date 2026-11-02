---
name: portfolio-app
description: Bootstrap a new sub-app of the personal site (akshayprabhakant.com) — its Cloudflare Access app for the path, its D1 database id in backend/wrangler.jsonc, and its GitHub production environment secrets; or, for an app explicitly meant to be public, its D1 database, R2 bucket and Worker routes with no Access step — and check that a path is behind Cloudflare Access. Use when asked to set up, bootstrap, onboard or wire a new sub-app or path of the site, put a path behind Access, set up a public sub-app, create a sub-app's D1 database or R2 bucket, route a path to a sub-app's Worker, set a sub-app repo's deploy secrets, or add a login check that blocks deploys when a path is public.
---

# Bootstrap a portfolio sub-app

Every sub-app of the site lives under its own path prefix (`/quiz`, `/graph`, …), in its own repo, deployed by that repo's CI. Before its first deploy it needs three things that used to be done by hand: a Cloudflare Access app guarding the prefix, its D1 database (and the id committed into `backend/wrangler.jsonc`), and the repo's GitHub `production` environment with the deploy secrets. The bundled script does all three, idempotently, and prints the plan before changing anything.

The script is `scripts/portfolio-app.mjs`, next to this file. Run it with `node` (Node 20+); it is also the `portfolio-app` bin of the npm package `@akshaydev17/portfolio-app`.

## Inputs

- **Repo** — `owner/name` of the sub-app (e.g. `akshayDev17/graph-notes`).
- **Prefix** — the path it lives under (e.g. `/graph`). Refused: empty, `/`, and anything under ai-chat-archive's site-wide `/api`, `/_next` or `/vendors`.
- **Local clone** — a checkout of that repo; the D1 id is written into its `backend/wrangler.jsonc`.
- **D1 name** — optional; by default the `database_name` of the entry still holding `REPLACE_AFTER_d1_create`.
- **Public or private** — private (behind Access) unless the user explicitly says the app is public. Never choose public yourself, from the repo being public, the path, or anything else. Public adds `--public`.

One-time machine setup (config file and the two keychain entries) is in the package README. If `bootstrap` reports a missing config or keychain entry, point the user there; never ask them to paste a token into the conversation.

## Steps

1. **Plan.**
   `node scripts/portfolio-app.mjs bootstrap --repo <owner/name> --path /<prefix> --clone <clone dir>`
   (add `--public` only for an app the user said is public). This reads only. Each line is `✓` exists, `+` will be created, `!` a warning (for `--public`, that the path will be reachable by anyone), `·` information, `✗` conflict.
   *Done when the plan is printed and shown to the user verbatim.*

2. **Resolve conflicts (`✗`) by hand.** Exit code 2 means a conflict: an Access app that overlaps the prefix without matching it, an Access app or Access redirect on a path planned as `--public`, a Worker route that reaches into the prefix and is not the app's own, a clone of the wrong repo, an ambiguous `wrangler.jsonc`, a setup token missing a permission (the line names it; the user adds it in the Cloudflare dashboard), or a missing deploy-token keychain entry. The script will not apply while one stands. Report it; do not work around it, and never remove an Access app to make `--public` pass.
   *Done when a re-plan shows no `✗`.*

3. **Get explicit approval for the exact plan, then apply.** Creating a policy, an Access app or a public route changes who can reach the site, so `--apply` runs only after the user approves the printed plan in this conversation. Then re-run the same command with `--apply`. It creates the `+` lines in order. For a private app it first waits until the prefix redirects to `<team>.cloudflareaccess.com`, and stops with an error before touching anything else if it never does. For a public app there is no wait. If the plan said the Worker is not deployed yet, the routes come from a re-run after its first deploy.
   *Done when the run ends with `done` (and, for a public app, a re-run after the first deploy shows both routes `✓`).*

4. **Commit the `wrangler.jsonc` change through the repo's normal delivery path.** The script edits the clone and never commits. The diff is one line: the placeholder replaced by the database id.
   *Done when the change is on a branch headed for review.*

5. **Private apps only: gate the deploy on the login check.** Skip this for a public app; `verify` fails on a public path by design. The first step of the repo's deploy job should be:
   `npx -y @akshaydev17/portfolio-app@0.1.0 verify --domain akshayprabhakant.com --team shiny-moon-6b37 --path /<prefix>`
   (or `node <path>/portfolio-app.mjs verify …` until the package is on npm). It needs no secrets and exits non-zero unless both `/<prefix>` and a path under it redirect to Access.
   *Done when the deploy workflow runs `verify` before any deploy step.*

## Never

- Never run `--apply` without the user's approval of the plan it will carry out.
- Never print, log or echo a token. The script reads them from the keychain itself.
- Never give GitHub the setup token; only the narrow deploy token goes into `CLOUDFLARE_API_TOKEN`.
- Never widen an Access app or a Worker route beyond `<domain>/<prefix>` and `<domain>/<prefix>/*`.
- Never pass `--public` unless the user explicitly asked for a public app.

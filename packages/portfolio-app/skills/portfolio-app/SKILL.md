---
name: portfolio-app
description: Bootstrap a new sub-app of the personal site (akshayprabhakant.com) — its Cloudflare Access app for the path, its D1 database id in backend/wrangler.jsonc, and its GitHub production environment secrets — and check that a path is behind Cloudflare Access. Use when asked to set up, bootstrap, onboard or wire a new sub-app or path of the site, put a path behind Access, create a sub-app's D1 database, set a sub-app repo's deploy secrets, or add a login check that blocks deploys when a path is public.
---

# Bootstrap a portfolio sub-app

Every sub-app of the site lives under its own path prefix (`/quiz`, `/graph`, …), in its own repo, deployed by that repo's CI. Before its first deploy it needs three things that used to be done by hand: a Cloudflare Access app guarding the prefix, its D1 database (and the id committed into `backend/wrangler.jsonc`), and the repo's GitHub `production` environment with the deploy secrets. The bundled script does all three, idempotently, and prints the plan before changing anything.

The script is `scripts/portfolio-app.mjs`, next to this file. Run it with `node` (Node 20+); it is also the `portfolio-app` bin of the npm package `@akshaydev17/portfolio-app`.

## Inputs

- **Repo** — `owner/name` of the sub-app (e.g. `akshayDev17/graph-notes`).
- **Prefix** — the path it lives under (e.g. `/graph`). Refused: empty, `/`, and anything under ai-chat-archive's site-wide `/api`, `/_next` or `/vendors`.
- **Local clone** — a checkout of that repo; the D1 id is written into its `backend/wrangler.jsonc`.
- **D1 name** — optional; by default the `database_name` of the entry still holding `REPLACE_AFTER_d1_create`.

One-time machine setup (config file and the two keychain entries) is in the package README. If `bootstrap` reports a missing config or keychain entry, point the user there; never ask them to paste a token into the conversation.

## Steps

1. **Plan.**
   `node scripts/portfolio-app.mjs bootstrap --repo <owner/name> --path /<prefix> --clone <clone dir>`
   This reads only. Each line is `✓` exists, `+` will be created, `!` differs but will be left alone, `✗` conflict.
   *Done when the plan is printed and shown to the user verbatim.*

2. **Resolve conflicts (`✗`) by hand.** Exit code 2 means a conflict: an Access app that overlaps the prefix without matching it, a clone of the wrong repo, an ambiguous `wrangler.jsonc`, or a missing keychain entry. The script will not apply while one stands. Report it; do not work around it.
   *Done when a re-plan shows no `✗`.*

3. **Get explicit approval for the exact plan, then apply.** Creating a policy or Access app changes who can reach the site, so `--apply` runs only after the user approves the printed plan in this conversation. Then re-run the same command with `--apply`. It creates the `+` lines in order, waits until the prefix 302s to `<team>.cloudflareaccess.com`, and stops with an error before touching GitHub if it never does.
   *Done when the run ends with `done`.*

4. **Commit the `wrangler.jsonc` change through the repo's normal delivery path.** The script edits the clone and never commits. The diff is one line: the placeholder replaced by the database id.
   *Done when the change is on a branch headed for review.*

5. **Gate the deploy on the login check.** The first step of the repo's deploy job should be:
   `npx -y @akshaydev17/portfolio-app@0.1.0 verify --domain akshayprabhakant.com --team shiny-moon-6b37 --path /<prefix>`
   (or `node <path>/portfolio-app.mjs verify …` until the package is on npm). It needs no secrets and exits non-zero unless both `/<prefix>` and a path under it redirect to Access.
   *Done when the deploy workflow runs `verify` before any deploy step.*

## Never

- Never run `--apply` without the user's approval of the plan it will carry out.
- Never print, log or echo a token. The script reads them from the keychain itself.
- Never give GitHub the setup token; only the narrow deploy token goes into `CLOUDFLARE_API_TOKEN`.
- Never widen an Access app beyond `<domain>/<prefix>` and `<domain>/<prefix>/*`.

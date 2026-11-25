# @akshaydev17/portfolio-app

Set up a new sub-app of the personal site in one command, and check from CI that its path is
behind Cloudflare Access before every deploy. A sub-app meant for everyone can instead be set up
as a public app with `--public`, which skips Access and gives it an R2 bucket and the Worker
route `<domain>/<prefix>*`.
A sub-app that is signed in by default but has a few public paths is a mixed app: name each
public path with `--public-path`, and it gets a second Access app whose destinations are exactly
those paths. A path listed without a final `/*` is meant to be public on its own, so its Allow
app also gets a narrowing `<path>/*` that keeps the paths under it behind sign-in, and they are
checked to still ask for it.

Ships one skill (`skills/portfolio-app/SKILL.md`) and the script it drives
(`skills/portfolio-app/scripts/portfolio-app.mjs`), which is also the package's `portfolio-app`
bin, so it runs with or without an agent. Works with Claude Code, Codex, Gemini and DSH; the
`lib/` + `cordis.patch.yml` pairing is DSH's mounting glue only. Node 20+, no dependencies.

## `bootstrap`

```sh
portfolio-app bootstrap --repo <owner/name> --path /<prefix> [--public | --public-path <path>…]
                        [--d1 <name>] [--clone <dir>] [--wrangler <path>] [--access-vars] [--apply]
```

A prefix may have several segments (`/work/election-atlas`). Without `--public`, three steps,
each one only filling in what is missing:

1. **Cloudflare Access.** Find or create one reusable *allow* policy named
   `portfolio-app sign-in` that includes the config's `allowedEmails`. Then find or create a self-hosted Access
   app whose destinations are exactly `<domain>/<prefix>` and `<domain>/<prefix>/*`, attached to
   that policy. With `--apply`, it then waits until the path redirects (3xx) to
   `<team>.cloudflareaccess.com/cdn-cgi/access/login/…`, and stops before touching D1 or GitHub
   if it never does.
2. **D1.** Find the database by name or create it, then write its id into the clone's
   `backend/wrangler.jsonc`, replacing the placeholder `REPLACE_AFTER_d1_create` or
   `00000000-0000-0000-0000-000000000000` held by a `database_id` in the top-level
   `d1_databases` (comments are preserved, and so is an all-zero id in any other field, such
   as a KV namespace's `id`, or in a nested section such as `env.preview.d1_databases`). The
   edit is a one-line diff. The commit is left to the repo's normal delivery path. `--clone`
   defaults to the current directory, and its `origin` must be `--repo`. `--d1` defaults to the
   `database_name` of the entry that holds the placeholder. Entries holding either placeholder
   for one database are replaced together; entries naming several databases are a conflict.
   `--wrangler <path>` names the Worker config when it is not `backend/wrangler.jsonc`, say
   `--wrangler shell/wrangler.jsonc`: a path inside `--clone` (an absolute one, or one that
   leaves the clone, is a usage error, exit 2). It replaces the default for the whole run:
   the D1 step's plan and apply, the "already carries the database_id" check, the write, every
   message that names the file, and, with `--public`, the reads of `r2_buckets` and the Worker
   `name`, which come from the same file. Without it nothing changes.
3. **GitHub.** Create the repo's `production` environment and set `CLOUDFLARE_API_TOKEN` (the
   narrow deploy token) and `CLOUDFLARE_ACCOUNT_ID` through `gh`. Secret values go to `gh` over
   stdin, never in argv. With `--access-vars` (off by default, so existing apps and re-runs
   change nothing; not allowed with `--public`, which has no Access app) it also sets two
   non-secret *variables* in that environment: `ACCESS_AUD`, the sign-in Access app's AUD tag
   (never the Bypass app's), and `ACCESS_TEAM_DOMAIN`, the config's `<team>.cloudflareaccess.com`.
   The plan shows both lines before `--apply`; a variable that already holds the value is
   skipped, one holding another value is updated, and for an app the run is about to create
   the plan says the AUD is known once it exists. An existing sign-in app whose answer has no
   AUD is a conflict. The owner never types either value.

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
4. **Worker route.** Exactly one, `<domain>/<prefix>*`, pointing at the Worker named by `name`
   in the clone's Worker config, `backend/wrangler.jsonc` or the `--wrangler` file (why one
   route and not `<domain>/<prefix>` + `<domain>/<prefix>/*`, and what the Worker must do with
   it: see
   [The Worker route and its pass-through](#the-worker-route-and-its-pass-through)). A route
   can only name a deployed Worker (see the research notes), so before the first deploy the
   plan says so and creates nothing here; re-run after that deploy to add the route. With
   `--apply`, whenever the route exists or this run creates it, it waits until the route check
   passes, and stops before GitHub, deleting nothing, if it never does (a route this run created
   stays in place). The check, signed out: `/<prefix>?portfolio-app-verify=1` must answer with
   the status `/<prefix>` answers with, or redirect to `/<prefix>` or a path under `/<prefix>/`
   on the same host, with neither answer from the origin nor a redirect to an Access login; and
   `/<prefix>portfolio-app-verify` must come from the origin. "From the origin" means the answer
   carries GitHub Pages' `x-github-request-id` header. A query probe from the origin means the
   Worker route misses `/<prefix>?<query>`; a sibling probe without the header means the Worker
   serves paths outside its prefix; an Access login redirect means nothing about the route can
   be told signed out. An app bootstrapped
   before this change holds that same Worker's exact pair `<domain>/<prefix>` +
   `<domain>/<prefix>/*`: the plan shows each as a `-` line, and apply deletes them only after
   `<domain>/<prefix>*` exists and the check passes. Both point at the same Worker, so the app
   answers throughout.
5. **GitHub**, as above.

`verify` fails by design on a public path, so a public app's deploy job does not run it.

### `--public-path`: signed in by default, with named public paths

A mixed app is a private app (all of the above) plus a list of public paths given on the
command line, one `--public-path` per path. Nothing is inferred: a path is public only when it
is listed. Each path is one of:

- `/<prefix>` itself;
- `/<prefix>/`, the prefix's trailing-slash path, exactly, and only alongside `/<prefix>` (see
  below);
- an exact path under it, such as `/graph/public.txt`;
- a directory under it with a final `/*`, such as `/graph/_next/*`.

A path without a final `/*` is meant to be public on its own, not with what is under it:
`/graph/public` does not make `/graph/public/x` public. Cloudflare lets a sub-path inherit its
parent path's rule, so on its own an exact Bypass destination opens its children too (seen on
the live account; see the research notes). So the Allow app gets a **narrowing destination**
`<path>/*` for each such path: `/graph/public` adds `<domain>/graph/public/*`. It is more specific
than the inherited `<domain>/graph/public`, so the children ask for sign-in while the path itself
stays public. None is added for the bare prefix, whose children are the Allow app's own
`<prefix>/*` already, nor for a path whose children another listed `/*` path makes public. The
tool still requests a child of each exact public path and requires the Access login; if one
answers signed out, `--apply` stops and `verify` fails, naming that child. To make the children
public too, list `<path>/*` as well.

The leading `/` is optional. Refused before anything is read: a path outside the prefix,
`/<prefix>/*` (the whole app; that is `--public`), a `*` anywhere but a final `/*`, empty, `.` or
`..` segments (the one exception is `/<prefix>/` itself: `/<prefix>//`, `/<prefix>/x/` and
`/<prefix>//x` are refused), queries, and `--public` together with `--public-path`.

**The trailing-slash path `/<prefix>/`.** `/time-track` and `/time-track/` are two request paths,
and Access tells them apart: with `time-track` public and `time-track/*` behind sign-in, the first
answers signed out and the second asks for sign-in (seen on the live account). Listing
`/<prefix>/` makes exactly `<domain>/<prefix>/` a public destination. It gets no narrowing
destination: that would be `<prefix>/*`, which the Allow app already holds. But it is the parent
of every page under the prefix, and an Access destination covers its subpaths too; the docs do
not say whether `<prefix>/` or the Allow app's `<prefix>/*` is the more specific for
`<prefix>/<child>` (see the research notes). So the plan says the children are meant for the
Allow app and that a child is checked, and `--apply` probes `<prefix>/portfolio-app-verify` and
`<prefix>/api/portfolio-app-verify`, which must still redirect to the login. If this run added
`<prefix>/` and they answer signed out, or the check fails in any other way, apply takes
`<prefix>/` out of the public app again (step 5 below). A run that does not add it (a re-run
after it is in place) only reports a failed check and removes nothing, and the plan's line for
the children says which of the two applies.

`/<prefix>/` is accepted only alongside `/<prefix>`. Listing `--public-path /<prefix>/` without
`--public-path /<prefix>` is a plan conflict (`✗`, exit 2, nothing applied), whether the public
app is new or already exists. That way the public app always holds the bare prefix, so taking
`<prefix>/` out again always leaves it a destination to keep, and the tool never has to delete
an app.

```sh
# time-track: everything signed in except the landing page and its static assets
portfolio-app bootstrap --repo owner/time-track --path /time-track \
  --public-path /time-track --public-path '/time-track/_next/*' --clone ../time-track

# graph-notes: its Allow app and D1 came first; a first mixed run on such an app adds the
# public app, and the narrowing destinations for its three exact public paths to the Allow app
portfolio-app bootstrap --repo owner/graph-notes --path /graph \
  --public-path /graph/public --public-path /graph/public.txt --public-path '/graph/_next/*' \
  --public-path '/graph/icons/*' --public-path /graph/manifest.webmanifest \
  --public-path '/graph/api/public/*' --clone ../graph-notes
```

Both apps are set up on the live account now, so re-running either command plans zero changes.
graph-notes' Allow app already holds its three narrowing destinations (the owner approved adding
them on 2026-11-15) and its public app matches the list; its Access section reads:

```
  ✓ reusable policy "portfolio-app sign-in" exists
  ✓ Access app "graph-notes (/graph)" guards example.com/graph + example.com/graph/* + example.com/graph/public/* + example.com/graph/public.txt/* + example.com/graph/manifest.webmanifest/*
  ✓ Bypass app "graph-notes (public)" lets anyone reach example.com/graph/public + example.com/graph/public.txt + example.com/graph/_next/* + example.com/graph/icons/* + example.com/graph/manifest.webmanifest + example.com/graph/api/public/*
  · who answers (…): one line per destination, as below
  ✓ /graph already redirects to <team>.cloudflareaccess.com, and its public paths answer signed out
…
nothing to create — already bootstrapped
```

time-track's only exact public path is the bare prefix, so it has no narrowing destination:

```
  ✓ reusable policy "portfolio-app sign-in" exists
  ✓ Access app "time-track (/time-track)" guards example.com/time-track/*
  ✓ Bypass app "time-track (public)" lets anyone reach example.com/time-track + example.com/time-track/_next/*
  · who answers (…)
  ✓ /time-track already redirects to <team>.cloudflareaccess.com, and its public paths answer signed out
…
nothing to create — already bootstrapped
```

Signed out, `/time-track/` (with the slash) still asks for sign-in, because only the exact
`time-track` is public. The owner's fix is the same command with that path listed too:

```sh
portfolio-app bootstrap --repo owner/time-track --path /time-track \
  --public-path /time-track --public-path '/time-track/_next/*' --public-path /time-track/ \
  --clone ../time-track
```

It plans one change, to the public app only:

```
  ✓ Bypass app "time-track (public)" lets anyone reach example.com/time-track + example.com/time-track/_next/*
  + add public destination(s) example.com/time-track/ to Bypass app "time-track (public)" (anyone, no sign-in)
  · who answers (…):
  ·   example.com/time-track → "time-track (public)" — Bypass, anyone
  ·   example.com/time-track/_next/* → "time-track (public)" — Bypass, anyone
  ·   example.com/time-track/ → "time-track (public)" — Bypass, anyone
  ·   example.com/time-track/* (the rest) → "time-track (/time-track)" — Allow, sign-in
  ·   example.com/time-track/<child> → "time-track (/time-track)" (by example.com/time-track/*) — Allow, sign-in; example.com/time-track/ covers its subpaths too and the docs do not rank it against example.com/time-track/*, so apply checks a child and takes example.com/time-track/ out again if one answers signed out
…
1 change(s) planned
```

`verify` for time-track then takes the same three `--public-path` flags.

The Cloudflare Access step, in apply order:

1. **The Allow app**, as for a private app, on `<domain>/<prefix>` and `<domain>/<prefix>/*`,
   plus a narrowing `<domain><path>/*` for each exact public path (above). If `/<prefix>` itself
   is public, that destination is left out, so time-track's Allow app holds only
   `<domain>/time-track/*`. An existing Allow app that holds the base destinations and some or
   none of the narrowing ones is reused, and the plan shows
   `+ add narrowing destination(s) … to "<app>"` for the missing ones. That is the one change the
   tool makes to an existing Allow app, and it only shrinks what is public (the other change it
   makes to an existing app is adding newly listed paths to the public app, step 3). Apply reads
   the app again, refuses if its destinations changed since the plan, and sends it back with
   `PUT` (which replaces the whole app) carrying every current setting, the same policies by id and
   precedence, and only the missing destinations added. It never removes or alters any other
   destination, policy or setting. This comes before the Bypass app, so no child is ever public.
2. **The Bypass policy.** Find or create one reusable policy named `portfolio-app public`, with
   decision *bypass* and the single include rule *Everyone*. If a policy by that name exists and
   is anything else, the plan is a conflict; the tool does not rewrite it.
3. **The Bypass app** `<repo name> (public)` (graph-notes gets `graph-notes (public)`), whose
   destinations are exactly the listed paths, attached to that policy. An existing app holding
   exactly those destinations, all of whose policies are Bypass for Everyone, is reused under
   whatever name it has. An existing `<repo name> (public)` that holds only some of the listed
   paths and nothing else, is Bypass for Everyone and can take a plain `PUT` (self-hosted, its
   paths in `destinations`, all public hostnames) gets the missing ones added, the same way as
   the narrowing destinations: the plan shows `+ add public destination(s) … to Bypass app
   "<app>"`, and apply reads the app again, refuses if it changed since the plan, and sends it
   back with only those destinations added. So a re-run with one more `--public-path` adds only
   that destination.
4. **Wait for both answers.** With `--apply`, it waits until every private probe redirects to
   the Access login and every public path answers signed out (see `verify` below), and stops
   before D1 or GitHub if that never happens. The private probes include a child of each public
   path listed without a `/*`, so a run whose exact public paths open their children despite
   the narrowing destinations stops here, with both Access apps already in place; nothing is
   removed for you (the one exception is a run that added `<prefix>/`, step 5).
   The two apps start answering at different moments, so the first round in which every probe
   passes may hold private answers taken before the Bypass app was live. That round is run
   again at once, and apply goes on only when the second run passes too. If the second run
   fails, the wait carries on like after any failed round, until a round and its second run
   both pass or the attempts run out.
5. **Take `<prefix>/` out again, if this run added it and the wait fails.** This applies only
   to a run that added the trailing-slash destination `<domain>/<prefix>/`, to an existing
   Bypass app or as part of one it created; no narrowing destination protects that path's
   children, so a failed check may mean they are public. Apply reads the Bypass app again and
   sends it back with only `<domain>/<prefix>/` taken out. Everything else the app holds stays,
   no app is deleted, and nothing that existed before the run is removed. Then it runs the
   wait again with the other public paths, and reports all three: the original failure, what
   it removed, and whether the site passes the check without `<prefix>/`. It exits 1 either way,
   and D1 and GitHub are not touched. The error reads `Rollback: removed <domain>/<prefix>/
   from Bypass app "<name>", which keeps …`, naming what the app still holds; the bare
   `<domain>/<prefix>` is always among it, because `/<prefix>/` is planned only alongside
   `/<prefix>`. It ends with `Without /<prefix>/ listed, the site passes the check` or the
   probes that still fail.
   The same removal runs when the write that adds `<domain>/<prefix>/` to an existing Bypass
   app fails, because a `PUT` can go through and still fail (a lost response, or an answer
   that does not list the destination as sent). Apply reads the app again and sends a removal
   only if the app holds `<domain>/<prefix>/` under exactly that name. Otherwise the error says
   `does not hold <domain>/<prefix>/, so nothing was removed`, lists what the app guards, and
   asks you to check it in the dashboard now. If Cloudflare answers the removal with the
   destination still there, the error says `tried to remove … but it still holds it`.
   A failed create of a new Bypass app that would hold `<domain>/<prefix>/` gives no app id to
   work with, so that error says the app may exist all the same and must be checked in the
   dashboard now.
   Before such a run adds `<domain>/<prefix>/`, apply records which private probes ask for
   sign-in. If one of those answers signed out during the wait, the run has made it public, so
   the wait stops at once and the destination comes out, instead of staying public until the
   attempts run out.
   Every other mixed run that fails the wait stops with `Access is not answering for <prefix>: …`
   and leaves the apps it created in place for you to look at.

D1 and GitHub follow as for a private app. Each step only fills in what is missing, so a mixed
run on an app that already has its Allow app, D1 and secrets adds only the narrowing
destinations it lacks, and the Bypass policy and app (or the public destinations its Bypass app
lacks).

**Which app answers which path.** When several Access apps match a request, the one with the
most specific matching destination applies, and within one app Bypass policies are evaluated
before Allow. A listed destination under the prefix is more specific than the Allow app's
`<prefix>/*`, so the Bypass app wins there; a public `/<prefix>` is left out of the Allow app, so
only the Bypass app holds it. The paths under an exact public path would inherit its Bypass, so
its narrowing destination, more specific again, sends them to the Allow app. Everything else
under the prefix goes to the Allow app's `<prefix>/*`. A public `<prefix>/` is the exception: the
paths under it are meant for `<prefix>/*` too, but which of the two wins there is not documented,
and no narrowing destination can decide it, so the plan adds a line saying so. A child of each
exact public path is still probed and must ask for sign-in. The plan prints the intended answer
per destination:

```
  · who answers (the app with the most specific matching destination wins; Bypass lets anyone in, Allow asks for sign-in):
  ·   example.com/time-track → "time-track (public)" — Bypass, anyone
  ·   example.com/time-track/_next/* → "time-track (public)" — Bypass, anyone
  ·   example.com/time-track/* (the rest) → "time-track (/time-track)" — Allow, sign-in
```

and, for graph-notes, after its six Bypass lines:

```
  ·   example.com/graph → "graph-notes (/graph)" — Allow, sign-in
  ·   example.com/graph/public/* (narrowing) → "graph-notes (/graph)" — Allow, sign-in
  ·   example.com/graph/public.txt/* (narrowing) → "graph-notes (/graph)" — Allow, sign-in
  ·   example.com/graph/manifest.webmanifest/* (narrowing) → "graph-notes (/graph)" — Allow, sign-in
  ·   example.com/graph/* (the rest) → "graph-notes (/graph)" — Allow, sign-in
```

A `/*` destination does not cover its own base: `/graph/_next/*` makes `/graph/_next/a.js` public,
not `/graph/_next`.

### Plan, apply, exit codes

**AUD tags.** Each Access app a run creates or finds (the sign-in app and, for a mixed app,
the `<repo name> (public)` Bypass app) is printed with its audience tag, the value the app's
Worker checks Access tokens against. Plan lines for apps that exist end in `(AUD <tag>)`; an
app the plan would create has no tag yet and none is shown. After `--apply`'s Access check the
run prints `AUD tag of Access app "<name>" (created|found): <tag>` for each, so a re-run on an
already bootstrapped app prints them again. The tag is read from the `aud` field of the app
record Cloudflare returns on create and list; if an answer has none the line says so rather
than showing a value. The tag is not a secret; no token or secret value is ever printed.

Without `--apply`, the command prints the plan and changes nothing: planning makes Cloudflare
`GET`s (with the setup token read from the keychain), `gh` reads, `git remote get-url`, and an
*existence* check on the deploy-token keychain entry only. Exit codes: `0`
ok, `1` a step failed, `2` usage error or a conflict in the plan.

**What it refuses or leaves alone.**

- **Refused prefixes:** an empty prefix, `/`, and anything under ai-chat-archive's site-wide
  `/api`, `/_next` or `/vendors`. Also refused: wildcards, dots, queries and uppercase.
- **Conflicts (`✗`), where nothing is applied:**
  - an existing Access app that overlaps the two destinations without matching them exactly
    (for a mixed app: one holding anything beyond the base and narrowing destinations, such as a
    narrowing destination for a path no longer listed);
  - with `--public-path`, an existing Allow app that lacks a narrowing destination but cannot take
    one by a plain `PUT`: one that is not self-hosted, holds its paths in the deprecated
    `self_hosted_domains`, holds a destination that is not a public hostname, or has a Bypass
    policy (a destination added to it would not ask for sign-in);
  - with `--public`, any Access app on the destinations, or a path that already redirects to
    an Access login;
  - with `--public-path`: an app other than the public app that already holds a listed path (a
    destination is never moved from one app to another); an app named `<repo name> (public)`
    that holds anything beyond the listed paths, or only some of them but is not Bypass for
    Everyone or cannot take a plain `PUT`; an app holding exactly the listed paths
    that is not Bypass for Everyone; a Bypass app that opens more of the prefix than the list
    (say `<domain>/graph/api/*` when only `/graph/api/public/*` is listed); a
    `portfolio-app public` policy that is not Bypass for Everyone; and `/<prefix>/` listed
    without `/<prefix>`;
  - a clone of a different repo;
  - placeholder entries that name more than one database; with `--public`, `r2_buckets` that
    name several buckets, an entry with no `bucket_name` or an invalid one, or a
    `wrangler.jsonc` with no `name` for the routes;
  - a Worker route that reaches into the prefix and is not the app's own: one owned by another
    Worker or by none (another Worker's `<domain>/<prefix>*`, `<domain>/<prefix>` or
    `<domain>/<prefix>/*` included), or one of the app's Worker that also matches paths outside
    the prefix and is not `<domain>/<prefix>*` (`<domain>/work/*` for `/work/election-atlas`);
  - with `--public`, a setup token that may not read R2 buckets, the zone, its Worker routes
    or the Worker scripts (the line names the permission; see the keychain table);
  - a missing deploy-token keychain entry.
- **Reported but left alone (`!` or `·`):**
  - a policy whose emails differ from the config;
  - an exact-match Access app that is not attached to the policy;
  - a broader route of another Worker, such as the home site's `<domain>/*`: the app's longer
    pattern is more specific, so Cloudflare sends the prefix to the app;
  - a route on a sibling path that `<domain>/<prefix>*` also matches, such as
    `<domain>/work/election-atlas-old/*`: it is more specific, so it keeps its paths;
  - with `--public-path`, a Bypass app above the prefix (such as one on `<domain>`): the app's
    own destinations are more specific, so it does not reach the private paths.

  Changing who can sign in is a decision for the Zero Trust dashboard, not for a re-run.

## The Worker route and its pass-through

Cloudflare matches a Worker route against the whole request URL, **query string included**, and
a route pattern cannot contain a query; its only wildcards are a leading `*` on the host and a
trailing `*` on the path. So the exact route `<domain>/<prefix>` never matches
`/<prefix>?<query>`, and `<domain>/<prefix>/*` needs the slash: `/graph?g=rag` matched no
route and went to the zone's origin, GitHub Pages, which answered 404 (seen for every sub-app
on 2026-11-15). The only pattern that matches it is **`<domain>/<prefix>*`**, so that is the one
route every app's Worker gets: from `bootstrap --public`, and in the app's own
`frontend/wrangler.jsonc` for the others:

```jsonc
"routes": [{ "pattern": "akshayprabhakant.com/graph*", "zone_name": "akshayprabhakant.com" }],
```

More specific routes still win under it (`/graph/api/*` on the API Worker, `/_site/*` on
site-shell). But `<prefix>*` also matches every sibling path that starts with the prefix:
`/graphfoo`, `/graphs`, `/graph.html`, `/graph-x/y`. **The app's Worker must pass each of those
to the origin untouched**, returning `fetch(req)` for any path that is neither `/<prefix>` nor
under `/<prefix>/`. A Worker's subrequest to its own zone goes to the origin and does not
re-enter the Worker, and `fetch(req)` forwards the incoming request itself (method, headers,
body; a 3xx comes back as is). At the top of the Worker's `fetch`, after any `/_site/*` forward
(under `wrangler dev` the Worker stands in for site-shell's route), with `PREFIX` the app's
prefix as a pattern such as `/^\/graph(?=\/|$)/`:

```js
const u = new URL(req.url);
const ours = PREFIX.test(u.pathname);            // /graph or /graph/…, not /graphfoo
u.pathname = u.pathname.replace(PREFIX, '') || '/';
if (u.pathname.startsWith('/_site/') && env.SHELL) return env.SHELL.fetch(new Request(u, req));
if (!ours) return fetch(req);                     // /graphfoo etc.: the origin, untouched
```

Then `/<prefix>?<query>` reaches the app. Whether it serves that in place or redirects to
`/<prefix>/?<query>` (keeping the query) is the app's choice: redirect only when `/<prefix>` and
`/<prefix>/` have the same Access (both signed in or both public) and the app's canonical URL
has the trailing slash; otherwise serve in place (so a mixed app with `--public-path /<prefix>`
but not `/<prefix>/` serves in place). To redirect, test the request's own path: after the
rewrite above, `u.pathname` is `/` for both `/graph` and `/graph/`:

```js
const { pathname, search } = new URL(req.url);
if (pathname === '/graph' && search) {
  return new Response(null, { status: 308, headers: { location: `/graph/${search}` } });
}
```

**Access does not change.** An Access app keeps exactly `<domain>/<prefix>` and
`<domain>/<prefix>/*`: Access already matches `/<prefix>?<query>` with the `<prefix>`
destination (signed out, `/graph?x=1` redirects to the login like `/graph`), and widening it to
`<prefix>*` would put `/<prefix>foo` behind sign-in. Those sibling paths stay public, as before.
They now count as Worker requests (the free plan allows 100,000 a day); they are 404s nobody
links to.

## `verify`: the login check for deploy jobs

```sh
portfolio-app verify --path /<prefix> --domain <host> [--team <team>] [--public-path <path>…]
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

**A mixed app** passes the same `--public-path` list it was bootstrapped with, and then the check
goes both ways. The probes run in this order:

1. The private probes, each of which must redirect to the Access login: `/<prefix>`,
   `/<prefix>/portfolio-app-verify`, `/<prefix>/api/portfolio-app-verify`, then
   `<path>/portfolio-app-verify` for each public path listed without a `/*`, in the order
   listed. Any of these that a listed public path covers is left out (`/<prefix>` when it is
   public, a child when `<path>/*` is listed too), and one that appears twice is requested once.
2. The public probes, in the order listed, each of which must answer without a redirect to any
   Access login: an exact path as itself, a `<dir>/*` path as `<dir>/portfolio-app-verify`. The
   app's own 404 counts as an answer.

For graph-notes' six paths that is `/graph`, `/graph/portfolio-app-verify`,
`/graph/api/portfolio-app-verify`, `/graph/public/portfolio-app-verify`,
`/graph/public.txt/portfolio-app-verify` and `/graph/manifest.webmanifest/portfolio-app-verify`
as private, then the six public probes. The three children pass only because the Allow app holds
their narrowing destinations. For time-track, the child of the public `/time-track` is
`/time-track/portfolio-app-verify`, which is already a private probe, and so is the child of
`/time-track/`. With `/time-track/` listed, time-track's probes are
`/time-track/portfolio-app-verify` and `/time-track/api/portfolio-app-verify` as private, then
`/time-track`, `/time-track/_next/portfolio-app-verify` and `/time-track/` as public. Without
`--public-path`, `verify` behaves exactly as before.

**Limit:** a public path that answers a bare `401` or `403` fails the check. Access can answer
that way, and so can Cloudflare's bot protection when it refuses an automated client; from a
signed-out request without a browser the two look the same, so the line says to open the path in
a private browser window. The private probes need a real 3xx to the login, so a bot-protection
`403` can never pass a private path.

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
| Account · Access: Apps and Policies · Edit | every run: Access policies and apps (a `--public` run only reads the apps) |
| Account · D1 · Edit | every run: the D1 database |
| Account · Workers R2 Storage · Edit | `--public` when the clone binds an R2 bucket |
| Zone · Zone · Read, on the site's zone | `--public`: finding the zone id for the routes |
| Zone · Workers Routes · Edit, on the site's zone | `--public`: listing, creating and replacing (deleting the old pair of) the route |
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

- **AUD tag:** an application record carries a read-only `aud` string ("Audience tag", at most
  64 characters) in the create, list and get answers. Checked against Cloudflare's published
  OpenAPI schema (`cloudflare/api-schemas`, `openapi.json`, read 2026-11-25): the
  `GET /accounts/{account_id}/access/apps` item schema has `aud`. The tests' Cloudflare stand-in
  mints one on create, as the API does; no live account call has confirmed it.
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

### Mixed apps: Bypass policies and overlapping apps (read 2026-11-15)

- **Bypass for Everyone:** a reusable policy's `decision` may be `allow`, `deny`, `non_identity`
  or `bypass`. The Everyone rule is `{"everyone": {}}`, "an empty object which matches on all
  users".
- **Order inside one app:** "Bypass and Service Auth policies are evaluated first", then Block
  and Allow.
- **Overlapping apps:** the application-paths page says, in full: "When multiple rules are set
  for a common root path, the more specific rule takes precedence. For example, when setting
  rules for `dashboard.com/eng` and `dashboard.com/eng/exec` separately, the more specific rule
  for `dashboard.com/eng/exec` takes precedence, and no rule is inherited from
  `dashboard.com/eng`. If no separate, specific rule is set for `dashboard.com/eng/exec`, it
  will inherit any rules set for `dashboard.com/eng`." On wildcards: "Using a wildcard in the
  **Path** field does not cover the parent path nor the apex domain", so `example.com/alpha/*`
  does not cover `example.com/alpha`.
- **What the last sentence means for an exact public path:** a Bypass destination without a
  wildcard, such as `<domain>/graph/public`, also applies to `<domain>/graph/public/x`. That
  child also matches the Allow app's `<domain>/graph/*`, and the page does not say which of the
  two is "more specific".
- **Inheritance confirmed on the live account, 2026-11-15.** With a Bypass app holding the exact
  path `graph/public` and the Allow app holding `graph/*`, `graph/public/<anything>` answered
  signed out: the exact Bypass destination won. The tool's child probe caught it and stopped
  `--apply`, as designed. With the owner's approval, `graph/public/*`, `graph/public.txt/*` and
  `graph/manifest.webmanifest/*` were then added to the existing Allow app
  `graph-notes (/graph)`; after that, a child of each exact public path redirected to the Access
  login while the exact paths stayed public. So the more specific `<path>/*` beats the inherited
  exact destination, and the tool now adds that narrowing destination itself for every exact
  public path but the bare prefix and `<prefix>/` (next section), whose children the Allow
  app's own `<prefix>/*` already answers. It still probes a child of each one, in `--apply`
  and in `verify`.
- **Updating an app:** `PUT /accounts/{account_id}/access/apps/{app_id}` replaces the app with
  the body sent; it is not a patch. So the tool reads the app with
  `GET /accounts/{account_id}/access/apps/{app_id}` and sends back all of it, with policies as
  `{ id, precedence }` links and only its destinations changed: the narrowing ones added to the
  Allow app, the newly listed public ones added to the Bypass app, or `<prefix>/` taken out of
  the Bypass app again (step 5 of the mixed app's Access step).
- **Not answered by the docs:**
  - which app applies when two apps hold the same destination. The tool never creates one:
    a listed destination held by any other app is a conflict.
  - whether the apps list always carries policies inline. Bypass detection reads a policy
    inline when it is there and otherwise looks it up by id in the policy list.
- **Confirmed against the live account:** the inheritance above, and that a narrowing
  `<path>/*` on the Allow app beats it (2026-11-15). The rest is from the docs only.
- Sources:
  - [reusable policy create](https://developers.cloudflare.com/api/resources/zero_trust/subresources/access/subresources/policies/methods/create/)
  - [Access policies, order of execution](https://developers.cloudflare.com/cloudflare-one/access-controls/policies/)
  - [application paths](https://developers.cloudflare.com/cloudflare-one/access-controls/policies/app-paths/)
  - [update app](https://developers.cloudflare.com/api/resources/zero_trust/subresources/access/subresources/applications/methods/update/)

### The trailing-slash path `<prefix>/` (read 2026-11-15)

- **A trailing-slash destination is a path like any other.** The API's public destination is
  `{ "type": "public", "uri" }`, where `uri` is "The public hostname and optional path to
  secure." The application-paths page lists what Access paths do not support, under
  "Unsupported URLs": port numbers, query strings ("Query strings (such as `?foo=bar`) are not
  supported in Access application paths") and anchor links. A trailing `/` is not among them,
  and the only wildcard limits are "At most one wildcard in between each slash in the **Path**".
  Neither page mentions a trailing slash anywhere, so the docs neither forbid `<domain>/<prefix>/`
  nor describe it. The tool maps `/<prefix>/` to exactly that destination.
- **Access does not fold `/x/` into `/x`.** Cloudflare's Gateway docs say "Gateway ignores trailing
  forward slashes (`/`) in URLs", but that is Gateway's URL lists, not Access. On the live account
  (2026-11-15), with the Bypass app holding exactly `time-track` and the Allow app `time-track/*`,
  `/time-track` answered 200 signed out and `/time-track/` redirected to the Access login. If
  Access ignored the slash, both would have answered the same way. The answer shows that
  `time-track/*` covers `/time-track/` and beats the `time-track` that `/time-track/` would
  otherwise inherit.
- **A destination covers its subpaths.** The API's destination override field says "Unlike the
  `uri` in public destinations, override path patterns do not implicitly cover subpaths; to do
  that, use a wildcard", so a public destination's `uri` does implicitly cover them. This matches
  the application-paths page ("If no separate, specific rule is set for `dashboard.com/eng/exec`,
  it will inherit any rules set for `dashboard.com/eng`") and the live inheritance noted above.
  So `<prefix>/` in the Bypass app also applies to `<prefix>/<child>`.
- **Not answered by the docs:** whether `<prefix>/` or the Allow app's `<prefix>/*` is "the more
  specific rule" for `<prefix>/<child>`. The page ranks only a parent against a deeper child
  (`dashboard.com/eng` against `dashboard.com/eng/exec`), and nowhere ranks an exact destination
  against a wildcard one at the same depth. `<prefix>/*` is one character longer, which is how it
  beat the exact `time-track` for `/time-track/`, but the docs never say that length decides. A
  narrowing destination cannot settle it, since `<prefix>/`'s would be `<prefix>/*` itself. So
  the tool relies on its probes. `<prefix>/portfolio-app-verify` and
  `<prefix>/api/portfolio-app-verify` must still redirect after `<prefix>/` is added. If they
  answer signed out, the run that added it takes `<prefix>/` out of the Bypass app again at
  once (step 5 of the mixed app's Access step) and exits 1. It removes that one destination and
  nothing else.
- **Also documented, not used:** a public destination can carry `overrides:
  [{ "behavior": "public", "path_pattern" }]`, "Rules that override how Access handles requests to
  this destination. Each rule can make a matching path public, bypassing Access authentication",
  whose patterns do not cover subpaths. That would make an exact path public on the Allow app
  itself without a Bypass app, but it is a different design from this tool's Bypass app, and the
  guides do not describe it, only the API reference, so it is noted here and not used.
- **Not confirmed against the live account:** whether `<prefix>/` actually wins for
  `<prefix>/` itself, and whether `<prefix>/*` wins for its children. The first `--apply` with
  `/time-track/` will show both, and it takes `time-track/` out again if the children lose.
- Sources:
  - [application paths](https://developers.cloudflare.com/cloudflare-one/access-controls/policies/app-paths/) (Unsupported URLs, Policy inheritance, Limitations)
  - [create app: `destinations[].uri` and `overrides[].path_pattern`](https://developers.cloudflare.com/api/resources/zero_trust/subresources/access/subresources/applications/methods/create/)
  - [Gateway lists: URL slashes](https://developers.cloudflare.com/cloudflare-one/reusable-components/lists/), Gateway only

### R2 buckets and Worker routes (read 2026-11-02)

- **R2:** `GET /accounts/{account_id}/r2/buckets/{bucket_name}` returns one bucket;
  `POST /accounts/{account_id}/r2/buckets` with `{ "name" }` creates one and needs
  `Workers R2 Storage Write`. A missing bucket answers 404, which the plan reads as "create".
- **Routes:** `GET`/`POST /zones/{zone_id}/workers/routes`, items `{ id, pattern, script }`,
  and `DELETE /zones/{zone_id}/workers/routes/{route_id}`, needing `Workers Routes Read`/`Write`.
  The zone id comes from `GET /zones?name=<domain>`, which lists only zones the token can read,
  so an empty answer means a missing Zone Read.
- **Routes need a deployed Worker:** the create docs do not say so, but creating a route for a
  missing script is reported (in a Stack Overflow question, not the docs) to fail with
  `10019: workers.api.error.invalid_route_script_missing`.
  So the plan checks `GET /accounts/{account_id}/workers/scripts` first and defers the routes
  until the Worker exists. **Not confirmed against a live account.**
- **Precedence:** when several route patterns match a request, the most specific one wins,
  which is why a broader route such as the home `<domain>/*` is left alone.
- **Query strings (read 2026-11-15):** "Route pattern matching considers the entire request
  URL, including the query parameter string. Since route patterns may not contain query
  parameters, the only way to have a route pattern match URLs with query parameters is to
  terminate it with a wildcard, `*`." Hence the one route `<domain>/<prefix>*`.
- Sources:
  - [create route](https://developers.cloudflare.com/api/resources/workers/subresources/routes/methods/create/)
  - [list routes](https://developers.cloudflare.com/api/resources/workers/subresources/routes/methods/list/)
  - [delete route](https://developers.cloudflare.com/api/resources/workers/subresources/routes/methods/delete/)
  - [routes: matching behavior](https://developers.cloudflare.com/workers/configuration/routing/routes/)
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

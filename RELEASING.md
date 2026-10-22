# Releasing

One repo, one npm package per skill, independent versions. Nothing publishes on a merge:
a release is a **manual dispatch** of `.github/workflows/release.yml`.

## One-time setup — a Trusted Publisher per package

For each package under `packages/`, on npmjs.com ([Trusted Publishing](https://docs.npmjs.com/trusted-publishers)):

| Field | Value |
|---|---|
| Repository | `akshayDev17/my-skills-package` |
| Workflow filename | `release.yml` |
| Package | e.g. `coursework-author` |
| Allow npm publish | **check it** |

No long-lived token exists anywhere; the workflow exchanges a short-lived OIDC token.

## Releasing a package

1. Actions → **release** → *Run workflow*.
2. Pick the `package`, and a `bump` (`patch` / `minor` / `major`) — or set an explicit
   `version`, which overrides the bump.
3. The workflow computes the next version from the latest `<package>@<semver>` tag
   (falling back to `package.json`), bumps that package's three manifests **and its pin in
   both marketplace catalogues**, publishes, waits for the registry to confirm the version
   is readable, and only then commits + tags + pushes.

Publishing before tagging is deliberate: a failed publish must not consume a version — and
nothing is committed until the registry answers for the version, so a publish that consumed
a version without shipping it cannot be papered over by a tag. The wait is not paranoia: npm
answers 404 for a version it accepted seconds earlier (*"your package is being processed and
may take a few minutes to become available"*), so the workflow polls for up to 12 minutes.

If a run dies after publishing but before its bookkeeping, re-dispatch with an explicit
`version` equal to the published one: the workflow sees it already on the registry, skips the
publish, and finishes the commit + tag.

## Tag convention

`<package>@<semver>` — e.g. `coursework-author@1.0.0`. Per-package tags keep
`git describe --match "$PKG@*"` unambiguous when several packages carry different versions.

## Adding a skill

1. `packages/<skill>/` with `package.json`, `plugin.json`, `.claude-plugin/plugin.json`,
   `cordis.patch.yml`, `lib/index.js`, `skills/<skill>/SKILL.md`.
2. Add a `plugins[]` entry for it in **both** catalogues — `.claude-plugin/marketplace.json` and
   `.agents/plugins/marketplace.json` — with `source: { source: "npm", package: "<skill>",
   version: "^<current>" }`. A package that is not listed there cannot be installed on Claude,
   Codex or Antigravity at all, and `scripts/check-marketplace-pins.mjs` fails CI until it is.
3. Add `<skill>` to the `package` options in `.github/workflows/release.yml`.
4. Register its trusted publisher (above).

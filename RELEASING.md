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
   (falling back to `package.json`), bumps that package's three manifests, **publishes
   first**, and only then commits + tags + pushes.

Publishing before tagging is deliberate: a failed publish must not consume a version.
The reverse is also guarded — the workflow asks the registry for the version it just
published and fails if it isn't there, so a silent no-op cannot look like a release.

## Tag convention

`<package>@<semver>` — e.g. `coursework-author@1.0.0`. Per-package tags keep
`git describe --match "$PKG@*"` unambiguous when several packages carry different versions.

## Adding a skill

1. `packages/<skill>/` with `package.json`, `plugin.json`, `.claude-plugin/plugin.json`,
   `cordis.patch.yml`, `lib/index.js`, `skills/<skill>/SKILL.md`.
2. Add `<skill>` to the `package` options in `.github/workflows/release.yml`.
3. Register its trusted publisher (above).

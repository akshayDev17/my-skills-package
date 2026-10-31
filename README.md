# my-skills-package

A shelf of skill plugins — **one npm package per skill**, each installable on Claude Code,
Codex, Gemini/Antigravity and DeepSeek Harness. Adding a skill means adding a directory under
`packages/`; it does not touch its neighbours, and each package carries its own version.

| package | what it does |
|---|---|
| [`coursework-author`](./packages/coursework-author) | Author a structured, module-based self-study course on any topic: syllabus blueprint, one README per module, a fixed module anatomy, then two verification gates (a memory-less logic review and a literature survey). |
| [`@akshaydev17/portfolio-app`](./packages/portfolio-app) | Bootstrap a sub-app of the personal site in one command: its Cloudflare Access app for the path, its D1 database id, and its GitHub `production` secrets. Plans by default and applies on `--apply`. Also ships `verify`, a token-free login check that deploy jobs run first. Not yet published to npm. |

## Install

Every host installs the **same** npm package and reads only its own manifest surface out of it.
The marketplace catalogue that lists these packages lives in this repo, because Claude Code
accepts npm as a *plugin* source but not as a *marketplace* source.

**Claude Code**

```sh
claude plugin marketplace add akshayDev17/my-skills-package
claude plugin install coursework-author@akshaydev17-skills
```

**Codex**

```sh
codex plugin marketplace add akshayDev17/my-skills-package
codex plugin add coursework-author@akshaydev17-skills
```

**DeepSeek Harness**

```sh
dsh plugin --profile web add coursework-author
```

**Gemini / Antigravity**

```sh
agy plugin install https://github.com/akshayDev17/my-skills-package
```

Verify what actually loaded, rather than trusting the install:

```sh
claude plugin details coursework-author@akshaydev17-skills     # component inventory
claude plugin list                                             # version + enabled
```

## The pair is optional, in both directions

`coursework-author` is also shipped *inside*
[`explainer-video-from-coursework`](https://github.com/akshayDev17/ai-explainer-video): that
plugin vendors a copy of this skill so one install gives you both — author the coursework, then
turn it into videos. The copy is generated (`scripts/sync-coursework-author.mjs` over there) and
gated in that repo's CI, never hand-edited.

Neither direction is forced. Installing the video plugin does not install this package, and
installing this package does not pull in the video toolchain. You get the pairing by choosing
the video plugin; you get a standalone authoring skill by choosing this one.

## Layout

```
packages/<skill>/                 one npm package per skill
  package.json                    dsh.bundle.patch → cordis.patch.yml
  plugin.json                     Agent Plugins manifest (Codex, Gemini/Antigravity)
  .claude-plugin/plugin.json      Claude Code manifest
  cordis.patch.yml + lib/index.js the DSH bundle plugin (serves this package's skills/)
  skills/<skill>/SKILL.md         the skill itself
.claude-plugin/marketplace.json   Claude Code catalogue
.agents/plugins/marketplace.json  Codex / Antigravity catalogue
scripts/                          version bump, release helpers, CI gates
```

Both catalogue files pin the version a host installs from npm (`source: npm`, `version: ^x.y.z`).
`scripts/bump-package.mjs` rewrites the pin during a release and
`scripts/check-marketplace-pins.mjs` fails CI when a pin disagrees with its package — a
hand-maintained range rots without a symptom, which is exactly what happened in the video repo.

## Releasing

Nothing publishes on a merge. A release is a manual dispatch of `release.yml`, one package at a
time, with an independent version and a `<package>@<semver>` tag — see
[`RELEASING.md`](./RELEASING.md).

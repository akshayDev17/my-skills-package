# graph-notes (Claude Code plugin)

Lets Claude Code read and edit your graphs in the
[graph-notes](https://akshayprabhakant.com/graph) app. A graph is a knowledge graph of notes,
links and clusters, kept as an Obsidian canvas. The plugin bundles two things:

- **The graph-notes MCP server** (`server/`), a local stdio server (Python, FastMCP) that Claude
  Code starts from this plugin's `.mcp.json`. Its tools list, read, create, copy and rename
  graphs; add, change, remove, link and unlink notes; make web-page cards and edit web-page and
  file cards; change links in place; set colours, card borders, shapes and text alignment, and
  link line styles, paths and sides; move and resize notes and boxes; draw, nest, rename, refit,
  collapse and remove group boxes; browse history and undo or redo by restoring a version; make
  a graph public or private; download every graph for Obsidian; and move graphs to the trash and
  back. Every edit the web app's editor can make has a tool. No tool deletes a graph forever.
  The model works with notes, links and boxes, and the server places new cards on the canvas
  while leaving everything else as it was (a box it draws or refits moves only the notes in its
  way, and says which). Every change names the version it was made from, so a
  change made from an outdated copy saves nothing and never overwrites newer work.
- **A skill** (`skills/graph-notes/SKILL.md`) that teaches the model what a graph is (an
  Obsidian canvas: cards, edges, tag clusters, group boxes) and how to edit one well through
  those tools: how to group a graph's parts in titled, collapsible, nestable boxes, style and
  move things, and undo a change.

The server's code is bundled in the plugin, so you need no access to the graph-notes repo and
no clone. Works in Claude Code only.

## Requirements

- [Claude Code](https://code.claude.com).
- [uv](https://docs.astral.sh/uv/). It runs the bundled server, and fetches Python 3.11+ and the
  locked dependencies on first start.
- [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/),
  for the sign-in. On macOS: `brew install uv cloudflared`.
- **An account that the site's sign-in lets in.** Installing the plugin gives nobody access. Until
  the app's Cloudflare Access sign-in accepts your email, every tool answers with a sign-in
  message.

## Install

```sh
claude plugin marketplace add akshayDev17/my-skills-package
claude plugin install graph-notes@akshaydev17-skills
```

Then restart Claude Code. `claude plugin details graph-notes@akshaydev17-skills` shows what
loaded: one MCP server and one skill. The first start builds the server's environment with uv,
which takes a few seconds. The environment goes in the plugin's data directory
(`~/.claude/plugins/data/…`, through `UV_PROJECT_ENVIRONMENT`), which survives plugin updates,
and uv rebuilds it when the bundled lock changes. If `/mcp` shows the server failed on that first
start, reconnect it once.

Claude Code runs `uv` from its own `PATH`. If it was started from the desktop rather than a
shell and cannot find `uv`, start it from a terminal or put `uv` in a system path such as
`/usr/local/bin`. The server itself also looks for `cloudflared` in `/opt/homebrew/bin` and
`/usr/local/bin`.

## Signing in (24 hours)

There is nothing to set up. If there is no saved sign-in, the first tool call opens your browser
on the app's Cloudflare Access sign-in (your email, then the code sent to it) and waits up to
five minutes. `cloudflared` keeps the session under `~/.cloudflared` for **24 hours** and the
server reuses it. After that, the next call opens the browser again. To sign in ahead of time:

```sh
cloudflared access login https://akshayprabhakant.com/graph/signin
```

The session stays in the server's memory and goes only into the request header. It is never
printed, logged or written anywhere. `GRAPH_NOTES_URL` points the server at another deployment
of the app.

## Use

Ask for what you want, for example "add what we just decided to my `example` graph" or "show me
the notes in the `reading-list` graph". The tools appear as
`mcp__plugin_graph-notes_graph-notes__<tool>`.

## How the copy is kept in step with graph-notes

`server/` is a copy of the private graph-notes repo's `mcp-server/` directory, which is where the
server is developed. The copy is never edited here.

- **Copied from:** graph-notes commit `63cba6cbf5e7b9b1291f9ea7dc06b4a5029fbdbf`
  (graph-notes PR 25, after which the server signs in at `/graph/signin`, since `/graph` itself
  is now the public gallery and no longer asks for a sign-in).
- `SERVER_SOURCE` records that commit and a sha256 for every file in `server/`. CI
  (`scripts/check-path-plugins.mjs`) fails if `server/` differs from it in any way: a file
  changed, added or removed by hand. It also fails if this README names a different commit.

To refresh the copy after a change to the server in graph-notes:

1. Merge the change in graph-notes and update your checkout of it.
2. From this repo, run `scripts/refresh-graph-notes-server.sh <graph-notes checkout> [<commit>]`
   (the default commit is `HEAD`). It takes `mcp-server/` exactly as committed at that commit,
   runs graph-notes' own test suite against that copy (in a temporary directory, next to the
   same commit's `backend/`, which the offline tests run against), replaces `server/` and
   rewrites `SERVER_SOURCE`. It writes nothing into the graph-notes checkout.
3. Put the new commit in the "Copied from" line above, bump `version` in
   `.claude-plugin/plugin.json` (installed copies update only when it changes), update the
   skill if the tools changed, and run `node scripts/check-path-plugins.mjs`.
4. Open a PR here. Merging it is the release: the marketplace serves this directory from the
   default branch, and nothing is published to npm.

## Tests

- `test/test_plugin.py` runs the exact command in `.mcp.json`, with the plugin root and data
  directory filled in as Claude Code does. A real MCP client talks to it over stdio, and the
  server reads a graph from a stand-in app on a local port through a stand-in `cloudflared`.
  CI runs it:

  ```sh
  uv run --frozen --project plugins/graph-notes/server pytest plugins/graph-notes/test
  ```

- The server's own test suite (`server/tests/`) runs the real graph-notes API code, which lives
  in the private repo. So it runs in graph-notes' CI and on every refresh (step 2 above), not in
  this repo.
  `GRAPH_NOTES_LIVE=1 uv run pytest tests/test_live.py -s`, run from a graph-notes checkout,
  exercises every tool against the live app with your saved sign-in. It touches only
  `mcp-test-*` graphs that it creates, and leaves them in the trash.

## Layout

```
.claude-plugin/plugin.json  the plugin manifest
.mcp.json                   starts server/ with uv from ${CLAUDE_PLUGIN_ROOT}, its environment in ${CLAUDE_PLUGIN_DATA}
skills/graph-notes/         the skill: what a graph is, and how to edit one well
server/                     the copy of graph-notes' mcp-server/ (never hand-edited)
SERVER_SOURCE               the commit server/ was copied from, and a sha256 per file
test/                       the plugin's own offline test
```

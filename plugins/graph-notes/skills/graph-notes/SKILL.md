---
name: graph-notes
description: Use when the person asks to look at, create, add to, reorganise or fix one of their graph-notes graphs (knowledge graphs drawn as Obsidian canvases in the graph-notes app), to box or group parts of one, or to turn notes, a document or a conversation into a graph, through the graph-notes MCP tools (list_graphs, read_graph, add_notes, add_group_boxes and the rest).
---

# graph-notes: reading and editing graphs well

The `graph-notes` MCP server bundled with this plugin reads and edits the person's graphs in
the graph-notes web app. Its tools appear in Claude Code as
`mcp__plugin_graph-notes_graph-notes__<tool>`; this skill names them by `<tool>` alone.

## What a graph is

A graph is an **Obsidian canvas**: a [JSON Canvas](https://jsoncanvas.org) document of cards
(`nodes`, each with a position and size) and arrows between them (`edges`). The app stores the
canvas exactly as Obsidian would write it, and the person can download any graph and open it in
Obsidian. These tools never show you that drawing. A read gives you the graph as plain parts,
and an edit names the parts to change. The server places new cards on the canvas and keeps
every existing card's position, size, colour and style as they were. The one exception is
drawing or refitting a group box, which moves notes only where the box needs room, and says
which.

| Part | On the canvas | What you see and send |
|---|---|---|
| **Graph** | one canvas | `slug` (its permanent address, never changes), `title`, `version`, `private`/`public`, `url` |
| **Note** | a text card | `id` (stable), `text` (Markdown), `clusters` |
| **Link** | an edge | `id`, `from_note` → `to_note`, optional `label`, `direction`: `forward` (default), `both`, `none` |
| **Cluster** | `#tags` on a card's last line, or a labelled group box | a name, lowercase with `-`; a note can be in several |
| **Group box** | a group node | `id`, `label`, the notes it `contains`, `collapsed`; its label names a cluster of the cards inside it |
| **Other cards** | web-page and file cards | read, link and remove them; you cannot create them |

Cluster names are stored as a final line of `#tags`, as in Obsidian. Pass them in
`clusters` and never type `#tags` into a note's text. A group box labelled "Backend" and the
tag `#backend` are the same cluster. A new note with a box's cluster name goes inside that box
when there is room.

The server's own `about_graphs` tool returns its guide to this model. Call it if anything
here seems to disagree with what the tools do; the server is the authority.

## The workflow

1. **Find the graph**: `list_graphs` returns the person's graphs (`mine`) and other people's
   public graphs (`public_from_others`, which you can read and copy but not change). Use
   `create_graph` only when the person wants a new one.
2. **Read before you write**: `read_graph` returns the notes, links, clusters and the current
   `version`. Read it in full before you add anything, so that you do not add a note the graph
   already has, invent a cluster name close to an existing one, or link a pair that is
   already linked.
3. **Make the change in as few calls as possible.** Each call that changes notes or links saves
   one new version, and the history is what the person browses later. Put related changes in
   one call. `add_notes`, `update_notes`, `remove_notes`, `link_notes`, `unlink_notes`,
   `add_group_boxes`, `update_group_boxes` and `remove_group_boxes` all take lists.
4. **Box the parts**: when the graph has parts (the stages of a pipeline, say), finish with one
   `add_group_boxes` call with a box per part.
5. **Report back**: say what changed (notes added, links, clusters, boxes, notes that moved)
   and give the graph's `url`.

## Group boxes: titled, collapsible parts of a graph

A group box is a labelled rectangle around some notes. It shows a stage or a part of the graph
at a glance (the stages of a pipeline, the layers of a system, the sides of an argument), and a
collapsed box folds that part down to its title until the reader opens it. `read_graph` lists
the boxes in `group_boxes`, each with its `id`, `label`, the notes it `contains` and whether it
starts `collapsed`. All three box tools take a list and save one version per call.

- **Create**: `add_group_boxes` draws boxes, each with a `label` around the `notes` you name
  (their ids), optionally `collapsed: true`. Draw boxes after the notes and their links exist,
  so take the ids from the result of `add_notes` or from `read_graph`.
- **Rename**: `update_group_boxes` with `id` and a new `label`. That renames the box's cluster
  too. The box stays where it is and only widens if the new label needs more room.
- **Refit**: `update_group_boxes` with `id` and `notes`, the full new set it should hold. The
  box is refitted around exactly those notes.
- **Collapse or open**: `update_group_boxes` with `id` and `collapsed`. `true` makes the box
  start collapsed in the web app: only its label shows, and its notes and their links stay
  hidden until the reader clicks the label. `false` makes it start open.
- **Remove**: `remove_group_boxes` with the box ids. The notes stay where they are, with their
  links, and leave only the box's cluster. A link drawn to the box itself is removed with it.

A box holds exactly the notes inside it. So the server sizes each box to its notes and, when
other notes or another box are in the way, moves notes until each box holds only its own and no
two boxes overlap. The result lists every note that `moved`, plus any `notices`; tell the person
what moved.

To box a graph well:

- **One box per part, all in one `add_group_boxes` call**, as the last step of building a graph
  that has parts. Every note in a part goes in its box; a note that belongs to no part stays
  outside all of them.
- **Title each box with a short name for the part** ("Ingest", "Training", "Serving"), not a
  sentence. The title is all a reader sees of a collapsed box, and it is also the cluster name.
- **The box is the cluster.** A box already makes its notes a cluster named after its label, so
  there is no need to add that name to their `clusters` too. Reuse an existing cluster's name as
  the label when the box shows that cluster.
- **Collapse for detail, not for the main path.** Keep the parts a reader needs first open, and
  start collapsed the boxes that hold detail they open on demand (background, alternatives,
  appendices). Collapse only when the person asks or the graph is large enough that it helps.
- **Boxes do not nest or overlap.** If two parts share a note, put it in one box and link it to
  the other part.

## Versions: never overwrite newer work

Every tool that changes a graph takes `version`, the version number you last saw. Take it from
`list_graphs`, from `read_graph`, or from the result of your previous change, which returns
the new version. Chain those results: the version returned by one change is the version you
pass to the next one.

If the graph changed in the meantime, because the person edited it in the browser for example,
**nothing is saved**. The tool answers that the graph changed and includes its current content.
When that happens:

- Read the content you were given. Do not just retry with the new number.
- Decide whether your change still makes sense against what is there now. If it does, make it
  again from the new version. If the person's edit conflicts with it, ask them.

History is never lost. `list_history` lists the versions, `read_graph` with `at_version` shows
an old one, and `restore_version` makes an old version the newest again. The restore is
itself a new version, so it can be undone too.

## Writing notes that read well

- **One idea per note.** Start with a short line that says what the note is (a `## heading` or
  a bold phrase), then a few lines of detail. Split long text into several linked notes rather
  than one large card.
- **Add notes together with their links**, in one `add_notes` call. Give each new note a short
  `key` (`"ingest"`, `"retry-policy"`) and use those keys, or the ids of existing notes, as
  `from_note`/`to_note` in `links`. Notes that arrive with their links are laid out left to
  right along them, so the graph reads in the direction of the arrows. Use `link_notes` only
  for notes that are already in the graph.
- **Place notes deliberately.** `near` = the id of an existing note puts a new note beside it.
  Without `near`, a note goes beside the notes it links to or shares a cluster with.
- **Label links with a short verb phrase** that reads from `from_note` to `to_note`: "depends
  on", "causes", "is an example of". Point the arrow the way the sentence reads. Use `both` for
  a symmetric relation and `none` for a plain association.
- **Reuse clusters.** Check the `clusters` that `read_graph` lists and pick an existing name
  before you make a new one. A cluster that holds a single note is usually noise.
- **`update_notes` replaces the clusters list.** `clusters` is the note's full new list (an
  empty list removes it from all of them). Leave `clusters` out to keep them, and leave `text`
  out to keep the text.

## Changes that need the person's say-so

- **Removing content they wrote.** `remove_notes` also removes every link attached to those
  notes. The old version stays in the history, but confirm before you remove notes you did not
  add in this conversation.
- **Visibility.** `set_visibility` to `public` lets anyone view the graph's current version.
  Change it only when the person asks.
- **The trash.** `trash_graph` moves a graph to the trash. `list_trash` and
  `restore_from_trash` bring it back with its whole history. Trash a graph only when asked. No
  tool deletes a graph forever; that is for the person to do, in the web app.
- **Other people's graphs** cannot be changed. `copy_graph` makes a private copy the person
  owns, with the same notes, links and layout but no history.

## Signing in

The server signs in through `cloudflared` and Cloudflare Access. If there is no saved sign-in,
the first tool call opens the person's browser and waits up to five minutes for them to finish.
A sign-in lasts 24 hours. If a tool answers with a sign-in or install message, pass it on: the
person finishes signing in in the browser, or runs `cloudflared access login <app url>` in a
terminal, or installs `cloudflared`. Then try once more. Do not retry in a loop. If the app still
refuses a fresh sign-in, the account is not allowed in by the site's sign-in policy, and no
tool call can change that.

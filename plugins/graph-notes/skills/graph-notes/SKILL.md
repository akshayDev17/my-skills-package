---
name: graph-notes
description: Use when the person asks to look at, create, add to, reorganise, restyle or fix one of their graph-notes graphs (knowledge graphs drawn as Obsidian canvases in the graph-notes app), to box or group parts of one, to colour, move or resize its cards, to undo a change, or to turn notes, a document or a conversation into a graph, through the graph-notes MCP tools (list_graphs, read_graph, add_notes, add_group_boxes and the rest).
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
every existing card's position, size, colour and style as they were, unless you change them.
The one exception is drawing or refitting a group box, which moves notes only where the box
needs room, and says which. Every edit the web app's editor can make has a tool here, and each
writes exactly what the web app writes.

| Part | On the canvas | What you see and send |
|---|---|---|
| **Graph** | one canvas | `slug` (its permanent address, never changes), `title`, `version`, `private`/`public`, `url` |
| **Note** | a text card | `id` (stable), `text` (Markdown), `clusters`; and when set: `color`, `border`, `shape`, `text_align` |
| **Link** | an edge | `id`, `from_note` → `to_note`, optional `label`, `direction`: `forward` (default), `backward`, `both`, `none`; and when set: `color`, `line`, `path`, `from_side`, `to_side` |
| **Cluster** | `#tags` on a card's last line, or a labelled group box | a name, lowercase with `-`; a note can be in several |
| **Group box** | a group node | `id`, `label`, the notes it `contains`, the `boxes` inside it, `collapsed`, `color`; its label names a cluster of the cards inside it |
| **Other cards** | web-page and file cards | `kind`, `url` or `file`; make web-page cards (`add_notes` with `url`), edit a page's `url` or a file card's `file` (`update_notes`); link, style, move and remove them |

Cluster names are stored as a final line of `#tags`, as in Obsidian. Pass them in
`clusters` and never type `#tags` into a note's text. A group box labelled "Backend" and the
tag `#backend` are the same cluster. A new note with a box's cluster name goes inside that box
when there is room.

## How things look, and where

- **Looks** are the web app's own choices; `read_graph` shows each one that is not the default.
  `style_notes` sets a note's, card's or box's `color` (`red`, `orange`, `yellow`, `green`,
  `cyan`, `purple`, any `#rrggbb`, or `none`) and a card's `border` (`solid`, `dashed`,
  `dotted`, `none`), `shape` (`rectangle`, `pill`, `circle`, `database`) and `text_align`
  (`left`, `center`, `right`); a box takes a colour only. Change looks only when the person
  asks, or to follow a convention the graph already uses (one colour per kind of note, say).
- **Links** take the same `color`, a `line` (`solid`, `dashed`, `short-dashed`, `dotted`), a
  `path` (`curved`, `straight`, `right-angles`) and the `from_side` / `to_side` they attach to
  (`top`, `right`, `bottom`, `left`), when made (`add_notes`, `link_notes`) or later. Sides come
  as a pair: both or neither; without them a link attaches to the facing sides. As in the web
  app, sides cannot be taken off in place: remove the link and add it again without them.
  `update_links` changes a link in place (label, where `''` removes it; direction; look; sides);
  it keeps its id and its notes.
- **Web-page cards**: a new note in `add_notes` with a `url` instead of `text` is a web-page
  card. `update_notes` changes a web-page card's `url` or a file card's `file`. Neither has text
  or clusters of its own. File cards come from Obsidian and cannot be made here, as in the web
  app.
- **Boxes nest.** A box's `notes` may name other boxes (their id, or the `key` of a box earlier
  in the same call): draw the inner boxes first, then the outer one around them. A box drawn
  around notes that are all inside one box goes inside that box, which grows to keep room
  (the result says which boxes grew). A box with no `notes` is an empty box, as the web app's
  "+ Group" makes one.
- **Moving and resizing by hand** is rarely needed: the server places what it adds. When the
  person asks for it, `read_graph` with `layout: true` gives each card's and box's `x`, `y`,
  `width`, `height` (canvas pixels, `y` down), and `move_notes` sets new ones, as dragging and
  the resize handles do in the web app. An entry with only `x`/`y` moves a box with everything
  inside it; an entry that changes `width` or `height` resizes it and carries nothing. To move
  and resize a box, send two entries: the move, then the resize. A card moved into or out of a
  box joins or leaves its cluster, and the result says so. To put notes in a box, use the
  group-box tools instead: they place everything for you.

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
   `update_links`, `style_notes`, `move_notes`, `add_group_boxes`, `update_group_boxes` and
   `remove_group_boxes` all take lists.
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
  (their ids, or other boxes' ids or keys to nest them), optionally `collapsed: true`. Draw boxes after the notes and their links exist,
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
- **Boxes nest but do not overlap.** Show a part's sub-parts as boxes inside its box. If two
  parts share a note, put it in one box and link it to the other part.

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

**Undo and redo.** The web app's undo (⌘Z) lives only in the browser tab where the edits were
made, so no tool can reach it. Here every change to content or title is a version: to undo your
last change, `restore_version` to the version before it; to redo, restore the version the undo
replaced. Each restore is a new version, so nothing is lost. A version holds content and title
only: undo a visibility change with `set_visibility`, and a restore that would change nothing
saves no new version.

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
- **The Obsidian download.** `download_for_obsidian` saves the same zip as the web app's
  ⋯ → Download for Obsidian (every graph, not the trash) to a new file on this computer
  (Downloads, or the `folder` named; it never overwrites a file) and returns its `path`. Only
  when the person asks; tell them to unzip it and use Obsidian's "Open folder as vault".
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

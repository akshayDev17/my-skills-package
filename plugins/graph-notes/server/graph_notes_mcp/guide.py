"""What a graph is, for a model that has never seen the app (the ``about_graphs`` tool)."""

INSTRUCTIONS = (
    "Reads and edits the person's graphs in the graph-notes app: a graph is a set of notes, "
    "links between them and clusters that group them. Call about_graphs once to learn the model, "
    "list_graphs to see what exists, and read_graph before changing a graph. Every change names the "
    "version of the graph you last saw, so newer work is never overwritten."
)

GUIDE = """\
# graph-notes: what a graph is

graph-notes is a personal knowledge-graph app. A person keeps several **graphs**; each
graph is a drawing of **notes** joined by **links**, which the app lays out on a canvas.
These tools let you read and change a graph as notes and links. You never deal with the
canvas: this server places new notes on it and keeps every existing position, size,
colour and style exactly as it was unless you change it. The one exception is drawing or
refitting a group box, which moves notes only where the box needs it, and says which.

## The parts of a graph

- **Graph**: has a `slug` (its permanent address, lowercase with `-`; it never changes),
  a `title` (can be renamed), a `version` number, and is `private` or `public`.
- **Note**: a card holding Markdown `text`. It has a stable `id`; use that id to change
  it, remove it or link it.
- **Link**: an arrow from one note to another (`from_note` -> `to_note`), with an optional
  `label` and a `direction`: `forward` (the default, an arrow at `to_note`), `backward`
  (an arrow at `from_note` only), `both`, or `none` (a plain line). Each link has its own
  `id`. `update_links` changes links in place (label, direction, look, sides); the link
  keeps its id and its two notes.
- **Cluster**: a named set of notes, for a theme that cuts across the graph. A note can
  be in several clusters. You put a note in clusters with its `clusters` list; names are
  lowercase with `-` for spaces (they are stored as `#tags` on the note's last line, as
  in Obsidian, so do not also write them into the text).
- **Group box**: a labelled rectangle drawn around some notes. The cards inside it form a
  cluster named after its label, so a box is the way to show a stage or a part of the
  graph at a glance. Each box has an `id`; `read_graph` lists the boxes in `group_boxes`,
  with the notes each one `contains` and whether it starts `collapsed`.
  - `add_group_boxes` draws boxes, each with a `label` around the `notes` you name (their
    ids). A box holds exactly the notes inside it, so the server sizes it to them and,
    when other notes or another box are in the way, moves notes until each box holds
    only its own notes and no two boxes overlap. Every note that moved is listed in the
    result as `moved`, and a box moved out of the way is named in its `notices`. Draw
    boxes after adding the notes and their links.
  - `update_group_boxes` renames a box (`label`, which renames its cluster; the box stays
    where it is and only widens when the new label needs more room), changes which notes
    it holds (`notes`: the full new set; the box is refitted around them), or sets
    `collapsed`.
  - `remove_group_boxes` takes boxes away; their notes stay where they are.
  - `collapsed: true` makes a box start collapsed in the web app: only its label shows, and
    its notes and their links are hidden until the reader clicks the label to open it.
  - **Boxes nest.** A box's `notes` may name other boxes (by id, or by the `key` of a box
    earlier in the same call), which go inside it with everything they hold: draw the inner
    boxes first, then the outer one around them. A box drawn around notes that are all in
    one box already is drawn inside that box, which grows to keep room around it (cards in
    the way move, and stay in the box they were in). `read_graph` lists the `boxes` inside
    each box; a note in a box inside another is in both clusters.
  - A box with no `notes` is an empty box of the web app's size for a new one, placed clear
    of everything, as the web app's "+ Group" makes it; `move_notes` puts it where you want.
  A note you add with a box's cluster name is placed inside that box when there is room.
- **Other cards**: a graph may also hold **web-page cards** (a link to a page, shown as its
  address) and **file cards** (a path to a file in the person's Obsidian vault). `read_graph`
  lists them in `other_cards`; link, style, move and remove them like notes. `add_notes`
  makes a web-page card when a new note has a `url` instead of `text`; `update_notes`
  changes a web-page card's `url` or a file card's `file` path. Neither has text or
  clusters of its own (a group box around one puts it in a cluster). File cards cannot be
  made here, as in the web app: they come from Obsidian.

## Moving and resizing by hand

The server places what it adds, and you rarely need more. When the person asks for a
note somewhere particular, or for a bigger card: `read_graph` with `layout: true` gives
each note, card and box its `x`, `y` (top-left, in canvas pixels; `y` grows downwards),
`width` and `height`, and `move_notes` sets new ones, as dragging and resizing in the web
app does. An entry that changes only a group box's `x`/`y` drags it, carrying everything
inside it. An entry that changes its `width` or `height` (with or without `x`/`y`) resizes
it, as the web app's resize handles do, and carries nothing: a box resized from its
top-left takes the new `x`, `y`, `width` and `height` and its contents stay put. To move
a box and resize it, send two entries: a move, then a resize.
A card moved into or out of a labelled box joins or leaves its cluster, and the result
says so (and says when a card now overlaps another). To put notes in a box, use the
group-box tools instead: they place everything for you.

## How things look

These are the web app's own choices, and `read_graph` shows each one that is not the
default. Leave them alone unless the person asks, or the graph already uses them to mean
something (one colour per kind of note, say); then follow what is there.

- **Colour** of a note, other card or group box (`style_notes`) or of a link
  (`update_links`, or when the link is made): `red`, `orange`, `yellow`, `green`, `cyan`,
  `purple` (the app's six colours), any `#rrggbb`, or `none` (the default). A group box's
  colour also colours its cluster.
- **A card's border** `solid` (default), `dashed`, `dotted` or `none`; **shape** `rectangle`
  (default), `pill`, `circle` or `database` (a cylinder); **text alignment** `left`
  (default), `center` or `right` (`style_notes`; a group box has only a colour).
- **A link's line** `solid` (default), `dashed`, `short-dashed` or `dotted`; its **path**
  `curved` (default), `straight` or `right-angles`; and the **sides** of the cards it
  attaches to, `from_side` / `to_side`: `top`, `right`, `bottom` or `left`. Sides come as a
  pair: give both or neither. Without them (the default) a link attaches to the sides
  facing each other, wherever the cards move. Set them when the link is made (`add_notes`,
  `link_notes`) or later (`update_links`). As in the web app, a link's sides cannot be
  taken away in place: remove the link and add it again without sides.

## Versions, history and the trash

- Every saved change makes a new **version**. Old versions are kept: `list_history`
  lists them, `read_graph` with `at_version` shows one, and `restore_version` brings back
  an old version's content and title as a new version, so nothing is lost. Who can see the
  graph is not part of what a restore brings back.
- **Every tool that changes a graph takes `version`: the version number you last saw**
  (from `list_graphs`, `read_graph`, or the result of your previous change, which returns
  the new version). If the graph has changed since, for example because the person
  edited it in the web app, nothing is saved: you are told, shown the current
  graph, and can make the change again if it still applies. Newer work is never
  overwritten.
- Each tool call that changes notes or links saves one version, so put related changes
  in one call: `add_notes` (notes and their links), `update_notes`, `remove_notes`,
  `link_notes`, `unlink_notes`, `update_links`, `style_notes`, `move_notes` and the three
  group-box tools all take a list.
- `trash_graph` moves a graph to the **trash**; `list_trash` shows the trash and
  `restore_from_trash` brings a graph back with its whole history, for as long as the
  app keeps it there. No tool here deletes a graph forever: that is for the person to
  do, in the web app.

## Undo and redo

The web app's Undo and Redo (⌘Z, ⇧⌘Z) live only in the browser tab where the edits were
made: each tab keeps its own list of steps, which no other tab and no tool here can see.
Here a change to a graph's content or title (notes, cards, links, boxes, their looks, a
rename) is already a saved version, so its undo and redo are `restore_version`: to undo it,
restore the version before it (the `version` you made it from); to redo it, restore the
version the undo replaced. A restore saves a new version, so nothing is lost, and
`list_history` shows what each version was; restoring a version whose content and title are
the same as now saves nothing and leaves the version as it is. A version holds content and
title only: a change of who can see the graph is undone with `set_visibility`, not
`restore_version`, and a graph sent to the trash comes back with `restore_from_trash`.

## Downloading for Obsidian

`download_for_obsidian` does what the web app's ⋯ → Download for Obsidian does: it saves a
zip of every one of the person's graphs (not the trash), each as an Obsidian canvas, here
on this computer (the Downloads folder, or the `folder` you name; it never overwrites a
file), and says where. Unzip it, then in Obsidian: Open folder as vault. Edits made there
are not sent back. Only do this when the person asks.

## Private and public

A graph is **private** (only its owner sees it) or **public** (anyone can view its
current version, read-only). `set_visibility` changes it; only do so when the person
asks. `list_graphs` also shows other people's public graphs: you can read and copy
those, but not change them.

## A typical session

1. `list_graphs` to find the graph (or `create_graph` for a new one).
2. `read_graph` to see its notes, links, clusters and current `version`.
3. `add_notes` with the new notes **and the links between them in the same call**: give
   each note a short `key` (any name you like) and use those keys, or the ids of notes
   already in the graph, as `from_note` / `to_note` in `links`. Notes that arrive with
   their links are laid out left to right along them, so the graph reads well; a note
   with `near` = the id of an existing note is placed beside that note. Use `link_notes`
   only to link notes that are already there.
4. If parts of the graph belong together (the stages of a pipeline, say), `add_group_boxes`
   with one box per part, all in one call.
5. Tell the person what changed and give them the graph's `url`.
"""

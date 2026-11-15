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
colour and style exactly as it was. The one exception is drawing or refitting a group
box, which moves notes only where the box needs it, and says which.

## The parts of a graph

- **Graph**: has a `slug` (its permanent address, lowercase with `-`; it never changes),
  a `title` (can be renamed), a `version` number, and is `private` or `public`.
- **Note**: a card holding Markdown `text`. It has a stable `id`; use that id to change
  it, remove it or link it.
- **Link**: an arrow from one note to another (`from_note` -> `to_note`), with an optional
  `label` and a `direction`: `forward` (the default), `both`, or `none` (a plain line).
  Each link has its own `id`.
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
    result as `moved`. Draw boxes after adding the notes and their links.
  - `update_group_boxes` renames a box (`label`, which renames its cluster; the box stays
    where it is and only widens when the new label needs more room), changes which notes
    it holds (`notes`: the full new set; the box is refitted around them), or sets
    `collapsed`.
  - `remove_group_boxes` takes boxes away; their notes stay where they are.
  - `collapsed: true` makes a box start collapsed in the web app: only its label shows, and
    its notes and their links are hidden until the reader clicks the label to open it.
  A note you add with a box's cluster name is placed inside that box when there is room.
- **Other cards**: a graph may also hold web-page cards and file cards. You can read,
  link and remove them, but not create them.

## Versions, history and the trash

- Every saved change makes a new **version**. Old versions are kept: `list_history`
  lists them, `read_graph` with `at_version` shows one, and `restore_version` makes an old
  version the newest again (which is itself a new version, so nothing is lost).
- **Every tool that changes a graph takes `version`: the version number you last saw**
  (from `list_graphs`, `read_graph`, or the result of your previous change, which returns
  the new version). If the graph has changed since, for example because the person
  edited it in the web app, nothing is saved: you are told, shown the current
  graph, and can make the change again if it still applies. Newer work is never
  overwritten.
- Each tool call that changes notes or links saves one version, so put related changes
  in one call: `add_notes` (notes and their links), `update_notes`, `remove_notes`,
  `link_notes`, `unlink_notes` and the three group-box tools all take a list.
- `trash_graph` moves a graph to the **trash**; `list_trash` shows the trash and
  `restore_from_trash` brings a graph back with its whole history, for as long as the
  app keeps it there. No tool here deletes a graph forever: that is for the person to
  do, in the web app.

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

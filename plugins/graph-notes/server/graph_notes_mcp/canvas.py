"""A stored canvas (JSON Canvas + Advanced Canvas, exactly as the app keeps it) as plain
notes and links, and the edits a model may make to it.

Pure functions over parsed JSON: no I/O. Every edit returns a new document in which the
items it does not change are the very same objects, in the same order, with every key
this module does not know about still on them, so what goes back to the app is the
same canvas with only the edited fields changed.

The cluster rules are the app's own (frontend/lib/canvas.ts):
- a note's clusters are the ``#tags`` on its last line, when that line holds nothing but
  tags; that line is not part of the note's text;
- a labelled group box is a cluster of the cards wholly inside it;
- names normalize (lowercase, spaces to ``-``), so group "Backend" and ``#backend`` merge.

Because a box holds whatever lies wholly inside it, making or refitting a box is a
layout problem: the box is drawn around its members, and cards move where that is
needed for it to enclose only them (``fit_box``).
"""

from __future__ import annotations

import math
import re
import secrets
import textwrap
from typing import Callable

# A tag's characters: letters, digits, `_`, `/` and `-` (the app's [\p{L}\p{N}_/-]).
_TAG_CHARS = r"[\w/-]"
_TAG = re.compile(rf"#{_TAG_CHARS}+")
_TAG_LINE = re.compile(rf"\s*#{_TAG_CHARS}+(?:\s+#{_TAG_CHARS}+)*\s*")
_NOT_TAG_CHAR = re.compile(r"[^\w/-]")

NOTE_WIDTH, NOTE_HEIGHT = 260, 120  # the app's own new note
WIDE_NOTE = 360                     # for text that would make a default-width card very tall
GAP = 40           # the least space left around a new card
STEP = 60          # how finely positions are tried
RINGS = 40         # how far out, in steps, before giving up on "near"
COLUMN_GAP = 160   # between the columns of notes laid out together: room for a link's label
ROW_GAP = 40       # between notes in one column
BOX_PAD = 40       # clear space between a group box's edge and its cards, on every side
LABEL_BAR = 44     # the app's label bar across a box's top (all a collapsed box shows)
BOX_TOP = LABEL_BAR + BOX_PAD  # above its cards: the label bar, then the same clear space as elsewhere
BOX_CLEAR = 16     # how near another card or box may stay to a box and still count as outside it


class EditError(ValueError):
    """An edit that cannot be made as asked; the message says why, for the model."""


# ── clusters ───────────────────────────────────────────────────────────────

def cluster_key(name: str) -> str:
    return "-".join(name.strip().lstrip("#").lower().split())


def split_tags(text: str) -> tuple[str, list[str]]:
    """``"note\\n#a #b"`` -> ``("note", ["a", "b"])``; text without a tag line is unchanged."""
    lines = text.rstrip().split("\n")
    if not _TAG_LINE.fullmatch(lines[-1]):
        return text, []
    tags = list(dict.fromkeys(cluster_key(tag) for tag in _TAG.findall(lines[-1])))
    return "\n".join(lines[:-1]).rstrip(), tags


def clean_tags(names: list[str]) -> list[str]:
    """Cluster names as a tag line can hold them: normalized, other characters dropped, no repeats."""
    return list(dict.fromkeys(tag for tag in (_NOT_TAG_CHAR.sub("", cluster_key(n)) for n in names) if tag))


def join_tags(body: str, tags: list[str]) -> str:
    """The inverse of ``split_tags``: ``body`` with ``tags`` as its last line."""
    if not tags:
        return body
    line = " ".join(f"#{tag}" for tag in tags)
    return f"{body.rstrip()}\n{line}" if body.strip() else line


def _written(text: str, clusters: list[str]) -> tuple[str, list[str]]:
    """What a note given as ``text`` and ``clusters`` holds: every trailing tag line of
    the text is clusters too, so a note always reads back as it was written."""
    tags: list[str] = []
    while True:
        text, found = split_tags(text)
        if not found:
            return text, clean_tags([*clusters, *tags])
        tags = [*found, *tags]


def is_inside(inner: dict, outer: dict) -> bool:
    return (inner["x"] >= outer["x"] and inner["y"] >= outer["y"]
            and inner["x"] + inner["width"] <= outer["x"] + outer["width"]
            and inner["y"] + inner["height"] <= outer["y"] + outer["height"])


def _group_cluster(node: dict) -> str | None:
    """The cluster a group box names, or None for a card or an unlabelled box."""
    label = node.get("label")
    if node.get("type") != "group" or not isinstance(label, str) or not label.strip():
        return None
    return cluster_key(label)


def _memberships(nodes: list[dict]) -> tuple[dict[str, list[str]], set[str], set[str]]:
    """``({card id: its clusters}, clusters named by tags, clusters named by group boxes)``."""
    of: dict[str, list[str]] = {n["id"]: [] for n in nodes if n.get("type") != "group"}
    tagged: set[str] = set()
    boxed: set[str] = set()
    for n in nodes:
        if n.get("type") == "text":
            for tag in split_tags(_text(n))[1]:
                tagged.add(tag)
                of[n["id"]].append(tag)
    for g in nodes:
        name = _group_cluster(g)
        if name is None:
            continue
        boxed.add(name)
        for n in nodes:
            if n.get("type") != "group" and is_inside(n, g) and name not in of[n["id"]]:
                of[n["id"]].append(name)
    return of, tagged, boxed


def _text(node: dict) -> str:
    return node["text"] if isinstance(node.get("text"), str) else ""


# ── the plain form ─────────────────────────────────────────────────────────

_DIRECTIONS = {("none", "arrow"): "forward", ("arrow", "arrow"): "both",
               ("none", "none"): "none", ("arrow", "none"): "backward"}


def _direction(edge: dict) -> str:
    return _DIRECTIONS[("arrow" if edge.get("fromEnd") == "arrow" else "none",
                        "none" if edge.get("toEnd") == "none" else "arrow")]


def plain(canvas: dict) -> dict:
    """What a graph contains, without the drawing: notes, links, clusters, and (only
    when there are any) group boxes and the cards that are not notes."""
    nodes = canvas.get("nodes") or []
    of, tagged, boxed = _memberships(nodes)
    notes, others, groups = [], [], []
    for n in nodes:
        if n.get("type") == "text":
            notes.append({"id": n["id"], "text": split_tags(_text(n))[0], "clusters": sorted(of[n["id"]])})
        elif n.get("type") == "group":
            label = n.get("label") if isinstance(n.get("label"), str) and n["label"].strip() else None
            inside = [m["id"] for m in nodes if m.get("type") != "group" and is_inside(m, n)]
            groups.append({"id": n["id"], "label": label, "cluster": _group_cluster(n), "contains": inside,
                           "collapsed": n.get("collapsed") is True})
        else:
            what = {"link": ("web page", "url"), "file": ("file", "file")}.get(n.get("type"), ("card", "text"))
            others.append({"id": n["id"], "kind": what[0], what[1]: n.get(what[1]), "clusters": sorted(of[n["id"]])})
    links = []
    for e in canvas.get("edges") or []:
        link = {"id": e["id"], "from_note": e["fromNode"], "to_note": e["toNode"], "direction": _direction(e)}
        if isinstance(e.get("label"), str) and e["label"]:
            link["label"] = e["label"]
        links.append(link)
    clusters = [
        {"name": name,
         "made_by": "both" if name in tagged and name in boxed else "group box" if name in boxed else "tags",
         "members": [card for card, names in of.items() if name in names]}
        for name in sorted(tagged | boxed)
    ]
    result = {"notes": notes, "links": links, "clusters": clusters}
    if groups:
        result["group_boxes"] = groups
    if others:
        result["other_cards"] = others
    return result


# ── placement ──────────────────────────────────────────────────────────────

def note_size(body: str) -> tuple[int, int]:
    """A card that shows ``body`` in the web app (13px text, 17.55px lines, 8px 12px
    padding, measured there): the app's default, taller for more lines, wider first
    when the text is long."""
    def lines(width: int) -> int:
        per_line = (width - 24) // 7
        return sum(len(textwrap.wrap(line, per_line)) or 1 for line in body.split("\n"))
    width = NOTE_WIDTH if lines(NOTE_WIDTH) <= 8 else WIDE_NOTE
    source = body.split("\n")
    headings = sum(1 for line in source if line.startswith("#"))
    lists = sum(1 for before, line in zip([""] + source, source)
                if line.lstrip()[:2] in ("- ", "* ") and before.lstrip()[:2] not in ("- ", "* "))
    needed = 16 + 18 * lines(width) + 8 * headings + 10 * lists + 14
    return width, min(max(NOTE_HEIGHT, math.ceil(needed / 20) * 20), 600)


def _center(nodes: list[dict]) -> tuple[float, float]:
    return (sum(n["x"] + n["width"] / 2 for n in nodes) / len(nodes),
            sum(n["y"] + n["height"] / 2 for n in nodes) / len(nodes))


def _apart(a: dict, b: dict, gap: int) -> bool:
    return (a["x"] >= b["x"] + b["width"] + gap or a["x"] + a["width"] + gap <= b["x"]
            or a["y"] >= b["y"] + b["height"] + gap or a["y"] + a["height"] + gap <= b["y"])


def free_spot(nodes: list[dict], center: tuple[float, float], size: tuple[int, int], joins: set[str]) -> tuple[int, int]:
    """Top-left for a new card (or block of cards) of ``size`` as near ``center`` as it
    can go while

    - at least ``GAP`` from every other card, and
    - wholly inside or wholly outside each group box, and inside only the boxes whose
      cluster is in ``joins``: a card inside a labelled box joins its cluster, so a
      position must never add a cluster nobody asked for.

    Tries rings of positions outward from the centre (the app's own search); when no
    ring has room, takes the space to the right of everything.
    """
    width, height = size
    cards = [n for n in nodes if n.get("type") != "group"]
    boxes = [n for n in nodes if n.get("type") == "group"]
    x0, y0 = round(center[0] - width / 2), round(center[1] - height / 2)

    def clear(x: int, y: int) -> bool:
        card = {"x": x, "y": y, "width": width, "height": height}
        if not all(_apart(card, other, GAP) for other in cards):
            return False
        return all(_apart(card, box, GAP) or (is_inside(card, box) and _group_cluster(box) in joins) for box in boxes)

    for ring in range(RINGS + 1):
        spots = [(i * i + j * j, x0 + i * STEP, y0 + j * STEP)
                 for i in range(-ring, ring + 1) for j in range(-ring, ring + 1) if max(abs(i), abs(j)) == ring]
        for _, x, y in sorted(spots):
            if clear(x, y):
                return x, y
    return max((n["x"] + n["width"] for n in nodes), default=x0 - 2 * GAP) + 2 * GAP, y0


def _below(nodes: list[dict], size: tuple[int, int]) -> tuple[int, int]:
    """Top-left for a block that relates to nothing in the graph: under everything, at
    the graph's left edge; centred on the origin when the graph is empty."""
    if not nodes:
        return round(-size[0] / 2), round(-size[1] / 2)
    return min(n["x"] for n in nodes), max(n["y"] + n["height"] for n in nodes) + 2 * GAP


def _grid(sizes: dict[str, tuple[int, int]]) -> dict[str, tuple[int, int]]:
    """Positions for notes with no links between them: rows, in the order given, about as wide as tall."""
    names = list(sizes)
    columns = math.ceil(math.sqrt(len(names)))
    rows = [names[i:i + columns] for i in range(0, len(names), columns)]
    widths = [max(sizes[row[c]][0] for row in rows if c < len(row)) for c in range(columns)]
    at, y = {}, 0
    for row in rows:
        for c, name in enumerate(row):
            at[name] = (sum(widths[:c]) + c * GAP, y)
        y += max(sizes[name][1] for name in row) + GAP
    return at


def _layers(order: list[str], edges: list[tuple[str, str]]) -> list[list[str]]:
    """The notes in columns, each link pointing to a later column (a link that closes a
    cycle is ignored), and each column ordered to keep linked notes level."""
    after: dict[str, list[str]] = {n: [] for n in order}
    before: dict[str, list[str]] = {n: [] for n in order}
    state: dict[str, int] = {}

    def visit(start: str) -> None:
        """Depth first, keeping an edge unless it leads back to a note still being visited."""
        state[start] = 1
        path = [(start, iter([t for s, t in edges if s == start]))]
        while path:
            node, targets = path[-1]
            target = next(targets, None)
            if target is None:
                state[node] = 2
                path.pop()
            elif state.get(target) != 1 and target not in after[node]:
                after[node].append(target)
                before[target].append(node)
                if target not in state:
                    state[target] = 1
                    path.append((target, iter([t for s, t in edges if s == target])))

    for n in order:
        if n not in state:
            visit(n)

    column: dict[str, int] = {}

    def depth(n: str) -> int:
        if n not in column:
            column[n] = 1 + max((depth(p) for p in before[n]), default=-1)
        return column[n]

    for n in order:
        depth(n)
    for n in order:  # a note nothing points to sits just left of what it points to
        if not before[n] and after[n]:
            column[n] = min(column[t] for t in after[n]) - 1
    layers = [layer for layer in ([n for n in order if column[n] == c] for c in range(max(column.values()) + 1)) if layer]

    def level(n: str, beside: list[str], neighbours: dict[str, list[str]], fallback: int) -> float:
        places = [beside.index(m) for m in neighbours[n] if m in beside]
        return sum(places) / len(places) if places else fallback

    for _ in range(4):  # the barycentre method: order each column by where its neighbours are
        for i in range(1, len(layers)):
            layers[i] = [n for _, _, n in sorted((level(n, layers[i - 1], before, k), k, n) for k, n in enumerate(layers[i]))]
        for i in range(len(layers) - 2, -1, -1):
            layers[i] = [n for _, _, n in sorted((level(n, layers[i + 1], after, k), k, n) for k, n in enumerate(layers[i]))]
    return layers


def flow_layout(sizes: dict[str, tuple[int, int]], edges: list[tuple[str, str]]) -> dict[str, tuple[int, int]]:
    """Positions (top-left, the block's own top-left at 0,0) for notes that are linked
    to each other: columns from left to right along the links, ``COLUMN_GAP`` apart so
    a link's label fits between them, each note level with the notes it is linked to."""
    layers = _layers(list(sizes), edges)
    linked: dict[str, list[str]] = {n: [] for n in sizes}
    for source, target in edges:
        linked[source].append(target)
        linked[target].append(source)
    x_of, x = {}, 0
    for layer in layers:
        for n in layer:
            x_of[n] = x
        x += max(sizes[n][0] for n in layer) + COLUMN_GAP
    y_of: dict[str, float] = {}

    def settle(layer: list[str]) -> None:
        """Puts the column's notes, in order and ``ROW_GAP`` apart, where they are on the
        whole level with the notes they are linked to in the other columns."""
        wanted = {}
        for n in layer:
            middles = [y_of[m] + sizes[m][1] / 2 for m in linked[n] if m in y_of and m not in layer]
            if middles:
                wanted[n] = sum(middles) / len(middles) - sizes[n][1] / 2
        floor = -math.inf
        for n in layer:
            y_of[n] = max(wanted.get(n, 0 if floor == -math.inf else floor), floor)
            floor = y_of[n] + sizes[n][1] + ROW_GAP
        if wanted:  # pushed down to make room: share that between the notes above and below
            shift = sum(y_of[n] - wanted[n] for n in wanted) / len(wanted)
            for n in layer:
                y_of[n] -= shift

    for _ in range(2):
        for layer in layers:  # left to right, then back so a parent is level with its children
            for n in layer:
                y_of.pop(n, None)
            settle(layer)
        for layer in reversed(layers[:-1]):
            for n in layer:
                del y_of[n]
            settle(layer)
    top = min(y_of.values())
    return {n: (x_of[n], round(y_of[n] - top)) for n in sizes}


# ── edits ──────────────────────────────────────────────────────────────────

def new_id(canvas: dict, random: Callable[[], str] = lambda: secrets.token_hex(8)) -> str:
    """A fresh id in Obsidian's style: 16 hex digits, used by no node or edge."""
    taken = {item["id"] for key in ("nodes", "edges") for item in canvas.get(key) or []}
    while True:
        candidate = random()
        if candidate not in taken:
            return candidate


def _find(nodes: list[dict], card_id: str, what: str = "id") -> dict:
    for n in nodes:
        if n["id"] == card_id:
            return n
    raise EditError(f"{what} '{card_id}' names no note or card in this graph; read the graph for its ids")


def _edge(edge_id: str, link: dict, source: str, target: str) -> dict:
    """The stored edge for a link. No sides are stored: the app and Obsidian attach such
    an edge to the facing sides of its two cards, wherever they move."""
    if source == target:
        raise EditError(f"a link needs two different notes; '{link['from_note']}' was given as both ends")
    edge = {"id": edge_id, "fromNode": source, "toNode": target}
    direction = link.get("direction") or "forward"
    if direction == "both":
        edge["fromEnd"] = "arrow"
    elif direction == "none":
        edge["toEnd"] = "none"
    elif direction != "forward":
        raise EditError(f"direction must be 'forward', 'both' or 'none', not '{direction}'")
    if link.get("label"):
        edge["label"] = link["label"]
    return edge


def _plain_link(edge: dict) -> dict:
    return {"id": edge["id"], "from_note": edge["fromNode"], "to_note": edge["toNode"], "direction": _direction(edge),
            **({"label": edge["label"]} if "label" in edge else {})}


def _groups(members: list[str], pairs: list[tuple[str, str]]) -> list[list[str]]:
    """``members`` split into the sets that ``pairs`` connect, each in its given order."""
    group = {m: m for m in members}

    def root(m: str) -> str:
        while group[m] != m:
            m = group[m]
        return m

    for a, b in pairs:
        group[root(a)] = root(b)
    return [[m for m in members if root(m) == r] for r in dict.fromkeys(root(m) for m in members)]


def add_notes(canvas: dict, notes: list[dict], links: list[dict] = ()) -> tuple[dict, list[dict], list[dict]]:
    """``canvas`` with a new text card per ``{text, clusters?, near?, key?}`` and a new
    edge per ``{from_note, to_note, label?, direction?}``, whose ends are a new note's
    ``key`` or the id of a card already there; and the plain form of what was added.

    Where the new cards go:
    - new notes linked to each other are laid out together (``flow_layout``) as one
      block, which is then placed like a single card;
    - a note (or block) goes beside the card it names as ``near``, else beside the
      cards it is linked to, else beside the cards that share one of its clusters,
      never touching another card and never inside a group box whose cluster it was
      not given (``free_spot``); a single note given a box's cluster goes inside the
      box when there is room;
    - what relates to nothing in the graph goes below it (``_below``): a block of
      linked notes on its own, the remaining single notes together in a grid.
    """
    if not notes:
        return canvas, [], []
    nodes = list(canvas.get("nodes") or [])
    existing = {n["id"] for n in nodes}
    keys = [note.get("key") for note in notes if note.get("key")]
    for key in keys:
        if keys.count(key) > 1 or key in existing:
            raise EditError(f"key '{key}' must be unique: " + ("another new note has it" if keys.count(key) > 1
                                                                  else "a card of this graph already has that id"))
    made = []  # (node without a place yet, its note)
    for note in notes:
        body, tags = _written(note["text"], note.get("clusters") or [])
        width, height = note_size(body)
        node_id = new_id({"nodes": nodes + [node for node, _ in made], "edges": canvas.get("edges")})
        made.append(({"id": node_id, "type": "text", "text": join_tags(body, tags), "width": width, "height": height}, note))
    by_key = {note["key"]: node["id"] for node, note in made if note.get("key")}
    new_ids = [node["id"] for node, _ in made]

    edges = list(canvas.get("edges") or [])
    added_edges = []
    for link in links:
        ends = []
        for end in ("from_note", "to_note"):
            ends.append(by_key.get(link[end]) or _find(nodes, link[end], end)["id"])
        edge = _edge(new_id({"nodes": nodes + [node for node, _ in made], "edges": edges}), link, *ends)
        edges.append(edge)
        added_edges.append(edge)

    among = [(e["fromNode"], e["toNode"]) for e in added_edges if e["fromNode"] in new_ids and e["toNode"] in new_ids]
    node_of = {node["id"]: node for node, _ in made}
    note_of = {node["id"]: note for node, note in made}

    def put(block: list[str], at: dict[str, tuple[int, int]], beside: list[dict], joins: set[str]) -> None:
        """Places the block (its notes at ``at``, from its own top-left) beside those cards, or below everything."""
        size = (max(at[m][0] + node_of[m]["width"] for m in block), max(at[m][1] + node_of[m]["height"] for m in block))
        left, top = free_spot(nodes, _center(beside), size, joins) if beside else _below(nodes, size)
        for m in block:
            node_of[m]["x"], node_of[m]["y"] = left + at[m][0], top + at[m][1]
            nodes.append(node_of[m])

    loose = []  # single notes that relate to nothing there: put together, last
    for block in sorted(_groups(new_ids, among), key=len, reverse=True):  # the big blocks first, while there is room
        cards = [n for n in nodes if n.get("type") != "group"]
        tags = {tag for m in block for tag in split_tags(node_of[m]["text"])[1]}
        near = next((note_of[m]["near"] for m in block if note_of[m].get("near")), None)
        if near is not None:
            beside = [_find(nodes, near, "near")]
        else:
            of, _, _ = _memberships(nodes)
            beside = ([n for n in cards if any({e["fromNode"], e["toNode"]} == {n["id"], m} for e in added_edges for m in block)]
                      or [n for n in cards if set(of[n["id"]]) & tags])
        if len(block) > 1:
            put(block, flow_layout({m: (node_of[m]["width"], node_of[m]["height"]) for m in block},
                                   [(a, b) for a, b in among if a in block]), beside, set())
        elif beside:
            put(block, {block[0]: (0, 0)}, beside, tags)
        else:
            loose.append(block[0])
    if loose:
        put(loose, _grid({m: (node_of[m]["width"], node_of[m]["height"]) for m in loose}), [], set())

    placed = [n for n in canvas.get("nodes") or []] + [
        {"id": node["id"], "type": "text", "text": node["text"], "x": node["x"], "y": node["y"],
         "width": node["width"], "height": node["height"]} for node, _ in made]
    after = {**canvas, "nodes": placed, **({"edges": edges} if added_edges else {})}
    added = [{"id": node["id"], **({"key": note["key"]} if note.get("key") else {}),
              "text": split_tags(node["text"])[0], "clusters": split_tags(node["text"])[1]} for node, note in made]
    return after, added, [_plain_link(e) for e in added_edges]


def update_notes(canvas: dict, changes: list[dict]) -> tuple[dict, list[str]]:
    """``canvas`` with each ``{id, text?, clusters?}`` applied to its note, and a notice
    for each cluster a note keeps because it comes from a group box (which only the box
    no longer holding the card can change: update_group_boxes, or moving the card in the
    web app). ``clusters`` replaces the note's tags; ``text`` its body."""
    nodes = canvas.get("nodes") or []
    replaced: dict[str, dict] = {}
    for change in changes:
        node = replaced.get(change["id"]) or _find(nodes, change["id"])
        if node.get("type") != "text":
            raise EditError(f"'{change['id']}' is not a note, so it has no text or clusters to change")
        body, tags = split_tags(_text(node))
        new_body, new_tags = _written(
            change["text"] if change.get("text") is not None else body,
            change["clusters"] if change.get("clusters") is not None else tags,
        )
        if (new_body, new_tags) != (body, tags):
            replaced[node["id"]] = {**node, "text": join_tags(new_body, new_tags)}
    after = {**canvas, "nodes": [replaced.get(n["id"], n) for n in nodes]} if replaced else canvas
    of, _, _ = _memberships(after.get("nodes") or [])
    notices = []
    for card_id in dict.fromkeys(c["id"] for c in changes if c.get("clusters") is not None):
        tags = split_tags(_text(_find(after["nodes"], card_id)))[1]
        notices += [
            f"note '{card_id}' is still in cluster '{name}': it sits inside the group box of that name, "
            "and leaves it only when the box stops holding it (update_group_boxes with the box's new notes)"
            for name in of[card_id] if name not in tags
        ]
    return after, notices


def remove_cards(canvas: dict, ids: list[str]) -> tuple[dict, list[str]]:
    """``canvas`` without those cards and without every link that touched one of them
    (returned, as link ids). Removing a group box removes only the box."""
    nodes = canvas.get("nodes") or []
    for card_id in ids:
        _find(nodes, card_id)
    if not ids:
        return canvas, []
    gone = set(ids)
    edges = canvas.get("edges") or []
    dropped = [e["id"] for e in edges if e["fromNode"] in gone or e["toNode"] in gone]
    after = {**canvas, "nodes": [n for n in nodes if n["id"] not in gone]}
    if dropped:
        after["edges"] = [e for e in edges if e["id"] not in set(dropped)]
    return after, dropped


def add_links(canvas: dict, links: list[dict]) -> tuple[dict, list[dict]]:
    """``canvas`` with a new edge per ``{from_note, to_note, label?, direction?}`` between
    cards already there, and the plain form of the links added. Nothing moves."""
    if not links:
        return canvas, []
    nodes = canvas.get("nodes") or []
    edges = list(canvas.get("edges") or [])
    count = len(edges)
    for link in links:
        ends = [_find(nodes, link[end], end)["id"] for end in ("from_note", "to_note")]
        edges.append(_edge(new_id({**canvas, "edges": edges}), link, *ends))
    return {**canvas, "edges": edges}, [_plain_link(e) for e in edges[count:]]


def remove_links(canvas: dict, ids: list[str]) -> dict:
    edges = canvas.get("edges") or []
    known = {e["id"] for e in edges}
    for link_id in ids:
        if link_id not in known:
            raise EditError(f"'{link_id}' names no link in this graph; read the graph for its link ids")
    return {**canvas, "edges": [e for e in edges if e["id"] not in set(ids)]} if ids else canvas


# ── group boxes ────────────────────────────────────────────────────────────

def _label_width(label: str) -> int:
    """The least width that shows ``label`` in the app's label bar: "▾ " and the label
    in bold, with the bar's 12px padding either side."""
    return 24 + 16 + math.ceil(9 * len(label))


def _overlaps(a: dict, b: dict) -> bool:
    return not _apart(a, b, BOX_CLEAR)


def _rect_around(cards: list[dict], label: str) -> dict:
    """The box drawn around ``cards``: their bounds and the box's padding, wide enough for its label."""
    left, top = min(c["x"] for c in cards), min(c["y"] for c in cards)
    right, bottom = max(c["x"] + c["width"] for c in cards), max(c["y"] + c["height"] for c in cards)
    return {"x": left - BOX_PAD, "y": top - BOX_TOP,
            "width": max(right - left + 2 * BOX_PAD, _label_width(label)), "height": bottom - top + BOX_TOP + BOX_PAD}


def _reading_order(cards: list[dict]) -> list[dict]:
    return sorted(cards, key=lambda c: (c["y"], c["x"], c["id"]))


def fit_box(nodes: list[dict], edges: list[dict], box_id: str, label: str,
            members: list[str]) -> tuple[dict, dict[str, tuple[int, int]]]:
    """Where box ``box_id`` goes so that it holds ``members`` and nothing else, and the
    cards that must move for that: ``(x, y, width and height of the box, {card id: new top-left})``.

    A card or box that comes within ``BOX_CLEAR`` of the box counts as in it; the box
    itself, when it is already there, is no obstacle. With ``R`` the box drawn around
    the members where they are, the first case that applies (so the fewest cards move):

    1. nothing else is in ``R``: the box is ``R`` and nothing moves;
    2. only cards are in ``R``, fewer of them than there are members (none of them is in
       a box: that box would be in ``R`` too): each moves to the nearest free spot,
       outside every box and outside ``R``;
    3. only boxes are in ``R`` (a member sits in another box, or ``R`` crosses one): the
       members move together, arranged as they are, to the nearest place where the box
       is clear of everything;
    4. otherwise the members are laid out afresh (along their links, as ``add_notes``
       lays out new notes, or in a grid) and placed as in 3.

    A card that moves never lands in a box, so the only clusters a move changes are
    those of a box a member had to leave.
    """
    of = {n["id"]: n for n in nodes}
    chosen = set(members)
    cards = [of[m] for m in members]
    rest = [n for n in nodes if n["id"] != box_id and n["id"] not in chosen]
    rect = _rect_around(cards, label)
    intruders = [n for n in rest if n.get("type") != "group" and _overlaps(n, rect)]
    clashes = [n for n in rest if n.get("type") == "group" and _overlaps(n, rect)]
    if not intruders and not clashes:
        return rect, {}
    if not clashes and len(intruders) < len(members):
        staying = [n for n in rest if n not in intruders] + cards + [{**rect, "id": box_id, "type": "group"}]
        moves = {}
        for n in _reading_order(intruders):
            moves[n["id"]] = free_spot(staying, _center([n]), (n["width"], n["height"]), set())
            staying.append({**n, "x": moves[n["id"]][0], "y": moves[n["id"]][1]})
        return rect, moves
    if intruders:
        sizes = {c["id"]: (c["width"], c["height"]) for c in _reading_order(cards)}
        among = [(e["fromNode"], e["toNode"]) for e in edges
                 if e["fromNode"] in chosen and e["toNode"] in chosen and e["fromNode"] != e["toNode"]]
        at = flow_layout(sizes, among) if among else _grid(sizes)
    else:
        left, top = min(c["x"] for c in cards), min(c["y"] for c in cards)
        at = {c["id"]: (c["x"] - left, c["y"] - top) for c in cards}
    width = max(max(at[c["id"]][0] + c["width"] for c in cards) + 2 * BOX_PAD, _label_width(label))
    height = max(at[c["id"]][1] + c["height"] for c in cards) + BOX_TOP + BOX_PAD
    x, y = free_spot(rest, _center([rect]), (width, height), set())
    return ({"x": x, "y": y, "width": width, "height": height},
            {m: (x + BOX_PAD + at[m][0], y + BOX_TOP + at[m][1]) for m in members})


def widen_box(nodes: list[dict], box: dict, label: str) -> tuple[dict, dict[str, tuple[int, int]]]:
    """How box ``box`` changes when it is renamed to ``label``, and the cards that must
    move for that: ``({x and width of the box}, {card id: new top-left})``.

    A rename never re-lays a box: its y, height and the cards wholly inside it stay. A box
    already wide enough to show the label keeps its place and size (``{}``). A narrower one
    widens just as far as the label needs, to the right, or to the left (its right edge
    kept) when the new strip on the right would come within ``BOX_CLEAR`` of another box;
    when both sides would, the rename is refused. Every other card the new strip would
    cover or come within ``BOX_CLEAR`` of moves to the nearest free spot, outside every box.
    """
    need = _label_width(label)
    if box["width"] >= need:
        return {}, {}
    extra = need - box["width"]
    others = [n for n in nodes if n["id"] != box["id"]]
    for strip_x in (box["x"] + box["width"], box["x"] - extra):
        strip = {"x": strip_x, "y": box["y"], "width": extra, "height": box["height"]}
        if any(n.get("type") == "group" and _overlaps(n, strip) for n in others):
            continue
        rect = {"x": min(box["x"], strip_x), "width": need}
        intruders = [n for n in others if n.get("type") != "group" and not is_inside(n, box) and _overlaps(n, strip)]
        staying = [n for n in others if n not in intruders] + [{**box, **rect}]
        moves = {}
        for n in _reading_order(intruders):
            moves[n["id"]] = free_spot(staying, _center([n]), (n["width"], n["height"]), set())
            staying.append({**n, "x": moves[n["id"]][0], "y": moves[n["id"]][1]})
        return rect, moves
    raise EditError(f"group box '{box['id']}' is too narrow to show the label '{label}' ({need}px wide), and "
                    "another group box is in the way on both sides; choose a shorter label")


def _patched(item: dict, patch: dict) -> dict:
    """``item`` with ``patch`` applied as the app applies one: a key already there keeps its
    place, a new key goes last, and ``None`` removes the key."""
    after = dict(item)
    for key, value in patch.items():
        if value is None:
            after.pop(key, None)
        else:
            after[key] = value
    return after


def _moved(nodes: list[dict], moves: dict[str, tuple[int, int]]) -> list[dict]:
    return [_patched(n, {"x": moves[n["id"]][0], "y": moves[n["id"]][1]}) if n["id"] in moves else n for n in nodes]


def _box_label(label: str | None) -> str:
    if not isinstance(label, str) or not label.strip():
        raise EditError("a group box needs a label: it names the cluster of the notes inside it")
    return label.strip()


def _box_members(nodes: list[dict], ids: list[str], claimed: dict[str, str], box: str) -> list[str]:
    """``ids`` checked as what ``box`` can hold: at least one card of this graph, no box,
    and no card another box of the same call holds (``claimed``: card id -> that box)."""
    if not ids:
        raise EditError("a group box must hold at least one note; to take a box away, use remove_group_boxes")
    for card_id in ids:
        if _find(nodes, card_id, "note").get("type") == "group":
            raise EditError(f"'{card_id}' is a group box; a box holds notes and other cards, not boxes")
        if claimed.get(card_id, box) != box:
            raise EditError(f"'{card_id}' is given to two boxes in one call; boxes never overlap, so a card is in one at most")
    for card_id in ids:
        claimed[card_id] = box
    return list(dict.fromkeys(ids))


def _box_report(nodes: list[dict], box: dict) -> dict:
    return {"id": box["id"], "label": box.get("label"), "cluster": _group_cluster(box),
            "contains": [n["id"] for n in nodes if n.get("type") != "group" and is_inside(n, box)],
            "collapsed": box.get("collapsed") is True}


def _moves_report(before: list[dict], after: list[dict], boxes: set[str]) -> tuple[list[str], list[str]]:
    """The cards whose place changed, and a notice for each box (other than ``boxes``,
    the ones this call made or changed) that a moved card left, and so its cluster."""
    was = {n["id"]: n for n in before}
    moved = [n["id"] for n in after if n["id"] in was and n.get("type") != "group"
             and (n["x"], n["y"]) != (was[n["id"]]["x"], was[n["id"]]["y"])]
    notices = []
    for card_id in moved:
        now = next(n for n in after if n["id"] == card_id)
        for g in before:
            if g.get("type") == "group" and g["id"] not in boxes and is_inside(was[card_id], g) and not is_inside(now, g):
                name = _group_cluster(g)
                notices.append(f"'{card_id}' was moved out of group box '{g['id']}'"
                               + (f", so it is no longer in cluster '{name}'" if name else ""))
    return moved, notices


def add_group_boxes(canvas: dict, boxes: list[dict]) -> tuple[dict, list[dict], list[str], list[str]]:
    """``canvas`` with a new group box per ``{label, notes, collapsed?}`` holding exactly
    those cards (``fit_box``; boxes are made in the order given, each clear of the ones
    before); the plain form of the new boxes; the cards that moved; and notices.

    A new box is the group node the web app makes ({type, label, width, height, id, x,
    y}, in that order, no colour), with ``collapsed: true`` last when it starts
    collapsed, as the app's "Starts collapsed" writes it. It goes last in the canvas,
    as the app adds one.
    """
    before = list(canvas.get("nodes") or [])
    nodes, edges = list(before), canvas.get("edges") or []
    claimed: dict[str, str] = {}
    made = []
    for spec in boxes:
        label = _box_label(spec.get("label"))
        box_id = new_id({"nodes": nodes, "edges": edges})
        members = _box_members(nodes, spec.get("notes") or [], claimed, box_id)
        rect, moves = fit_box(nodes, edges, box_id, label, members)
        nodes = _moved(nodes, moves)
        box = {"type": "group", "label": label, "width": rect["width"], "height": rect["height"],
               "id": box_id, "x": rect["x"], "y": rect["y"], **({"collapsed": True} if spec.get("collapsed") else {})}
        nodes.append(box)
        made.append(box_id)
    if not made:
        return canvas, [], [], []
    moved, notices = _moves_report(before, nodes, set(made))
    return {**canvas, "nodes": nodes}, [_box_report(nodes, _find(nodes, b)) for b in made], moved, notices


def update_group_boxes(canvas: dict, changes: list[dict]) -> tuple[dict, list[dict], list[str], list[str]]:
    """``canvas`` with each ``{id, label?, notes?, collapsed?}`` applied to its group box:
    ``label`` renames it (and so its cluster; the box keeps its place, its size and the
    cards inside it, and only widens when it is too narrow to show the new label,
    ``widen_box``), ``notes`` is the full new set of cards it
    holds (the box is refitted around them, ``fit_box``), ``collapsed`` sets whether it
    starts collapsed. What is left out stays, and so does every other field of the box
    (its colour, anything the app or Obsidian added). Returns the boxes as they are now,
    the cards that moved, and notices."""
    before = list(canvas.get("nodes") or [])
    nodes, edges = list(before), canvas.get("edges") or []
    claimed: dict[str, str] = {}
    for change in changes:
        box = _find(nodes, change["id"])
        if box.get("type") != "group":
            raise EditError(f"'{change['id']}' is not a group box; read the graph's group_boxes for their ids")
        patch: dict = {}
        if change.get("label") is not None:
            patch["label"] = _box_label(change["label"])
        label = patch.get("label") or (box["label"] if isinstance(box.get("label"), str) else "")
        if change.get("notes") is not None:
            members = _box_members(nodes, change["notes"], claimed, box["id"])
            rect, moves = fit_box(nodes, edges, box["id"], label, members)
        elif "label" in patch:
            rect, moves = widen_box(nodes, box, label)
        else:
            rect, moves = {}, {}
        nodes = _moved(nodes, moves)
        patch |= rect
        if change.get("collapsed") is not None:
            patch["collapsed"] = True if change["collapsed"] else None
        nodes = [_patched(n, patch) if n["id"] == box["id"] else n for n in nodes]
    changed = list(dict.fromkeys(c["id"] for c in changes))
    after = {**canvas, "nodes": nodes} if nodes != before else canvas
    moved, notices = _moves_report(before, nodes, set(changed))
    return after, [_box_report(nodes, _find(nodes, b)) for b in changed], moved, notices


def remove_group_boxes(canvas: dict, ids: list[str]) -> tuple[dict, list[str]]:
    """``canvas`` without those group boxes, and without any link drawn to a box itself
    (returned, as link ids). The cards inside stay exactly where they are."""
    nodes = canvas.get("nodes") or []
    for box_id in ids:
        if _find(nodes, box_id).get("type") != "group":
            raise EditError(f"'{box_id}' is not a group box; remove_notes removes notes and cards")
    return remove_cards(canvas, ids)

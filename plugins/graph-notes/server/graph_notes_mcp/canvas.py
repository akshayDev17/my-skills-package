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
PAGE_WIDTH, PAGE_HEIGHT = 400, 240  # the app's own new web-page card (NEW_CARDS.link)
EMPTY_BOX = (480, 320)  # the app's own new group box (NEW_CARDS.group)
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


# ── appearance ─────────────────────────────────────────────────────────────
#
# The web app's Inspector (frontend/components/Inspector.tsx) writes each choice as
# Advanced Canvas stores it; each table maps the name a model uses (the Inspector's
# own label) to the stored value, and None is the app's default, which it writes by
# removing the attribute.

COLORS = {"red": "1", "orange": "2", "yellow": "3", "green": "4", "cyan": "5", "purple": "6"}
_HEX = re.compile(r"#[0-9a-fA-F]{6}")
BORDERS = {"solid": None, "dashed": "dashed", "dotted": "dotted", "none": "invisible"}  # styleAttributes.border
SHAPES = {"rectangle": None, "pill": "pill", "circle": "circle", "database": "database"}  # styleAttributes.shape
ALIGNS = {"left": None, "center": "center", "right": "right"}                           # styleAttributes.textAlign
LINES = {"solid": None, "dashed": "long-dashed", "short-dashed": "short-dashed", "dotted": "dotted"}  # .path
PATHS = {"curved": None, "straight": "direct", "right-angles": "square"}               # .pathfindingMethod
SIDES = ("top", "right", "bottom", "left")
# How a link's arrows are stored: the Inspector's "Arrows" writes both ends at once.
ENDS = {"forward": {"toEnd": None, "fromEnd": None}, "backward": {"toEnd": "none", "fromEnd": "arrow"},
        "both": {"toEnd": None, "fromEnd": "arrow"}, "none": {"toEnd": "none", "fromEnd": None}}


def _named(table: dict[str, str | None], stored: object) -> object:
    """The model's name for a stored value; a value the app does not offer is shown as it is."""
    return next((name for name, value in table.items() if value == stored), stored)


def color_value(color: str) -> str | None:
    """The stored ``color`` for a model's colour: a preset's digit, a lowercase ``#rrggbb``
    (what the app's colour input writes), or None for 'none' (the key is removed)."""
    name = color.strip().lower()
    if name == "none":
        return None
    if name in COLORS:
        return COLORS[name]
    if _HEX.fullmatch(name):
        return name
    raise EditError(f"colour '{color}' is not one the app offers: use one of {', '.join(COLORS)}, "
                    "a '#rrggbb' colour, or 'none'")


def _style(item: dict) -> dict:
    raw = item.get("styleAttributes")
    return raw if isinstance(raw, dict) else {}


def _style_patch(item: dict, attributes: dict) -> dict:
    """The patch for ``item``'s ``styleAttributes`` (the app's ``stylePatch``): a None
    attribute is removed, and an attribute set left empty is removed altogether."""
    after = _patched(_style(item), attributes)
    return {"styleAttributes": after or None}


def appearance(item: dict) -> dict:
    """What an item looks like, in the model's names, leaving out the app's defaults."""
    shown = {}
    if isinstance(item.get("color"), str):
        shown["color"] = _named(COLORS, item["color"])
    style = _style(item)
    tables = (("border", BORDERS, "border"), ("shape", SHAPES, "shape"), ("text_align", ALIGNS, "textAlign")) \
        if "fromNode" not in item else (("line", LINES, "path"), ("path", PATHS, "pathfindingMethod"))
    for name, table, key in tables:
        if isinstance(style.get(key), str):
            shown[name] = _named(table, style[key])
    if "fromNode" in item:
        for end in ("from", "to"):
            if isinstance(item.get(f"{end}Side"), str):
                shown[f"{end}_side"] = item[f"{end}Side"]
    return shown


def _choice(table: dict[str, str | None], name: str, what: str) -> str | None:
    if name not in table:
        raise EditError(f"{what} must be one of {', '.join(table)}, not '{name}'")
    return table[name]


def _sides(link: dict) -> dict:
    """The stored ``fromSide`` and ``toSide`` for a link's ``from_side`` and ``to_side``:
    both or neither, as the app's "connect side to side" writes them."""
    given = {f"{end}Side": link.get(f"{end}_side") for end in ("from", "to")}
    if all(name is None for name in given.values()):
        return {}
    if any(name is None for name in given.values()):
        raise EditError("from_side and to_side go together, as when a link is drawn from side to side in the web app")
    for key, name in given.items():
        if name not in SIDES:
            raise EditError(f"{key[:-4]}_side must be one of {', '.join(SIDES)}, not '{name}'")
    return given


# ── the plain form ─────────────────────────────────────────────────────────

_DIRECTIONS = {("none", "arrow"): "forward", ("arrow", "arrow"): "both",
               ("none", "none"): "none", ("arrow", "none"): "backward"}


def _direction(edge: dict) -> str:
    return _DIRECTIONS[("arrow" if edge.get("fromEnd") == "arrow" else "none",
                        "none" if edge.get("toEnd") == "none" else "arrow")]


def plain(canvas: dict, layout: bool = False) -> dict:
    """What a graph contains, without the drawing: notes, links, clusters, and (only
    when there are any) group boxes and the cards that are not notes. With ``layout``,
    each card and box also has its place and size (``x, y, width, height``)."""
    nodes = canvas.get("nodes") or []
    place = (lambda n: {k: n.get(k) for k in ("x", "y", "width", "height")}) if layout else (lambda n: {})  # noqa: E731
    of, tagged, boxed = _memberships(nodes)
    notes, others, groups = [], [], []
    for n in nodes:
        if n.get("type") == "text":
            notes.append({"id": n["id"], "text": split_tags(_text(n))[0], "clusters": sorted(of[n["id"]]), **appearance(n),
                          **place(n)})
        elif n.get("type") == "group":
            groups.append({**_box_report(nodes, n), **place(n)})
        else:
            what = {"link": ("web page", "url"), "file": ("file", "file")}.get(n.get("type"), ("card", "text"))
            others.append({"id": n["id"], "kind": what[0], what[1]: n.get(what[1]), "clusters": sorted(of[n["id"]]),
                           **appearance(n), **place(n)})
    links = [_plain_link(e) for e in canvas.get("edges") or []]
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


def free_spot(nodes: list[dict], center: tuple[float, float], size: tuple[int, int], joins: set[str],
              within: set[str] = frozenset()) -> tuple[int, int]:
    """Top-left for a new card (or block of cards) of ``size`` as near ``center`` as it
    can go while

    - at least ``GAP`` from every other card, and
    - wholly inside or wholly outside each group box, and inside only the boxes whose
      cluster is in ``joins`` (with the boxes around such a box, when it is inside that
      one too) or whose id is in ``within`` (the boxes a moving card is already in): a
      card inside a labelled box joins its cluster, so a position must never add a
      cluster nobody asked for.

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
        joined = [box for box in boxes if is_inside(card, box) and _group_cluster(box) in joins]
        return all(_apart(card, box, GAP) or (is_inside(card, box) and (box["id"] in within
                                                                         or any(is_inside(j, box) for j in joined)))
                   for box in boxes)

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
    """The stored edge for a link. Without ``from_side``/``to_side`` no sides are stored:
    the app and Obsidian attach such an edge to the facing sides of its two cards,
    wherever they move. With them, the sides sit where the app's "connect side to side"
    writes them (``{id, fromNode, fromSide, toNode, toSide}``); label, colour, line,
    arrows and path follow as the Inspector adds them."""
    if source == target:
        raise EditError(f"a link needs two different notes; '{link['from_note']}' was given as both ends")
    sides = _sides(link)
    edge = {"id": edge_id, "fromNode": source, **({"fromSide": sides["fromSide"]} if sides else {}),
            "toNode": target, **({"toSide": sides["toSide"]} if sides else {})}
    return _restyled(edge, link)


def _restyled(edge: dict, change: dict) -> dict:
    """``edge`` with the ``label`` ('' removes it), ``color``, ``line``, ``direction`` and
    ``path`` of ``change`` (those given), applied as the Inspector applies its fields,
    from top to bottom."""
    if change.get("label") is not None:
        edge = _patched(edge, {"label": change["label"] or None})
    if change.get("color") is not None:
        edge = _patched(edge, {"color": color_value(change["color"])})
    if change.get("line") is not None:
        edge = _patched(edge, _style_patch(edge, {"path": _choice(LINES, change["line"], "line")}))
    if change.get("direction") is not None:
        edge = _patched(edge, _choice(ENDS, change["direction"], "direction"))
    if change.get("path") is not None:
        edge = _patched(edge, _style_patch(edge, {"pathfindingMethod": _choice(PATHS, change["path"], "path")}))
    return edge


def _plain_link(edge: dict) -> dict:
    return {"id": edge["id"], "from_note": edge["fromNode"], "to_note": edge["toNode"], "direction": _direction(edge),
            **({"label": edge["label"]} if isinstance(edge.get("label"), str) and edge["label"] else {}),
            **appearance(edge)}


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


def _url(url: object) -> str:
    if not isinstance(url, str) or not url.strip():
        raise EditError("a web-page card needs its url, e.g. 'https://example.com'")
    return url.strip()


def add_notes(canvas: dict, notes: list[dict], links: list[dict] = ()) -> tuple[dict, list[dict], list[dict]]:
    """``canvas`` with a new text card per ``{text, clusters?, near?, key?}`` (a web-page
    card per ``{url, near?, key?}``) and a new edge per ``{from_note, to_note, label?,
    direction?, color?, line?, path?, from_side?, to_side?}`` (as ``_edge`` writes it),
    whose ends are a new note's ``key`` or the id of a card already there; and the plain
    form of what was added.

    A web-page card is the node the web app's "+ Link" makes and its URL field fills in:
    ``{type: "link", url, width: 400, height: 240, id, x, y}``, in that order. It has no
    text, so no tag line: it joins a cluster only by being in a group box.

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
        node_id = new_id({"nodes": nodes + [node for node, _ in made], "edges": canvas.get("edges")})
        if note.get("url") is not None:
            if note.get("text") is not None or note.get("clusters"):
                raise EditError("a web-page card has a url and nothing else: no text, and no clusters (it has no "
                                "text to hold #tags; put it in a group box to cluster it)")
            made.append(({"type": "link", "url": _url(note["url"]), "width": PAGE_WIDTH, "height": PAGE_HEIGHT,
                          "id": node_id}, note))
            continue
        if note.get("text") is None:
            raise EditError("a new note needs its text (or, for a web-page card, its url)")
        body, tags = _written(note["text"], note.get("clusters") or [])
        width, height = note_size(body)
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
        tags = {tag for m in block for tag in split_tags(_text(node_of[m]))[1]}
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
         "width": node["width"], "height": node["height"]} if node["type"] == "text" else
        {"type": "link", "url": node["url"], "width": node["width"], "height": node["height"], "id": node["id"],
         "x": node["x"], "y": node["y"]} for node, _ in made]
    after = {**canvas, "nodes": placed, **({"edges": edges} if added_edges else {})}
    added = [{"id": node["id"], **({"key": note["key"]} if note.get("key") else {}),
              **({"text": split_tags(node["text"])[0], "clusters": split_tags(node["text"])[1]} if node["type"] == "text"
                 else {"kind": "web page", "url": node["url"]})} for node, note in made]
    return after, added, [_plain_link(e) for e in added_edges]


def update_notes(canvas: dict, changes: list[dict]) -> tuple[dict, list[str]]:
    """``canvas`` with each ``{id, text?, clusters?, url?, file?}`` applied to its card,
    and a notice for each cluster a note keeps because it comes from a group box (which
    only the box no longer holding the card can change: update_group_boxes, or moving
    the card in the web app). ``clusters`` replaces the note's tags; ``text`` its body;
    ``url`` a web-page card's address and ``file`` a file card's path in the Obsidian
    vault, each changed in place as the web app's URL and File fields change them."""
    nodes = canvas.get("nodes") or []
    replaced: dict[str, dict] = {}
    for change in changes:
        node = replaced.get(change["id"]) or _find(nodes, change["id"])
        kind = node.get("type")
        field = {"link": "url", "file": "file"}.get(kind)
        wrong = [k for k in ("text", "clusters", "url", "file")
                 if change.get(k) is not None and k != field and not (kind == "text" and k in ("text", "clusters"))]
        if wrong:
            what = {"link": "is not a note but a web-page card (it has a url)",
                    "file": "is not a note but a file card (it has a file path)",
                    "group": "is not a note but a group box (update_group_boxes changes it)",
                    "text": "is a note (it has text and clusters)"}.get(kind, "is not a note")
            raise EditError(f"'{change['id']}' {what}, so it has no {wrong[0]} to change")
        if field:
            if change.get(field) is not None:
                value = _url(change["url"]) if field == "url" else change["file"].strip()
                if not value:
                    raise EditError("a file card needs its path in the Obsidian vault, e.g. 'notes/idea.md'")
                if value != node.get(field):
                    replaced[node["id"]] = _patched(node, {field: value})
            continue
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
    """``canvas`` with a new edge per ``{from_note, to_note, label?, direction?, color?,
    line?, path?, from_side?, to_side?}`` (as ``_edge`` writes it) between cards already
    there, and the plain form of the links added. Nothing moves."""
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


def update_links(canvas: dict, changes: list[dict]) -> tuple[dict, list[dict]]:
    """``canvas`` with each ``{id, label?, direction?, color?, line?, path?, from_side?,
    to_side?}`` applied to its link, in place (its id, ends and every other field stay),
    as the Inspector applies its fields from top to bottom: label ('' removes it),
    colour, line, arrows, path; then the sides, both or neither, which the app sets when
    a link is drawn from one side to another. Returns the links as they are now."""
    edges = canvas.get("edges") or []
    known = {e["id"]: e for e in edges}
    replaced: dict[str, dict] = {}
    for change in changes:
        if change["id"] not in known:
            raise EditError(f"'{change['id']}' names no link in this graph; read the graph for its link ids")
        edge = _patched(_restyled(replaced.get(change["id"]) or known[change["id"]], change), _sides(change))
        replaced[edge["id"]] = edge
    after = {**canvas, "edges": [replaced.get(e["id"], e) for e in edges]} if replaced else canvas
    return after, [_plain_link(replaced[i]) for i in dict.fromkeys(c["id"] for c in changes)]


def style_notes(canvas: dict, changes: list[dict]) -> tuple[dict, list[dict]]:
    """``canvas`` with each ``{id, color?, border?, shape?, text_align?}`` applied to its
    card as the Inspector applies them: ``color`` ('none' removes it), then the card's
    border, shape and text alignment in ``styleAttributes`` (the app's default of each
    is written by removing it). A group box takes a colour only: the app offers it no
    border, shape or alignment. Returns how each card looks now."""
    nodes = canvas.get("nodes") or []
    replaced: dict[str, dict] = {}
    for change in changes:
        node = replaced.get(change["id"]) or _find(nodes, change["id"])
        if node.get("type") == "group" and any(change.get(k) is not None for k in ("border", "shape", "text_align")):
            raise EditError(f"'{node['id']}' is a group box: it takes a colour, but no border, shape or text alignment")
        if change.get("color") is not None:
            node = _patched(node, {"color": color_value(change["color"])})
        attributes = {}
        for name, table, key in (("border", BORDERS, "border"), ("shape", SHAPES, "shape"), ("text_align", ALIGNS, "textAlign")):
            if change.get(name) is not None:
                attributes[key] = _choice(table, change[name], name)
        if attributes:
            node = _patched(node, _style_patch(node, attributes))
        replaced[node["id"]] = node
    after = {**canvas, "nodes": [replaced.get(n["id"], n) for n in nodes]} if replaced else canvas
    return after, [{"id": i, **appearance(replaced[i])} for i in dict.fromkeys(c["id"] for c in changes)]


# ── group boxes ────────────────────────────────────────────────────────────

def _label_width(label: str) -> int:
    """The least width that shows ``label`` in the app's label bar: "▾ " and the label
    in bold, with the bar's 12px padding either side."""
    return 24 + 16 + math.ceil(9 * len(label))


def _overlaps(a: dict, b: dict) -> bool:
    return not _apart(a, b, BOX_CLEAR)


def _bounds(items: list[dict]) -> dict:
    left, top = min(c["x"] for c in items), min(c["y"] for c in items)
    right, bottom = max(c["x"] + c["width"] for c in items), max(c["y"] + c["height"] for c in items)
    return {"x": left, "y": top, "width": right - left, "height": bottom - top}


def _padded(rect: dict) -> dict:
    """The least a box holding ``rect`` covers: its padding on every side and its label bar on top."""
    return {"x": rect["x"] - BOX_PAD, "y": rect["y"] - BOX_TOP,
            "width": rect["width"] + 2 * BOX_PAD, "height": rect["height"] + BOX_TOP + BOX_PAD}


def _rect_around(cards: list[dict], label: str) -> dict:
    """The box drawn around ``cards``: their bounds and the box's padding, wide enough for its label."""
    rect = _padded(_bounds(cards))
    return {**rect, "width": max(rect["width"], _label_width(label))}


def _geometry(node: dict) -> dict:
    return {k: node[k] for k in ("x", "y", "width", "height")}


def _reading_order(cards: list[dict]) -> list[dict]:
    return sorted(cards, key=lambda c: (c["y"], c["x"], c["id"]))


def _inside(nodes: list[dict], box: dict) -> list[dict]:
    """Every card and box wholly inside ``box``: what dragging the box in the app carries."""
    return [n for n in nodes if n["id"] != box["id"] and is_inside(n, box)]


def _holders(nodes: list[dict], items: list[dict], skip: set[str]) -> list[dict]:
    """The boxes (other than ``skip``) that wholly hold every one of ``items``, innermost first."""
    found = [g for g in nodes if g.get("type") == "group" and g["id"] not in skip and all(is_inside(i, g) for i in items)]
    return sorted(found, key=lambda g: (g["width"] * g["height"], g["id"]))


def _moved_out(nodes: list[dict], taken: list[dict], staying: list[dict], box_id: str) -> dict[str, tuple[int, int]]:
    """New top-lefts for ``taken`` cards, out of box ``box_id``: each at the nearest free
    spot clear of ``staying`` (and of those moved before it), still inside the other boxes
    it was in where there is room."""
    moves = {}
    for n in _reading_order(taken):
        within = {g["id"] for g in nodes if g.get("type") == "group" and g["id"] != box_id and is_inside(n, g)}
        moves[n["id"]] = free_spot(staying, _center([n]), (n["width"], n["height"]), set(), within)
        staying = [*staying, {**n, "x": moves[n["id"]][0], "y": moves[n["id"]][1]}]
    return moves


def _make_room(nodes: list[dict], box_id: str, rect: dict, holders: list[dict],
               keep: set[str]) -> tuple[dict[str, dict], dict[str, tuple[int, int]]]:
    """Room for box ``box_id`` at ``rect`` inside the boxes ``holders`` (innermost first):
    ``({box id: its new x, y, width and height}, {card or box id: new top-left})``.

    Level by level, from the box outwards: whatever is in the way of the box (or of a
    holder that grew), other than ``keep`` and what was already inside it, moves to the
    nearest free spot, a box with everything inside it. What sat in the next holder up
    stays in it: it may go past that holder's edge, and the holder grows round it. Each
    holder grows just enough to hold what is inside it with a box's padding and label
    bar around it. So a box drawn inside another never pushes notes out of their box,
    and nothing anywhere ends up overlapping a box it is not in."""
    was = {n["id"]: n for n in nodes}
    current = dict(was)
    current[box_id] = {**was.get(box_id, {"id": box_id, "type": "group"}), **rect}
    keep = set(keep) | {box_id}
    chain = [h["id"] for h in holders]
    grown: dict[str, dict] = {}
    moves: dict[str, tuple[int, int]] = {}
    level_id, level_rect, level_old, inner = box_id, rect, None, []
    for level in range(len(holders) + 1):
        if level:
            holder = holders[level - 1]
            want = _bounds([holder, *(_padded(r) for r in inner)])
            if want != _geometry(holder):
                grown[holder["id"]] = want
                current[holder["id"]] = {**current[holder["id"]], **want}
            level_id, level_rect, level_old = holder["id"], want, holder
        above = chain[level:]
        upper = was[above[0]] if above else None
        in_way = [n for n in current.values() if n["id"] not in keep and n["id"] not in chain and n["id"] != level_id
                  and not (level_old is not None and is_inside(was[n["id"]], level_old)) and _overlaps(n, level_rect)]
        tops = [n for n in in_way if not any(m is not n and m.get("type") == "group" and is_inside(n, m) for m in in_way)]
        beside = []
        for n in _reading_order(tops):
            carried = [m for m in current.values() if n.get("type") == "group" and m["id"] != n["id"] and is_inside(m, n)]
            gone = {n["id"], *(m["id"] for m in carried)}
            sibling = upper is not None and is_inside(was[n["id"]], upper)
            staying = [m for m in current.values() if m["id"] not in gone and not (sibling and m["id"] in above)]
            within = set() if sibling else {g["id"] for g in was.values() if g.get("type") == "group" and g["id"] not in gone
                                            and g["id"] not in grown and g["id"] != level_id and is_inside(was[n["id"]], g)}
            x, y = free_spot(staying, _center([n]), (n["width"], n["height"]), set(), within)
            for m in (n, *carried):
                current[m["id"]] = {**m, "x": m["x"] + x - n["x"], "y": m["y"] + y - n["y"]}
                moves[m["id"]] = (current[m["id"]]["x"], current[m["id"]]["y"])
            if sibling:
                keep |= gone
                beside.append(current[n["id"]])
        inner = [level_rect, *beside]
    return grown, moves


def fit_box(nodes: list[dict], edges: list[dict], box_id: str, label: str,
            members: list[str]) -> tuple[dict, dict[str, tuple[int, int]], dict[str, dict]]:
    """Where box ``box_id`` goes so that it holds ``members`` and nothing else, the cards
    that must move for that, and the boxes around it that grow: ``(x, y, width and height
    of the box, {card id: new top-left}, {box id: its new x, y, width and height})``.

    A member may be a box: it comes with everything inside it, as it does when it is
    dragged in the app. Any card or box that comes within ``BOX_CLEAR`` of the box counts
    as in it; the box itself, when it is already there, is no obstacle. With ``R`` the box
    drawn around the members where they are:

    0. when a box holds every member already, the new box is drawn inside it (boxes
       nest) at ``R``, unless another box is in ``R``: each card in the way moves to the
       nearest free spot, staying in that box when it was in it, and that box (and the
       ones around it) grows to keep room around everything in it (``_grow``).

    Otherwise, the first case that applies (so the fewest cards move):

    1. nothing else is in ``R``: the box is ``R`` and nothing moves;
    2. only cards are in ``R``, fewer of them than there are members (none of them is in
       a box: that box would be in ``R`` too): each moves to the nearest free spot,
       outside ``R`` and every box it was not already in;
    3. only boxes are in ``R`` (a member sits in another box, or ``R`` crosses one): the
       members move together, arranged as they are, to the nearest place where the box
       is clear of everything;
    4. otherwise the members are laid out afresh (along their links, as ``add_notes``
       lays out new notes, or in a grid) and placed as in 3.

    A card that moves never lands in a box it was not in, so the only clusters a move
    changes are those of a box a member had to leave.
    """
    of = {n["id"]: n for n in nodes}
    named = [of[m] for m in members]
    carried = {n["id"]: n for m in named if m.get("type") == "group" for n in _inside(nodes, m)}
    tops = [m for m in named if m["id"] not in carried]  # a member inside another member comes with it
    holders = _holders(nodes, tops, {box_id, *members, *carried})
    chain = {h["id"] for h in holders}
    others = [n for n in nodes if n["id"] != box_id and n["id"] not in members and n["id"] not in carried]
    rest = [n for n in others if n["id"] not in chain]
    rect = _rect_around(tops, label)
    intruders = [n for n in rest if n.get("type") != "group" and _overlaps(n, rect)]
    clashes = [n for n in rest if n.get("type") == "group" and _overlaps(n, rect)]
    if holders and not clashes:
        grown, moves = _make_room(nodes, box_id, rect, holders, {*members, *carried})
        return rect, moves, grown
    moves: dict[str, tuple[int, int]] = {}
    if clashes or intruders:
        if not clashes and len(intruders) < len(tops):
            moves = _moved_out(nodes, intruders, [n for n in nodes if n["id"] != box_id and n not in intruders]
                               + [{**rect, "id": box_id, "type": "group"}], box_id)
        else:
            if intruders:
                sizes = {c["id"]: (c["width"], c["height"]) for c in _reading_order(tops)}
                chosen = set(sizes)
                among = [(e["fromNode"], e["toNode"]) for e in edges
                         if e["fromNode"] in chosen and e["toNode"] in chosen and e["fromNode"] != e["toNode"]]
                at = flow_layout(sizes, among) if among else _grid(sizes)
            else:
                left, top = min(c["x"] for c in tops), min(c["y"] for c in tops)
                at = {c["id"]: (c["x"] - left, c["y"] - top) for c in tops}
            width = max(max(at[c["id"]][0] + c["width"] for c in tops) + 2 * BOX_PAD, _label_width(label))
            height = max(at[c["id"]][1] + c["height"] for c in tops) + BOX_TOP + BOX_PAD
            x, y = free_spot(others, _center([rect]), (width, height), set(), chain)
            rect = {"x": x, "y": y, "width": width, "height": height}
            for c in tops:
                moves[c["id"]] = (x + BOX_PAD + at[c["id"]][0], y + BOX_TOP + at[c["id"]][1])
                for n in (_inside(nodes, c) if c.get("type") == "group" else []):
                    moves[n["id"]] = (n["x"] + moves[c["id"]][0] - c["x"], n["y"] + moves[c["id"]][1] - c["y"])
    # a box drawn elsewhere that still landed in a box it was in: that box grows round it
    still = [h for h in holders if is_inside(rect, h)]
    grown, pushed = _make_room(_moved(nodes, moves), box_id, rect, still, {*members, *carried, *moves})
    return rect, {**moves, **pushed}, grown


def widen_box(nodes: list[dict], box: dict, label: str) -> tuple[dict, dict[str, tuple[int, int]], dict[str, dict]]:
    """How box ``box`` changes when it is renamed to ``label``, the cards that must move
    for that, and the boxes around it that grow: ``({x and width of the box}, {card id:
    new top-left}, {box id: its new x, y, width and height})``.

    A rename never re-lays a box: its y, height and the cards wholly inside it stay. A box
    already wide enough to show the label keeps its place and size (``{}``). A narrower one
    widens just as far as the label needs, to the right, or to the left (its right edge
    kept) when the new strip on the right would come within ``BOX_CLEAR`` of another box
    (other than one it is inside); when both sides would, the rename is refused. Every
    other card the new strip would cover or come within ``BOX_CLEAR`` of moves to the
    nearest free spot, outside every box it was not in; one in the box this box is inside
    stays in it, as ``_make_room`` keeps it. A box it is inside grows to keep room around
    what is in it.
    """
    need = _label_width(label)
    if box["width"] >= need:
        return {}, {}, {}
    extra = need - box["width"]
    holders = _holders(nodes, [box], {box["id"]})
    others = [n for n in nodes if n["id"] != box["id"] and not is_inside(n, box) and n not in holders]
    for strip_x in (box["x"] + box["width"], box["x"] - extra):
        strip = {"x": strip_x, "y": box["y"], "width": extra, "height": box["height"]}
        if any(n.get("type") == "group" and _overlaps(n, strip) for n in others):
            continue
        rect = {"x": min(box["x"], strip_x), "width": need}
        intruders = [n for n in others if n.get("type") != "group" and _overlaps(n, strip)
                     and not (holders and is_inside(n, holders[0]))]  # those _make_room keeps in their box
        moves = _moved_out(nodes, intruders, [n for n in nodes if n["id"] != box["id"] and n not in intruders]
                           + [{**box, **rect}], box["id"])
        inside = {n["id"] for n in _inside(nodes, box)}
        grown, pushed = _make_room(_moved(nodes, moves), box["id"], {**_geometry(box), **rect}, holders, {*inside, *moves})
        return rect, {**moves, **pushed}, grown
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


def _moved(nodes: list[dict], moves: dict[str, tuple[int, int]], grown: dict[str, dict] | None = None) -> list[dict]:
    """``nodes`` with cards at their new top-lefts and boxes at their new geometry."""
    grown = grown or {}
    return [_patched(n, grown[n["id"]]) if n["id"] in grown
            else _patched(n, {"x": moves[n["id"]][0], "y": moves[n["id"]][1]}) if n["id"] in moves else n for n in nodes]


def _stacked(nodes: list[dict], box_id: str) -> list[dict]:
    """``nodes`` with box ``box_id`` written before the first box it holds, or else after
    the last box it is inside: the app draws every box at the same depth, so a box later
    in the canvas covers one before it, and an inner box must stay on top to be clicked."""
    box = next(n for n in nodes if n["id"] == box_id)
    rest = [n for n in nodes if n is not box]
    at = nodes.index(box)
    held = [i for i, n in enumerate(rest) if n.get("type") == "group" and is_inside(n, box)]
    holders = [i for i, n in enumerate(rest) if n.get("type") == "group" and is_inside(box, n) and not is_inside(n, box)]
    if held and min(held) < at:
        at = min(held)
    elif holders and max(holders) >= at:
        at = max(holders) + 1
    return [*rest[:at], box, *rest[at:]]


def _box_label(label: str | None) -> str:
    if not isinstance(label, str) or not label.strip():
        raise EditError("a group box needs a label: it names the cluster of the notes inside it")
    return label.strip()


def _box_members(nodes: list[dict], ids: list[str], claimed: dict[str, str], box: str) -> list[str]:
    """``ids`` checked as what ``box`` can hold: at least one card or box of this graph,
    not the box itself nor a box it is inside, and nothing another box of the same call
    names (``claimed``: id -> that box), unless that box holds all of ``ids``, so that
    this one is drawn inside it."""
    if not ids:
        raise EditError("a group box must hold at least one note; to take a box away, use remove_group_boxes")
    of = {n["id"]: n for n in nodes}
    current = of.get(box)
    for card_id in ids:
        member = _find(nodes, card_id, "note")
        if card_id == box:
            raise EditError(f"group box '{box}' cannot hold itself")
        if current is not None and member.get("type") == "group" and is_inside(current, member):
            raise EditError(f"group box '{card_id}' holds group box '{box}', so '{box}' cannot hold it")
        other = claimed.get(card_id, box)
        if other != box and not (other in of and all(is_inside(of[i], of[other]) for i in ids if i in of)):
            raise EditError(f"'{card_id}' is given to two boxes in one call; give it to the inner box only, "
                            "and the inner box (its id or key) to the outer one")
    for card_id in ids:
        claimed[card_id] = box
    return list(dict.fromkeys(ids))


def _box_report(nodes: list[dict], box: dict) -> dict:
    label = box.get("label") if isinstance(box.get("label"), str) and box["label"].strip() else None
    inner = _inside(nodes, box)
    boxes = [n["id"] for n in inner if n.get("type") == "group"]
    return {"id": box["id"], "label": label, "cluster": _group_cluster(box),
            "contains": [n["id"] for n in inner if n.get("type") != "group"],
            **({"boxes": boxes} if boxes else {}), "collapsed": box.get("collapsed") is True, **appearance(box)}


def _moves_report(before: list[dict], after: list[dict], boxes: set[str]) -> tuple[list[str], list[str]]:
    """The cards whose place changed, and notices: for each box (other than ``boxes``,
    the ones this call made or changed) that a moved card left, and so its cluster, and
    for each box moved out of the way (an empty one has no card to show it moved)."""
    was = {n["id"]: n for n in before}
    now = {n["id"]: n for n in after}
    moved = [n["id"] for n in after if n["id"] in was and n.get("type") != "group"
             and (n["x"], n["y"]) != (was[n["id"]]["x"], was[n["id"]]["y"])]
    notices = [f"group box '{g['id']}' was moved out of the way, with everything inside it" for g in after
               if g.get("type") == "group" and g["id"] in was and g["id"] not in boxes
               and (g["x"], g["y"]) != (was[g["id"]]["x"], was[g["id"]]["y"])
               and (g["width"], g["height"]) == (was[g["id"]]["width"], was[g["id"]]["height"])]  # a box that grew says so
    for card_id in moved:
        for g in before:
            if g.get("type") == "group" and g["id"] not in boxes and is_inside(was[card_id], g) and not is_inside(now[card_id], now[g["id"]]):
                name = _group_cluster(g)
                notices.append(f"'{card_id}' was moved out of group box '{g['id']}'"
                               + (f", so it is no longer in cluster '{name}'" if name else ""))
    return moved, notices


def _grown_report(grown: set[str], boxes: set[str]) -> list[str]:
    return [f"group box '{g}' grew to keep room around the box inside it" for g in sorted(grown) if g not in boxes]


def add_group_boxes(canvas: dict, boxes: list[dict]) -> tuple[dict, list[dict], list[str], list[str]]:
    """``canvas`` with a new group box per ``{label, notes, collapsed?, key?}`` holding
    exactly those cards and boxes (``fit_box``; with no notes, an empty box of the app's
    new box's size, clear of everything; boxes are made in the order given, each
    clear of the ones before unless it holds them or sits inside one); the plain form of
    the new boxes; the cards that moved; and notices. ``notes`` may name a box made
    earlier in the same call by its ``key``.

    A new box is the group node the web app makes ({type, label, width, height, id, x,
    y}, in that order, no colour), with ``collapsed: true`` last when it starts
    collapsed, as the app's "Starts collapsed" writes it. It goes last in the canvas,
    as the app adds one, unless it holds boxes: then it goes just before the first of
    them (``_stacked``), so that the app draws them over it and they can be clicked.
    """
    before = list(canvas.get("nodes") or [])
    nodes, edges = list(before), canvas.get("edges") or []
    claimed: dict[str, str] = {}
    made, keys, grew = [], {}, set()
    for spec in boxes:
        label = _box_label(spec.get("label"))
        box_id = new_id({"nodes": nodes, "edges": edges})
        key = spec.get("key")
        if key and (key in keys or any(n["id"] == key for n in nodes)):
            raise EditError(f"key '{key}' must be unique: another new box or a card of this graph has it")
        if spec.get("notes"):
            members = _box_members(nodes, [keys.get(m, m) for m in spec["notes"]], claimed, box_id)
            rect, moves, grown = fit_box(nodes, edges, box_id, label, members)
        else:  # the web app's "+ Group": its new box's size, somewhere clear
            size = (max(EMPTY_BOX[0], _label_width(label)), EMPTY_BOX[1])
            x, y = free_spot(nodes, _center(nodes) if nodes else (0, 0), size, set())
            rect, moves, grown = {"x": x, "y": y, "width": size[0], "height": size[1]}, {}, {}
        nodes = _moved(nodes, moves, grown)
        grew |= set(grown)
        nodes.append({"type": "group", "label": label, "width": rect["width"], "height": rect["height"],
                      "id": box_id, "x": rect["x"], "y": rect["y"], **({"collapsed": True} if spec.get("collapsed") else {})})
        nodes = _stacked(nodes, box_id)
        made.append(box_id)
        if key:
            keys[key] = box_id
    if not made:
        return canvas, [], [], []
    moved, notices = _moves_report(before, nodes, set(made))
    key_of = {box_id: key for key, box_id in keys.items()}
    report = [{**_box_report(nodes, _find(nodes, b)), **({"key": key_of[b]} if b in key_of else {})} for b in made]
    return {**canvas, "nodes": nodes}, report, moved, notices + _grown_report(grew, set(made))


def update_group_boxes(canvas: dict, changes: list[dict]) -> tuple[dict, list[dict], list[str], list[str]]:
    """``canvas`` with each ``{id, label?, notes?, collapsed?}`` applied to its group box:
    ``label`` renames it (and so its cluster; the box keeps its place, its size and the
    cards inside it, and only widens when it is too narrow to show the new label,
    ``widen_box``), ``notes`` is the full new set of cards and boxes it holds (the box is
    refitted around them, ``fit_box``, and written before the boxes it now holds,
    ``_stacked``), ``collapsed`` sets whether it starts collapsed.
    What is left out stays, and so does every other field of the box (its colour,
    anything the app or Obsidian added). Returns the boxes as they are now, the cards
    that moved, and notices."""
    before = list(canvas.get("nodes") or [])
    nodes, edges = list(before), canvas.get("edges") or []
    claimed: dict[str, str] = {}
    grew: set[str] = set()
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
            rect, moves, grown = fit_box(nodes, edges, box["id"], label, members)
        elif "label" in patch:
            rect, moves, grown = widen_box(nodes, box, label)
        else:
            rect, moves, grown = {}, {}, {}
        nodes = _moved(nodes, moves, grown)
        grew |= set(grown)
        patch |= rect
        if change.get("collapsed") is not None:
            patch["collapsed"] = True if change["collapsed"] else None
        nodes = [_patched(n, patch) if n["id"] == box["id"] else n for n in nodes]
        if change.get("notes") is not None:
            nodes = _stacked(nodes, box["id"])
    changed = list(dict.fromkeys(c["id"] for c in changes))
    after = {**canvas, "nodes": nodes} if nodes != before else canvas
    moved, notices = _moves_report(before, nodes, set(changed))
    return after, [_box_report(nodes, _find(nodes, b)) for b in changed], moved, notices + _grown_report(grew, set(changed))


def remove_group_boxes(canvas: dict, ids: list[str]) -> tuple[dict, list[str]]:
    """``canvas`` without those group boxes, and without any link drawn to a box itself
    (returned, as link ids). The cards inside stay exactly where they are."""
    nodes = canvas.get("nodes") or []
    for box_id in ids:
        if _find(nodes, box_id).get("type") != "group":
            raise EditError(f"'{box_id}' is not a group box; remove_notes removes notes and cards")
    return remove_cards(canvas, ids)


# ── moving and resizing by hand ────────────────────────────────────────────

MIN_SIZE = 40  # the app's resize handles stop here (GraphView.tsx)


def _js_round(value: float) -> int:
    """``Math.round``, as the app rounds a dragged position: halves go up (Python's ``round`` goes to even)."""
    return math.floor(value + 0.5)


def move_cards(canvas: dict, changes: list[dict]) -> tuple[dict, list[dict], list[str]]:
    """``canvas`` with each ``{id, x?, y?, width?, height?}`` applied as the web app applies
    a drag or a resize, in the order given: a card or box takes its new top-left and size
    (never below ``MIN_SIZE``). An entry that changes only a box's place is a drag: the box
    carries every card and box wholly inside it, by the same distance. An entry that
    changes a box's width or height, with or without its place, is a resize from a handle
    and carries nothing, so moving a box and resizing it are two entries, a move then a
    resize. What is left out stays. Returns every card and box whose place or size changed
    (its id and new geometry), and notices: a card that now enters or leaves a labelled box joins or
    leaves its cluster, and a card that now overlaps another card."""
    before = canvas.get("nodes") or []
    nodes = list(before)
    for change in changes:
        node = _find(nodes, change["id"])
        if all(change.get(k) is None for k in ("x", "y", "width", "height")):
            continue
        for side in ("width", "height"):
            if change.get(side) is not None and change[side] < MIN_SIZE:
                raise EditError(f"{side} of '{node['id']}' must be at least {MIN_SIZE}, as in the web app")
        dx = change["x"] - node["x"] if change.get("x") is not None else 0
        dy = change["y"] - node["y"] if change.get("y") is not None else 0
        size = {k: _js_round(change[k]) for k in ("width", "height") if change.get(k) is not None}
        resized = any(size[k] != node[k] for k in size)
        carried = {n["id"] for n in _inside(nodes, node)} if node.get("type") == "group" and (dx or dy) and not resized else set()
        place = lambda n: {"x": _js_round(n["x"] + dx), "y": _js_round(n["y"] + dy)}  # noqa: E731
        nodes = [_patched(n, place(n)) if n["id"] in carried
                 else _patched(n, {**place(n), **size}) if n["id"] == node["id"] else n for n in nodes]
    was = {n["id"]: n for n in before}
    changed = [n for n in nodes if _geometry(n) != _geometry(was[n["id"]])]
    if not changed:
        return canvas, [], []
    of_before, _, _ = _memberships(before)
    of_after, _, _ = _memberships(nodes)
    notices = []
    for n in nodes:
        if n.get("type") == "group":
            continue
        joined = [c for c in of_after[n["id"]] if c not in of_before[n["id"]]]
        left = [c for c in of_before[n["id"]] if c not in of_after[n["id"]]]
        notices += [f"'{n['id']}' is now inside a group box, so it is in cluster '{c}'" for c in joined]
        notices += [f"'{n['id']}' left a group box, so it is no longer in cluster '{c}'" for c in left]
        if n not in changed:
            continue
        notices += [f"'{n['id']}' now overlaps '{o['id']}'" for o in nodes
                    if o["id"] != n["id"] and o.get("type") != "group" and not _apart(n, o, 0)
                    and (o not in changed or o["id"] > n["id"])]
    return {**canvas, "nodes": nodes}, [{"id": n["id"], **_geometry(n)} for n in changed], notices

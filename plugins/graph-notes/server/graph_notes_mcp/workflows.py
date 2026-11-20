"""Workflows: named routes through a graph along its links, which people follow with Next
and Previous in the web app.

A workflow is graph data, kept in the canvas document's top-level ``workflows`` list
beside ``nodes`` and ``edges`` (the list's order is the workflows' order)::

    {"id": "<16 hex digits>", "name": "W1 · axes", "description": "What does PCA compute?",
     "start": "<card id>" | null, "steps": [{"link": "<link id>", "from": "<card id>"}, ...],
     "after": ["<workflow id>", ...]}

``description`` and ``after`` are optional. A step names an existing link and the card it
is walked from, along the link's arrow (a two-way or plain link either way). The ends are
derived: the cards on the route with no next step.

References are read leniently: a step whose link is gone, a start card that was deleted
or a prerequisite that no longer exists is a warning, never a refusal, so a save from a
client that does not know workflows never breaks the graph. What is made here is checked
strictly, except loops: until issue 46 is decided, a step that makes the route loop back is
accepted with a warning that loops are not supported yet. The edits that remove links or
cards also remove the steps that used them (``pruned``), in the same version.

Follow walks a route one depth level per step: a card's step is one more than the
longest way to it from the start, so a card comes after everything leading into it, and
at a split it follows every branch. Within a step the cards are in reading order (top to
bottom, then left to right). Loops are not designed yet
(https://github.com/akshayDev17/graph-notes/issues/46): a route that loops back is
reported, and given no step order, rather than walked.

Pure functions over parsed JSON, as in ``canvas``: no I/O.
"""

from __future__ import annotations

from . import canvas
from .canvas import EditError

LOOPS_ISSUE = "https://github.com/akshayDev17/graph-notes/issues/46"
TITLE_LENGTH = 80


# ── reading ────────────────────────────────────────────────────────────────

def all_of(doc: dict) -> list[dict]:
    """The graph's workflows, in their order (anything in the list that is not one is passed over)."""
    listed = doc.get("workflows")
    return [w for w in listed if isinstance(w, dict) and isinstance(w.get("id"), str)] if isinstance(listed, list) else []


def title(node: dict) -> str:
    """How a card is named to a person: a note's first line (its heading marks taken off),
    a web page's address, a file's path, a box's label."""
    kind = node.get("type")
    if kind == "text":
        body = canvas.split_tags(node["text"] if isinstance(node.get("text"), str) else "")[0]
        line = next((line.strip() for line in body.split("\n") if line.strip()), "")
        shown = line.lstrip("#").strip().strip("*").strip()
    else:
        shown = str(node.get({"link": "url", "file": "file", "group": "label"}.get(kind, "id")) or "").strip()
    if not shown:
        return f"(empty card {node['id']})"
    return _short(shown)


def _short(text: str) -> str:
    return text if len(text) <= TITLE_LENGTH else text[:TITLE_LENGTH - 1].rstrip() + "…"


def note(names: list[str], done: str) -> str:
    """The short label History gives a version that changed these workflows: one by its name
    ('W1 · axes updated'), more by their number ('2 workflows added')."""
    return f"{_short(str(names[0]))} {done}" if len(names) == 1 else f"{len(names)} workflows {done}"


def _walkable(edge: dict, start: str) -> bool:
    """Whether ``edge`` may be walked from card ``start``: along its arrow, or either way when it
    has arrows at both ends or at neither."""
    direction = canvas._direction(edge)
    return direction in ("both", "none") or start == (edge["fromNode"] if direction == "forward" else edge["toNode"])


def _reading(nodes: dict[str, dict]):
    def key(card: str) -> tuple:
        n = nodes.get(card, {})
        return (n.get("y", 0), n.get("x", 0), card)
    return key


def route(doc: dict, workflow: dict) -> dict:
    """Where ``workflow`` goes in ``doc``: ``{start, walks, cards, levels, ends, warnings}``.

    ``walks`` are its usable steps reached from the start, as ``(link, from card, to card)``
    in stored order; ``cards`` the cards they reach (with the start); ``levels`` Follow's
    steps, each a list of cards in reading order, or None when there is no start or the
    route loops; ``ends`` the cards with no next step; ``warnings`` what is wrong with it,
    for the person to fix. Never refuses and never loops forever."""
    nodes = {n["id"]: n for n in doc.get("nodes") or [] if isinstance(n, dict) and "id" in n}
    edges = {e["id"]: e for e in doc.get("edges") or [] if isinstance(e, dict) and "id" in e}
    named = lambda card: title(nodes[card]) if card in nodes else card  # noqa: E731
    warnings: list[str] = []
    walks: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for step in workflow.get("steps") or []:
        link = step.get("link") if isinstance(step, dict) else None
        start_of = step.get("from") if isinstance(step, dict) else None
        edge = edges.get(link)
        if edge is None:
            warnings.append(f"a step's link '{link}' is no longer in the graph, so that step is skipped")
            continue
        ends = (edge["fromNode"], edge["toNode"])
        if start_of not in ends:
            warnings.append(f"the step over link '{link}' starts at '{start_of}', which is not one of the link's "
                            "two cards, so that step is skipped")
            continue
        to = ends[1] if start_of == ends[0] else ends[0]
        if link in seen:
            warnings.append(f"the step {named(start_of)} → {named(to)} is listed twice")
            continue
        seen.add(link)
        if not _walkable(edge, start_of):
            warnings.append(f"the step {named(start_of)} → {named(to)} now goes against its link's arrow")
        walks.append((link, start_of, to))

    start = workflow.get("start")
    if start is None:
        warnings.append("it has no start card: give it one (update_workflows with start)")
    elif start not in nodes:
        warnings.append(f"its start card '{start}' is no longer in the graph: give it a new one "
                        "(update_workflows with start)")
        start = None

    reached: set[str] = set()
    if start is not None:
        reached.add(start)
        frontier = [start]
        while frontier:
            card = frontier.pop()
            for _, start_of, to in walks:
                if start_of == card and to not in reached:
                    reached.add(to)
                    frontier.append(to)
    cut_off = [w for w in walks if w[1] not in reached]
    if cut_off and start is not None:
        warnings.append(f"{len(cut_off)} step{'s' * (len(cut_off) != 1)} can't be reached from the start card "
                        f"{named(start)}: " + ", ".join(f"{named(a)} → {named(b)}" for _, a, b in cut_off))
    walks = [w for w in walks if w[1] in reached]

    loops = _loops(start, walks) if start is not None else []
    if loops:
        warnings.append("the route loops back: " + ", ".join(f"{named(a)} → {named(b)}" for _, a, b in loops)
                        + f" leads back to a card before it. Loops in a route are not supported yet ({LOOPS_ISSUE}), "
                        "so it has no step order to follow")

    known = {w["id"] for w in all_of(doc)}
    for other in workflow.get("after") or []:
        if other not in known:
            warnings.append(f"it comes after workflow '{other}', which is no longer in the graph")

    order = _reading(nodes)
    levels = None if start is None or loops else _levels(start, walks, order)
    leads_on = {a for _, a, _ in walks}
    placed = {card: i for i, level in enumerate(levels or []) for card in level}
    ends = sorted((card for card in reached if card not in leads_on), key=lambda c: (placed.get(c, 0), order(c)))
    return {"start": start, "walks": walks, "cards": reached, "levels": levels, "ends": ends, "warnings": warnings}


def _loops(start: str, walks: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    """The steps that lead back to a card still being walked, depth first from ``start``."""
    state = {start: 1}
    loops = []
    path = [(start, iter([w for w in walks if w[1] == start]))]
    while path:
        card, onward = path[-1]
        walk = next(onward, None)
        if walk is None:
            state[card] = 2
            path.pop()
        elif state.get(walk[2]) == 1:
            loops.append(walk)
        elif walk[2] not in state:
            state[walk[2]] = 1
            path.append((walk[2], iter([w for w in walks if w[1] == walk[2]])))
    return loops


def _levels(start: str, walks: list[tuple[str, str, str]], order) -> list[list[str]]:
    """Follow's steps for a route without loops: each card at one more than the longest way to it."""
    level = {start: 0}
    waiting = {to: sum(1 for w in walks if w[2] == to) for _, _, to in walks}
    ready = [start]
    while ready:
        card = ready.pop()
        for _, start_of, to in walks:
            if start_of == card:
                level[to] = max(level.get(to, 0), level[card] + 1)
                waiting[to] -= 1
                if waiting[to] == 0:
                    ready.append(to)
    return [sorted((c for c, n in level.items() if n == i), key=order) for i in range(max(level.values()) + 1)]


def _card(nodes: dict[str, dict], card: str) -> dict:
    return {"id": card, "title": title(nodes[card]) if card in nodes else card}


def summary(doc: dict, workflow: dict, found: dict | None = None) -> dict:
    """A workflow as a list shows it: id, name, question, start card, how many cards and
    steps it has and how many steps Follow takes, its ends, what it comes after, and warnings."""
    nodes = {n["id"]: n for n in doc.get("nodes") or [] if isinstance(n, dict) and "id" in n}
    names = {w["id"]: w.get("name") for w in all_of(doc)}
    found = found or route(doc, workflow)
    description = workflow.get("description")
    return {
        "id": workflow["id"], "name": workflow.get("name"),
        **({"description": description} if isinstance(description, str) and description else {}),
        "start": _card(nodes, found["start"]) if found["start"] is not None else None,
        "cards": len(found["cards"]), "steps": len(workflow.get("steps") or []),
        "follow_steps": len(found["levels"] or []),
        "ends": [_card(nodes, c) for c in found["ends"]],
        "after": [{"id": w, "name": names[w]} for w in workflow.get("after") or [] if w in names],
        **({"warnings": found["warnings"]} if found["warnings"] else {}),
    }


def listed(doc: dict) -> list[dict]:
    return [summary(doc, w) for w in all_of(doc)]


def find(doc: dict, workflow_id: str) -> dict:
    for w in all_of(doc):
        if w["id"] == workflow_id:
            return w
    there = ", ".join(f"'{w['id']}' ({w.get('name')})" for w in all_of(doc)) or "none"
    raise EditError(f"'{workflow_id}' names no workflow in this graph; its workflows are: {there}")


def directions(doc: dict, workflow_id: str) -> dict:
    """The route of a workflow as Follow walks it: one entry per step (a depth level), with
    its cards, the cards it came from, the cards it leads to (a card reached by a longer way
    is marked as reached at a later step) and the links in and out; its ends; the workflows
    that come after it; and all of that as plain directions text."""
    workflow = find(doc, workflow_id)
    nodes = {n["id"]: n for n in doc.get("nodes") or [] if isinstance(n, dict) and "id" in n}
    found = route(doc, workflow)
    others = [w for w in all_of(doc) if w["id"] != workflow["id"]]
    step_of = {card: i for i, level in enumerate(found["levels"] or [], 1) for card in level}
    order = _reading(nodes)
    ends = set(found["ends"])
    steps = []
    for i, level in enumerate(found["levels"] or [], 1):
        into = [w for w in found["walks"] if w[2] in level]
        onward = [w for w in found["walks"] if w[1] in level]
        came = sorted({w[1] for w in into}, key=lambda c: (step_of[c], order(c)))
        going = sorted({w[2] for w in onward}, key=lambda c: (step_of[c], order(c)))
        steps.append({
            "step": i,
            "cards": [{**_card(nodes, c), **({"end": True} if c in ends else {}),
                       **({"starts": [{"id": w["id"], "name": w.get("name")} for w in others if w.get("start") == c]}
                          if any(w.get("start") == c for w in others) else {})} for c in level],
            "came_from": [{**_card(nodes, c), "step": step_of[c]} for c in came],
            "next": [{**_card(nodes, c), "step": step_of[c], **({"reached_later": True} if step_of[c] > i + 1 else {})}
                     for c in going],
            "links_in": [w[0] for w in into],
            "links_out": [w[0] for w in onward],
        })
    followed_by = [{"id": w["id"], "name": w.get("name")} for w in others if workflow["id"] in (w.get("after") or [])]
    shown = summary(doc, workflow, found)
    return {"workflow": shown, "route": steps, "ends": shown["ends"], "followed_by": followed_by,
            "directions": _text(shown, steps, followed_by)}


def _text(shown: dict, steps: list[dict], followed_by: list[dict]) -> str:
    """The route as directions a person can read: step by step, what you see and where it goes."""
    head = shown["name"] + (f": {shown['description']}" if shown.get("description") else "")
    lines = [head]
    if shown["after"]:
        lines.append("Comes after " + ", ".join(w["name"] for w in shown["after"]) + ".")
    if not steps:
        lines.append("There is no route to follow: " + "; ".join(shown.get("warnings") or ["it has no steps"]) + ".")
        return "\n".join(lines)
    lines.append(f"{len(steps)} step{'s' * (len(steps) != 1)}: each step is one depth level of the route, and at a "
                 "split Next follows every branch.")
    for step in steps:
        def card(c: dict) -> str:
            notes = [*(["an end"] if c.get("end") and step["step"] > 1 else []),
                     *(f"starts {w['name']}" for w in c.get("starts", []))]
            return c["title"] + (f" ({'; '.join(notes)})" if notes else "")
        kind = " (start)" if step["step"] == 1 else ""
        lines.append(f"Step {step['step']} of {len(steps)}{kind}: " + "; ".join(card(c) for c in step["cards"]) + ".")
        if step["came_from"]:
            lines.append("  From: " + ", ".join(c["title"] for c in step["came_from"]) + ".")
        soon = [c["title"] for c in step["next"] if not c.get("reached_later")]
        later = [f"{c['title']} (step {c['step']})" for c in step["next"] if c.get("reached_later")]
        if soon or later:
            lines.append("  Next: " + ", ".join(soon) + ("; " if soon and later else "")
                         + ("and later " + ", ".join(later) if later else "") + ".")
    lines.append("Ends: " + ", ".join(c["title"] for c in shown["ends"]) + ".")
    if followed_by:
        lines.append("After it: " + ", ".join(w["name"] for w in followed_by) + ".")
    if shown.get("warnings"):
        lines.append("Warnings: " + "; ".join(shown["warnings"]) + ".")
    return "\n".join(lines)


# ── edits ──────────────────────────────────────────────────────────────────

def _name(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EditError("a workflow needs a name, e.g. 'W1 · axes' or the question it answers")
    return value.strip()


def _start(nodes: dict[str, dict], card: str) -> str:
    if card not in nodes:
        raise EditError(f"start '{card}' names no note or card in this graph; read the graph for its ids")
    if nodes[card].get("type") == "group":
        raise EditError(f"start '{card}' is a group box; a workflow starts at a note or another card")
    return card


def _step(doc: dict, step: dict) -> tuple[str, str, str]:
    """``(link, from card, to card)`` for a step named by its link (``link``, with ``from_note``
    when the link may be walked either way) or by the two notes it joins (``from_note``,
    ``to_note``), walked along the link's arrow."""
    nodes = {n["id"]: n for n in doc.get("nodes") or []}
    edges = doc.get("edges") or []
    named = lambda card: title(nodes[card]) if card in nodes else card  # noqa: E731
    link, start_of, to = step.get("link"), step.get("from_note"), step.get("to_note")
    for end, card in (("from_note", start_of), ("to_note", to)):
        if card is not None and card not in nodes:
            raise EditError(f"{end} '{card}' names no note or card in this graph; read the graph for its ids")
    if link is not None:
        edge = next((e for e in edges if e["id"] == link), None)
        if edge is None:
            raise EditError(f"'{link}' names no link in this graph; read the graph for its link ids")
        ends = (edge["fromNode"], edge["toNode"])
        for end, card in (("from_note", start_of), ("to_note", to)):
            if card is not None and card not in ends:
                raise EditError(f"{end} '{card}' is not one of the two cards link '{link}' joins")
        if start_of is None and to is not None:
            start_of = ends[0] if to == ends[1] else ends[1]
        if start_of is None:
            direction = canvas._direction(edge)
            if direction in ("both", "none"):
                raise EditError(f"link '{link}' ({named(ends[0])} — {named(ends[1])}) can be walked either way: "
                                "say which way with from_note")
            start_of = ends[0] if direction == "forward" else ends[1]
        to = ends[1] if start_of == ends[0] else ends[0]
        if not _walkable(edge, start_of):
            raise EditError(f"link '{link}' points the other way: a step follows the link's arrow, "
                            f"and {named(start_of)} → {named(to)} goes against it")
        return link, start_of, to
    if start_of is None or to is None:
        raise EditError("a step names its link (link), or the two notes it joins (from_note and to_note)")
    joining = [e for e in edges if {e["fromNode"], e["toNode"]} == {start_of, to} and start_of != to]
    usable = [e for e in joining if _walkable(e, start_of)]
    if len(usable) == 1:
        return usable[0]["id"], start_of, to
    if len(usable) > 1:
        raise EditError(f"{named(start_of)} → {named(to)} could be any of the links "
                        + ", ".join(f"'{e['id']}'" for e in usable) + ": name the step by its link")
    if joining:
        raise EditError(f"the link between {named(start_of)} and {named(to)} points the other way: "
                        "a step follows the link's arrow")
    raise EditError(f"{named(start_of)} → {named(to)} is not a link in this graph: link the notes first "
                    "(link_notes, or add_notes with links), then add the step")


def _with_steps(doc: dict, workflow: dict, given: list[dict]) -> dict:
    """``workflow`` with the steps ``given`` added after its own, each reached from its start."""
    nodes = {n["id"]: n for n in doc.get("nodes") or []}
    named = lambda card: title(nodes[card]) if card in nodes else card  # noqa: E731
    steps = list(workflow.get("steps") or [])
    taken = {s.get("link") for s in steps if isinstance(s, dict)}
    added = []
    for spec in given:
        link, start_of, to = _step(doc, spec)
        if link in taken:
            raise EditError(f"the step {named(start_of)} → {named(to)} (link '{link}') is already in "
                            f"{workflow.get('name')}")
        taken.add(link)
        steps.append({"link": link, "from": start_of})
        added.append((link, start_of, to))
    after = {**workflow, "steps": steps}
    if added:
        found = route({**doc, "workflows": [after]}, after)
        reached = {w[0] for w in found["walks"]}
        for link, start_of, to in added:
            if link not in reached:
                where = f"the start card {named(found['start'])}" if found["start"] else "the start card (it has none)"
                raise EditError(f"the step {named(start_of)} → {named(to)} can't be reached from {where}: add the "
                                "steps that lead to it, in this call or before it")
    return after


def _prerequisites(names: list[str], known: set[str], own: str, keys: dict[str, str] = {}) -> list[str]:
    """The workflow ids ``names`` (ids, or keys of workflows made in the same call) stand for."""
    ids = []
    for name in names:
        other = keys.get(name, name)
        if other not in known:
            raise EditError(f"after names '{name}', which is no workflow of this graph"
                            + (" nor the key of one in this call" if keys else "") + "; list_workflows shows their ids")
        if other == own:
            raise EditError("a workflow cannot come after itself")
        ids.append(other)
    return list(dict.fromkeys(ids))


def add(doc: dict, specs: list[dict]) -> tuple[dict, list[dict]]:
    """``doc`` with a workflow per ``{key?, name, description?, start, steps, after?}``, added
    at the end of the list in the order given, and how each one is now. ``after`` takes ids
    of the graph's workflows or keys of others in the same call."""
    nodes = {n["id"]: n for n in doc.get("nodes") or []}
    workflows = list(doc.get("workflows") or []) if isinstance(doc.get("workflows"), list) else []
    taken = {i.get("id") for key in ("nodes", "edges") for i in doc.get(key) or []} | {w["id"] for w in all_of(doc)}
    keys: dict[str, str] = {}  # key -> the new workflow's id
    ids: list[str] = []
    for spec in specs:
        key = spec.get("key")
        if key and (key in keys or key in taken):
            raise EditError(f"key '{key}' must be unique: another new workflow, or a workflow, card or link of "
                            "this graph, has it")
        ids.append(canvas.new_id({**doc, "workflows": [*workflows, *({"id": i} for i in ids)]}))
        if key:
            keys[key] = ids[-1]
    known = {w["id"] for w in all_of(doc)} | set(ids)
    made_ones = []
    for spec, made in zip(specs, ids):
        description = (spec.get("description") or "").strip()
        workflow = {"id": made, "name": _name(spec.get("name")), **({"description": description} if description else {}),
                    "start": _start(nodes, spec.get("start")), "steps": []}
        workflow = _with_steps(doc, workflow, spec.get("steps") or [])
        after = _prerequisites(spec.get("after") or [], known, made, keys)
        made_ones.append({**workflow, **({"after": after} if after else {})})
    result = {**doc, "workflows": [*workflows, *made_ones]}
    key_of = {v: k for k, v in keys.items()}
    return result, [{**({"key": key_of[w["id"]]} if w["id"] in key_of else {}), **summary(result, w)} for w in made_ones]


def update(doc: dict, changes: list[dict]) -> tuple[dict, list[dict]]:
    """``doc`` with each ``{id, name?, description?, start?, remove_steps?, add_steps?, after?,
    move_to?}`` applied to its workflow, in that order, in place: ``description`` '' removes
    it; ``after`` is the full new list; ``move_to`` the workflow's new place in the list (1 is
    first). Returns how each changed workflow is now."""
    nodes = {n["id"]: n for n in doc.get("nodes") or []}
    edges = {e["id"]: e for e in doc.get("edges") or []}
    workflows = list(doc.get("workflows") or []) if isinstance(doc.get("workflows"), list) else []
    named = lambda card: title(nodes[card]) if card in nodes else card  # noqa: E731
    for change in changes:
        workflow = find({**doc, "workflows": workflows}, change["id"])
        patch: dict = {}
        if change.get("name") is not None:
            patch["name"] = _name(change["name"])
        if change.get("description") is not None:
            patch["description"] = change["description"].strip() or None
        steps = list(workflow.get("steps") or [])
        for spec in change.get("remove_steps") or []:
            at = _step_at(steps, edges, spec)
            if at is None:
                shown = (f"link '{spec['link']}'" if spec.get("link")
                         else f"{named(spec.get('from_note'))} → {named(spec.get('to_note'))}")
                raise EditError(f"{shown} is not a step of {workflow.get('name')}; read_workflow shows its steps")
            del steps[at]
        if change.get("remove_steps"):
            patch["steps"] = steps
        if change.get("start") is not None:
            patch["start"] = _start(nodes, change["start"])
        if change.get("after") is not None:
            after = _prerequisites(change["after"], {w["id"] for w in all_of({"workflows": workflows})}, workflow["id"])
            patch["after"] = after or None
        updated = canvas._patched(workflow, patch)
        if change.get("add_steps"):
            updated = _with_steps({**doc, "workflows": workflows}, updated, change["add_steps"])
        at = next(i for i, w in enumerate(workflows) if w is workflow)
        workflows[at] = updated
        if change.get("move_to") is not None:
            count = len(all_of({"workflows": workflows}))
            if not 1 <= change["move_to"] <= count:
                raise EditError(f"move_to must be from 1 (first) to {count} (last), not {change['move_to']}")
            workflows.pop(at)
            kept = [i for i, w in enumerate(workflows) if isinstance(w, dict) and isinstance(w.get("id"), str)]
            place = kept[change["move_to"] - 1] if change["move_to"] - 1 < len(kept) else len(workflows)
            workflows.insert(place, updated)
    result = {**doc, "workflows": workflows}
    return result, [summary(result, find(result, i)) for i in dict.fromkeys(c["id"] for c in changes)]


def _step_at(steps: list, edges: dict[str, dict], spec: dict) -> int | None:
    """Where the step ``spec`` names (by its link, or by the two notes it joins) is in ``steps``."""
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        if spec.get("link") is not None:
            if step.get("link") == spec["link"] and spec.get("from_note") in (None, step.get("from")):
                return i
            continue
        edge = edges.get(step.get("link"))
        if edge is None or step.get("from") != spec.get("from_note"):
            continue
        if spec.get("to_note") in ((edge["toNode"],) if step["from"] == edge["fromNode"] else (edge["fromNode"],)):
            return i
    return None


def remove(doc: dict, ids: list[str]) -> tuple[dict, list[str]]:
    """``doc`` without those workflows, and without them in other workflows' ``after``; the
    cards and links stay. Returns notices for each workflow that no longer comes after one."""
    gone = {i: find(doc, i).get("name") for i in ids}
    workflows = []
    notices = []
    for w in doc.get("workflows") or []:
        if isinstance(w, dict) and w.get("id") in gone:
            continue
        if isinstance(w, dict) and any(other in gone for other in w.get("after") or []):
            kept = [other for other in w["after"] if other not in gone]
            notices += [f"{w.get('name')} no longer comes after {gone[other]}, which was removed"
                        for other in w["after"] if other in gone]
            w = canvas._patched(w, {"after": kept or None})
        workflows.append(w)
    return {**doc, "workflows": workflows}, notices


def pruned(before: dict, after: dict, links: list[str], cards: list[str] = ()) -> tuple[dict, list[str]]:
    """``after`` (``before`` with ``links`` and ``cards`` removed) without the steps that used
    those links, and with a removed start card's workflow left without a start (``start``:
    null) until one is given; and a notice for each, naming cards as ``before`` had them."""
    if not all_of(after) or not (links or cards):
        return after, []
    nodes = {n["id"]: n for n in before.get("nodes") or []}
    edges = {e["id"]: e for e in before.get("edges") or []}
    named = lambda card: title(nodes[card]) if card in nodes else card  # noqa: E731
    gone_links, gone_cards = set(links), set(cards)
    workflows, notices = [], []
    for w in after.get("workflows") or []:
        if not (isinstance(w, dict) and isinstance(w.get("id"), str)):
            workflows.append(w)
            continue
        steps = w.get("steps") or []
        lost = [s for s in steps if isinstance(s, dict) and s.get("link") in gone_links]
        patch: dict = {}
        if lost:
            patch["steps"] = [s for s in steps if s not in lost]
            for s in lost:
                edge = edges[s["link"]]
                to = edge["toNode"] if s.get("from") == edge["fromNode"] else edge["fromNode"]
                notices.append(f"{w.get('name')} lost the step {named(s.get('from'))} → {named(to)}")
        if w.get("start") in gone_cards:
            patch["start"] = None
            notices.append(f"{w.get('name')} lost its start card {named(w['start'])}: it has no start until one is "
                           "given (update_workflows with start)")
        workflows.append({**w, **patch} if patch else w)
    return {**after, "workflows": workflows}, notices

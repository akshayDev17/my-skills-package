---
name: coursework-author
description: Author a structured, module-based self-study course on any technical topic — a syllabus blueprint, one README per module, a fixed module anatomy, and verification gates (memory-less logic review + literature survey). Use when asked to create, build, or author a course, coursework, curriculum, or syllabus; to turn a topic into a module sequence; or to scaffold a self-study program grounded in a reference stack.
---

# Author a structured coursework

Produce a self-study curriculum on any technical topic: a `syllabus.md` blueprint, one README per module (`modules/NN-topic/README.md`), a fixed module anatomy repeated in every module, a running domain spine, and named cross-cutting threads — with every claim grounded where it belongs: stack claims against the stack's own docs, field claims against the literature.

The core insight: **the structure is the product.** A course is not a list of topics; it is a designed system — a spine that runs through every module, a fixed anatomy repeated in each, and failure modes named up front. Author the structure first; the prose follows it.

## Inputs

Capture these before writing anything:

- **Topic** and **learner** — who is this for (senior engineer, beginner, self-study), and the subject.
- **Reference stack** — the one framework/SDK every stack claim gets grounded in (e.g. ADK). One stack, not several.
- **Backends** — the models/providers the examples target (e.g. LiteLLM/Azure), if any.
- **Scope knobs** — labs? capstone? assessment? and the depth target ("as broad and deep as possible" is a legitimate answer; record it, don't leave it implicit).

## Steps

1. **Freeze scope and audience.**
   Decide the learner profile, the reference stack, the backends, and each scope knob.
   *Done when every knob has an explicit value — even "deferred" — and the reference stack is a single named framework.*

2. **Write the blueprint first (`syllabus.md`).**
   Enumerate the modules `M1..Mn` as a table with a status column, then sketch each module leanly — core question · content bullets · domain · exercise. That sketch is what the module gets written from; the table alone is too thin to write from. Define the repo structure (`modules/`, `discussions/`, any reference maps). Name the cross-cutting threads that will repeat, and name the one module that owns each recurring claim — that ownership map is what stops the same paper being surveyed five times.
   The status column has three states — `⬜ planned`, `🟡 written`, `✅ verified` — and **written is not verified**: a module becomes `✅` only after **both** gates in Steps 7 and 8 have run on it, with their rounds and residual findings recorded in the module's Verification note.
   *Done when the module list is complete, every module carries its lean sketch, every cross-cutting claim has a named owning module, its count is frozen before any module prose exists, and the three-state status vocabulary is written into the syllabus.*

3. **Define the module anatomy once, then repeat it.**
   Write down the fixed README template (see "The module anatomy" below) and reuse it for every module.
   *Done when every module header carries the same blocks, in the same order.*

4. **Ground the reference stack.**
   Attach the stack's docs (an MCP server, the docs site) and verify every API/feature claim against them — never from memory. Flag anything thinly documented honestly, in place.
   This step owns **stack API claims only**; conceptual and scientific claims are Step 8's, so the two never survey the same sentence twice.
   *Done when no stack claim rests on memory; each is traceable to a doc page.*

5. **Write modules in order, cross-referenced.**
   Each module opens with its question + spine + opening scene, delivers its payload, ends with a design exercise and a next-module link. Cross-reference forward and back ("→ M6", "M2's position bias").
   *Done when every module is self-contained AND wired into its neighbours by links.*

6. **End every module with a design exercise.**
   Paper-based, "think, then write", applied to the learner's own context — not a recall quiz.
   *Done when each exercise forces a decision (an ADR, a schema, a budget) rather than a recall.*

7. **Verify every claim-bearing artifact with a memory-less verifier.**
   Self-review cannot catch your own unstated assumptions. Author what you believe, then have it audited by a fresh agent that sees only the artifact, has no memory of writing it, and is forbidden from fixing anything.
   - Dump one artifact per pass to a scratch file outside the module tree (`tmp/<slug>.md` at the repo root) — the claim-bearing note, not the whole tree. Delete it once the pass is recorded; it is working space, not a deliverable.
   - Dispatch a fresh subagent with the fixed prompt below. It must end with an explicit verdict on whether it could follow every step *without supplying an assumption of its own*; that verdict, not the finding count, is the signal.
   - Fix the artifact, never the verifier, then re-dispatch the **identical** prompt. Keep it byte-identical across rounds so the rounds are comparable and the decay of findings means something.
   - Expect decay: substantive first (wrong inequality direction, unstated premise, contradiction, circular definition, stale cross-reference, a symbol defined twice), cosmetic later (label collisions, forward-reference nits, wording).
   - **Stopping rule.** Converged = one round whose findings are all cosmetic; record the residual list and stop. Cap the rounds: ~3 for prose-only modules, ~5–8 for artifacts carrying derivations, budgets, or thresholds. Past the cap the findings are style and the cost is real — stop, write down what remains, and mark the artifact `✅` with that note.
   - For prose-only modules the same prompt degrades gracefully into a "what did you assume" review; use it unchanged.

   Verifier prompt (fixed; substitute the absolute path and one descriptive sentence):

   ```
   You are a memory-less file verifier. Your ONLY job: examine the single file at the
   absolute path <ABSOLUTE PATH> and report on it. You have no other context. Do not fix
   anything, do not propose edits, do not rewrite content. Read the file carefully, then
   produce a findings report.

   The file is <ONE SENTENCE: what the artifact claims to be, and what it is for>. Your job
   is adversarial verification of the file's internal logic only.

   Report, exhaustively and pedantically:
   1. EVERY unstated assumption the text relies on but never declares.
   2. EVERY skipped mathematical step: anywhere an equation, inequality, or numeric claim
      appears, verify it can be derived from what precedes it using only standard results;
      flag any step you cannot reproduce line by line. Check every arithmetic claim by hand.
   3. EVERY internal inconsistency: symbols defined more than once or used before definition;
      claims that contradict other claims in the file; requirements that reference concepts
      not yet defined at that point; any place where the ordering (what is pre-committed,
      what is derived, what is verified) breaks down or becomes circular.
   4. Ambiguities that force a reader to guess.

   End your report with a clear statement of whether, reading the file ALONE (no outside
   knowledge, no conversation history), you were able to follow every step in order. If you
   had to assume anything yourself to make sense of a step, that counts as a finding. Be
   exhaustive and pedantic. Do not fix anything.
   ```

   *Done when the latest round produced only cosmetic findings (or the round cap was hit), and the artifact carries its round count plus residual findings in the module's Verification note.*

8. **Literature-survey each module before it earns `✅`.**
   Invoke the `lit-survey` skill on the module and follow it. It owns the bibliography format, the contradictions section, and the citation-graph check, so none of that is restated here — only the course-specific deltas:
   - **Bootstrap the gold standard.** The first claim-dense module establishes the key format, entry format, and section order. Freeze that module as the course's named gold standard in the syllabus; every later module mirrors it.
   - **Exempt the narrative.** Opening scenes, invented domains with their worked examples, and design exercises carry no citation burden. Step 4 owns stack API claims; this step owns conceptual and scientific claims.
   - **Respect the ownership map.** A claim reaching several modules is owned and surveyed once, in its owning module (named in Step 2); later modules cite it by the same key rather than re-surveying it.
   - **Survey by weight, not by count.** Framing-bearing claims — taxonomies, causal mechanisms, numbers — are surveyed here, in-module, because a contradiction found at this point attacks the module's *structure*, not a paragraph. Supporting references risk nothing structural and batch into Step 11.
   - **Keep bibliographies per-module.** `lit-survey` edits only its target file, and module self-containment wants the entries local; the key format is the part that stays course-wide.
   *Done when the module carries a bibliography in the frozen gold-standard format, a contradictions section immediately before it, a one-to-one citation graph, and either a citation or an unsupported-bucket entry for every non-exempt claim.*

9. **Fold refinements into the files (the iteration loop).**
   The learner's probing questions ("but what about X?", "is this legitimate?") are the real curriculum driver. Answer them, then write the answer back into the relevant module — not into a chat that vanishes. Capture meta-discussions (Q&A, worked examples) into `discussions/` as durable files. An edit that touches verified content re-opens Steps 7 and 8 for that module (or marks it `🟡` until it is re-verified).
   *Done when every open question raised during authoring has landed in a file, or is explicitly parked.*

10. **Add reference maps and verify sources.**
    For breadth topics (e.g. "the other frameworks"), build a map file: a matrix with per-entry "covers / gaps", verified sources, and licenses. Verify links resolve; replace dead ones.
    *Done when every external claim carries a working source, and thin coverage is flagged, not papered over.*

11. **Verify the whole tree.**
    Walk the repo: the syllabus status matches reality (every `✅` carries a Verification note from Steps 7 and 8), every module has all anatomy blocks, every cross-reference resolves, no orphan files. Then the course-wide survey pass: one key format across every bibliography, no module's contradictions section contradicting another module's claim, and the batched sweep of the supporting references deferred from Step 8.
    *Done when the syllabus's status table is true, the cross-module key and contradiction check is clean, and a fresh reader can follow links from module 1 to module N.*

## The module anatomy

Every module README is this skeleton, filled in — same blocks, same order, same form:

```markdown
# M# · <Topic>

> **Module question:** <the one question this module answers>
> **Cross-cutting threads:** Failure modes · Tradeoff ledger · <Stack> at a glance
> **Domain spine:** <the running example>

---

## Opening scene — <a narrative that ends on the failure this module prevents>
<3–6 short paragraphs.>

> **Failure mode (the module in one line):** treating <X> as <Y>, not "the model was wrong".

---

## <Payload — one concept per section>
<Prose + a table + a mermaid diagram where shape matters.>

> **<Stack> at a glance:** <the stack's primitive here — verified against its docs.>
> **Tradeoff (the ledger entry):** <decision> costs <cost>; accepted because <reason>.

---

## Worked example: <the domain spine, made concrete>

---

## Design exercise
> *Paper-based. Think, then write.*
**Task.** <decision-forcing task: an ADR, a schema, a budget>
1. … 2. … 3. …
**Why this exercise matters.** <one paragraph>

---

## Where the literature disagrees
- **<claim>** — what this module says; what the sources say, quoted; what to change.   ← omit when the literature is unanimous

---

## Sources
- [<stack primitive>](<url>)   ← stack-docs bullet: conditional, see below
- [<paper>](<url>) — `<key>` · owns <what it owns>

---

**Verification note:** <Steps 7–8 rounds run + residual findings accepted>

**Next module:** [M## — <title>](../NN-slug/README.md) — <one line>
```

Blocks marked `<Stack>` — and the stack-docs bullet in Sources — are conditional; drop them for a topic with no reference stack.

## Cross-cutting threads

Pick two or three and repeat them in every module — they are the connective tissue:

- **Failure modes** — a named taxonomy that every module maps back to.
- **Tradeoff ledger** — every decision's cost recorded as an entry.
- **[Stack] at a glance** — the reference stack's primitives, one line each.

## Failure modes (of authoring a course)

- **The topic list disguised as a course.** Modules that are adjacent facts instead of a spine with a running example. A course has a shape; a topic list doesn't.
- **Claims from memory.** Describing a framework's API without opening its docs. Every stack claim must be traceable.
- **The unsourced field claim.** A confident conceptual claim — a taxonomy, a causal mechanism, a number — resting on no paper at all. Stack docs cannot support it; only the literature can.
- **The prose monolith.** No opening scene, no tables, no diagrams — a wall of paragraphs the learner skims. Structure is the medium.
- **The missing exercise.** Modules that end on content instead of a decision. The exercise is where learning lands.
- **The chat-only refinement.** Answering the learner's sharp question in chat and never writing it into a file, so it evaporates. Fold it in.
- **Papering over thin coverage.** Pretending a thinly-documented area is solid. Flag it honestly; "thinly documented" is a legitimate answer.

## Completion criteria

Every run ends when:

- `syllabus.md` exists with a complete module list + status table + repo structure, and the status table is true — every module `⬜ planned`, `🟡 written`, or `✅ verified`, with none silently missing.
- Every module is sketched in the syllabus and follows the fixed anatomy, ending with a decision-forcing design exercise and a next-module link.
- No module is `✅ verified` on self-review alone: each carries a Verification note with its Steps 7 and 8 rounds and any residual (cosmetic) findings, and the loop ended by the Step 7 stopping rule — a cosmetic-only round or the round cap — not by exhaustion.
- Every non-exempt claim carries a citation or sits in the unsupported bucket, and every module's bibliography matches the frozen gold-standard key format.
- The reference stack is grounded in its own docs; no claim rests on memory.
- Every cross-reference resolves; there are no orphan files.
- All refinements raised during authoring are folded into files, and every external source link is verified.

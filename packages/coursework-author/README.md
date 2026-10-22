# coursework-author

Author a structured, module-based self-study course on any technical topic: a
`syllabus.md` blueprint, one `README.md` per module in a fixed anatomy, and two
verification gates — a memory-less logic review and a literature survey — that a module
must clear before it is marked `✅ verified`.

Ships one skill: `skills/coursework-author/SKILL.md`. Works with Claude Code, Codex,
Gemini and DSH; the `lib/` + `cordis.patch.yml` pairing is DSH's mounting glue only.

Consumed by [`explainer-video-from-coursework`](https://github.com/akshayDev17/ai-explainer-video),
which vendors this skill at release time so that installing the video pipeline also gives
you the authoring half. That is a soft pairing: authoring a course never requires video,
and video work never requires this package to be installed separately.

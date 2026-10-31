// Reads and edits a sub-app's `backend/wrangler.jsonc` without losing its comments.
//
// The file is JSONC: the repos explain their bindings in comments, so a parse-and-
// reserialize would delete the explanation. Reading strips comments and trailing
// commas; writing replaces only the placeholder *string tokens*, leaving every byte
// around them as it was — including a comment that happens to mention the placeholder.

export const D1_PLACEHOLDER = "REPLACE_AFTER_d1_create";

/** Splits JSONC into string, comment and other segments; joining them gives back the input. */
function tokenize(text) {
  const tokens = [];
  let other = "";
  let i = 0;
  const flush = () => {
    if (other) tokens.push({ kind: "other", text: other });
    other = "";
  };
  while (i < text.length) {
    const ch = text[i];
    const next = text[i + 1];
    if (ch === '"') {
      let j = i + 1;
      while (j < text.length && text[j] !== '"') j += text[j] === "\\" ? 2 : 1;
      flush();
      tokens.push({ kind: "string", text: text.slice(i, j + 1) });
      i = j + 1;
    } else if (ch === "/" && next === "/") {
      const end = text.indexOf("\n", i);
      const j = end === -1 ? text.length : end;
      flush();
      tokens.push({ kind: "comment", text: text.slice(i, j) });
      i = j;
    } else if (ch === "/" && next === "*") {
      const end = text.indexOf("*/", i + 2);
      const j = end === -1 ? text.length : end + 2;
      flush();
      tokens.push({ kind: "comment", text: text.slice(i, j) });
      i = j;
    } else {
      other += ch;
      i += 1;
    }
  }
  flush();
  return tokens;
}

export function parseJsonc(text) {
  const withoutComments = tokenize(text)
    .filter((t) => t.kind !== "comment")
    .map((t) => t.text)
    .join("");
  // With comments gone, a trailing comma and its closing bracket share one segment.
  const json = tokenize(withoutComments)
    .map((t) => (t.kind === "other" ? t.text.replace(/,(\s*[}\]])/g, "$1") : t.text))
    .join("");
  return JSON.parse(json);
}

export function replacePlaceholder(text, databaseId) {
  const placeholder = JSON.stringify(D1_PLACEHOLDER);
  return tokenize(text)
    .map((t) => (t.kind === "string" && t.text === placeholder ? JSON.stringify(databaseId) : t.text))
    .join("");
}

/**
 * Which D1 database bootstrap should ensure for this config.
 *   { state: "none" }                       no D1 binding, nothing to do
 *   { state: "present", name }              every binding already carries a real id
 *   { state: "missing", name }              placeholder entries all name `name`
 *   { state: "conflict", reason }           ambiguous or inconsistent; a human decides
 */
export function d1Target(config, d1Flag) {
  const entries = config.d1_databases ?? [];
  const placeholders = entries.filter((e) => e.database_id === D1_PLACEHOLDER);
  const names = [...new Set(placeholders.map((e) => e.database_name))];

  if (placeholders.some((e) => !e.database_name)) {
    return { state: "conflict", reason: `a d1_databases entry with the ${D1_PLACEHOLDER} placeholder has no database_name` };
  }
  if (names.length > 1) {
    return { state: "conflict", reason: `placeholder entries name several databases (${names.join(", ")}); bootstrap handles one per app` };
  }
  if (d1Flag && names.length === 1 && names[0] !== d1Flag) {
    return { state: "conflict", reason: `--d1 ${d1Flag} but the placeholder entry names database "${names[0]}"` };
  }
  if (names.length === 1) return { state: "missing", name: names[0] };
  if (d1Flag) {
    return entries.some((e) => e.database_name === d1Flag)
      ? { state: "present", name: d1Flag }
      : { state: "conflict", reason: `--d1 ${d1Flag} but wrangler.jsonc has no d1_databases entry for it; add the binding first` };
  }
  if (entries.length === 0) return { state: "none" };
  return { state: "present", name: [...new Set(entries.map((e) => e.database_name))].join(", ") };
}

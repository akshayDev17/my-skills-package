// Reads and edits a sub-app's Worker config (`backend/wrangler.jsonc` unless `--wrangler`
// names another file) without losing its comments.
//
// The file is JSONC: the repos explain their bindings in comments, so a parse-and-
// reserialize would delete the explanation. Reading strips comments and trailing
// commas; writing replaces only the placeholder *string tokens* that are the value of a
// `database_id` key, leaving every byte around them as it was — including a comment that
// mentions the placeholder, and an all-zero id held by some other field.

import { isAbsolute, relative, resolve } from "node:path";

export const DEFAULT_WRANGLER = "backend/wrangler.jsonc";

/** The two stand-ins a repo may hold for a D1 id that does not exist yet. */
export const D1_PLACEHOLDERS = ["REPLACE_AFTER_d1_create", "00000000-0000-0000-0000-000000000000"];
export const D1_PLACEHOLDER = D1_PLACEHOLDERS[0];

/** Absolute path of the Worker config inside `cloneDir`; throws if `relPath` is absolute or leaves the clone. */
export function resolveWranglerPath(cloneDir, relPath = DEFAULT_WRANGLER) {
  const abs = resolve(cloneDir, relPath);
  const inside = relative(resolve(cloneDir), abs);
  if (isAbsolute(relPath) || inside === "" || inside === ".." || inside.startsWith("../") || isAbsolute(inside)) {
    throw new Error(`--wrangler ${relPath} must be a file path inside the clone ${cloneDir}`);
  }
  return abs;
}

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

/** Replaces each placeholder that is the value of a `"database_id":` key; every other byte stays. */
export function replacePlaceholder(text, databaseId) {
  const placeholders = D1_PLACEHOLDERS.map((p) => JSON.stringify(p));
  let afterKey = false; // the last string token was `"database_id"`
  let between = ""; // the non-comment text since that string
  return tokenize(text)
    .map((t) => {
      if (t.kind === "comment") return t.text;
      if (t.kind === "other") {
        between += t.text;
        return t.text;
      }
      const isValue = afterKey && /^\s*:\s*$/.test(between) && placeholders.includes(t.text);
      afterKey = t.text === '"database_id"';
      between = "";
      return isValue ? JSON.stringify(databaseId) : t.text;
    })
    .join("");
}

/**
 * Which D1 database bootstrap should ensure for this config.
 *   { state: "none" }                       no D1 binding, nothing to do
 *   { state: "present", name }              every binding already carries a real id
 *   { state: "missing", name, placeholders } placeholder entries all name `name`; `placeholders` are the ones found
 *   { state: "conflict", reason }           ambiguous or inconsistent; a human decides
 */
export function d1Target(config, d1Flag) {
  const entries = config.d1_databases ?? [];
  const placeholders = entries.filter((e) => D1_PLACEHOLDERS.includes(e.database_id));
  const names = [...new Set(placeholders.map((e) => e.database_name))];

  if (placeholders.some((e) => !e.database_name)) {
    return { state: "conflict", reason: `a d1_databases entry with a placeholder database_id (${D1_PLACEHOLDERS.join(" or ")}) has no database_name` };
  }
  if (names.length > 1) {
    return { state: "conflict", reason: `placeholder entries name several databases (${names.join(", ")}); bootstrap handles one per app` };
  }
  if (d1Flag && names.length === 1 && names[0] !== d1Flag) {
    return { state: "conflict", reason: `--d1 ${d1Flag} but the placeholder entry names database "${names[0]}"` };
  }
  if (names.length === 1) return { state: "missing", name: names[0], placeholders: [...new Set(placeholders.map((e) => e.database_id))] };
  if (d1Flag) {
    return entries.some((e) => e.database_name === d1Flag)
      ? { state: "present", name: d1Flag }
      : { state: "conflict", reason: `--d1 ${d1Flag} but the Worker config has no d1_databases entry for it; add the binding first` };
  }
  if (entries.length === 0) return { state: "none" };
  return { state: "present", name: [...new Set(entries.map((e) => e.database_name))].join(", ") };
}

/** R2's rule for bucket names: 3–63 lowercase letters, digits and hyphens, alphanumeric at both ends. */
const R2_BUCKET_NAME = /^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$/;

/**
 * Which R2 bucket bootstrap should ensure for this config. R2 bindings carry no id, so
 * there is no placeholder: the bucket is the one `r2_buckets` names.
 *   { state: "none" }                       no R2 binding, nothing to do
 *   { state: "wanted", name }               ensure bucket `name` exists
 *   { state: "conflict", reason }           ambiguous or inconsistent; a human decides
 */
export function r2Target(config) {
  const entries = config.r2_buckets ?? [];
  if (entries.some((e) => !e.bucket_name)) {
    return { state: "conflict", reason: "an r2_buckets entry has no bucket_name" };
  }
  const names = [...new Set(entries.map((e) => e.bucket_name))];
  if (names.length === 0) return { state: "none" };
  if (names.length > 1) {
    return { state: "conflict", reason: `r2_buckets name several buckets (${names.join(", ")}); bootstrap handles one per app` };
  }
  if (!R2_BUCKET_NAME.test(names[0])) {
    return { state: "conflict", reason: `bucket_name "${names[0]}" is not a valid R2 bucket name (3–63 lowercase letters, digits or hyphens)` };
  }
  return { state: "wanted", name: names[0] };
}

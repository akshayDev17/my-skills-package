#!/usr/bin/env bash
# Refreshes plugins/graph-notes/server/ from a graph-notes checkout.
#
#   scripts/refresh-graph-notes-server.sh <graph-notes checkout> [<commit>]
#
# The plugin carries a copy of graph-notes' mcp-server/, because the graph-notes repo is
# private and installers must not need it. The copy is never edited here: this script
# replaces it with mcp-server/ exactly as committed at <commit> (default HEAD), and
# writes plugins/graph-notes/SERVER_SOURCE (the commit and a sha256 per file), which
# scripts/check-path-plugins.mjs holds the copy to in CI.
#
# Before copying, it runs graph-notes' own test suite against that exact copy, in a
# temporary directory that also holds the same commit's backend/ (the offline tests run
# the real API over SQLite) and frontend/lib/ (the picture tests check the server against
# the web app's own TypeScript, with node 22.6+). Nothing is written into the checkout.
set -euo pipefail

checkout=${1:?usage: $0 <graph-notes checkout> [<commit>]}
ref=${2:-HEAD}
repo=$(cd "$(dirname "$0")/.." && pwd)
plugin="$repo/plugins/graph-notes"

commit=$(git -C "$checkout" rev-parse --verify "$ref^{commit}")
git -C "$checkout" cat-file -e "$commit:mcp-server/pyproject.toml" 2>/dev/null \
  || { echo "$commit has no mcp-server/" >&2; exit 1; }

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
git -C "$checkout" archive "$commit" mcp-server backend frontend/lib | tar -x -C "$work"

echo "== graph-notes $commit: running mcp-server's tests against the copy"
(cd "$work/mcp-server" && uv run --frozen --quiet pytest -q -p no:cacheprovider)
rm -rf "$work/mcp-server/.venv"
find "$work/mcp-server" -name __pycache__ -type d -prune -exec rm -rf {} +

rm -rf "$plugin/server"
cp -R "$work/mcp-server" "$plugin/server"

{
  echo "# graph-notes mcp-server/, copied by scripts/refresh-graph-notes-server.sh. Do not edit by hand."
  echo "commit $commit"
  (cd "$plugin/server" && find . -type f | sed 's|^\./||' | LC_ALL=C sort | while read -r f; do
    shasum -a 256 "$f"
  done)
} > "$plugin/SERVER_SOURCE"

echo "== copied mcp-server/ at $commit into plugins/graph-notes/server"
echo "next: put the commit in plugins/graph-notes/README.md, bump the version in"
echo "      plugins/graph-notes/.claude-plugin/plugin.json, and run"
echo "      node scripts/check-path-plugins.mjs"

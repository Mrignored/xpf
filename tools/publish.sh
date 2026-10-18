#!/usr/bin/env bash
# One command to publish a new version from a zip you downloaded:
#     bash ~/publish.sh                 (uses the newest xui-factor-panel*.zip in ~/Downloads)
#     bash ~/publish.sh path/to/new.zip
# It replaces the files in your local clone, commits and pushes. GitHub then creates the
# tag and the Release by itself (.github/workflows/release.yml); servers see the update.
set -euo pipefail

# The script replaces the repository content (including itself): run from a temporary copy.
if [ -z "${XFP_PUBLISH_COPY:-}" ]; then
  copy="$(mktemp)"; cp "$0" "$copy"
  XFP_PUBLISH_COPY=1 bash "$copy" "$@"; rc=$?; rm -f "$copy"; exit "$rc"
fi

REPO_DIR="${REPO_DIR:-$HOME/xui-factor-panel}"
DOWNLOADS="${DOWNLOADS:-$HOME/Downloads}"
zip="${1:-$(ls -t "$DOWNLOADS"/xui-factor-panel*.zip 2>/dev/null | head -n1 || true)}"

die() { echo "error: $*" >&2; exit 1; }
version_of() { sed -n 's/^__version__ = "\(.*\)"/\1/p' "$1" | tr -d '\r' | head -n1; }

[ -d "$REPO_DIR/.git" ] || die "repository folder not found: $REPO_DIR (set REPO_DIR=/path/to/clone)"
[ -n "$zip" ] && [ -f "$zip" ] || die "no zip found: pass its path, e.g. bash ~/publish.sh ~/Downloads/xui-factor-panel.zip"
command -v unzip >/dev/null || die "unzip is not available"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
unzip -q "$zip" -d "$tmp"
src="$(find "$tmp" -maxdepth 2 -name install.sh -printf '%h\n' | head -n1)"
[ -n "$src" ] && [ -f "$src/xfpanel/__init__.py" ] || die "this zip is not an XuiFactor panel release"

new="$(version_of "$src/xfpanel/__init__.py")"
cur="$(version_of "$REPO_DIR/xfpanel/__init__.py")"
[[ "$new" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]] || die "unreadable version in the zip: '$new'"
echo "zip      : $zip"
echo "version  : ${cur:-none} -> $new"
[ "$new" != "$cur" ] || die "the zip has the same version as the repository; ask for a newer zip"

cd "$REPO_DIR"
if ! git diff --quiet || ! git diff --cached --quiet; then die "your clone has uncommitted changes; commit or discard them first"; fi

find . -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
cp -a "$src"/. .
git add -A
if git diff --cached --quiet; then die "nothing changed compared to the repository"; fi
git commit -q -m "v$new"
git push origin HEAD
echo
echo "Pushed v$new. GitHub creates the Release in about a minute:"
url="$(git remote get-url origin | sed 's/\.git$//; s|^git@github.com:|https://github.com/|')"
echo "  $url/actions"
echo "  $url/releases"

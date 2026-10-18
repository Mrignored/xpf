#!/usr/bin/env bash
# Publish a new version:   tools/release.sh 1.2.0 ["optional note"]
# It bumps the version in the code, commits, tags v1.2.0 and pushes. The GitHub Action in
# .github/workflows/release.yml then creates the Release, and every installed panel will
# offer the update (button in the dashboard, or `xfp update`).
set -euo pipefail
cd "$(dirname "$0")/.."
ver="${1:-}"; note="${2:-}"
[[ "$ver" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]] || { echo "usage: $0 MAJOR.MINOR.PATCH [note]   e.g. $0 1.2.0" >&2; exit 1; }
command -v git >/dev/null || { echo "git is required" >&2; exit 1; }
git rev-parse --git-dir >/dev/null 2>&1 || { echo "run this inside your cloned repository" >&2; exit 1; }
git rev-parse "v$ver" >/dev/null 2>&1 && { echo "tag v$ver already exists" >&2; exit 1; }
cur="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' xfpanel/__init__.py)"
echo "Version: $cur -> $ver"
sed -i "s/^__version__ = \".*\"/__version__ = \"$ver\"/" xfpanel/__init__.py
if [ -n "${XFP_TEST_BIN:-}" ] && command -v python3 >/dev/null; then   # optional: XFP_TEST_BIN=/path/to/xui-factor tools/release.sh ...
  python3 -m unittest discover -s tests >/dev/null 2>&1 && echo "tests: ok" || { echo "tests FAILED - aborting (nothing was committed)"; git checkout -- xfpanel/__init__.py; exit 1; }
fi
git add -A
git commit -m "Release v$ver${note:+: $note}"
git tag -a "v$ver" -m "v$ver${note:+: $note}"
git push origin HEAD
git push origin "v$ver"
echo "Done. Release v$ver will appear on GitHub in about a minute."

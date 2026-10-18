#!/usr/bin/env bash
# Alternative to publish.sh when you edit the code yourself:   tools/release.sh 1.2.0 ["note"]
# Bumps the version, commits and pushes; GitHub (release.yml) creates the tag and the Release.
set -euo pipefail
cd "$(dirname "$0")/.."
ver="${1:-}"; note="${2:-}"
[[ "$ver" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]] || { echo "usage: $0 MAJOR.MINOR.PATCH [note]   e.g. $0 1.2.0" >&2; exit 1; }
git rev-parse --git-dir >/dev/null 2>&1 || { echo "run this inside your cloned repository" >&2; exit 1; }
cur="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' xfpanel/__init__.py | tr -d '\r')"
echo "Version: $cur -> $ver"
sed -i "s/^__version__ = \".*\"/__version__ = \"$ver\"/" xfpanel/__init__.py
if [ -n "${XFP_TEST_BIN:-}" ] && command -v python3 >/dev/null; then
  python3 -m unittest discover -s tests >/dev/null 2>&1 && echo "tests: ok" || { echo "tests FAILED - aborting"; git checkout -- xfpanel/__init__.py; exit 1; }
fi
git add -A
git commit -m "v$ver${note:+: $note}"
git push origin HEAD
echo "Done. The Release v$ver appears on GitHub in about a minute."

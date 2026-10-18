#!/usr/bin/env bash
# Point this project at YOUR GitHub repository (run once, before the first commit):
#   tools/set-repo.sh yourname/xui-factor-panel
set -euo pipefail
slug="${1:-}"
[[ "$slug" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || { echo "usage: $0 owner/repo   (e.g. $0 myname/xui-factor-panel)" >&2; exit 1; }
cd "$(dirname "$0")/.."
sed -i "s|XFP_REPO=\"\${XFP_REPO:-[^}]*}\"|XFP_REPO=\"\${XFP_REPO:-$slug}\"|" install.sh
sed -i "s|OWNER/REPO|$slug|g" README.md README.en.md 2>/dev/null || true
grep -n '^XFP_REPO=' install.sh
echo
echo "One-line install command for your users:"
echo "  bash <(curl -Ls https://raw.githubusercontent.com/$slug/main/install.sh)"

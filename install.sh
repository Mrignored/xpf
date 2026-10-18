#!/usr/bin/env bash
# XuiFactor + web panel installer / updater.
#
#   One line from GitHub (after you publish the repo, see README):
#     bash <(curl -Ls https://raw.githubusercontent.com/OWNER/REPO/main/install.sh)
#
#   From an unpacked copy:
#     sudo ./install.sh                  interactive
#     sudo ./install.sh --yes --no-web   unattended, factor service only
#     sudo ./install.sh --yes --port 909 --username admin --password 'S3cret!pass' --domain panel.example.com
#   Options: --version vX.Y.Z (pick a release)  --repo owner/name  --update (used by `xfp update`)
set -euo pipefail

# >>> Your GitHub repository as owner/name. `tools/set-repo.sh owner/name` edits this line for you.
XFP_REPO="${XFP_REPO:-Mrignored/xpf}"
# <<<

ORIG_ARGS=("$@")
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || echo /nonexistent)"
ROOT="${DESTDIR:-}"
APP_DIR="$ROOT/opt/xui-factor-panel"
BIN_DIR="$ROOT/usr/local/bin"
SRC_STASH="$ROOT/opt/xui-factor-panel-src"
SYSTEMD_DIR="$ROOT/etc/systemd/system"

WEB=ask; YES=0; UPDATE=0; VERSION_TAG=""; WEB_ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --no-web) WEB=no ;;
    --web) WEB=yes ;;
    --yes|-y) YES=1; WEB_ARGS+=(--yes) ;;
    --update) UPDATE=1; YES=1; WEB=no ;;
    --version) VERSION_TAG="${2:?missing value for --version}"; shift ;;
    --repo) XFP_REPO="${2:?missing value for --repo}"; shift ;;
    --port|--username|--password|--domain|--acme-port) WEB=yes; WEB_ARGS+=("$1" "${2:?missing value for $1}"); shift ;;
    --ip-cert|--no-ssl) WEB=yes; WEB_ARGS+=("$1") ;;
    -h|--help) sed -n '2,13p' "${BASH_SOURCE[0]}" 2>/dev/null || true; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
  shift
done

die()  { echo "error: $*" >&2; exit 1; }
info() { echo "==> $*"; }
SLUG_RE='^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$'

[ "$(uname -s)" = Linux ] || die "Linux only"
[ -n "$ROOT" ] || [ "$(id -u)" -eq 0 ] || die "run as root (sudo ...)"

install_deps() {
  [ -z "$ROOT" ] || return 0
  local need=() c
  for c in python3 curl tar openssl socat; do command -v "$c" >/dev/null 2>&1 || need+=("$c"); done
  command -v crontab >/dev/null 2>&1 || need+=(cron)
  [ ${#need[@]} -gt 0 ] || return 0
  info "Installing missing packages: ${need[*]}"
  if command -v apt-get >/dev/null 2>&1; then apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${need[@]}"
  elif command -v dnf >/dev/null 2>&1; then dnf install -y -q "${need[@]//cron/cronie}"
  elif command -v yum >/dev/null 2>&1; then yum install -y -q "${need[@]//cron/cronie}"
  elif command -v pacman >/dev/null 2>&1; then pacman -Sy --noconfirm --needed "${need[@]//cron/cronie}"
  elif command -v apk >/dev/null 2>&1; then apk add --no-cache "${need[@]//cron/dcron}"
  else die "unsupported package manager; install manually: ${need[*]}"; fi
}

check_python() {
  command -v python3 >/dev/null 2>&1 || die "python3 is required"
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' || die "Python 3.8 or newer is required"
}

# ------------------------------------------------------------ online (one-line) mode
latest_tag() {   # latest_tag owner/repo -> newest release tag, else highest version tag, else empty
  local slug="$1" tag
  tag="$(curl -fsSL -H 'Accept: application/vnd.github+json' "https://api.github.com/repos/$slug/releases/latest" 2>/dev/null \
        | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tag_name",""))' 2>/dev/null || true)"
  if [ -z "$tag" ]; then
    tag="$(curl -fsSL "https://api.github.com/repos/$slug/tags?per_page=50" 2>/dev/null | python3 -c '
import json, re, sys
best = None
for t in json.load(sys.stdin):
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)(?:-(.+))?$", t.get("name", ""))
    if m:
        key = (int(m[1]), int(m[2]), int(m[3]), 0 if m[4] else 1)
        if best is None or key > best[0]:
            best = (key, t["name"])
print(best[1] if best else "")' 2>/dev/null || true)"
  fi
  printf '%s' "$tag"
}

bootstrap_online() {
  local slug="$XFP_REPO" tag="$VERSION_TAG" tmp dir url rc=0
  [[ "$slug" =~ $SLUG_RE ]] && [ "$slug" != "OWNER/REPO" ] || die "no GitHub repository configured: run with --repo owner/name (or edit XFP_REPO at the top of install.sh)"
  install_deps
  check_python
  [ -n "$tag" ] || tag="$(latest_tag "$slug")"
  tmp="$(mktemp -d)"
  if [ -n "$tag" ]; then url="https://github.com/$slug/archive/refs/tags/$tag.tar.gz"; info "Downloading release $tag of $slug"
  else url="https://github.com/$slug/archive/refs/heads/main.tar.gz"; info "No release found; downloading the main branch of $slug"; fi
  curl -fSL --retry 3 --connect-timeout 15 --max-time 600 -o "$tmp/src.tar.gz" "$url" || { rm -rf "$tmp"; die "download failed: $url"; }
  tar -xzf "$tmp/src.tar.gz" -C "$tmp" || { rm -rf "$tmp"; die "the downloaded archive is not valid"; }
  dir="$(find "$tmp" -mindepth 2 -maxdepth 2 -name install.sh -printf '%h\n' | head -n1)"
  [ -n "$dir" ] && [ -f "$dir/xfpanel/__init__.py" ] || { rm -rf "$tmp"; die "the archive does not look like this project"; }
  XFP_REPO_SLUG="$slug" bash "$dir/install.sh" "${ORIG_ARGS[@]}" || rc=$?
  rm -rf "$tmp"
  exit "$rc"
}

# ------------------------------------------------------------------- local install
install_factor() {   # install_factor [only-if-different]
  local arch pkg dir installed bundled
  case "$(uname -m)" in x86_64|amd64) arch=amd64 ;; aarch64|arm64) arch=arm64 ;; *) die "unsupported CPU: $(uname -m)" ;; esac
  pkg="$(ls "$HERE"/vendor/xui-factor_*_linux_"$arch".tar.gz 2>/dev/null | head -n1 || true)"
  [ -n "$pkg" ] || die "no xui-factor release for linux_$arch in vendor/ (download xui-factor_*_linux_$arch.tar.gz from https://github.com/PdYrust/XuiFactor/releases and put it there)"
  TMP_PKG="$(mktemp -d)"; trap 'rm -rf "${TMP_PKG:-}"' EXIT
  tar -xzf "$pkg" -C "$TMP_PKG"
  dir="$(find "$TMP_PKG" -maxdepth 1 -mindepth 1 -type d | head -n1)"
  [ -f "$dir/scripts/install.sh" ] || die "unexpected package layout"
  if [ "${1:-}" = only-if-different ] && [ -x "$BIN_DIR/xui-factor" ]; then
    installed="$("$BIN_DIR/xui-factor" version 2>/dev/null | head -n1 | awk '{print $2}' || true)"
    bundled="$(tr -d '[:space:]v' < "$dir/VERSION" 2>/dev/null || true)"
    if [ -n "$installed" ] && [ "$installed" = "$bundled" ]; then info "xui-factor $installed is already installed"; return 0; fi
  fi
  info "Installing xui-factor from $(basename "$pkg")"
  rm -rf "$SRC_STASH"; mkdir -p "$SRC_STASH"; cp -a "$dir" "$SRC_STASH/"   # kept for `xfp uninstall --all`
  (cd "$dir" && sh scripts/install.sh)
}

install_panel() {
  info "Installing web panel files"
  local prev_repo=""
  [ -f "$APP_DIR/REPO" ] && prev_repo="$(tr -d '[:space:]' < "$APP_DIR/REPO")"
  rm -rf "$APP_DIR"; mkdir -p "$APP_DIR" "$BIN_DIR"
  cp -a "$HERE/xfpanel" "$APP_DIR/"
  find "$APP_DIR" -name '__pycache__' -type d -prune -exec rm -rf {} +
  # remember where updates come from (the repo this copy was downloaded from, else the previous value)
  local repo="${XFP_REPO_SLUG:-$prev_repo}"
  if [ -z "$repo" ] && [ "$XFP_REPO" != "OWNER/REPO" ]; then repo="$XFP_REPO"; fi
  if [[ "$repo" =~ $SLUG_RE ]]; then printf '%s\n' "$repo" > "$APP_DIR/REPO"; fi
  install -m 0755 "$HERE/scripts/xfp.sh" "$BIN_DIR/xfp"
  ln -sf xfp "$BIN_DIR/xui-factor-panel"
}

main_local() {
  check_python
  if [ "$UPDATE" = 1 ]; then
    local web_was=0
    [ -f "$APP_DIR/xfpanel/web/index.html" ] && web_was=1
    install_deps
    install_factor only-if-different
    install_panel
    [ "$web_was" = 1 ] || rm -rf "$APP_DIR/xfpanel/web"   # a removed web panel must stay removed
    if [ "$web_was" = 1 ] && [ -f "$SYSTEMD_DIR/xui-factor-web.service" ]; then "$BIN_DIR/xfp" refresh-web; fi
    return 0
  fi
  install_deps
  install_factor
  install_panel
  local want="$WEB"
  if [ "$want" = ask ]; then
    if [ "$YES" = 1 ] || [ ! -t 0 ]; then want=no
    else read -r -p "Do you want the web panel? [y/N]: " a || true; [[ "${a:-n}" =~ ^[Yy] ]] && want=yes || want=no; fi
  fi
  if [ "$want" = yes ]; then
    "$BIN_DIR/xfp" web-setup "${WEB_ARGS[@]}"
  else
    echo "Web panel skipped. Enable it any time with: sudo xfp web-setup"
  fi
  echo
  echo "Done. Manage everything from the terminal with:  sudo xfp"
}

if [ -f "$HERE/xfpanel/__init__.py" ] && [ -f "$HERE/scripts/xfp.sh" ]; then main_local; else bootstrap_online; fi

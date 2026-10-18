#!/usr/bin/env bash
# xfp - management command for XuiFactor + its web panel.
# Every feature of the web panel is available here as a command (run `xfp help`).
set -uo pipefail

ROOT="${DESTDIR:-}"
APP_DIR="${XFP_APP_DIR:-$ROOT/opt/xui-factor-panel}"
CONF_DIR="${XFP_CONF_DIR:-$ROOT/etc/xui-factor}"
PANEL_CONF="$CONF_DIR/panel.json"
CERT_DIR="$CONF_DIR/cert"
SYSTEMD_DIR="${XFP_SYSTEMD_DIR:-$ROOT/etc/systemd/system}"
SRC_STASH="$ROOT/opt/xui-factor-panel-src"
UPDATE_LOG="${XFP_UPDATE_LOG:-$ROOT/var/log/xfp-update.log}"
export XFP_UPDATE_LOG="$UPDATE_LOG"
WEB_SERVICE="xui-factor-web"
DEFAULT_PORT=909
ACME="${HOME:-/root}/.acme.sh/acme.sh"

export XFP_CONFIG="$PANEL_CONF"
export XFP_FACTOR_CONFIG="${XFP_FACTOR_CONFIG:-$CONF_DIR/config.json}"
export XFP_FACTOR_BIN="${XFP_FACTOR_BIN:-$ROOT/usr/local/bin/xui-factor}"
export XFP_FACTOR_UNIT="${XFP_FACTOR_UNIT:-$SYSTEMD_DIR/xui-factor.service}"
export PYTHONPATH="$APP_DIR${PYTHONPATH:+:$PYTHONPATH}"

YES="${XFP_YES:-0}"          # 1 = never prompt, use defaults / flags
R=$'\033[31m'; G=$'\033[32m'; Y=$'\033[33m'; N=$'\033[0m'
err()  { printf '%s\n' "${R}error:${N} $*" >&2; }
ok()   { printf '%s\n' "${G}$*${N}"; }
warn() { printf '%s\n' "${Y}warning:${N} $*" >&2; }
info() { printf '%s\n' "$*"; }

need_root() { [ -n "$ROOT" ] || [ "$(id -u)" -eq 0 ] || { err "run as root (sudo xfp ...)"; exit 1; }; }
# run the installed package, never a same-named folder in the current directory
xpy()       { python3 -c 'import sys; sys.path[0:1] = []; from xfpanel.cli import run; sys.exit(run(sys.argv[1:]))' "$@"; }
has_systemd() { [ -z "$ROOT" ] && command -v systemctl >/dev/null 2>&1; }
sysctl_() { has_systemd && systemctl "$@"; }
interactive() { [ "$YES" != "1" ] && [ -t 0 ]; }

# ask VAR "prompt" "default"  -> value in VAR (default when non-interactive or empty)
ask() {
  local __var="$1" __prompt="$2" __def="${3:-}" __ans=""
  if interactive; then
    read -r -p "$__prompt${__def:+ [$__def]}: " __ans || true
  fi
  printf -v "$__var" '%s' "${__ans:-$__def}"
}
# yesno "prompt" default(y|n) -> 0 for yes
yesno() {
  local __ans="" __def="${2:-n}"
  if interactive; then read -r -p "$1 [$( [ "$__def" = y ] && echo Y/n || echo y/N )]: " __ans || true; fi
  __ans="${__ans:-$__def}"
  [[ "$__ans" =~ ^[Yy] ]]
}
valid_port() { [[ "$1" =~ ^[0-9]+$ ]] && [ "$1" -ge 1 ] && [ "$1" -le 65535 ]; }
port_in_use() {
  if command -v ss >/dev/null 2>&1; then ss -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "[:.]$1\$"
  elif command -v netstat >/dev/null 2>&1; then netstat -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "[:.]$1\$"
  else return 1; fi
}
is_ipv4() { [[ "$1" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] && ! [[ "$1" =~ (^|\.)(25[6-9]|2[6-9][0-9]|[3-9][0-9][0-9])(\.|$) ]]; }
valid_domain() { [[ "$1" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$ ]]; }
public_ip() {
  local ip="" url
  for url in https://api.ipify.org https://ifconfig.me/ip https://icanhazip.com; do
    ip="$(curl -4 -fsS --max-time 6 "$url" 2>/dev/null | tr -d '[:space:]')" && is_ipv4 "$ip" && { echo "$ip"; return; }
  done
  ip="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}' | head -n1)"
  is_ipv4 "$ip" && echo "$ip"
}
# ------------------------------------------------------------------ web panel
write_web_unit() {
  mkdir -p "$SYSTEMD_DIR"
  cat > "$SYSTEMD_DIR/$WEB_SERVICE.service" <<EOF
[Unit]
Description=XuiFactor web panel
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/xui-factor-panel
Environment=PYTHONPATH=/opt/xui-factor-panel
Environment=XFP_CONFIG=/etc/xui-factor/panel.json
ExecStart=$(command -v python3 || echo /usr/bin/python3) -m xfpanel serve
ExecReload=/bin/kill -HUP \$MAINPID
Restart=on-failure
RestartSec=3s
# keep the panel light: it must never compete with 3x-ui / xray
Nice=10
CPUQuota=25%
MemoryMax=128M
TasksMax=64
IOSchedulingClass=best-effort
IOSchedulingPriority=7
PrivateTmp=true
ProtectHome=read-only

[Install]
WantedBy=multi-user.target
EOF
  sysctl_ daemon-reload
}

panel_url() {
  local port cert dom host scheme=http
  port="$(xpy show-config 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["port"], d["cert"] and "https" or "http", d["ssl_domain"])' 2>/dev/null)"
  set -- $port
  [ "${2:-http}" = https ] && scheme=https
  host="${3:-}"; [ -n "$host" ] || host="$(public_ip || echo SERVER_IP)"
  echo "$scheme://$host:${1:-$DEFAULT_PORT}/"
}

panel_restart() { need_root; sysctl_ enable "$WEB_SERVICE" >/dev/null 2>&1; sysctl_ restart "$WEB_SERVICE"; }

setup_credentials() {  # setup_credentials USER PASS   (empty pass => prompt or generate)
  local user="$1" pass="$2" out
  if [ -z "$pass" ] && interactive; then
    local p1 p2
    read -rs -p "Panel password (empty = generate random): " p1; echo
    if [ -n "$p1" ]; then read -rs -p "Repeat password: " p2; echo; [ "$p1" = "$p2" ] || { err "passwords do not match"; return 1; }; pass="$p1"; fi
  fi
  if [ -n "$pass" ]; then
    printf '%s\n' "$pass" | xpy credentials --username "$user" --password-stdin || return 1
  else
    out="$(xpy credentials --username "$user" --generate)" || return 1
    printf '%s\n' "$out"; warn "save this generated password now; it is not shown again."
  fi
}

web_setup() {   # web_setup [port] [user] [pass] [domain] [acme_port] [ssl: auto|none|ip]
  need_root
  local port="${1:-}" user="${2:-}" pass="${3:-}" domain="${4:-}" acme_port="${5:-}" sslmode="${6:-auto}"
  [ -f "$APP_DIR/xfpanel/web/index.html" ] || { err "web panel files are not installed (they were removed). Re-run: ./install.sh"; return 1; }
  mkdir -p "$CONF_DIR"
  if [ -z "$port" ]; then ask port "Web panel port" "$DEFAULT_PORT"; fi
  valid_port "$port" || { err "invalid port: $port"; return 1; }
  if port_in_use "$port" && ! sysctl_ is-active --quiet "$WEB_SERVICE" 2>/dev/null; then
    warn "port $port is already in use by another process"
    interactive && { ask port "Choose another port" ""; valid_port "$port" || return 1; }
  fi
  [ -n "$user" ] || ask user "Panel username" "admin"
  setup_credentials "$user" "$pass" || return 1
  xpy set-port "$port" >/dev/null || return 1

  if [ "$sslmode" = auto ]; then
    if yesno "Do you have a domain pointed to this server?" n; then
      [ -n "$domain" ] || ask domain "Enter your domain (e.g. panel.example.com)" ""
      sslmode=domain
    else
      yesno "Issue an SSL certificate for the server IP (Let's Encrypt, ~6 day validity, auto-renews)?" y && sslmode=ip || sslmode=none
    fi
  fi
  case "$sslmode" in
    domain) valid_domain "$domain" || { err "invalid domain: $domain"; return 1; }
            ssl_issue "$domain" domain "$acme_port" || ssl_failed ;;
    ip)     ssl_issue "" ip "$acme_port" || ssl_failed ;;
    none)   xpy set-ssl >/dev/null; warn "panel will run on plain HTTP (credentials travel unencrypted)." ;;
  esac
  write_web_unit
  panel_restart
  ok "Web panel is ready."
  info "  URL: $(panel_url)"
  info "  Manage anything from the terminal with: xfp"
}
ssl_failed() {
  warn "SSL setup failed; continuing WITHOUT TLS. Retry later with: xfp ssl-issue"
  xpy set-ssl >/dev/null
}


# --------------------------------------------------------------------- updates
# python snippets run against the installed package only (the current directory is dropped from sys.path)
pyi()         { python3 -c 'import sys; sys.path[0:1] = []; exec(sys.argv[1])' "$1"; }
app_version() { pyi 'import xfpanel; print(xfpanel.__version__)' 2>/dev/null; }
json_field()  { python3 -c 'import json,sys; print(json.load(sys.stdin).get(sys.argv[1], ""))' "$1" 2>/dev/null; }

refresh_web() {   # re-create the unit (limits may change between versions) and restart the panel
  need_root
  [ -f "$APP_DIR/xfpanel/web/index.html" ] || return 0
  write_web_unit
  sysctl_ is-enabled "$WEB_SERVICE" >/dev/null 2>&1 && sysctl_ restart "$WEB_SERVICE"
  return 0
}

rollback_update() {
  warn "rolling back to the previous version"
  rm -rf "$APP_DIR"
  [ -d "$APP_DIR.bak" ] && mv "$APP_DIR.bak" "$APP_DIR"
  [ -f "$ROOT/usr/local/bin/xfp.bak" ] && mv -f "$ROOT/usr/local/bin/xfp.bak" "$ROOT/usr/local/bin/xfp"
  [ -f "$APP_DIR/xfpanel/web/index.html" ] && { write_web_unit; sysctl_ restart "$WEB_SERVICE" >/dev/null 2>&1 || true; }
  return 0
}

health_check() {  # the new code must import, and the panel service (if used) must come up
  pyi 'import xfpanel, xfpanel.server, xfpanel.cli' >/dev/null 2>&1 || return 1
  if has_systemd && [ -f "$SYSTEMD_DIR/$WEB_SERVICE.service" ] && [ -f "$APP_DIR/xfpanel/web/index.html" ]; then
    local i; for i in 1 2 3 4 5 6 7 8 9 10; do sysctl_ is-active --quiet "$WEB_SERVICE" && return 0; sleep 2; done
    return 1
  fi
  return 0
}

do_update() {
  local check=0 force_yes=0 tag="" archive="" delay=0 force=0 repo cur new_ver tmp dir url
  while [ $# -gt 0 ]; do case "$1" in
    --check) check=1 ;; --yes|-y) force_yes=1 ;; --tag) tag="${2:-}"; shift ;; --archive) archive="${2:-}"; shift ;;
    --delay) delay="${2:-0}"; shift ;; --force) force=1 ;;
    *) err "unknown option: $1"; return 1 ;; esac; shift; done
  [ "$delay" -gt 0 ] 2>/dev/null && sleep "$delay"
  cur="$(app_version)"; [ -n "$cur" ] || { err "cannot determine the installed version"; return 1; }

  if [ -z "$archive" ]; then
    repo="$(xpy update-check --repo 2>/dev/null)"
    [ -n "$repo" ] || { err "update source is not configured. Set it with: xfp set-repo owner/repo"; return 1; }
    if [ -z "$tag" ]; then
      local info newer
      info="$(xpy update-check --json --force 2>/dev/null)" || true
      [ -n "$info" ] || { err "could not check for updates"; return 1; }
      [ -z "$(printf '%s' "$info" | json_field error)" ] || { err "$(printf '%s' "$info" | json_field error)"; return 1; }
      tag="$(printf '%s' "$info" | json_field tag)"
      newer="$(printf '%s' "$info" | json_field newer)"
      info "Installed version: $cur    Latest release: $tag"
      if [ "$check" = 1 ]; then [ "$newer" = True ] && ok "Update available: $tag (run: xfp update)" || ok "You are up to date."; return 0; fi
      if [ "$newer" != True ] && [ "$force" != 1 ]; then ok "Already up to date ($cur)."; return 0; fi
    fi
    [[ "$tag" =~ ^v?[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z.-]+)?$ ]] || { err "invalid release tag: $tag"; return 1; }
    [ "$check" = 1 ] && { info "Target release: $tag"; return 0; }
    if [ "$force_yes" != 1 ]; then yesno "Update to release $tag?" n || { info "aborted"; return 1; }; fi
  else
    [ -f "$archive" ] || { err "archive not found: $archive"; return 1; }
    if [ "$force_yes" != 1 ]; then yesno "Update from local archive $archive?" n || { info "aborted"; return 1; }; fi
  fi

  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' RETURN
  mkdir -p "$tmp/x"
  if [ -n "$archive" ]; then
    case "$archive" in *.zip) unzip -q "$archive" -d "$tmp/x" || { err "cannot unzip $archive"; return 1; } ;;
                       *)     tar -xzf "$archive" -C "$tmp/x" || { err "cannot extract $archive"; return 1; } ;;
    esac
  else
    url="https://github.com/$repo/archive/refs/tags/$tag.tar.gz"
    info "Downloading $url"
    curl -fSL --retry 3 --connect-timeout 15 --max-time 600 -o "$tmp/src.tar.gz" "$url" || { err "download failed"; return 1; }
    tar -xzf "$tmp/src.tar.gz" -C "$tmp/x" || { err "the downloaded archive is not valid"; return 1; }
  fi
  dir="$(find "$tmp/x" -maxdepth 2 -name install.sh -printf '%h\n' | head -n1)"
  [ -n "$dir" ] && [ -f "$dir/xfpanel/__init__.py" ] && [ -f "$dir/scripts/xfp.sh" ] || { err "this archive is not a valid XuiFactor panel release"; return 1; }
  new_ver="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$dir/xfpanel/__init__.py" | head -n1)"
  [ -n "$new_ver" ] || { err "cannot read the version inside the release"; return 1; }
  if [ -n "$tag" ] && [ "${tag#v}" != "$new_ver" ]; then
    err "release tag $tag does not match the version inside the code ($new_ver); publish it with tools/release.sh"; return 1
  fi
  info "Updating $cur -> $new_ver"

  xpy backup >/dev/null 2>&1 && info "Safety backup of the factor records created" || true
  rm -rf "$APP_DIR.bak"; cp -a "$APP_DIR" "$APP_DIR.bak"
  cp -a "$ROOT/usr/local/bin/xfp" "$ROOT/usr/local/bin/xfp.bak" 2>/dev/null || true
  if ! XFP_REPO_SLUG="${repo:-}" bash "$dir/install.sh" --update; then err "the installer failed"; rollback_update; return 1; fi
  if ! health_check; then err "the new version did not start correctly"; rollback_update; return 1; fi
  rm -f "$ROOT/usr/local/bin/xfp.bak"
  ok "Updated to $new_ver. (previous files kept in $APP_DIR.bak)"
}

update_cmd() {
  need_root
  mkdir -p "$(dirname "$UPDATE_LOG")" 2>/dev/null || true
  do_update "$@" 2>&1 | tee "$UPDATE_LOG"
  return "${PIPESTATUS[0]}"
}

# ------------------------------------------------------------------------ SSL
install_acme() {
  [ -x "$ACME" ] && return 0
  info "Installing acme.sh ..."
  if command -v curl >/dev/null 2>&1; then curl -fsSL https://get.acme.sh | sh -s email="${XFP_ACME_EMAIL:-}" >/dev/null 2>&1
  elif command -v wget >/dev/null 2>&1; then wget -qO- https://get.acme.sh | sh -s email="${XFP_ACME_EMAIL:-}" >/dev/null 2>&1
  else err "curl or wget is required to install acme.sh"; return 1; fi
  [ -x "$ACME" ] || { err "acme.sh installation failed (check internet access)"; return 1; }
}

pick_acme_port() {  # echoes a usable validation port
  local p="${1:-}"
  [ -n "$p" ] || ask p "Port for SSL validation (default 80)" "80"
  while true; do
    valid_port "$p" || { err "invalid port"; interactive || return 1; ask p "Port for SSL validation" "80"; continue; }
    if port_in_use "$p"; then
      warn "port $p is in use."
      interactive || return 1
      ask p "Enter another port (empty = abort)" ""; [ -n "$p" ] || return 1
      continue
    fi
    break
  done
  [ "$p" = 80 ] || warn "Let's Encrypt always connects to port 80; forward external port 80 to $p." >&2
  echo "$p"
}

ssl_deploy() {   # ssl_deploy TARGET -> install cert files + reload hook
  local target="$1"
  mkdir -p "$CERT_DIR"; chmod 700 "$CERT_DIR"
  "$ACME" --installcert -d "$target" --key-file "$CERT_DIR/privkey.pem" --fullchain-file "$CERT_DIR/fullchain.pem" \
    --reloadcmd "systemctl reload $WEB_SERVICE 2>/dev/null || systemctl restart $WEB_SERVICE 2>/dev/null || true" >/dev/null 2>&1 || true
  [ -s "$CERT_DIR/fullchain.pem" ] && [ -s "$CERT_DIR/privkey.pem" ] || return 1
  chmod 600 "$CERT_DIR/privkey.pem"; chmod 644 "$CERT_DIR/fullchain.pem"
}

ssl_issue() {   # ssl_issue DOMAIN|"" domain|ip [acme_port]
  need_root
  local target="$1" mode="$2" port
  install_acme || return 1
  if [ "$mode" = ip ]; then
    local ip; ip="$(public_ip)"
    ask ip "Server public IPv4" "$ip"
    is_ipv4 "$ip" || { err "invalid IPv4 address"; return 1; }
    target="$ip"
  fi
  port="$(pick_acme_port "${3:-}")" || { err "no usable validation port"; return 1; }
  "$ACME" --set-default-ca --server letsencrypt >/dev/null 2>&1
  if [ "$mode" = ip ]; then
    "$ACME" --issue -d "$target" --standalone --server letsencrypt --certificate-profile shortlived --days 6 --httpport "$port" --force || { err "certificate issue failed (is port 80 reachable from the internet?)"; return 1; }
  else
    "$ACME" --issue -d "$target" --standalone --server letsencrypt --httpport "$port" --force || { err "certificate issue failed (does the domain point to this server and is port 80 open?)"; return 1; }
  fi
  ssl_deploy "$target" || { err "certificate files were not installed"; return 1; }
  "$ACME" --upgrade --auto-upgrade >/dev/null 2>&1 || true
  xpy set-ssl --cert "$CERT_DIR/fullchain.pem" --key "$CERT_DIR/privkey.pem" --domain "$target" >/dev/null || return 1
  ok "SSL certificate installed for $target"
}

ssl_renew() {
  need_root
  local cur target flag=""
  cur="$(python3 -c 'import json,os; print(json.load(open(os.environ["XFP_CONFIG"])).get("ssl_domain",""))' 2>/dev/null)"
  [ -x "$ACME" ] || { err "acme.sh is not installed; issue a certificate first: xfp ssl-issue"; return 1; }
  ask target "Enter the domain to renew (empty = server IP certificate; current: ${cur:-none})" "$cur"
  [ -n "$target" ] || { err "no domain or IP known; issue a certificate first: xfp ssl-issue"; return 1; }
  [ -d "$(dirname "$ACME")/${target}_ecc" ] && flag="--ecc"
  info "Renewing certificate for $target ..."
  if ! "$ACME" --renew -d "$target" $flag --force; then
    err "renewal failed. Make sure port 80 is free/reachable, or re-issue with: xfp ssl-issue"; return 1
  fi
  ssl_deploy "$target" || { err "renewed files could not be installed"; return 1; }
  sysctl_ reload "$WEB_SERVICE" 2>/dev/null || sysctl_ restart "$WEB_SERVICE" 2>/dev/null || true
  ok "Certificate renewed and panel reloaded."
}

ssl_issue_interactive() {
  local d=""
  if yesno "Use a domain (otherwise the server IP)?" n; then ask d "Enter your domain" ""; valid_domain "$d" || { err "invalid domain"; return 1; }; ssl_issue "$d" domain
  else ssl_issue "" ip; fi
}

# --------------------------------------------------------------------- helpers
uninstall_web() {
  need_root
  local mode="" delay=0 dom="" a force=0
  while [ $# -gt 0 ]; do case "$1" in
    --web-only) mode=web ;; --all) mode=all ;; --yes|-y) force=1 ;; --delay) delay="${2:-0}"; shift ;; --purge) ;;
    *) err "unknown option: $1"; return 1 ;; esac; shift; done
  if [ -z "$mode" ]; then
    interactive || { err "choose --web-only or --all"; return 1; }
    cat <<'EOF'
What do you want to remove?
  1) Web panel only      - xui-factor keeps running on the server and keeps applying factors
                           (the 'xfp' terminal command stays available)
  2) EVERYTHING          - pause nothing: drops all factor records from the database, removes the
                           xui-factor service/binary and the web panel (a backup is taken first)
EOF
    read -r -p "Choose [1/2, empty = cancel]: " a || true
    case "$a" in 1) mode=web ;; 2) mode=all ;; *) info "aborted"; return 1 ;; esac
  fi
  if [ "$mode" = all ]; then
    [ "$force" = 1 ] || yesno "This REMOVES XuiFactor completely (factors stop being applied). Continue?" n || { info "aborted"; return 1; }
  else
    [ "$force" = 1 ] || yesno "Remove the web panel (factors keep working)?" n || { info "aborted"; return 1; }
  fi
  [ "$delay" -gt 0 ] 2>/dev/null && sleep "$delay"

  dom="$(python3 -c 'import json,os; print(json.load(open(os.environ["XFP_CONFIG"])).get("ssl_domain",""))' 2>/dev/null || true)"
  if [ "$mode" = all ]; then
    # records first, while the python tooling still exists (this also saves a safety backup)
    python3 -m xfpanel purge --yes 2>&1 | sed 's/^/  /' || true
  fi
  sysctl_ disable --now "$WEB_SERVICE" >/dev/null 2>&1
  rm -f "$SYSTEMD_DIR/$WEB_SERVICE.service"; sysctl_ daemon-reload
  [ -n "$dom" ] && [ -x "$ACME" ] && "$ACME" --remove -d "$dom" >/dev/null 2>&1 || true
  rm -rf "$CERT_DIR" "$PANEL_CONF"
  if [ "$mode" = web ]; then
    rm -rf "$APP_DIR/xfpanel/web"
    ok "Web panel removed. xui-factor keeps running; manage it with: xfp (restore the panel any time with ./install.sh)"
    return 0
  fi
  local pkg; pkg="$(ls -d "$SRC_STASH"/xui-factor_* 2>/dev/null | head -n1 || true)"
  if [ -n "$pkg" ]; then sh "$pkg/scripts/uninstall.sh" --purge || warn "xui-factor uninstall script reported a problem"
  else warn "original package not found; remove /usr/local/bin/xui-factor and its service manually"; fi
  rm -rf "$APP_DIR" "$APP_DIR.bak" "$SRC_STASH"
  rm -f "$ROOT/usr/local/bin/xfp.bak" "$ROOT/usr/local/bin/xfp" "$ROOT/usr/local/bin/xui-factor-panel"
  ok "XuiFactor fully removed. Backups were kept in /var/backups/xui-factor-panel and /var/backups/xui-factor."
  exit 0
}

pause_key() { interactive && read -r -p "Press Enter to continue..." _ || true; }

menu() {
  while true; do
    clear 2>/dev/null || true
    cat <<'EOF'
==================== XuiFactor ====================
  1) Status                       13) Create backup (factor)
  2) List inbounds                14) List backups (+ where they are)
  3) Set inbound factor           15) Restore backup
  4) Remove inbound factor        16) Health check (doctor)
  5) List clients                 17) Web panel: start/stop/restart/logs
  6) Set client factor            18) Change panel username/password
  7) Remove client factor         19) Change panel port
  8) Events / audit               20) Issue SSL certificate
  9) Clear ALL factors            21) Renew SSL certificate
 10) Disable factor (pause+stop)  22) Re-run web panel setup
 11) Enable factor                23) Remove web panel / uninstall
 12) Purge factor from database   24) Check for / install updates
  0) Exit
===================================================
EOF
    local c a b f; read -r -p "Choose: " c || exit 0
    case "$c" in
      1) xpy status ;;
      2) xpy inbounds ;;
      3) read -r -p "Inbound ID(s) separated by space: " a; read -r -p "Factor (>= 1): " f; xpy set-inbound "$f" $a ;;
      4) read -r -p "Inbound ID(s) separated by space: " a; xpy remove-inbound $a ;;
      5) read -r -p "Search email (optional): " a; xpy clients ${a:+--query "$a"} ;;
      6) read -r -p "Client email: " a; read -r -p "Inbound ID (optional): " b; read -r -p "Factor (>= 1): " f; xpy set-client "$a" $b "$f" ;;
      7) read -r -p "Client email(s) separated by space: " a; xpy remove-client $a ;;
      8) xpy events ;;
      9) yesno "Remove EVERY factor?" n && xpy clear-all ;;
      10) yesno "Pause all factors and stop the service?" n && xpy disable ;;
      11) xpy enable ;;
      12) xpy purge ;;
      13) xpy backup ;;
      14) xpy backups ;;
      15) xpy restore ;;
      16) xpy doctor ;;
      17) read -r -p "start|stop|restart|status|logs: " a; panel_service "$a" ;;
      18) read -r -p "Username: " a; need_root; setup_credentials "${a:-admin}" "" && panel_restart ;;
      19) read -r -p "New port: " a; valid_port "$a" && xpy set-port "$a" && panel_restart ;;
      20) ssl_issue_interactive ;;
      21) ssl_renew ;;
      22) web_setup ;;
      23) uninstall_web ;;
      24) update_cmd ;;
      0) exit 0 ;;
      *) warn "unknown option" ;;
    esac
    pause_key
  done
}

panel_service() {
  need_root
  case "${1:-status}" in
    start|stop|restart) sysctl_ "$1" "$WEB_SERVICE" && ok "$1: done" ;;
    status) sysctl_ status "$WEB_SERVICE" --no-pager ;;
    logs)   journalctl -u "$WEB_SERVICE" -n 80 --no-pager ;;
    *) err "usage: xfp panel start|stop|restart|status|logs"; return 1 ;;
  esac
}

usage() {
  cat <<'EOF'
xfp - XuiFactor manager

Factors (same features as the web panel):
  xfp status                         summary of rules / traffic impact
  xfp inbounds [--page N --size N -q TEXT]
  xfp set-inbound FACTOR ID [ID...]  factor for whole inbound(s)
  xfp remove-inbound ID [ID...]
  xfp clients [--page N --size N -q TEXT --inbound ID --filter factored|single|scope|override|none]
  xfp set-client EMAIL [INBOUND_ID] FACTOR
  xfp remove-client EMAIL [EMAIL...]
  xfp events [--kind lifecycle|all|<type>] [--page N]
  xfp clear-all                      remove every factor
  xfp disable | enable               pause+stop / start+resume everything
  xfp purge [--yes]                  drop all xui_factor_* tables and the service
  xfp backup [--history | --full]    factor backup (small) or whole 3x-ui DB; prints the saved path
  xfp backups                        list backups and show their directories
  xfp restore [FILE|latest] [--yes]  restore factor records (asks which one when no FILE)
  xfp backup-delete FILE
  xfp cleanup [--apply] | doctor

Updates:
  xfp update [--check] [--yes]       check GitHub for a newer release and install it (asks to confirm)
  xfp update --archive FILE          update from a local .tar.gz/.zip of a release (no GitHub needed)
  xfp set-repo owner/repo            GitHub repository updates are taken from

Web panel:
  xfp web-setup [--port N --username U --password P --domain D --acme-port N --no-ssl --ip-cert --yes]
  xfp panel start|stop|restart|status|logs
  xfp credentials [--username U]     change panel login (prompts for password)
  xfp set-port N
  xfp url
  xfp ssl-issue [--domain D | --ip] [--acme-port N]
  xfp ssl-renew                      asks for the domain, then renews
  xfp uninstall [--web-only | --all] [--yes]
                                     --web-only: remove just the web panel (factors keep working)
                                     --all     : drop records, remove xui-factor + panel

Run `xfp` without arguments for the interactive menu.
EOF
}

main() {
  [ $# -gt 0 ] || { menu; return; }
  local cmd="$1"; shift
  case "$cmd" in
    help|-h|--help) usage ;;
    menu) menu ;;
    status|inbounds|clients|events|set-inbound|remove-inbound|set-client|remove-client|clear-all|disable|enable|purge|backup|backups|restore|backup-delete|cleanup|doctor)
      need_root; xpy "$cmd" "$@" ;;
    credentials)
      need_root
      if [ $# -gt 0 ] && [ "$1" = "--username" ]; then setup_credentials "${2:-admin}" ""; else setup_credentials "$(python3 -c 'import json,os; print(json.load(open(os.environ["XFP_CONFIG"])).get("username","admin"))' 2>/dev/null || echo admin)" ""; fi && panel_restart ;;
    set-port) need_root; valid_port "${1:-}" || { err "invalid port"; exit 1; }; xpy set-port "$1" && panel_restart ;;
    url) panel_url ;;
    panel) panel_service "${1:-status}" ;;
    web-setup)
      local port="" user="" pass="" domain="" ap="" ssl=auto
      while [ $# -gt 0 ]; do case "$1" in
        --port) port="$2"; shift ;; --username) user="$2"; shift ;; --password) pass="$2"; shift ;;
        --domain) domain="$2"; ssl=domain; shift ;; --acme-port) ap="$2"; shift ;;
        --ip-cert) ssl=ip ;; --no-ssl) ssl=none ;; --yes|-y) YES=1 ;;
        *) err "unknown option: $1"; exit 1 ;; esac; shift; done
      web_setup "$port" "$user" "$pass" "$domain" "$ap" "$ssl" ;;
    ssl-issue)
      local d="" ap="" m=""
      while [ $# -gt 0 ]; do case "$1" in --domain) d="$2"; m=domain; shift ;; --ip) m=ip ;; --acme-port) ap="$2"; shift ;; *) err "unknown option: $1"; exit 1 ;; esac; shift; done
      case "$m" in
        domain) valid_domain "$d" || { err "invalid domain"; exit 1; }; ssl_issue "$d" domain "$ap" ;;
        ip) ssl_issue "" ip "$ap" ;;
        *) ssl_issue_interactive ;;
      esac || exit 1
      panel_restart ;;
    ssl-renew) ssl_renew ;;
    update) update_cmd "$@" ;;
    set-repo) need_root; xpy set-repo "${1:-}" ;;
    refresh-web) refresh_web ;;
    uninstall) uninstall_web "$@" ;;
    *) err "unknown command: $cmd"; usage; exit 1 ;;
  esac
}

main "$@"

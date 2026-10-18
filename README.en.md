# XuiFactor Web Panel

A web panel (and `xfp` terminal tool) for [XuiFactor](https://github.com/PdYrust/XuiFactor) - traffic multipliers ("factors") for 3x-ui 3.x. The official `xui-factor` binary stays the engine; the panel only manages it.

## Install (one line)

```sh
bash <(curl -Ls https://raw.githubusercontent.com/Mrignored/xpf/main/install.sh)
```

The installer downloads the latest release, installs xui-factor and its service, then asks whether you want the web panel (port 909 by default, username/password, optional domain or IP SSL via Let's Encrypt).
Unattended: `... --yes --port 909 --username admin --password 'Pass#1234' --domain panel.example.com` (`--ip-cert`, `--no-ssl`, `--no-web`, `--version v1.1.0`).

## Features

* Inbound factors (by inbound ID) and per-client factors (e-mail + inbound ID): set, edit, delete, bulk select, search, pagination.
* Dashboard (active factors, extra traffic counted, latest events), reports/audit log with filters.
* Backup & restore (small factor-only backups, full database copy, download/upload), safe restore onto another server (clients matched by e-mail).
* Maintenance: disable/enable globally, clear all factors, remove the factor records from the database, remove only the web panel or everything.
* Light/dark theme in the style of 3x-ui; Persian and English UI.
* Updates: check GitHub releases and update from the dashboard or with `xfp update`.
* Everything is also available from the terminal: run `sudo xfp` (menu) or `xfp help`.

## Publish on GitHub

1. Create a **public** repository and copy this project into it (keep `vendor/` and `.github/`).
2. `bash tools/set-repo.sh USER/REPO` (sets your repository in `install.sh` and the READMEs).
3. `git add -A && git commit -m "v1.1.0" && git push -u origin main && git tag v1.1.0 && git push origin v1.1.0` - the workflow creates the GitHub Release.
4. Users install with the one-liner above.

## Release a new version

Copy the new files into the repo, then `bash tools/release.sh 1.2.0 "note"` (bumps `__version__`, commits, tags `v1.2.0`, pushes). Installed panels show the update within the hour (or immediately with the check button).

## Updating a server

Dashboard -> "Check for updates" -> "Update now", or `xfp update` (asks `Update to release vX? [y/N]`), `xfp update --check`, `xfp update --archive release.tar.gz` (offline). A factor backup is taken first, the previous files are kept in `/opt/xui-factor-panel.bak`, and a failed update is rolled back automatically. Config, password, certificates, database and factors are never touched.
Updates run code from your repository as root: enable two-factor authentication on your GitHub account.

## Limits

SQLite databases only for the web panel (CLI works with PostgreSQL); the bundled engine is `linux_amd64` (put an `arm64` release into `vendor/` for ARM). Python 3.8+ is required (installed automatically).

License: AGPL-3.0 (same as XuiFactor).

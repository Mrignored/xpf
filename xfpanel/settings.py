"""Runtime settings for the panel and for locating the xui-factor installation."""
from __future__ import annotations

import json
import os
import secrets
from typing import Any, Dict

PANEL_CONFIG_PATH = os.environ.get("XFP_CONFIG", "/etc/xui-factor/panel.json")
FACTOR_CONFIG_PATH = os.environ.get("XFP_FACTOR_CONFIG", "/etc/xui-factor/config.json")
FACTOR_BIN = os.environ.get("XFP_FACTOR_BIN", "/usr/local/bin/xui-factor")
FACTOR_SERVICE = os.environ.get("XFP_FACTOR_SERVICE", "xui-factor")
FACTOR_UNIT_PATH = os.environ.get("XFP_FACTOR_UNIT", "/etc/systemd/system/xui-factor.service")
DEFAULT_PORT = 909
DEFAULT_BACKUP_DIR = "/var/backups/xui-factor-panel"
MANAGER_BIN = os.environ.get("XFP_MANAGER_BIN", "/usr/local/bin/xfp")

DEFAULTS: Dict[str, Any] = {
    "host": "0.0.0.0",
    "port": DEFAULT_PORT,
    "username": "admin",
    "password_hash": "",
    "secret": "",
    "cert": "",
    "key": "",
    "ssl_domain": "",
    "session_hours": 12,
}


def load_panel_config(path: str = "") -> Dict[str, Any]:
    path = path or PANEL_CONFIG_PATH
    cfg = dict(DEFAULTS)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            cfg.update(data)
    except FileNotFoundError:
        pass
    return cfg


def save_panel_config(cfg: Dict[str, Any], path: str = "") -> None:
    """Atomically write the config with 0600 permissions (it holds secrets)."""
    path = path or PANEL_CONFIG_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def ensure_secret(cfg: Dict[str, Any]) -> Dict[str, Any]:
    if not cfg.get("secret"):
        cfg["secret"] = secrets.token_hex(32)
    return cfg


def backup_dir() -> str:
    """Where factor backups are written (env override > panel.json > default)."""
    return os.environ.get("XFP_BACKUP_DIR") or str(load_panel_config().get("backup_dir") or DEFAULT_BACKUP_DIR)

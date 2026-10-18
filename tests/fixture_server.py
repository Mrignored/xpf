"""Starts a panel on a throw-away database for the Node UI smoke test. Prints 'PORT <n>'."""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_panel as t  # noqa: E402  (sets the XFP_* environment as a side effect)
from xfpanel import auth, core, server, settings, update  # noqa: E402

t.build_db()
os.environ['XFP_REPO'] = 'acme/panel'
update.fetch_json = lambda url: {'tag_name': 'v9.0.0', 'html_url': 'https://github.com/acme/panel/releases/tag/v9.0.0', 'body': 'Fixes and news'}
core.Manager._spawn_manager = lambda self, args, unit: None
settings.save_panel_config(settings.ensure_secret({**settings.DEFAULTS, "username": "admin", 
                                                   "password_hash": auth.hash_password("secret-pass-1")}))
core.Manager.service_status = lambda self: {"active": "active", "enabled": "enabled", "installed": True}
core.Manager._systemctl = staticmethod(lambda *a, **k: core.Result(127, "", "n/a"))
import socket  # noqa: E402
with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    free_port = probe.getsockname()[1]
cfg = settings.load_panel_config()
cfg.update({"port": free_port, "host": "127.0.0.1"})
srv = server.PanelServer(cfg, server.ConfigCache(t.PANEL_CFG), core.Manager())
print("PORT", srv.server_address[1], flush=True)
threading.Thread(target=srv.serve_forever, daemon=True).start()
sys.stdin.read()  # run until the parent closes stdin

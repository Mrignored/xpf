"""End-to-end tests: real xui-factor binary + real SQLite database + real HTTP server.

Run with:  XFP_TEST_BIN=/path/to/xui-factor python3 -m unittest discover -s tests -v
"""
import gzip
import http.client
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

TMP = tempfile.mkdtemp(prefix="xfp-test-")
DB = os.path.join(TMP, "x-ui.db")
FACTOR_CFG = os.path.join(TMP, "config.json")
PANEL_CFG = os.path.join(TMP, "panel.json")
os.environ.update({
    "XFP_CONFIG": PANEL_CFG,
    "XFP_FACTOR_CONFIG": FACTOR_CFG,
    "XFP_FACTOR_BIN": os.environ.get("XFP_TEST_BIN", "/tmp/xui-factor-bin"),
    "XFP_FACTOR_UNIT": os.path.join(TMP, "xui-factor.service"),
    "XFP_BACKUP_DIR": os.path.join(TMP, "factor-backups"),
})

from xfpanel import auth, core, server, settings  # noqa: E402


def build_db():
    if os.path.exists(DB):
        os.remove(DB)
    c = sqlite3.connect(DB)
    c.executescript("""
    create table inbounds(id integer primary key autoincrement,user_id int,up int,down int,total int,remark text,enable int,expiry_time int,port int,protocol text,tag text unique,settings text);
    create table client_traffics(id integer primary key autoincrement,inbound_id int,enable int,email text unique,up int,down int,expiry_time int,total int,reset int default 0,reset_day int default 0,reset_weekday int default 0,reset_max int default 0,reset_count int default 0,last_online int default 0,last_sub_fetch int default 0);
    create table clients(id integer primary key autoincrement,email text unique not null,sub_id text,enable int default 1);
    create table client_inbounds(client_id int,inbound_id int,flow_override text,created_at int,primary key(client_id,inbound_id));
    create table settings(id integer primary key,key text,value text);
    insert into inbounds(id,remark,enable,port,protocol,tag) values(1,'vless-443',1,443,'vless','a'),(2,'trojan_8443',1,8443,'trojan','b'),(3,'empty',1,1,'vmess','c');
    """)
    for i in range(1, 8):
        inbound = 1 if i <= 3 else 2
        c.execute("insert into client_traffics(inbound_id,enable,email,up,down,total) values(?,?,?,?,?,0)", (inbound, 1, "user%d" % i, 1000, 2000))
        c.execute("insert into clients(email) values(?)", ("user%d" % i,))
        c.execute("insert into client_inbounds values(?,?,?,?)", (i, inbound, "", 0))
    c.commit()
    c.close()
    with open(FACTOR_CFG, "w") as fh:
        json.dump({"database_backend": "sqlite", "database_path": DB, "poll_interval": "5s", "busy_timeout": "5s",
                   "backup_dir": os.path.join(TMP, "bk"), "enable_backups": False, "log_level": "info",
                   "auto_cleanup": True, "missing_client_grace": "30s", "disabled_rule_retention": "7d",
                   "audit_retention": "30d"}, fh)


class Client:
    def __init__(self, port):
        self.port, self.cookie = port, None

    def call(self, method, path, body=None, headers=None, csrf=True, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        hdrs = dict(headers or {})
        if csrf and method == "POST":
            hdrs["X-Requested-With"] = "XFPanel"
        if self.cookie:
            hdrs["Cookie"] = self.cookie
        payload = None
        if raw is not None:
            payload = raw
            hdrs["Content-Type"] = "application/octet-stream"
        elif body is not None:
            payload = json.dumps(body)
            hdrs["Content-Type"] = "application/json"
        conn.request(method, path, payload, hdrs)
        resp = conn.getresponse()
        data = resp.read()
        set_cookie = resp.getheader("Set-Cookie")
        if set_cookie and method == "POST" and path == "/api/login" and resp.status == 200:
            self.cookie = set_cookie.split(";")[0]
        conn.close()
        if resp.getheader("Content-Disposition"):
            return resp.status, data, resp
        try:
            return resp.status, json.loads(data) if data else None, resp
        except ValueError:
            return resp.status, data, resp

    def get(self, path):
        return self.call("GET", path)[:2]

    def post(self, path, body=None):
        return self.call("POST", path, body or {})[:2]


class PanelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        settings.save_panel_config(settings.ensure_secret({**settings.DEFAULTS, "username": "admin",
                                                           "password_hash": auth.hash_password("secret-pass-1")}))
        core.Manager.service_status = lambda self: {"active": "unknown", "enabled": "unknown", "installed": False}
        core.Manager._systemctl = staticmethod(lambda *a, **k: core.Result(127, "", "systemctl not available"))
        cls.mgr = core.Manager()
        cfg = settings.load_panel_config()
        cfg["port"] = 0
        cls.srv = server.PanelServer(cfg, server.ConfigCache(PANEL_CFG), cls.mgr)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        build_db()
        shutil.rmtree(os.environ["XFP_BACKUP_DIR"], ignore_errors=True)  # every test starts with no backups
        self.c = Client(self.port)
        status, _ = self.c.post("/api/login", {"username": "admin", "password": "secret-pass-1"})
        self.assertEqual(status, 200)

    # ---------------------------------------------------------- auth
    def test_requires_login_and_csrf(self):
        anon = Client(self.port)
        self.assertEqual(anon.get("/api/overview")[0], 401)
        self.assertEqual(anon.post("/api/login", {"username": "admin", "password": "bad"})[0], 401)
        status, _, _ = self.c.call("POST", "/api/actions/doctor", {}, csrf=False)
        self.assertEqual(status, 403)
        status, _, _ = self.c.call("POST", "/api/actions/doctor", {}, headers={"X-Requested-With": "XFPanel", "Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        self.assertEqual(self.c.get("/api/me")[0], 200)

    def test_static_files_and_headers(self):
        status, body, resp = self.c.call("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("default-src 'self'", resp.getheader("Content-Security-Policy"))
        self.assertEqual(self.c.call("GET", "/../etc/passwd")[0], 404)
        self.assertEqual(self.c.call("GET", "/app.js")[0], 200)

    def test_login_throttle(self):
        anon = Client(self.port)
        statuses = [anon.post("/api/login", {"username": "admin", "password": "x%d" % i})[0] for i in range(7)]
        self.assertEqual(statuses[-1], 429)
        self.srv.throttle = auth.LoginThrottle()

    # ---------------------------------------------------------- factors
    def test_overview_without_tables(self):
        _, data = self.c.get("/api/overview")
        self.assertFalse(data["tables"])
        self.assertEqual(data["clients_total"], 7)

    def test_validation(self):
        for bad in ("0.5", "abc", "", "1.1234567", "-2", "99999999"):
            status, data = self.c.post("/api/clients/set", {"items": [{"email": "user1"}], "factor": bad})
            self.assertEqual(status, 400, bad)
        status, data = self.c.post("/api/clients/set", {"items": [], "factor": "2"})
        self.assertEqual(status, 400)

    def test_client_lifecycle(self):
        status, data = self.c.post("/api/clients/set", {"items": [{"email": "user1", "inbound_id": 1}], "factor": "1.5"})
        self.assertEqual((status, data["ok"], data["failed"]), (200, 1, []))
        _, lst = self.c.get("/api/clients?state=factored")
        self.assertEqual([(i["email"], i["factor"], i["source"]) for i in lst["items"]], [("user1", "1.5", "single")])
        # update goes through an override and wins
        self.c.post("/api/clients/set", {"items": [{"email": "user1", "inbound_id": 1}], "factor": "3"})
        _, lst = self.c.get("/api/clients?q=user1")
        self.assertEqual((lst["items"][0]["factor"], lst["items"][0]["source"], lst["items"][0]["base_factor"]), ("3", "override", "1.5"))
        # remove drops both the override and the rule
        status, data = self.c.post("/api/clients/remove", {"items": [{"email": "user1", "inbound_id": 1}]})
        self.assertEqual(data["ok"], 1)
        _, lst = self.c.get("/api/clients?state=factored")
        self.assertEqual(lst["total"], 0)
        # removing again reports a per-item failure instead of crashing
        status, data = self.c.post("/api/clients/remove", {"items": [{"email": "user1", "inbound_id": 1}]})
        self.assertEqual((status, data["ok"], len(data["failed"])), (200, 0, 1))

    def test_unknown_client_and_inbound(self):
        _, data = self.c.post("/api/clients/set", {"items": [{"email": "ghost"}], "factor": "2"})
        self.assertEqual(data["ok"], 0)
        self.assertIn("پیدا نشد", data["failed"][0]["error"])
        _, data = self.c.post("/api/inbounds/set", {"ids": [99], "factor": "2"})
        self.assertEqual(data["ok"], 0)

    def test_inbound_scope_and_client_override(self):
        _, data = self.c.post("/api/inbounds/set", {"ids": [2], "factor": "2.5"})
        self.assertEqual(data["ok"], 1)
        _, ib = self.c.get("/api/inbounds")
        by_id = {i["id"]: i for i in ib["items"]}
        self.assertEqual((by_id[2]["factor"], by_id[2]["state"], by_id[2]["clients"]), ("2.5", "active", 4))
        self.assertIsNone(by_id[1]["factor"])
        # client inside the scope gets its own factor through an override
        _, data = self.c.post("/api/clients/set", {"items": [{"email": "user5", "inbound_id": 2}], "factor": "4"})
        self.assertEqual(data["ok"], 1, data)
        _, lst = self.c.get("/api/clients?inbound_id=2")
        eff = {i["email"]: (i["factor"], i["source"]) for i in lst["items"]}
        self.assertEqual(eff["user5"], ("4", "override"))
        self.assertEqual(eff["user6"], ("2.5", "scope"))
        # removing the client factor falls back to the inbound factor
        self.c.post("/api/clients/remove", {"items": [{"email": "user5"}]})
        _, lst = self.c.get("/api/clients?q=user5")
        self.assertEqual((lst["items"][0]["factor"], lst["items"][0]["source"]), ("2.5", "scope"))
        # changing the inbound factor updates every member
        self.c.post("/api/inbounds/set", {"ids": [2], "factor": "3"})
        _, lst = self.c.get("/api/clients?inbound_id=2&state=scope")
        self.assertTrue(all(i["factor"] == "3" for i in lst["items"]) and lst["total"] == 4)

    def test_remove_inbound_keeps_client_rules(self):
        self.c.post("/api/clients/set", {"items": [{"email": "user2", "inbound_id": 1}], "factor": "7"})
        self.c.post("/api/inbounds/set", {"ids": [2], "factor": "2"})
        _, data = self.c.post("/api/inbounds/remove", {"ids": [2]})
        self.assertEqual(data["ok"], 1)
        _, lst = self.c.get("/api/clients?state=factored")
        self.assertEqual({i["email"]: i["factor"] for i in lst["items"]}, {"user2": "7"})
        # now remove inbound 1's scope-less state: the client rule must survive a scope removal on its inbound
        self.c.post("/api/inbounds/set", {"ids": [1], "factor": "2"})
        self.c.post("/api/inbounds/remove", {"ids": [1]})
        _, lst = self.c.get("/api/clients?state=factored")
        self.assertEqual({i["email"]: i["factor"] for i in lst["items"]}, {"user2": "7"})

    def test_changing_inbound_factor_keeps_client_specific_factor(self):
        self.c.post("/api/clients/set", {"items": [{"email": "user5", "inbound_id": 2}], "factor": "7"})
        self.c.post("/api/inbounds/set", {"ids": [2], "factor": "2"})
        _, data = self.c.post("/api/inbounds/set", {"ids": [2], "factor": "3"})
        self.assertEqual((data["ok"], data["failed"]), (1, []))
        _, lst = self.c.get("/api/clients?inbound_id=2")
        eff = {i["email"]: (i["factor"], i["source"]) for i in lst["items"]}
        self.assertEqual(eff["user5"][0], "7")
        self.assertEqual(eff["user4"], ("3", "scope"))
        _, ov = self.c.get("/api/overview")
        self.assertEqual(ov["inbound_scopes_active"], 1)  # never two stacked scopes
        # removing a factor that does not exist is reported, not fatal
        _, data = self.c.post("/api/inbounds/remove", {"ids": [3]})
        self.assertEqual((data["ok"], len(data["failed"])), (0, 1))

    def test_pagination_and_search(self):
        _, page = self.c.get("/api/clients?size=10&page=1")
        self.assertEqual((page["total"], page["pages"], len(page["items"])), (7, 1, 7))
        # 25 is the fallback for sizes that are not offered
        _, page = self.c.get("/api/clients?size=3")
        self.assertEqual(page["size"], 25)
        _, page = self.c.get("/api/clients?q=user%25")  # literal percent must not act as wildcard
        self.assertEqual(page["total"], 0)
        _, page = self.c.get("/api/inbounds?q=trojan_")
        self.assertEqual(page["total"], 1)

    def test_bulk_set_and_remove(self):
        items = [{"email": "user%d" % i, "inbound_id": 1 if i <= 3 else 2} for i in range(1, 8)]
        _, data = self.c.post("/api/clients/set", {"items": items, "factor": "1.2"})
        self.assertEqual((data["ok"], data["failed"]), (7, []))
        _, ov = self.c.get("/api/overview")
        self.assertEqual((ov["client_rules_active"], ov["factored_clients"]), (7, 7))
        _, data = self.c.post("/api/clients/remove", {"items": items[:4]})
        self.assertEqual(data["ok"], 4)
        _, ov = self.c.get("/api/overview")
        self.assertEqual(ov["client_rules_active"], 3)

    def test_clear_all(self):
        self.c.post("/api/inbounds/set", {"ids": [1], "factor": "2"})
        self.c.post("/api/clients/set", {"items": [{"email": "user1"}], "factor": "3"})
        self.c.post("/api/clients/set", {"items": [{"email": "user4"}], "factor": "3"})
        status, data = self.c.post("/api/actions/clear-all")
        self.assertEqual(status, 200)
        _, ov = self.c.get("/api/overview")
        self.assertEqual((ov["inbound_scopes_active"], ov["client_rules_active"], ov["overrides_active"], ov["factored_clients"]), (0, 0, 0, 0))
        # clearing when nothing exists is not an error
        self.assertEqual(self.c.post("/api/actions/clear-all")[0], 200)

    def test_disable_enable_cycle(self):
        self.c.post("/api/clients/set", {"items": [{"email": "user1"}], "factor": "2"})
        self.assertEqual(self.c.post("/api/actions/disable")[0], 200)
        _, ov = self.c.get("/api/overview")
        self.assertEqual((ov["client_rules_active"], ov["client_rules_paused"]), (0, 1))
        _, lst = self.c.get("/api/clients?state=factored")
        self.assertEqual(lst["items"][0]["state"], "paused")
        self.assertEqual(self.c.post("/api/actions/enable")[0], 200)
        _, ov = self.c.get("/api/overview")
        self.assertEqual((ov["client_rules_active"], ov["client_rules_paused"]), (1, 0))

    def test_traffic_is_really_multiplied(self):
        self.c.post("/api/clients/set", {"items": [{"email": "user1"}], "factor": "2"})
        db = sqlite3.connect(DB)
        db.execute("update client_traffics set up = up + 1000, down = down + 3000 where email = 'user1'")
        db.commit()
        db.close()
        self.mgr.cli_strict("tick")
        db = sqlite3.connect(DB)
        up, down = db.execute("select up, down from client_traffics where email='user1'").fetchone()
        db.close()
        self.assertEqual((up, down), (3000, 8000))  # 1000+1000*2, 2000+... new traffic counted twice
        _, ov = self.c.get("/api/overview")
        self.assertEqual((ov["applications"], ov["extra_bytes"]), (1, 4000))
        _, ev = self.c.get("/api/events?kind=traffic_applied")
        self.assertEqual((ev["total"], ev["items"][0]["email"], ev["items"][0]["extra_bytes"]), (1, "user1", 4000))

    def test_events_filters(self):
        self.c.post("/api/clients/set", {"items": [{"email": "user1"}], "factor": "2"})
        _, ev = self.c.get("/api/events?kind=all&size=10")
        self.assertGreaterEqual(ev["total"], 1)
        self.assertIn("rule_enabled", ev["types"])
        _, ev = self.c.get("/api/events?kind=rule_disabled")
        self.assertEqual(ev["total"], 0)

    def test_purge_drops_tables(self):
        self.c.post("/api/clients/set", {"items": [{"email": "user1"}], "factor": "2"})
        status, data = self.c.post("/api/actions/purge", {"confirm": "no"})
        self.assertEqual(status, 400)
        status, data = self.c.post("/api/actions/purge", {"confirm": "DELETE"})
        self.assertEqual(status, 200, data)
        self.assertIn("xui_factor_rules", data["dropped"])
        db = sqlite3.connect(DB)
        left = [r[0] for r in db.execute("select name from sqlite_master where name like 'xui_factor%'")]
        # 3x-ui's own tables must be untouched
        panel_tables = {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
        db.close()
        self.assertEqual(left, [])
        self.assertTrue({"inbounds", "client_traffics", "clients"} <= panel_tables)
        _, ov = self.c.get("/api/overview")
        self.assertFalse(ov["tables"])
        self.assertEqual(self.c.get("/api/clients")[1]["total"], 7)
        # the feature can be brought back afterwards
        self.assertEqual(self.c.post("/api/clients/set", {"items": [{"email": "user2"}], "factor": "2"})[0], 200)

    def test_account_change(self):
        status, data = self.c.post("/api/account", {"current_password": "wrong", "password": "new-password-9"})
        self.assertEqual(status, 403)
        status, data = self.c.post("/api/account", {"current_password": "secret-pass-1", "password": "short"})
        self.assertEqual(status, 400)
        status, data = self.c.post("/api/account", {"current_password": "secret-pass-1", "username": "boss", "password": "new-password-9"})
        self.assertEqual(status, 200)
        self.assertEqual(self.c.get("/api/overview")[0], 401)  # old session invalidated
        fresh = Client(self.port)
        self.assertEqual(fresh.post("/api/login", {"username": "boss", "password": "new-password-9"})[0], 200)
        # restore for the other tests
        settings.save_panel_config(settings.ensure_secret({**settings.DEFAULTS, "username": "admin",
                                                           "password_hash": auth.hash_password("secret-pass-1")}))

    def test_doctor_and_cleanup(self):
        _, data = self.c.post("/api/actions/doctor")
        self.assertIn("Doctor", data["output"])
        status, data = self.c.post("/api/actions/cleanup", {"dry_run": True})
        self.assertEqual(status, 200)


def rebuild_db_with_different_ids():
    """Same people, but a new server: client ids are shuffled and one client is gone."""
    os.remove(DB)
    build_db()
    db = sqlite3.connect(DB)
    emails = [r[0] for r in db.execute("select email from client_traffics order by id")]
    db.execute("delete from client_traffics")
    db.execute("delete from sqlite_sequence where name='client_traffics'")
    for email in reversed(emails):
        if email == "user6":
            continue  # missing on the new server
        db.execute("insert into client_traffics(inbound_id,enable,email,up,down,total) values(?,?,?,?,?,0)",
                   (1 if email in ("user1", "user2", "user3") else 2, 1, email, 50000, 90000))
    db.commit()
    db.close()


class BackupTest(PanelTest):
    def seed(self):
        self.c.post("/api/inbounds/set", {"ids": [2], "factor": "2"})
        self.c.post("/api/clients/set", {"items": [{"email": "user1", "inbound_id": 1}], "factor": "3"})
        self.c.post("/api/clients/set", {"items": [{"email": "user5", "inbound_id": 2}], "factor": "5"})

    def effective(self):
        _, lst = self.c.get("/api/clients?size=100")
        return {i["email"]: (i["factor"], i["source"]) for i in lst["items"]}

    def test_create_list_download_delete(self):
        self.seed()
        status, made = self.c.post("/api/backups/create", {})
        self.assertEqual(status, 200, made)
        self.assertTrue(made["path"].startswith(os.environ["XFP_BACKUP_DIR"]))
        self.assertEqual(oct(os.stat(made["path"]).st_mode & 0o777), "0o600")
        _, listing = self.c.get("/api/backups")
        self.assertEqual(listing["factor_dir"], os.environ["XFP_BACKUP_DIR"])
        self.assertIn(made["name"], [i["name"] for i in listing["items"]])
        status, blob, resp = self.c.call("GET", "/api/backups/download?kind=factor&name=" + made["name"])
        self.assertEqual(status, 200)
        self.assertIn("attachment", resp.getheader("Content-Disposition"))
        self.assertTrue(gzip.decompress(blob).startswith(b"SQLite format 3"))
        for bad in ("../../etc/passwd", "..%2f..%2fetc%2fpasswd", "nope.db.gz", "a/b.db"):
            self.assertIn(self.c.call("GET", "/api/backups/download?kind=factor&name=" + bad)[0], (400, 404))
        self.assertEqual(self.c.post("/api/backups/delete", {"name": "../x.db"})[0], 400)
        self.assertEqual(self.c.post("/api/backups/delete", {"name": made["name"]})[0], 200)
        self.assertEqual(self.c.call("GET", "/api/backups/download?kind=factor&name=" + made["name"])[0], 404)

    def test_backup_requires_login(self):
        anon = Client(self.port)
        self.assertEqual(anon.get("/api/backups")[0], 401)
        self.assertEqual(anon.call("GET", "/api/backups/download?name=x.db.gz")[0], 401)
        self.assertEqual(anon.call("POST", "/api/backups/upload", raw=b"abc")[0], 401)

    def test_restore_same_server_after_clear(self):
        self.seed()
        before = self.effective()
        _, made = self.c.post("/api/backups/create", {})
        self.c.post("/api/actions/clear-all")
        self.assertEqual([k for k, v in self.effective().items() if v[1] != "none"], [])
        status, res = self.c.post("/api/backups/restore", {"name": made["name"]})
        self.assertEqual(status, 200, res)
        self.assertEqual(res["skipped_missing_clients"], 0)
        self.assertEqual(self.effective(), before)
        _, ov = self.c.get("/api/overview")
        self.assertEqual(ov["inbound_scopes_active"], 1)

    def test_restore_on_new_server_remaps_ids_and_rebases(self):
        self.seed()
        _, made = self.c.post("/api/backups/create", {})
        status, blob, _ = self.c.call("GET", "/api/backups/download?kind=factor&name=" + made["name"])
        rebuild_db_with_different_ids()  # a brand new server: no factor tables at all
        _, ov = self.c.get("/api/overview")
        self.assertFalse(ov["tables"])
        status, up = self.c.call("POST", "/api/backups/upload", raw=blob)[:2]
        self.assertEqual(status, 200, up)
        status, res = self.c.post("/api/backups/restore", {"name": up["name"]})
        self.assertEqual(status, 200, res)
        self.assertGreaterEqual(res["skipped_missing_clients"], 1)  # user6 does not exist there
        self.assertTrue(any("نادیده" in w for w in res["warnings"]))
        eff = self.effective()
        self.assertEqual(eff["user1"], ("3", "single"))
        self.assertEqual(eff["user5"], ("5", "override"))
        self.assertEqual(eff["user4"], ("2", "scope"))
        db = sqlite3.connect(DB)
        mismatched = db.execute("select count(*) from xui_factor_rule_clients rc join client_traffics c on c.id = rc.traffic_id "
                                "where c.email != rc.email or rc.last_up != c.up or rc.last_down != c.down").fetchone()[0]
        db.close()
        self.assertEqual(mismatched, 0)  # ids remapped by e-mail, baselines reset to current counters
        # after the move the factor really applies to NEW traffic only
        db = sqlite3.connect(DB)
        db.execute("update client_traffics set up = up + 100, down = down + 100 where email = 'user1'")
        db.commit()
        db.close()
        self.mgr.cli_strict("tick")
        db = sqlite3.connect(DB)
        up, = db.execute("select up from client_traffics where email = 'user1'").fetchone()
        db.close()
        self.assertEqual(up, 50000 + 100 * 3)

    def test_upload_rejects_garbage(self):
        for blob in (b"not a backup at all", gzip.compress(b"hello"), b""):
            status, data = self.c.call("POST", "/api/backups/upload", raw=blob)[:2]
            self.assertEqual(status, 400, data)
        listing = self.c.get("/api/backups")[1]
        self.assertEqual([i for i in listing["items"] if i["name"].startswith("uploaded")], [])
        leftovers = [f for f in os.listdir(os.environ["XFP_BACKUP_DIR"]) if f.startswith(".")]
        self.assertEqual(leftovers, [])

    def test_history_option_and_empty_state(self):
        status, data = self.c.post("/api/backups/create", {})
        self.assertEqual(status, 404)  # no factor tables yet
        self.c.post("/api/clients/set", {"items": [{"email": "user1"}], "factor": "2"})
        db = sqlite3.connect(DB)
        db.execute("update client_traffics set up = up + 10 where email='user1'")
        db.commit()
        db.close()
        self.mgr.cli_strict("tick")
        small = self.c.post("/api/backups/create", {"history": False})[1]
        big = self.c.post("/api/backups/create", {"history": True})[1]
        self.assertEqual(small["history"], False)
        self.assertGreater(big["tables"]["xui_factor_events"], small["tables"]["xui_factor_events"])

    def test_purge_leaves_a_safety_backup(self):
        self.seed()
        _, res = self.c.post("/api/actions/purge", {"confirm": "DELETE"})
        self.assertTrue(res["backup"].endswith("-pre-purge.db.gz"))
        self.assertTrue(os.path.isfile(res["backup"]))
        status, data = self.c.post("/api/backups/restore", {"name": os.path.basename(res["backup"])})
        self.assertEqual(status, 200, data)
        self.assertEqual(self.effective()["user1"], ("3", "single"))

    def test_uninstall_endpoint(self):
        self.assertEqual(self.c.post("/api/actions/uninstall", {"mode": "web"})[0], 400)
        calls = []
        original = core.Manager.schedule_uninstall
        core.Manager.schedule_uninstall = lambda self, mode: calls.append(mode) or {"mode": mode, "scheduled": True}
        try:
            self.assertEqual(self.c.post("/api/actions/uninstall", {"mode": "all", "confirm": "DELETE"})[0], 200)
        finally:
            core.Manager.schedule_uninstall = original
        self.assertEqual(calls, ["all"])
        with self.assertRaises(core.FactorError):
            core.Manager().schedule_uninstall("bogus")

    def test_cli_backup_restore(self):
        from xfpanel import cli
        self.seed()
        self.assertEqual(cli.run(["backup"]), 0)
        self.c.post("/api/actions/clear-all")
        self.assertEqual(cli.run(["restore", "latest", "--yes"]), 0)
        self.assertEqual(self.effective()["user1"], ("3", "single"))


class OverlapTest(PanelTest):
    """A client attached to two inbounds must not get two scopes (the engine skips it forever and floods the audit log)."""

    def share_clients(self):
        db = sqlite3.connect(DB)
        db.execute("insert into client_inbounds values (1, 2, '', 0)")  # user1 is also on inbound 2
        db.commit()
        db.close()

    def test_second_scope_over_shared_clients_is_refused(self):
        self.share_clients()
        self.assertEqual(self.c.post("/api/inbounds/set", {"ids": [1], "factor": "1.1"})[1]["ok"], 1)
        status, data = self.c.post("/api/inbounds/set", {"ids": [2], "factor": "1.1"})
        self.assertEqual((status, data["ok"], len(data["failed"])), (200, 0, 1))
        self.assertIn("مشترک", data["failed"][0]["error"])
        _, en = self.c.call("POST", "/api/inbounds/set", {"ids": [2], "factor": "1.1"}, headers={"X-Lang": "en"})[:2]
        self.assertIn("shares clients with the factor of inbound 1", en["failed"][0]["error"])
        _, ov = self.c.get("/api/overview")
        self.assertEqual(ov["inbound_scopes_active"], 1)
        # no flood: the engine never saw a second scope
        db = sqlite3.connect(DB)
        skipped = db.execute("select count(*) from xui_factor_events where event_type='scope_auto_enroll_skipped'").fetchone()[0]
        db.close()
        self.assertEqual(skipped, 0)
        # editing the existing scope is still fine
        self.assertEqual(self.c.post("/api/inbounds/set", {"ids": [1], "factor": "1.3"})[1]["ok"], 1)

    def test_unrelated_inbounds_are_allowed_and_nothing_flagged(self):
        self.assertEqual(self.c.post("/api/inbounds/set", {"ids": [1, 2], "factor": "1.5"})[1]["ok"], 2)
        _, ib = self.c.get("/api/inbounds")
        rows = {i["id"]: i for i in ib["items"]}
        self.assertEqual((rows[1]["scope_clients"], rows[2]["scope_clients"]), (3, 4))

    def test_dashboard_ignores_deleted_clients(self):
        self.c.post("/api/inbounds/set", {"ids": [1], "factor": "1.5"})
        self.assertEqual(self.c.get("/api/overview")[1]["factored_clients"], 3)
        db = sqlite3.connect(DB)
        db.execute("delete from client_traffics where email='user1'")
        db.commit()
        db.close()
        self.mgr.reader.invalidate()
        self.assertEqual(self.c.get("/api/overview")[1]["factored_clients"], 2)

    def test_prune_repeated_events(self):
        self.c.post("/api/clients/set", {"items": [{"email": "user1"}], "factor": "2"})
        db = sqlite3.connect(DB)
        db.executemany("insert into xui_factor_events(rule_id,event_type,message,created_at) values (1,'scope_auto_enroll_skipped','x',1)", [()] * 45000)
        db.commit()
        keep = db.execute("select count(*) from xui_factor_events where event_type!='scope_auto_enroll_skipped'").fetchone()[0]
        db.close()
        status, res = self.c.post("/api/actions/prune-events")
        self.assertEqual((status, res["deleted"]), (200, 45000))
        db = sqlite3.connect(DB)
        left = db.execute("select count(*) from xui_factor_events").fetchone()[0]
        db.close()
        self.assertEqual(left, keep)  # real events untouched
        self.assertEqual(self.c.post("/api/actions/prune-events")[1]["deleted"], 0)

    def test_prune_refuses_other_types(self):
        with self.assertRaises(core.FactorError):
            self.mgr.prune_events("traffic_applied")


class UpdateAndLanguageTest(PanelTest):
    def setUp(self):
        super().setUp()
        from xfpanel import update
        self.update = update
        self.update.CHECKER = update.Checker()  # fresh cache for every test
        server.update.CHECKER = self.update.CHECKER
        os.environ["XFP_REPO"] = "acme/panel"
        self._fetch = update.fetch_json

    def tearDown(self):
        self.update.fetch_json = self._fetch
        os.environ.pop("XFP_REPO", None)

    def test_version_ordering(self):
        u = self.update
        self.assertTrue(u.is_newer("v1.10.0", "1.9.9"))
        self.assertTrue(u.is_newer("1.2.0", "1.2.0-beta"))      # a release beats its pre-release
        self.assertFalse(u.is_newer("1.2.0-beta", "1.2.0"))
        self.assertFalse(u.is_newer("1.1.0", "1.1.0"))
        self.assertFalse(u.is_newer("garbage", "1.0.0"))
        for bad in ("", "v1", "1.2", "1.2.3; rm -rf /", "../1.2.3", "v1.2.3 "):
            self.assertFalse(u.valid_tag(bad), bad)

    def test_check_reports_newer_release(self):
        self.update.fetch_json = lambda url: {"tag_name": "v9.0.0", "html_url": "https://github.com/acme/panel/releases/tag/v9.0.0", "body": "notes"}
        _, info = self.c.get("/api/update/check")
        self.assertEqual((info["newer"], info["tag"], info["latest"], info["error"]), (True, "v9.0.0", "9.0.0", ""))
        self.assertEqual(info["current"], __import__("xfpanel").__version__)

    def test_check_up_to_date_and_cache(self):
        calls = []
        def fake(url):
            calls.append(url)
            return {"tag_name": "v%s" % __import__("xfpanel").__version__, "html_url": "", "body": ""}
        self.update.fetch_json = fake
        self.assertFalse(self.c.get("/api/update/check")[1]["newer"])
        self.c.get("/api/update/check")
        self.assertEqual(len(calls), 1)  # second call came from the cache: no extra network use

    def test_tags_fallback_when_no_release(self):
        def fake(url):
            if url.endswith("/releases/latest"):
                return None
            return [{"name": "v1.2.0"}, {"name": "v1.10.0"}, {"name": "nightly"}, {"name": "v1.9.9"}]
        self.update.fetch_json = fake
        _, info = self.c.get("/api/update/check")
        self.assertEqual(info["tag"], "v1.10.0")

    def test_not_configured_and_network_errors_are_reported_not_raised(self):
        os.environ["XFP_REPO"] = "OWNER/REPO"
        self.update.CHECKER = server.update.CHECKER = self.update.Checker()
        _, info = self.c.get("/api/update/check")
        self.assertFalse(info["configured"])
        self.assertTrue(info["error"])
        os.environ["XFP_REPO"] = "acme/panel"
        def boom(url):
            raise core.FactorError("Could not reach GitHub")
        self.update.fetch_json = boom
        self.update.CHECKER = server.update.CHECKER = self.update.Checker()
        _, info = self.c.get("/api/update/check")
        self.assertEqual((info["newer"], bool(info["error"])), (False, True))

    def test_apply_validates_and_spawns(self):
        self.assertEqual(self.c.post("/api/update/apply", {"tag": "v9.0.0"})[0], 400)  # no confirmation
        self.assertEqual(self.c.post("/api/update/apply", {"confirm": True, "tag": "v1; reboot"})[0], 400)
        spawned = []
        original = core.Manager._spawn_manager
        core.Manager._spawn_manager = lambda self, args, unit: spawned.append((args, unit))
        try:
            status, data = self.c.post("/api/update/apply", {"confirm": True, "tag": "v9.0.0"})
        finally:
            core.Manager._spawn_manager = original
        self.assertEqual((status, data["scheduled"]), (200, True))
        self.assertEqual(spawned[0][0], ["update", "--yes", "--tag", "v9.0.0", "--delay", "3"])

    def test_update_endpoints_need_login(self):
        anon = Client(self.port)
        self.assertEqual(anon.get("/api/update/check")[0], 401)
        self.assertEqual(anon.post("/api/update/apply", {"confirm": True, "tag": "v9.0.0"})[0], 401)

    def test_server_messages_follow_x_lang(self):
        _, fa = self.c.call("POST", "/api/clients/set", {"items": [], "factor": "2"})[:2]
        _, en = self.c.call("POST", "/api/clients/set", {"items": [], "factor": "2"}, headers={"X-Lang": "en"})[:2]
        self.assertIn("هیچ", fa["error"])
        self.assertEqual(en["error"], "No items were selected.")
        _, bad = self.c.call("POST", "/api/clients/set", {"items": [{"email": "ghost"}], "factor": "2"}, headers={"X-Lang": "en"})[:2]
        self.assertIn("No client with e-mail", bad["failed"][0]["error"])
        status, bad = self.c.call("POST", "/api/clients/set", {"items": [{"email": "user1"}], "factor": "0.5"}, headers={"X-Lang": "en"})[:2]
        self.assertEqual(status, 400)
        self.assertEqual(bad["error"], "The factor must be greater than or equal to 1.")

    def test_cli_defaults_to_english(self):
        from xfpanel import i18n
        i18n.set_lang(None)
        self.assertEqual(i18n.tr("هیچ موردی انتخاب نشده است."), "No items were selected.")
        i18n.set_lang("fa")
        self.assertEqual(i18n.tr("هیچ موردی انتخاب نشده است."), "هیچ موردی انتخاب نشده است.")
        i18n.set_lang(None)

    def test_every_translation_key_exists(self):
        """Each tr('…') in the code needs an English entry, and the table has no dead keys."""
        import ast
        from xfpanel.messages_en import EN
        base = os.path.join(os.path.dirname(HERE), "xfpanel")
        used = set()
        for name in os.listdir(base):
            if not name.endswith(".py") or name in ("messages_en.py", "i18n.py"):
                continue
            with open(os.path.join(base, name), encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "tr" and node.args \
                        and isinstance(node.args[0], ast.Constant):
                    used.add(node.args[0].value)
                if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "_ERROR_HINTS":
                    used.update(t.elts[1].value for t in node.value.elts)
        self.assertEqual(sorted(used - set(EN)), [])
        self.assertEqual(sorted(set(EN) - used), [])
        self.assertTrue(all(v.strip() and not any("\u0600" <= ch <= "\u06ff" for ch in v) for v in EN.values()))


class UnitTests(unittest.TestCase):
    def test_parse_factor(self):
        self.assertEqual(core.parse_factor("1,5"), "1,5".replace(",", "."))
        self.assertEqual(core.parse_factor(2), "2")
        with self.assertRaises(core.FactorError):
            core.parse_factor(True)

    def test_ppm(self):
        self.assertEqual(core.ppm_to_str(1500000), "1.5")
        self.assertEqual(core.ppm_to_str(2000000), "2")
        self.assertEqual(core.ppm_to_str(1000001), "1.000001")

    def test_session_expiry(self):
        token = auth.make_session("k", "u", 10, now=1000)
        self.assertEqual(auth.read_session("k", token, now=1005), "u")
        self.assertIsNone(auth.read_session("k", token, now=1011))
        self.assertIsNone(auth.read_session("k", token[:-1] + "0"))


if __name__ == "__main__":
    unittest.main()

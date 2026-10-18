"""HTTP(S) server: JSON API + static single page app. Standard library only."""
from __future__ import annotations

import json
import logging
import os
import signal
import socket
import ssl
import sys
import threading
import traceback
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

from . import __version__, auth, backup, core, settings, update
from . import i18n
from .i18n import tr

LOG = logging.getLogger("xfpanel")
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/theme.js": ("theme.js", "application/javascript; charset=utf-8"),
    "/i18n-en.js": ("i18n-en.js", "application/javascript; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
}
COOKIE_NAME = "xfp_session"
MAX_BODY = 1 << 20
MAX_BULK = 2000
MAX_CONNECTIONS = 24


class ConfigCache:
    """Re-reads panel.json whenever it changes so CLI credential edits apply live."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._mtime: Optional[float] = None
        self._cfg: Dict[str, Any] = {}
        self._lock = threading.Lock()

    def get(self) -> Dict[str, Any]:
        with self._lock:
            try:
                mtime = os.stat(self.path).st_mtime
            except OSError:
                mtime = None
            if mtime != self._mtime or not self._cfg:
                self._cfg = settings.load_panel_config(self.path)
                self._mtime = mtime
            return self._cfg


class Api:
    """Maps API routes to Manager/Reader calls. Kept free of HTTP details."""

    def __init__(self, manager: core.Manager, config: ConfigCache) -> None:
        self.m = manager
        self.r = manager.reader
        self.config = config
        self.routes: Dict[Tuple[str, str], Callable[[Dict[str, Any], Dict[str, Any]], Any]] = {
            ("GET", "/api/overview"): self.overview,
            ("GET", "/api/inbounds"): lambda q, b: self.r.list_inbounds(q.get("page"), q.get("size"), q.get("q", "")),
            ("POST", "/api/inbounds/set"): self.inbounds_set,
            ("POST", "/api/inbounds/remove"): self.inbounds_remove,
            ("GET", "/api/clients"): lambda q, b: self.r.list_clients(
                q.get("page"), q.get("size"), q.get("q", ""), q.get("inbound_id"), q.get("state", "all")),
            ("POST", "/api/clients/set"): self.clients_set,
            ("POST", "/api/clients/remove"): self.clients_remove,
            ("GET", "/api/events"): lambda q, b: self.r.list_events(q.get("page"), q.get("size"), q.get("kind", "lifecycle"), q.get("q", "")),
            ("POST", "/api/actions/clear-all"): lambda q, b: self.m.clear_all(),
            ("POST", "/api/actions/disable"): lambda q, b: self.m.disable_factor(),
            ("POST", "/api/actions/enable"): lambda q, b: self.m.enable_factor(),
            ("POST", "/api/actions/purge"): self.purge,
            ("POST", "/api/actions/prune-events"): lambda q, b: self.m.prune_events(),
            ("POST", "/api/actions/backup"): lambda q, b: {"output": self.m.backup()},
            ("POST", "/api/actions/cleanup"): lambda q, b: {"output": self.m.cleanup(bool(b.get("dry_run", True)))},
            ("POST", "/api/actions/doctor"): lambda q, b: {"output": self.m.doctor()},
            ("POST", "/api/account"): self.account,
            ("POST", "/api/actions/uninstall"): self.uninstall,
            ("GET", "/api/update/check"): lambda q, b: update.CHECKER.check(force=q.get("force") == "1"),
            ("POST", "/api/update/apply"): self.update_apply,
            ("GET", "/api/backups"): lambda q, b: backup.list_backups(),
            ("POST", "/api/backups/create"): lambda q, b: backup.create_backup(self.m, bool(b.get("history", False))),
            ("POST", "/api/backups/full"): lambda q, b: backup.create_full_backup(self.m),
            ("POST", "/api/backups/delete"): self.backup_delete,
            ("POST", "/api/backups/restore"): lambda q, b: backup.restore_backup(self.m, str(b.get("name", "")), bool(b.get("rebase", True))),
        }

    # -- handlers
    def overview(self, q: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
        data = self.r.overview()
        data["service"] = self.m.service_status()
        data["version"] = __version__
        data["backend"] = core.detect_backend()
        return data

    @staticmethod
    def _list(body: Dict[str, Any], key: str) -> list:
        items = body.get(key)
        if not isinstance(items, list) or not items:
            raise core.FactorError(tr("هیچ موردی انتخاب نشده است."))
        if len(items) > MAX_BULK:
            raise core.FactorError(tr("تعداد موارد انتخابی زیاد است (حداکثر {}).", MAX_BULK))
        return items

    def inbounds_set(self, q: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
        factor = core.parse_factor(body.get("factor"))
        return self.m.bulk(self._list(body, "ids"), lambda i: self.m.set_inbound(i, factor))

    def inbounds_remove(self, q: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
        return self.m.bulk(self._list(body, "ids"), self.m.remove_inbound)

    @staticmethod
    def _client_item(item: Any) -> Tuple[Any, Any]:
        if not isinstance(item, dict):
            raise core.FactorError(tr("ساختار آیتم کلاینت نامعتبر است."))
        return item.get("email"), item.get("inbound_id")

    def clients_set(self, q: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
        factor = core.parse_factor(body.get("factor"))
        return self.m.bulk(self._list(body, "items"), lambda it: self.m.set_client(*self._client_item(it), factor))

    def clients_remove(self, q: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
        return self.m.bulk(self._list(body, "items"), lambda it: self.m.remove_client(*self._client_item(it)))

    def purge(self, q: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
        if body.get("confirm") != "DELETE":
            raise core.FactorError(tr("برای تأیید حذف، عبارت DELETE را ارسال کنید."))
        return self.m.purge(bool(body.get("remove_service", True)))

    def uninstall(self, q: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
        if body.get("confirm") != "DELETE":
            raise core.FactorError(tr("برای تأیید حذف، عبارت DELETE را ارسال کنید."))
        return self.m.schedule_uninstall(str(body.get("mode", "")))

    def update_apply(self, q: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
        if body.get("confirm") is not True:
            raise core.FactorError(tr("بروزرسانی بدون تأیید انجام نمی‌شود."))
        return self.m.schedule_update(str(body.get("tag", "")))

    @staticmethod
    def backup_delete(q: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
        backup.delete_backup(str(body.get("name", "")), str(body.get("kind", "factor")))
        return {"ok": True}

    def account(self, q: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
        cfg = settings.load_panel_config(self.config.path)
        if not auth.verify_password(str(body.get("current_password", "")), cfg.get("password_hash", "")):
            raise core.FactorError(tr("رمز عبور فعلی اشتباه است."), 403)
        username = str(body.get("username") or cfg["username"]).strip()
        password = str(body.get("password") or "")
        validate_credentials(username, password or None)
        cfg["username"] = username
        if password:
            cfg["password_hash"] = auth.hash_password(password)
        # Rotating the secret invalidates every existing session.
        cfg["secret"] = ""
        settings.save_panel_config(settings.ensure_secret(cfg), self.config.path)
        return {"username": username, "relogin": True}


def validate_credentials(username: str, password: Optional[str]) -> None:
    if not (3 <= len(username) <= 64) or any(ch.isspace() for ch in username):
        raise core.FactorError(tr("نام کاربری باید ۳ تا ۶۴ نویسه و بدون فاصله باشد."))
    if password is not None and len(password) < 8:
        raise core.FactorError(tr("رمز عبور باید حداقل ۸ نویسه باشد."))


class Handler(BaseHTTPRequestHandler):
    server_version = "XuiFactorPanel"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 30

    server: "PanelServer"  # type: ignore[assignment]

    # -- TLS handshake happens here, in the per-connection thread
    def setup(self) -> None:
        if self.server.tls:
            self.request.settimeout(10)
            self.request.do_handshake()
        super().setup()

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: D401 - never logs query strings
        LOG.info("%s %s", self.address_string(), fmt % args if args else fmt)

    def log_request(self, code: Any = "-", size: Any = "-") -> None:
        # Routine successful GETs (dashboard polling) are not logged: less journald I/O.
        if self.command == "GET" and str(code)[:1] in ("2", "3"):
            return
        self.log_message('"%s %s" %s', self.command, urlsplit(self.path).path, code)

    # -- response helpers
    def _send(self, status: int, body: bytes, ctype: str, extra: Optional[Dict[str, str]] = None, api: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                         "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.send_header("Cache-Control", "no-store" if api else "no-cache")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: Any, extra: Optional[Dict[str, str]] = None) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", extra, api=True)

    def _cookie_header(self, value: str, max_age: int) -> str:
        parts = ["{}={}".format(COOKIE_NAME, value), "Path=/", "HttpOnly", "SameSite=Strict", "Max-Age={}".format(max_age)]
        if self.server.tls:
            parts.append("Secure")
        return "; ".join(parts)

    def _session_user(self) -> Optional[str]:
        raw = self.headers.get("Cookie", "")
        if not raw:
            return None
        jar = SimpleCookie()
        try:
            jar.load(raw)
        except Exception:
            return None
        morsel = jar.get(COOKIE_NAME)
        cfg = self.server.config.get()
        if morsel is None or not cfg.get("secret"):
            return None
        user = auth.read_session(cfg["secret"], morsel.value)
        return user if user is not None and user == cfg.get("username") else None

    def _read_body(self) -> Dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise core.FactorError(tr("Content-Length نامعتبر است."))
        if length > MAX_BODY:
            raise core.FactorError(tr("درخواست بیش از حد بزرگ است."), 413)
        if length == 0:
            return {}
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise core.FactorError(tr("بدنه‌ی درخواست JSON معتبر نیست."))
        if not isinstance(data, dict):
            raise core.FactorError(tr("بدنه‌ی درخواست باید یک شیء JSON باشد."))
        return data

    def _csrf_ok(self) -> bool:
        if self.headers.get("X-Requested-With") != "XFPanel":
            return False
        origin = self.headers.get("Origin")
        if origin:
            host = self.headers.get("Host", "")
            if urlsplit(origin).netloc != host:
                return False
        return True

    # -- verbs
    def do_HEAD(self) -> None:
        self.do_GET()

    def _pick_language(self) -> None:
        """The browser sends its UI language, so server messages match it (Persian by default)."""
        i18n.set_lang(self.headers.get("X-Lang") or "fa")

    def do_GET(self) -> None:
        self._pick_language()
        url = urlsplit(self.path)
        if url.path in STATIC:
            name, ctype = STATIC[url.path]
            try:
                with open(os.path.join(WEB_DIR, name), "rb") as fh:
                    self._send(200, fh.read(), ctype)
            except OSError:
                self._json(404, {"error": "not found"})
            return
        if url.path == "/api/backups/download":
            self._download({k: v[0] for k, v in parse_qs(url.query).items()})
            return
        if url.path.startswith("/api/"):
            query = {k: v[0] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
            self._dispatch("GET", url.path, query, {})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        self._pick_language()
        url = urlsplit(self.path)
        if not url.path.startswith("/api/"):
            self._json(404, {"error": "not found"})
            return
        if not self._csrf_ok():
            self._json(403, {"error": tr("درخواست نامعتبر (CSRF).")})
            return
        if url.path == "/api/backups/upload":
            self._upload()
            return
        try:
            body = self._read_body()
        except core.FactorError as exc:
            self._json(exc.status, {"error": exc.message})
            return
        if url.path == "/api/login":
            self._login(body)
        elif url.path == "/api/logout":
            self._json(200, {"ok": True}, {"Set-Cookie": self._cookie_header("", 0)})
        else:
            self._dispatch("POST", url.path, {}, body)

    def _upload(self) -> None:
        if self._session_user() is None:
            self._json(401, {"error": tr("نشست شما منقضی شده است؛ دوباره وارد شوید.")})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            self._json(200, backup.save_upload(self.rfile, length))
        except core.FactorError as exc:
            self.close_connection = True  # an unread body would corrupt keep-alive framing
            self._json(exc.status, {"error": exc.message})
        except (ValueError, OSError):
            self.close_connection = True
            self._json(400, {"error": tr("ارسال فایل ناموفق بود.")})

    def _download(self, query: Dict[str, Any]) -> None:
        if self._session_user() is None:
            self._json(401, {"error": tr("نشست شما منقضی شده است؛ دوباره وارد شوید.")})
            return
        try:
            path = backup._path(query.get("name", ""), query.get("kind", "factor"))
        except core.FactorError as exc:
            self._json(exc.status, {"error": exc.message})
            return
        name = os.path.basename(path)
        size = os.path.getsize(path)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", 'attachment; filename="{}"'.format(name))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            with open(path, "rb") as fh:  # streamed: constant memory whatever the size
                while True:
                    chunk = fh.read(1 << 16)
                    if not chunk:
                        break
                    self.wfile.write(chunk)

    def _login(self, body: Dict[str, Any]) -> None:
        ip = self.client_address[0]
        wait = self.server.throttle.retry_after(ip)
        if wait:
            self._json(429, {"error": tr("تلاش‌های ناموفق زیاد بود؛ {} ثانیه دیگر دوباره امتحان کنید.", wait)})
            return
        cfg = self.server.config.get()
        user_ok = auth.hmac.compare_digest(str(body.get("username", "")).encode(), str(cfg.get("username", "")).encode())
        pass_ok = auth.verify_password(str(body.get("password", "")), cfg.get("password_hash", ""))
        if not (user_ok and pass_ok and cfg.get("secret")):
            self.server.throttle.failure(ip)
            self._json(401, {"error": tr("نام کاربری یا رمز عبور اشتباه است.")})
            return
        self.server.throttle.success(ip)
        ttl = int(cfg.get("session_hours", 12)) * 3600
        token = auth.make_session(cfg["secret"], cfg["username"], ttl)
        self._json(200, {"ok": True, "user": cfg["username"]}, {"Set-Cookie": self._cookie_header(token, ttl)})

    def _dispatch(self, method: str, path: str, query: Dict[str, Any], body: Dict[str, Any]) -> None:
        if path == "/api/me":
            user = self._session_user()
            self._json(200 if user else 401, {"user": user} if user else {"error": "unauthorized"})
            return
        handler = self.server.api.routes.get((method, path))
        if handler is None:
            self._json(404, {"error": "not found"})
            return
        if self._session_user() is None:
            self._json(401, {"error": tr("نشست شما منقضی شده است؛ دوباره وارد شوید.")})
            return
        try:
            result = handler(query, body)
        except core.FactorError as exc:
            self._json(exc.status, {"error": exc.message})
            return
        except Exception:  # never leak internals to the browser
            LOG.error("unhandled error on %s %s\n%s", method, path, traceback.format_exc())
            self._json(500, {"error": tr("خطای داخلی سرور؛ جزئیات در لاگ سرویس ثبت شد.")})
            return
        self._json(200, result)


class PanelServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64

    def __init__(self, cfg: Dict[str, Any], config: ConfigCache, manager: Optional[core.Manager] = None) -> None:
        host = cfg.get("host") or "0.0.0.0"
        if ":" in host:
            self.address_family = socket.AF_INET6
        self.config = config
        self.throttle = auth.LoginThrottle()
        self.api = Api(manager or core.Manager(), config)
        self.tls = False
        self.ssl_context: Optional[ssl.SSLContext] = None
        self._slots = threading.BoundedSemaphore(MAX_CONNECTIONS)  # caps threads => caps RAM
        self._cert, self._key = cfg.get("cert") or "", cfg.get("key") or ""
        super().__init__((host, int(cfg.get("port") or settings.DEFAULT_PORT)), Handler)
        if self._cert and self._key:
            self.ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            self.ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
            self.ssl_context.load_cert_chain(self._cert, self._key)
            self.tls = True

    def reload_certificate(self) -> bool:
        if not self.ssl_context:
            return False
        try:
            self.ssl_context.load_cert_chain(self._cert, self._key)
            LOG.info("TLS certificate reloaded")
            return True
        except (ssl.SSLError, OSError) as exc:
            LOG.error("certificate reload failed: %s", exc)
            return False

    def process_request(self, request, client_address) -> None:  # type: ignore[no-untyped-def]
        if not self._slots.acquire(timeout=5):
            self.shutdown_request(request)  # overloaded: refuse instead of piling up threads
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address) -> None:  # type: ignore[no-untyped-def]
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def get_request(self):  # type: ignore[no-untyped-def]
        sock, addr = super().get_request()
        if self.ssl_context:
            sock = self.ssl_context.wrap_socket(sock, server_side=True, do_handshake_on_connect=False)
        return sock, addr

    def handle_error(self, request, client_address) -> None:  # type: ignore[no-untyped-def]
        exc = sys.exc_info()[1]
        if isinstance(exc, (ssl.SSLError, ConnectionError, socket.timeout, BrokenPipeError)):
            return  # scanners, aborted handshakes and idle keep-alives are routine
        LOG.error("request error from %s\n%s", client_address[0], traceback.format_exc())


def serve(config_path: str = "") -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    config = ConfigCache(config_path or settings.PANEL_CONFIG_PATH)
    cfg = config.get()
    if not cfg.get("password_hash") or not cfg.get("secret"):
        sys.exit("panel credentials are not configured; run: xfp credentials")
    server = PanelServer(cfg, config)
    signal.signal(signal.SIGHUP, lambda *_: server.reload_certificate())
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    LOG.info("XuiFactor panel %s listening on %s://%s:%s", __version__, "https" if server.tls else "http",
             server.server_address[0], server.server_address[1])
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

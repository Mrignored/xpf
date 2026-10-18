"""Domain logic: reads metadata straight from the database and performs every
mutation through the official ``xui-factor`` binary so the engine's own rules
(baselines, conflicts, audit trail) are always respected."""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

from . import settings
from .i18n import tr

PPM = 1_000_000
MAX_FACTOR_PPM = 10_000 * PPM
FACTOR_RE = re.compile(r"^\d{1,5}(\.\d{1,6})?$")
LIVE_STATES = ("active", "paused")
PAGE_SIZES = (10, 25, 50, 100, 200)
FACTOR_TABLES = (
    "xui_factor_events",
    "xui_factor_rule_clients",
    "xui_factor_scopes",
    "xui_factor_rules",
    "xui_factor_excludes",
    "xui_factor_overrides",
    "xui_factor_meta",
)

UNIT_TEMPLATE = """[Unit]
Description=XuiFactor 3x-ui traffic factor sidecar
After=network.target x-ui.service

[Service]
EnvironmentFile=-/etc/default/x-ui
EnvironmentFile=-/etc/conf.d/x-ui
EnvironmentFile=-/etc/sysconfig/x-ui
Type=simple
User=root
Group=root
ExecStart={bin} --config {config} run
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
"""

_ERROR_HINTS = (
    ("must be greater than or equal to 1", "ضریب باید بزرگ‌تر یا مساوی ۱ باشد."),
    ("invalid factor", "مقدار ضریب نامعتبر است (حداکثر ۶ رقم اعشار)."),
    ("already targets traffic id", "این کلاینت هم‌اکنون زیر پوشش یک ضریب دیگر است."),
    ("no matching record", "رکورد مورد نظر پیدا نشد."),
    ("not found", "مورد درخواستی پیدا نشد."),
    ("metadata tables are missing", "جداول فاکتور در دیتابیس وجود ندارد."),
    ("database is locked", "دیتابیس موقتاً قفل است؛ دوباره تلاش کنید."),
)


class FactorError(Exception):
    """An error that is safe to show to the operator."""

    def __init__(self, message: str, status: int = 400, detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.detail = detail


# ----------------------------------------------------------------- helpers
def ppm_to_str(ppm: Optional[int]) -> Optional[str]:
    if ppm is None:
        return None
    whole, frac = divmod(int(ppm), PPM)
    text = "{}.{:06d}".format(whole, frac).rstrip("0").rstrip(".")
    return text


def parse_factor(value: Any) -> str:
    """Validate a user supplied factor and return its canonical text form."""
    if isinstance(value, bool) or value is None:
        raise FactorError(tr("ضریب را وارد کنید."))
    text = str(value).strip().replace(",", ".")
    if not FACTOR_RE.match(text):
        raise FactorError(tr("ضریب نامعتبر است؛ فقط عدد (حداکثر ۶ رقم اعشار) مجاز است."))
    whole, _, frac = text.partition(".")
    ppm = int(whole) * PPM + int((frac + "000000")[:6])
    if ppm < PPM:
        raise FactorError(tr("ضریب باید بزرگ‌تر یا مساوی ۱ باشد."))
    if ppm > MAX_FACTOR_PPM:
        raise FactorError(tr("ضریب بیش از حد بزرگ است (حداکثر ۱۰۰۰۰)."))
    return text


def parse_id(value: Any, label: str = "") -> int:
    label = label or tr("شناسه")
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise FactorError(tr("{} نامعتبر است.", label))
    if number < 1:
        raise FactorError(tr("{} نامعتبر است.", label))
    return number


def parse_email(value: Any) -> str:
    email = str(value or "").strip()
    if not email or len(email) > 255 or any(ord(ch) < 32 for ch in email):
        raise FactorError(tr("ایمیل کلاینت نامعتبر است."))
    return email


def friendly_error(stderr: str, stdout: str = "") -> str:
    raw = (stderr or stdout or "").strip().splitlines()
    line = raw[0].strip() if raw else tr("خطای ناشناخته")
    if line.lower().startswith("error:"):
        line = line[6:].strip()
    lowered = line.lower()
    for needle, text in _ERROR_HINTS:
        if needle in lowered:
            return "{} ({})".format(tr(text), line)
    return line


def paginate(page: Any, size: Any) -> Tuple[int, int]:
    try:
        page_n = max(1, int(page))
    except (TypeError, ValueError):
        page_n = 1
    try:
        size_n = int(size)
    except (TypeError, ValueError):
        size_n = 25
    if size_n not in PAGE_SIZES:
        size_n = 25
    return page_n, size_n


def page_payload(items: List[Dict[str, Any]], total: int, page: int, size: int) -> Dict[str, Any]:
    pages = max(1, (total + size - 1) // size)
    return {"items": items, "total": total, "page": min(page, pages), "pages": pages, "size": size}


# --------------------------------------------------------------- database
def detect_backend() -> str:
    """Return ``sqlite`` or ``postgres`` using the same sources as xui-factor."""
    cfg = _factor_config()
    backend = str(cfg.get("database_backend", "auto")).lower()
    if backend in ("sqlite", "postgres", "postgresql"):
        return "postgres" if backend.startswith("postgres") else "sqlite"
    env_type = os.environ.get("XUI_DB_TYPE", "").lower()
    if not env_type:
        for path in ("/etc/default/x-ui", "/etc/conf.d/x-ui", "/etc/sysconfig/x-ui"):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    for line in fh:
                        if line.strip().startswith("XUI_DB_TYPE="):
                            env_type = line.split("=", 1)[1].strip().strip("'\"").lower()
            except OSError:
                continue
    return "postgres" if env_type.startswith("postgres") else "sqlite"


def _factor_config() -> Dict[str, Any]:
    try:
        with open(settings.FACTOR_CONFIG_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def database_path() -> str:
    return str(_factor_config().get("database_path") or "/etc/x-ui/x-ui.db")


def connect_ro() -> sqlite3.Connection:
    if detect_backend() != "sqlite":
        raise FactorError(tr("پنل وب فعلاً فقط از دیتابیس SQLite پشتیبانی می‌کند (دستورات CLI برای PostgreSQL کار می‌کنند)."), 501)
    path = database_path()
    if not os.path.exists(path):
        raise FactorError(tr("فایل دیتابیس 3x-ui پیدا نشد: {}", path), 500)
    conn = sqlite3.connect("file:{}?mode=ro".format(quote(path)), uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def table_names(conn: sqlite3.Connection) -> set:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def has_factor_tables(conn: sqlite3.Connection) -> bool:
    return {"xui_factor_rules", "xui_factor_scopes", "xui_factor_rule_clients",
            "xui_factor_overrides", "xui_factor_excludes", "xui_factor_events"} <= table_names(conn)


class Coverage:
    """Which factor currently applies to each client (traffic id)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.cover: Dict[int, Dict[str, Any]] = {}
        self.override: Dict[int, int] = {}
        self.excluded: set = set()
        for row in conn.execute(
            """SELECT rc.traffic_id AS tid, r.id AS rule_id, r.state AS state, r.factor_ppm AS ppm,
                      (s.rule_id IS NOT NULL) AS is_scope, s.inbound_id AS scope_inbound
                 FROM xui_factor_rule_clients rc
                 JOIN xui_factor_rules r ON r.id = rc.rule_id
                 JOIN client_traffics ct ON ct.id = rc.traffic_id
                 LEFT JOIN xui_factor_scopes s ON s.rule_id = r.id
                WHERE r.state IN ('active','paused') AND rc.missing_since IS NULL"""
        ):
            current = self.cover.get(row["tid"])
            entry = {"rule_id": row["rule_id"], "state": row["state"], "ppm": row["ppm"], "scope": bool(row["is_scope"]),
                     "scope_inbound": row["scope_inbound"]}
            # A client's own rule outranks an inbound/global scope.
            if current is None or (current["scope"] and not entry["scope"]):
                self.cover[row["tid"]] = entry
        for row in conn.execute("SELECT traffic_id, factor_ppm FROM xui_factor_overrides WHERE state = 'active'"):
            self.override[row["traffic_id"]] = row["factor_ppm"]
        for row in conn.execute("SELECT traffic_id FROM xui_factor_excludes WHERE state = 'active'"):
            self.excluded.add(row["traffic_id"])

    def describe(self, traffic_id: int) -> Dict[str, Any]:
        cover = self.cover.get(traffic_id)
        override = self.override.get(traffic_id)
        if traffic_id in self.excluded:
            return {"source": "excluded", "factor": None, "state": None, "base_factor": ppm_to_str(cover["ppm"]) if cover else None}
        if cover is not None:
            if override is not None:
                return {"source": "override", "factor": ppm_to_str(override), "state": cover["state"], "base_factor": ppm_to_str(cover["ppm"])}
            return {"source": "scope" if cover["scope"] else "single", "factor": ppm_to_str(cover["ppm"]),
                    "state": cover["state"], "base_factor": ppm_to_str(cover["ppm"])}
        if override is not None:
            return {"source": "override_idle", "factor": ppm_to_str(override), "state": None, "base_factor": None}
        return {"source": "none", "factor": None, "state": None, "base_factor": None}


_EXTRA_RE = re.compile(r"extra_all_time=(\d+)")
_EMAIL_RE = re.compile(r"\bemail (\S+)")
_INBOUND_RE = re.compile(r"\binbound (\d+)")


def _hydrate_event(row: sqlite3.Row) -> Dict[str, Any]:
    message = row["message"] or ""
    email = _EMAIL_RE.search(message)
    inbound = _INBOUND_RE.search(message)
    extra = _EXTRA_RE.search(message)
    return {
        "id": row["id"], "rule_id": row["rule_id"], "type": row["event_type"], "message": message,
        "created_at": row["created_at"], "email": email.group(1) if email else None,
        "inbound_id": int(inbound.group(1)) if inbound else None,
        "extra_bytes": int(extra.group(1)) if extra else None,
    }


class Reader:
    """Read-only views used by both the web API and the command line.

    Expensive aggregates are cached for a short time so an open dashboard (or
    several browser tabs) cannot keep the CPU and the disk busy."""

    def __init__(self) -> None:
        self._cache: Dict[Any, Tuple[float, Any]] = {}
        self._cache_lock = threading.Lock()

    def _cached(self, key: Any, ttl: float, fn):
        now = time.monotonic()
        with self._cache_lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < ttl:
                return hit[1]
        value = fn()
        with self._cache_lock:
            self._cache[key] = (time.monotonic(), value)
            if len(self._cache) > 64:
                self._cache.clear()
        return value

    def invalidate(self) -> None:
        with self._cache_lock:
            self._cache.clear()

    def overview(self) -> Dict[str, Any]:
        return self._cached("overview", 5.0, self._overview)

    def _overview(self) -> Dict[str, Any]:
        conn = connect_ro()
        try:
            names = table_names(conn)
            info: Dict[str, Any] = {
                "tables": has_factor_tables(conn),
                "inbounds_total": conn.execute("SELECT COUNT(*) FROM inbounds").fetchone()[0] if "inbounds" in names else 0,
                "clients_total": conn.execute("SELECT COUNT(*) FROM client_traffics").fetchone()[0] if "client_traffics" in names else 0,
            }
            if not info["tables"]:
                info.update(self._empty_overview())
                return info
            q = lambda sql, *a: conn.execute(sql, a).fetchone()[0]  # noqa: E731
            scope_join = "FROM xui_factor_rules r JOIN xui_factor_scopes s ON s.rule_id = r.id WHERE r.state = ?"
            info.update({
                "inbound_scopes_active": q("SELECT COUNT(*) " + scope_join + " AND s.inbound_id IS NOT NULL", "active"),
                "inbound_scopes_paused": q("SELECT COUNT(*) " + scope_join + " AND s.inbound_id IS NOT NULL", "paused"),
                "global_scopes": q("SELECT COUNT(*) FROM xui_factor_rules r JOIN xui_factor_scopes s ON s.rule_id = r.id "
                                   "WHERE r.state IN ('active','paused') AND s.inbound_id IS NULL"),
                "client_rules_active": q("SELECT COUNT(*) FROM xui_factor_rules r WHERE r.state = 'active' "
                                         "AND r.id NOT IN (SELECT rule_id FROM xui_factor_scopes)"),
                "client_rules_paused": q("SELECT COUNT(*) FROM xui_factor_rules r WHERE r.state = 'paused' "
                                         "AND r.id NOT IN (SELECT rule_id FROM xui_factor_scopes)"),
                "overrides_active": q("SELECT COUNT(*) FROM xui_factor_overrides WHERE state = 'active'"),
                "excludes_active": q("SELECT COUNT(*) FROM xui_factor_excludes WHERE state = 'active'"),
                "tracked_clients": q("SELECT COUNT(DISTINCT rc.traffic_id) FROM xui_factor_rule_clients rc "
                                     "JOIN xui_factor_rules r ON r.id = rc.rule_id WHERE r.state IN ('active','paused')"),
            })
            info.update(self._impact(conn))
            coverage = Coverage(conn)
            factored = [t for t in coverage.cover if t not in coverage.excluded]
            info["factored_clients"] = len(factored)
            info["recent_events"] = [
                _hydrate_event(r) for r in conn.execute(
                    "SELECT * FROM xui_factor_events WHERE event_type != 'traffic_applied' ORDER BY id DESC LIMIT 8")]
            return info
        finally:
            conn.close()

    def _impact(self, conn: sqlite3.Connection) -> Dict[str, Any]:
        """Traffic added by factors, aggregated from audit events (cached: it scans them)."""
        def compute() -> Dict[str, Any]:
            expr = "COALESCE(SUM(CAST(substr(message, instr(message,'extra_all_time=') + 15) AS INTEGER)), 0)"
            total = conn.execute("SELECT COUNT(*), {}, MAX(created_at) FROM xui_factor_events WHERE event_type = 'traffic_applied'".format(expr)).fetchone()
            day = conn.execute("SELECT COUNT(*), {} FROM xui_factor_events WHERE event_type = 'traffic_applied' "
                               "AND created_at >= strftime('%s','now') - 86400".format(expr)).fetchone()
            return {"applications": total[0], "extra_bytes": total[1], "last_applied_at": total[2],
                    "applications_24h": day[0], "extra_bytes_24h": day[1]}
        return self._cached("impact", 60.0, compute)

    @staticmethod
    def _empty_overview() -> Dict[str, Any]:
        keys = ("inbound_scopes_active", "inbound_scopes_paused", "global_scopes", "client_rules_active",
                "client_rules_paused", "overrides_active", "excludes_active", "tracked_clients", "applications",
                "extra_bytes", "applications_24h", "extra_bytes_24h", "factored_clients")
        data: Dict[str, Any] = {k: 0 for k in keys}
        data.update({"last_applied_at": None, "recent_events": []})
        return data

    def list_inbounds(self, page: Any = 1, size: Any = 25, q: str = "") -> Dict[str, Any]:
        page_n, size_n = paginate(page, size)
        conn = connect_ro()
        try:
            if "inbounds" not in table_names(conn):
                return page_payload([], 0, page_n, size_n)
            has_meta = has_factor_tables(conn)
            where, params = "", []  # type: str, List[Any]
            q = (q or "").strip()
            if q:
                where = "WHERE i.remark LIKE ? ESCAPE '\\' OR CAST(i.id AS TEXT) = ? OR CAST(i.port AS TEXT) = ? OR i.protocol LIKE ? ESCAPE '\\'"
                like = "%{}%".format(q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_"))
                params = [like, q, q, like]
            total = conn.execute("SELECT COUNT(*) FROM inbounds i " + where, params).fetchone()[0]
            rows = conn.execute(
                "SELECT i.id, i.remark, i.protocol, i.port, i.enable, "
                "(SELECT COUNT(*) FROM client_traffics c WHERE c.inbound_id = i.id) AS clients "
                "FROM inbounds i {} ORDER BY i.id LIMIT ? OFFSET ?".format(where),
                params + [size_n, (page_n - 1) * size_n]).fetchall()
            scopes: Dict[int, sqlite3.Row] = {}
            covered: Dict[int, int] = {}
            singles: Dict[int, int] = {}
            overrides: Dict[int, int] = {}
            if has_meta:
                for r in conn.execute(
                        "SELECT s.inbound_id AS inbound_id, r.factor_ppm AS ppm, r.state AS state, r.id AS rule_id "
                        "FROM xui_factor_rules r JOIN xui_factor_scopes s ON s.rule_id = r.id "
                        "WHERE r.state IN ('active','paused') AND s.inbound_id IS NOT NULL ORDER BY r.id"):
                    scopes[r["inbound_id"]] = r
                for r in conn.execute("SELECT rule_id, COUNT(*) AS n FROM xui_factor_rule_clients WHERE missing_since IS NULL GROUP BY rule_id"):
                    covered[r["rule_id"]] = r["n"]
                for r in conn.execute(
                        "SELECT r.inbound_id AS inbound_id, COUNT(*) AS n FROM xui_factor_rules r "
                        "WHERE r.state IN ('active','paused') AND r.id NOT IN (SELECT rule_id FROM xui_factor_scopes) "
                        "GROUP BY r.inbound_id"):
                    singles[r["inbound_id"]] = r["n"]
                for r in conn.execute("SELECT inbound_id, COUNT(*) AS n FROM xui_factor_overrides WHERE state = 'active' GROUP BY inbound_id"):
                    overrides[r["inbound_id"]] = r["n"]
            items = []
            for row in rows:
                scope = scopes.get(row["id"])
                items.append({
                    "id": row["id"], "remark": row["remark"] or "", "protocol": row["protocol"], "port": row["port"],
                    "enable": bool(row["enable"]), "clients": row["clients"],
                    "factor": ppm_to_str(scope["ppm"]) if scope else None,
                    "state": scope["state"] if scope else None,
                    "scope_clients": covered.get(scope["rule_id"], 0) if scope else None,
                    "client_rules": singles.get(row["id"], 0), "client_overrides": overrides.get(row["id"], 0),
                })
            return page_payload(items, total, page_n, size_n)
        finally:
            conn.close()

    def list_clients(self, page: Any = 1, size: Any = 25, q: str = "", inbound_id: Any = None,
                     state: str = "all") -> Dict[str, Any]:
        page_n, size_n = paginate(page, size)
        conn = connect_ro()
        try:
            if "client_traffics" not in table_names(conn):
                return page_payload([], 0, page_n, size_n)
            where, params = [], []  # type: List[str], List[Any]
            q = (q or "").strip()
            if q:
                where.append("email LIKE ? ESCAPE '\\'")
                params.append("%{}%".format(q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")))
            if inbound_id not in (None, "", 0, "0"):
                where.append("inbound_id = ?")
                params.append(parse_id(inbound_id, tr("شناسه اینباند")))
            columns = "SELECT id, inbound_id, enable, email, up, down, total, expiry_time FROM client_traffics"
            clause = (" WHERE " + " AND ".join(where)) if where else ""
            order = " ORDER BY inbound_id, email COLLATE NOCASE"
            coverage = Coverage(conn) if has_factor_tables(conn) else None
            none_info = {"source": "none", "factor": None, "state": None, "base_factor": None}

            def build(row: sqlite3.Row) -> Dict[str, Any]:
                info = coverage.describe(row["id"]) if coverage else none_info
                return {"id": row["id"], "email": row["email"], "inbound_id": row["inbound_id"], "enable": bool(row["enable"]),
                        "up": row["up"], "down": row["down"], "total": row["total"], "expiry_time": row["expiry_time"], **info}

            if state in ("", "all"):
                # Common case: let SQLite paginate instead of materialising every client.
                total = conn.execute("SELECT COUNT(*) FROM client_traffics" + clause, params).fetchone()[0]
                rows = conn.execute(columns + clause + order + " LIMIT ? OFFSET ?", params + [size_n, (page_n - 1) * size_n]).fetchall()
                return page_payload([build(r) for r in rows], total, page_n, size_n)
            items = [item for item in map(build, conn.execute(columns + clause + order, params)) if _state_matches(state, item["source"])]
            start = (page_n - 1) * size_n
            return page_payload(items[start:start + size_n], len(items), page_n, size_n)
        finally:
            conn.close()

    def list_events(self, page: Any = 1, size: Any = 25, kind: str = "lifecycle", q: str = "") -> Dict[str, Any]:
        page_n, size_n = paginate(page, size)
        conn = connect_ro()
        try:
            if not has_factor_tables(conn):
                payload = page_payload([], 0, page_n, size_n)
                payload["types"] = []
                return payload
            where, params = [], []  # type: List[str], List[Any]
            if kind == "lifecycle":
                where.append("event_type != 'traffic_applied'")
            elif kind and kind != "all":
                where.append("event_type = ?")
                params.append(kind)
            q = (q or "").strip()
            if q:
                where.append("message LIKE ? ESCAPE '\\'")
                params.append("%{}%".format(q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")))
            clause = (" WHERE " + " AND ".join(where)) if where else ""
            count = lambda: conn.execute("SELECT COUNT(*) FROM xui_factor_events" + clause, params).fetchone()[0]  # noqa: E731
            total = count() if q else self._cached(("events", kind), 20.0, count)
            rows = conn.execute("SELECT * FROM xui_factor_events{} ORDER BY id DESC LIMIT ? OFFSET ?".format(clause),
                                params + [size_n, (page_n - 1) * size_n]).fetchall()
            payload = page_payload([_hydrate_event(r) for r in rows], total, page_n, size_n)
            payload["types"] = [r[0] for r in conn.execute("SELECT DISTINCT event_type FROM xui_factor_events ORDER BY 1")]
            return payload
        finally:
            conn.close()

    def client_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        conn = connect_ro()
        try:
            row = conn.execute("SELECT id, inbound_id, email FROM client_traffics WHERE email = ?", (email,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def inbound_exists(self, inbound_id: int) -> bool:
        conn = connect_ro()
        try:
            return conn.execute("SELECT 1 FROM inbounds WHERE id = ?", (inbound_id,)).fetchone() is not None
        finally:
            conn.close()

    def inbound_overlap(self, inbound_id: int) -> Dict[int, int]:
        """Clients of this inbound that already sit under another scope: {other inbound id (0 = global): count}.

        3x-ui lets one client belong to several inbounds and the engine factors a client once,
        so a second scope over the same clients covers nobody and only produces repeated events."""
        conn = connect_ro()
        try:
            if not has_factor_tables(conn):
                return {}
            names = table_names(conn)
            if "client_inbounds" in names and "clients" in names:
                rows = conn.execute("SELECT ct.id FROM client_inbounds ci JOIN clients c ON c.id = ci.client_id "
                                    "JOIN client_traffics ct ON ct.email = c.email WHERE ci.inbound_id = ?", (inbound_id,)).fetchall()
            else:
                rows = conn.execute("SELECT id FROM client_traffics WHERE inbound_id = ?", (inbound_id,)).fetchall()
            coverage = Coverage(conn)
            shared: Dict[int, int] = {}
            for row in rows:
                cover = coverage.cover.get(row[0])
                if cover and cover["scope"] and cover["scope_inbound"] != inbound_id:
                    key = int(cover["scope_inbound"] or 0)
                    shared[key] = shared.get(key, 0) + 1
            return shared
        finally:
            conn.close()

    def live_single_rules(self, inbound_id: int) -> List[Dict[str, Any]]:
        """Active/paused per-client rules of one inbound (scope rules excluded)."""
        conn = connect_ro()
        try:
            if not has_factor_tables(conn):
                return []
            rows = conn.execute(
                "SELECT r.email AS email, r.inbound_id AS inbound_id, r.factor_ppm AS ppm, r.state AS state "
                "FROM xui_factor_rules r WHERE r.inbound_id = ? AND r.state IN ('active','paused') "
                "AND r.id NOT IN (SELECT rule_id FROM xui_factor_scopes)", (inbound_id,)).fetchall()
            return [{"email": r["email"], "inbound_id": r["inbound_id"], "factor": ppm_to_str(r["ppm"]), "state": r["state"]} for r in rows]
        finally:
            conn.close()

    def live_inbound_scope(self, inbound_id: int) -> Optional[Dict[str, Any]]:
        """The active/paused persistent scope attached to one inbound, if any."""
        conn = connect_ro()
        try:
            if not has_factor_tables(conn):
                return None
            row = conn.execute(
                "SELECT r.id AS id, r.factor_ppm AS ppm, r.state AS state FROM xui_factor_rules r "
                "JOIN xui_factor_scopes s ON s.rule_id = r.id "
                "WHERE s.inbound_id = ? AND r.state IN ('active','paused') ORDER BY r.id DESC LIMIT 1", (inbound_id,)).fetchone()
            return {"id": row["id"], "factor": ppm_to_str(row["ppm"]), "state": row["state"]} if row else None
        finally:
            conn.close()

    def client_state(self, email: str) -> Optional[Dict[str, Any]]:
        conn = connect_ro()
        try:
            row = conn.execute("SELECT id, inbound_id FROM client_traffics WHERE email = ?", (email,)).fetchone()
            if row is None:
                return None
            state = {"traffic_id": row["id"], "inbound_id": row["inbound_id"], "own_rule": False, "override": False}
            if has_factor_tables(conn):
                state["own_rule"] = conn.execute(
                    "SELECT 1 FROM xui_factor_rules r WHERE r.email = ? AND r.state IN ('active','paused') "
                    "AND r.id NOT IN (SELECT rule_id FROM xui_factor_scopes)", (email,)).fetchone() is not None
                state["override"] = conn.execute(
                    "SELECT 1 FROM xui_factor_overrides WHERE traffic_id = ? AND state = 'active'", (row["id"],)).fetchone() is not None
            return state
        finally:
            conn.close()

    def active_override_targets(self) -> List[Tuple[str, int]]:
        conn = connect_ro()
        try:
            if not has_factor_tables(conn):
                return []
            return [(r["email"], r["inbound_id"]) for r in conn.execute(
                "SELECT email, inbound_id FROM xui_factor_overrides WHERE state = 'active'")]
        finally:
            conn.close()


def _state_matches(wanted: str, source: str) -> bool:
    if wanted in ("", "all"):
        return True
    if wanted == "factored":
        return source in ("single", "scope", "override")
    if wanted == "override":
        return source in ("override", "override_idle")
    return wanted == source


# ------------------------------------------------------------ operations
class Result:
    def __init__(self, rc: int, out: str, err: str) -> None:
        self.rc, self.out, self.err = rc, out, err

    @property
    def ok(self) -> bool:
        return self.rc == 0


def _invalidates(fn):
    """Drop cached reads after any mutation so the UI shows fresh numbers."""
    def wrapper(self, *args, **kwargs):
        try:
            return fn(self, *args, **kwargs)
        finally:
            self.reader.invalidate()
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


class Manager:
    """Mutating operations. A single lock keeps CLI invocations serialized."""

    def __init__(self) -> None:
        self.reader = Reader()
        self._lock = threading.RLock()

    # -- process plumbing
    def cli(self, *args: str, timeout: int = 90) -> Result:
        cmd = [settings.FACTOR_BIN, "--config", settings.FACTOR_CONFIG_PATH] + list(args)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        except FileNotFoundError:
            raise FactorError(tr("فایل اجرایی xui-factor پیدا نشد: {}", settings.FACTOR_BIN), 500)
        except subprocess.TimeoutExpired:
            raise FactorError(tr("اجرای دستور xui-factor بیش از حد طول کشید."), 504)
        return Result(proc.returncode, proc.stdout, proc.stderr)

    def cli_strict(self, *args: str) -> Result:
        res = self.cli(*args)
        if not res.ok:
            raise FactorError(friendly_error(res.err, res.out), 422, detail=(res.err or res.out).strip())
        return res

    def tick(self) -> None:
        """Flush pending traffic before changing rules; failures are harmless."""
        try:
            self.cli("tick", timeout=30)
        except FactorError:
            pass

    @staticmethod
    def _systemctl(*args: str, timeout: int = 30) -> Result:
        if shutil.which("systemctl") is None:
            return Result(127, "", "systemctl not available")
        try:
            proc = subprocess.run(["systemctl"] + list(args), capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return Result(124, "", "systemctl timed out")
        return Result(proc.returncode, proc.stdout, proc.stderr)

    # -- service state
    def service_status(self) -> Dict[str, Any]:
        unit = settings.FACTOR_SERVICE
        if shutil.which("systemctl") is None:
            return {"active": "unknown", "enabled": "unknown", "installed": os.path.exists(settings.FACTOR_UNIT_PATH)}
        active = self._systemctl("is-active", unit).out.strip() or "unknown"
        enabled = self._systemctl("is-enabled", unit).out.strip() or "unknown"
        return {"active": active, "enabled": enabled, "installed": os.path.exists(settings.FACTOR_UNIT_PATH)}

    def _require_running(self) -> None:
        status = self.service_status()
        if status["active"] in ("inactive", "failed", "deactivating") or (status["active"] == "unknown" and
                                                                          status["enabled"] == "not-found"):
            raise FactorError(tr("سرویس فاکتور غیرفعال است؛ ابتدا از بخش «نگهداری» آن را فعال کنید، وگرنه ضریب‌ها اعمال نمی‌شوند."), 409)

    def _ensure_unit(self) -> None:
        if os.path.exists(settings.FACTOR_UNIT_PATH):
            return
        text = UNIT_TEMPLATE.format(bin=settings.FACTOR_BIN, config=settings.FACTOR_CONFIG_PATH)
        with open(settings.FACTOR_UNIT_PATH, "w", encoding="utf-8") as fh:
            fh.write(text)
        self._systemctl("daemon-reload")

    # -- inbound factors
    def _apply_client(self, mail: str, iid: int, text: str) -> str:
        """Give one client its own factor; returns ``rule`` or ``override``."""
        res = self.cli("enable", "--email", mail, "--inbound-id", str(iid), "--factor", text)
        if res.ok:
            return "rule"
        if "conflict" in (res.err + res.out).lower():
            # Covered by an inbound/global scope: give the client its own factor on top.
            self.cli_strict("override", "--email", mail, "--inbound-id", str(iid), "--factor", text)
            return "override"
        raise FactorError(friendly_error(res.err, res.out), 422, detail=(res.err or res.out).strip())

    def _restore_clients(self, keep: List[Dict[str, Any]], iid: int) -> Tuple[int, List[str]]:
        restored, warnings = 0, []
        for rule in keep:
            try:
                self._apply_client(rule["email"], iid, rule["factor"])
                restored += 1
            except FactorError as exc:
                warnings.append("{}: {}".format(rule["email"], exc.message))
        return restored, warnings

    @_invalidates
    def set_inbound(self, inbound_id: Any, factor: Any) -> Dict[str, Any]:
        """Create or change the factor of a whole inbound.

        ``enable-all`` on an inbound that already has a scope would stack a second,
        empty scope, so an existing scope is replaced (disable, then enable).
        ``disable-all --inbound-id`` also switches off per-client rules of that
        inbound; those are snapshotted and re-applied on top of the new scope."""
        iid, text = parse_id(inbound_id, tr("شناسه اینباند")), parse_factor(factor)
        with self._lock:
            if not self.reader.inbound_exists(iid):
                raise FactorError(tr("اینباند {} وجود ندارد.", iid), 404)
            self._require_running()
            for other, count in sorted(self.reader.inbound_overlap(iid).items()):
                if other == 0:
                    raise FactorError(tr("اینباند {} کلاینت‌های مشترک با ضریب سراسری دارد ({} کلاینت)؛ ثبت ضریب برای آن بی‌اثر است و فقط رویدادهای تکراری می‌سازد.", iid, count), 409)
                raise FactorError(tr("اینباند {} کلاینت‌های مشترک با ضریب اینباند {} دارد ({} کلاینت)؛ ثبت ضریب برای آن بی‌اثر است و فقط رویدادهای تکراری می‌سازد. فقط یکی از آن‌ها را نگه دارید.", iid, other, count), 409)
            existing = self.reader.live_inbound_scope(iid)
            if existing is None:
                self.cli_strict("enable-all", "--inbound-id", str(iid), "--factor", text)
                return {"inbound_id": iid, "factor": text, "restored_client_rules": 0, "warnings": []}
            keep = self.reader.live_single_rules(iid)
            self.tick()
            self.cli_strict("disable-all", "--inbound-id", str(iid))
            self.cli_strict("enable-all", "--inbound-id", str(iid), "--factor", text)
            restored, warnings = self._restore_clients(keep, iid)
        return {"inbound_id": iid, "factor": text, "restored_client_rules": restored, "warnings": warnings}

    @_invalidates
    def remove_inbound(self, inbound_id: Any) -> Dict[str, Any]:
        """Remove an inbound's factor but keep individual client rules alive."""
        iid = parse_id(inbound_id, tr("شناسه اینباند"))
        with self._lock:
            if self.reader.live_inbound_scope(iid) is None:
                raise FactorError(tr("برای اینباند {} ضریبی ثبت نشده است.", iid), 404)
            keep = self.reader.live_single_rules(iid)
            self.tick()
            self.cli_strict("disable-all", "--inbound-id", str(iid))
            restored, warnings = self._restore_clients(keep, iid)
        return {"inbound_id": iid, "restored_client_rules": restored, "warnings": warnings}

    # -- client factors
    @_invalidates
    def set_client(self, email: Any, inbound_id: Any, factor: Any) -> Dict[str, Any]:
        mail, text = parse_email(email), parse_factor(factor)
        iid = parse_id(inbound_id, tr("شناسه اینباند")) if inbound_id not in (None, "") else None
        with self._lock:
            state = self.reader.client_state(mail)
            if state is None:
                raise FactorError(tr("کلاینتی با ایمیل «{}» پیدا نشد.", mail), 404)
            iid = iid or state["inbound_id"]
            self._require_running()
            if state["own_rule"] or state["override"]:
                # Already governed: update through an override, which wins over any rule or scope.
                self.cli_strict("override", "--email", mail, "--inbound-id", str(iid), "--factor", text)
                mode = "override"
            else:
                mode = self._apply_client(mail, iid, text)
        return {"email": mail, "inbound_id": iid, "factor": text, "mode": mode}

    @_invalidates
    def remove_client(self, email: Any, inbound_id: Any = None) -> Dict[str, Any]:
        mail = parse_email(email)
        with self._lock:
            state = self.reader.client_state(mail)
            if state is None:
                raise FactorError(tr("کلاینتی با ایمیل «{}» پیدا نشد.", mail), 404)
            iid = parse_id(inbound_id, tr("شناسه اینباند")) if inbound_id not in (None, "") else state["inbound_id"]
            if not (state["own_rule"] or state["override"]):
                raise FactorError(tr("برای این کلاینت ضریب اختصاصی ثبت نشده است (ممکن است فقط زیر ضریب اینباند باشد)."), 404)
            self.tick()
            removed = []
            if state["override"]:
                self.cli_strict("remove-override", "--email", mail, "--inbound-id", str(iid))
                removed.append("override")
            if state["own_rule"]:
                self.cli_strict("disable", "--email", mail, "--inbound-id", str(iid))
                removed.append("rule")
        return {"email": mail, "inbound_id": iid, "removed": removed}

    # -- bulk helpers
    def bulk(self, items: Iterable[Any], fn) -> Dict[str, Any]:
        ok, failed = [], []
        try:
            for item in items:
                try:
                    ok.append(fn(item))
                except FactorError as exc:
                    failed.append({"item": item, "error": exc.message})
        finally:
            self.reader.invalidate()
        return {"ok": len(ok), "failed": failed}

    # -- global actions
    @_invalidates
    def clear_all(self) -> Dict[str, Any]:
        """Remove every factor: all rules, scopes and overrides."""
        with self._lock:
            self.tick()
            res = self.cli("disable-all")
            if not res.ok and "not found" not in (res.err + res.out).lower():
                raise FactorError(friendly_error(res.err, res.out), 422)
            removed_overrides, warnings = 0, []
            for email, iid in self.reader.active_override_targets():
                r = self.cli("remove-override", "--email", email, "--inbound-id", str(iid))
                if r.ok:
                    removed_overrides += 1
                else:
                    warnings.append("{}: {}".format(email, friendly_error(r.err, r.out)))
        return {"overrides_removed": removed_overrides, "warnings": warnings}

    @_invalidates
    def disable_factor(self) -> Dict[str, Any]:
        """Master switch off: pause every rule and stop the daemon."""
        with self._lock:
            self.tick()
            res = self.cli("pause-all")
            if not res.ok and "not found" not in (res.err + res.out).lower():
                raise FactorError(friendly_error(res.err, res.out), 422)
            stop = self._systemctl("disable", "--now", settings.FACTOR_SERVICE)
        return {"service_stopped": stop.ok or stop.rc == 127, "detail": (stop.err or "").strip()}

    @_invalidates
    def enable_factor(self) -> Dict[str, Any]:
        """Master switch on: start the daemon and resume paused rules from current counters."""
        with self._lock:
            self._ensure_unit()
            start = self._systemctl("enable", "--now", settings.FACTOR_SERVICE)
            if not start.ok and start.rc != 127:
                raise FactorError(tr("راه‌اندازی سرویس ناموفق بود: {}", (start.err or start.out).strip()), 500)
            res = self.cli("resume-all")
            if not res.ok and "not found" not in (res.err + res.out).lower():
                raise FactorError(friendly_error(res.err, res.out), 422)
        return {"service_started": start.ok or start.rc == 127}

    @_invalidates
    def purge(self, remove_service: bool = True) -> Dict[str, Any]:
        """Stop the daemon and drop every ``xui_factor_*`` table from the panel database."""
        with self._lock:
            summary: Dict[str, Any] = {"dropped": [], "service_removed": False, "backup": ""}
            conn_ro = connect_ro()
            conn_ro.close()  # validates backend/path early
            from . import backup as backup_mod
            try:
                summary["backup"] = backup_mod.create_backup(self, history=False, tag="pre-purge")["path"]
            except FactorError:
                pass  # no factor tables yet: nothing worth saving
            self._systemctl("disable", "--now", settings.FACTOR_SERVICE)
            if remove_service:
                try:
                    if os.path.exists(settings.FACTOR_UNIT_PATH):
                        os.remove(settings.FACTOR_UNIT_PATH)
                        summary["service_removed"] = True
                except OSError as exc:
                    raise FactorError(tr("حذف فایل سرویس ناموفق بود: {}", exc), 500)
                self._systemctl("daemon-reload")
            summary["dropped"] = self._drop_tables()
        return summary

    @staticmethod
    def _drop_tables() -> List[str]:
        conn = sqlite3.connect(database_path(), timeout=30, isolation_level=None)
        dropped: List[str] = []
        try:
            conn.execute("PRAGMA busy_timeout = 30000")
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = [r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'xui\\_factor\\_%' ESCAPE '\\'")]
                for name in existing:
                    if not re.match(r"^xui_factor_[a-z_]+$", name):
                        continue  # never interpolate an unexpected identifier
                    conn.execute('DROP TABLE IF EXISTS "{}"'.format(name))
                    dropped.append(name)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        except sqlite3.Error as exc:
            raise FactorError(tr("حذف جدول‌ها ناموفق بود: {}", exc), 500)
        finally:
            conn.close()
        return dropped

    # -- web panel removal
    def schedule_uninstall(self, mode: str) -> Dict[str, Any]:
        """Hand the removal to a detached process: it must outlive (and stop) this very panel.

        ``web``  removes only the web panel; the xui-factor service keeps working.
        ``all``  also drops the factor records, removes the service and the binary."""
        if mode not in ("web", "all"):
            raise FactorError(tr("حالت حذف نامعتبر است."))
        self._spawn_manager(["uninstall", "--yes", "--all" if mode == "all" else "--web-only", "--delay", "3"], "xfp-uninstall")
        return {"mode": mode, "scheduled": True}

    def _spawn_manager(self, args: List[str], unit: str) -> None:
        """Run ``xfp <args>`` detached: it must outlive (and may restart) this very panel."""
        manager = settings.MANAGER_BIN
        if not os.path.isfile(manager):
            raise FactorError(tr("اسکریپت مدیریت xfp پیدا نشد: {}", manager), 500)
        cmd = [manager] + args
        if shutil.which("systemd-run"):
            cmd = ["systemd-run", "--unit={}-{}".format(unit, os.getpid()), "--collect", "--no-block"] + cmd
        try:
            subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True, close_fds=True)
        except OSError as exc:
            raise FactorError(tr("شروع فرایند حذف ناموفق بود: {}", exc), 500)

    def schedule_update(self, tag: str) -> Dict[str, Any]:
        from . import update
        if not update.valid_tag(tag):
            raise FactorError(tr("نسخه‌ی نامعتبر."))
        self._spawn_manager(["update", "--yes", "--tag", tag, "--delay", "3"], "xfp-update")
        return {"scheduled": True, "tag": tag}

    PRUNABLE_EVENTS = ("scope_auto_enroll_skipped",)

    def prune_events(self, event_type: str = "scope_auto_enroll_skipped") -> Dict[str, Any]:
        """Delete repeated audit events in small batches (never holds the database lock for long)."""
        if event_type not in self.PRUNABLE_EVENTS:
            raise FactorError(tr("نوع رویداد مجاز نیست."))
        if detect_backend() != "sqlite":
            raise FactorError(tr("پنل وب فعلاً فقط از دیتابیس SQLite پشتیبانی می‌کند (دستورات CLI برای PostgreSQL کار می‌کنند)."), 501)
        deleted = 0
        conn = sqlite3.connect(database_path(), timeout=30, isolation_level=None)
        try:
            conn.execute("PRAGMA busy_timeout = 30000")
            if "xui_factor_events" not in table_names(conn):
                return {"deleted": 0}
            for _ in range(200):  # at most ~4 million rows per call
                cur = conn.execute("DELETE FROM xui_factor_events WHERE id IN "
                                   "(SELECT id FROM xui_factor_events WHERE event_type = ? LIMIT 20000)", (event_type,))
                deleted += cur.rowcount
                if cur.rowcount < 20000:
                    break
                time.sleep(0.15)  # let 3x-ui write in between
        except sqlite3.Error as exc:
            raise FactorError(tr("حذف جدول‌ها ناموفق بود: {}", exc), 500)
        finally:
            conn.close()
            self.reader.invalidate()
        return {"deleted": deleted}

    # -- diagnostics
    def doctor(self) -> str:
        res = self.cli("doctor")
        return (res.out + res.err).strip()

    def backup(self) -> str:
        return self.cli_strict("backup").out.strip()

    def cleanup(self, dry_run: bool = True) -> str:
        args = ["cleanup"] + (["--dry-run"] if dry_run else [])
        return self.cli_strict(*args).out.strip()

"""Factor-only backup / restore.

A backup is a small SQLite file holding just the ``xui_factor_*`` tables (gzip
compressed). Everything is streamed in batches so memory and disk use stay low.
The engine's own ``xui-factor backup`` (a full x-ui.db copy) is exposed too, but
only the factor backups can be restored from here."""
from __future__ import annotations

import gzip
import os
import re
import secrets
import shutil
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

from . import core, settings
from .i18n import tr

FORMAT = "1"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.db(\.gz)?$")
IDENT_RE = re.compile(r"^[A-Za-z0-9_]+$")
SQLITE_MAGIC = b"SQLite format 3\x00"
GZIP_MAGIC = b"\x1f\x8b"
MAX_UPLOAD = 512 << 20
MAX_UNPACKED = 2 << 30
KEEP_AUTO = 30
BATCH = 5000
CHUNK = 1 << 16


def backup_dir() -> str:
    return settings.backup_dir()


def full_backup_dir() -> str:
    return str(core._factor_config().get("backup_dir") or "/var/backups/xui-factor")


def ensure_dir() -> str:
    path = backup_dir()
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
        os.chmod(path, 0o700)
    except OSError as exc:
        raise core.FactorError(tr("ساخت پوشه‌ی بکاپ ممکن نیست ({}): {}", path, exc), 500)
    return path


FULL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.(db|dump)$")


def _path(name: str, kind: str = "factor") -> str:
    """Resolve a listed backup name inside its directory (never a free path)."""
    pattern = NAME_RE if kind == "factor" else FULL_RE
    if not isinstance(name, str) or not pattern.match(name):
        raise core.FactorError(tr("نام فایل بکاپ نامعتبر است."))
    base = backup_dir() if kind == "factor" else full_backup_dir()
    path = os.path.join(base, name)
    if not os.path.isfile(path) or os.path.dirname(os.path.realpath(path)) != os.path.realpath(base):
        raise core.FactorError(tr("فایل بکاپ پیدا نشد."), 404)
    return path


def list_backups() -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    for kind, base, pattern in (("factor", backup_dir(), NAME_RE),
                                ("full", full_backup_dir(), FULL_RE)):
        try:
            names = os.listdir(base)
        except OSError:
            continue
        for name in names:
            full = os.path.join(base, name)
            if name.startswith(".") or not pattern.match(name) or not os.path.isfile(full):
                continue
            st = os.stat(full)
            items.append({"name": name, "kind": kind, "size": st.st_size, "mtime": int(st.st_mtime), "path": full})
    items.sort(key=lambda i: i["mtime"], reverse=True)
    return {"factor_dir": backup_dir(), "full_dir": full_backup_dir(), "items": items}


# ---------------------------------------------------------------- create
def _factor_objects(conn: sqlite3.Connection) -> Tuple[List[Tuple[str, str]], List[str]]:
    tables, indexes = [], []
    for row in conn.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL AND tbl_name LIKE 'xui\\_factor\\_%' ESCAPE '\\' "
            "ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END, rowid"):
        if not IDENT_RE.match(row[1]):
            continue
        if row[0] == "table":
            tables.append((row[1], row[2]))
        elif row[0] == "index":
            indexes.append(row[2])
    return tables, indexes


def _engine_version(mgr: "core.Manager") -> str:
    try:
        res = mgr.cli("version", timeout=10)
        return res.out.strip().splitlines()[0] if res.ok and res.out.strip() else ""
    except core.FactorError:
        return ""


def create_backup(mgr: "core.Manager", history: bool = False, tag: str = "") -> Dict[str, Any]:
    from . import __version__
    src = core.connect_ro()
    directory = ensure_dir()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    tag = re.sub(r"[^A-Za-z0-9-]", "", tag)[:20]
    name = "xui-factor-backup-{}{}.db.gz".format(stamp, "-" + tag if tag else "")
    tmp_db = os.path.join(directory, ".tmp-{}.db".format(secrets.token_hex(4)))
    final = os.path.join(directory, name)
    counts: Dict[str, int] = {}
    try:
        if not core.has_factor_tables(src):
            raise core.FactorError(tr("جدول‌های فاکتور در دیتابیس وجود ندارد؛ چیزی برای بکاپ نیست."), 404)
        dst = sqlite3.connect(tmp_db)
        try:
            dst.execute("PRAGMA journal_mode = OFF")
            dst.execute("PRAGMA synchronous = OFF")
            src.isolation_level = None
            src.execute("BEGIN")  # one consistent snapshot of every table
            try:
                tables, indexes = _factor_objects(src)
                for _, sql in tables:
                    dst.execute(sql)
                for sql in indexes:
                    dst.execute(sql)
                for table, _ in tables:
                    where = " WHERE event_type != 'traffic_applied'" if table == "xui_factor_events" and not history else ""
                    cur = src.execute('SELECT * FROM "{}"{}'.format(table, where))
                    marks = ",".join("?" * len(cur.description))
                    total = 0
                    while True:
                        rows = cur.fetchmany(BATCH)
                        if not rows:
                            break
                        dst.executemany('INSERT INTO "{}" VALUES ({})'.format(table, marks), [tuple(r) for r in rows])
                        total += len(rows)
                    counts[table] = total
                seq = [tuple(r) for r in src.execute("SELECT name, seq FROM sqlite_sequence WHERE name LIKE 'xui\\_factor\\_%' ESCAPE '\\'")] \
                    if "sqlite_sequence" in core.table_names(src) else []
            finally:
                src.execute("COMMIT")
            dst.execute("CREATE TABLE xfbackup_sequence (name TEXT, seq INTEGER)")
            dst.executemany("INSERT INTO xfbackup_sequence VALUES (?, ?)", seq)
            dst.execute("CREATE TABLE xfbackup_meta (key TEXT PRIMARY KEY, value TEXT)")
            meta = {"format": FORMAT, "created_at": str(int(time.time())), "panel_version": __version__,
                    "engine_version": _engine_version(mgr), "includes_traffic_history": "1" if history else "0",
                    "source_clients": str(src.execute("SELECT COUNT(*) FROM client_traffics").fetchone()[0])}
            dst.executemany("INSERT INTO xfbackup_meta VALUES (?, ?)", list(meta.items()))
            dst.commit()
        finally:
            dst.close()
        fd = os.open(final + ".part", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with open(tmp_db, "rb") as raw, os.fdopen(fd, "wb") as out:
            with gzip.GzipFile(fileobj=out, mode="wb", compresslevel=6) as gz:
                shutil.copyfileobj(raw, gz, CHUNK)
        os.replace(final + ".part", final)
    except sqlite3.Error as exc:
        raise core.FactorError(tr("ساخت بکاپ ناموفق بود: {}", exc), 500)
    finally:
        src.close()
        for leftover in (tmp_db, final + ".part"):
            try:
                os.remove(leftover)
            except OSError:
                pass
    _prune(directory)
    return {"name": name, "path": final, "size": os.path.getsize(final), "tables": counts, "history": history}


def _prune(directory: str) -> None:
    """Keep disk use bounded: drop the oldest automatic backups beyond KEEP_AUTO."""
    files = sorted((f for f in os.listdir(directory) if f.startswith("xui-factor-backup-") and NAME_RE.match(f)),
                   key=lambda f: os.path.getmtime(os.path.join(directory, f)), reverse=True)
    for old in files[KEEP_AUTO:]:
        try:
            os.remove(os.path.join(directory, old))
        except OSError:
            pass


def delete_backup(name: str, kind: str = "factor") -> None:
    os.remove(_path(name, kind))


def create_full_backup(mgr: "core.Manager") -> Dict[str, Any]:
    out = mgr.cli_strict("backup").out.strip().splitlines()
    path = out[-1].split(":", 1)[-1].strip() if out else ""
    for line in out:  # the engine prints the final path; locate it robustly
        if line.strip().startswith("/") or "/" in line:
            path = line[line.index("/"):].strip()
    return {"path": path, "name": os.path.basename(path), "size": os.path.getsize(path) if os.path.isfile(path) else 0}


# ---------------------------------------------------------- validate/upload
def _unpack(path: str) -> str:
    """Return a temp SQLite file with the backup's content (caller deletes it)."""
    directory = ensure_dir()
    with open(path, "rb") as fh:
        head = fh.read(16)
    tmp = os.path.join(directory, ".unpack-{}.db".format(secrets.token_hex(4)))
    try:
        if head[:2] == GZIP_MAGIC:
            written = 0
            with gzip.open(path, "rb") as gz, open(tmp, "wb") as out:
                while True:
                    chunk = gz.read(CHUNK)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > MAX_UNPACKED:
                        raise core.FactorError(tr("فایل بکاپ پس از باز شدن بیش از حد بزرگ است."))
                    out.write(chunk)
        elif head == SQLITE_MAGIC:
            shutil.copyfile(path, tmp)
        else:
            raise core.FactorError(tr("این فایل یک بکاپ XuiFactor نیست."))
        os.chmod(tmp, 0o600)
        return tmp
    except (OSError, EOFError, gzip.BadGzipFile) as exc:
        _rm(tmp)
        raise core.FactorError(tr("خواندن فایل بکاپ ممکن نیست: {}", exc))
    except core.FactorError:
        _rm(tmp)
        raise


def _rm(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _check(db_path: str) -> Dict[str, str]:
    """Validate structure of an unpacked backup; returns its metadata."""
    try:
        conn = sqlite3.connect("file:{}?mode=ro".format(db_path), uri=True)
        try:
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if "xfbackup_meta" not in names:
                raise core.FactorError(tr("این فایل یک بکاپ XuiFactor نیست."))
            meta = dict(conn.execute("SELECT key, value FROM xfbackup_meta").fetchall())
            if meta.get("format") != FORMAT:
                raise core.FactorError(tr("نسخه‌ی قالب بکاپ پشتیبانی نمی‌شود."))
            extra = {n for n in names if not n.startswith("xui_factor_") and n not in ("xfbackup_meta", "xfbackup_sequence", "sqlite_sequence")}
            if extra:
                raise core.FactorError(tr("بکاپ شامل جدول‌های غیرمجاز است: ") + ", ".join(sorted(extra)))
            if "xui_factor_rules" not in names:
                raise core.FactorError(tr("بکاپ ناقص است (جدول قانون‌ها وجود ندارد)."))
            meta["rules"] = str(conn.execute("SELECT COUNT(*) FROM xui_factor_rules").fetchone()[0])
            return meta
        finally:
            conn.close()
    except sqlite3.Error:
        raise core.FactorError(tr("فایل بکاپ خراب است."))


def save_upload(stream, length: int) -> Dict[str, Any]:
    """Store an uploaded backup (streamed) and validate it. ``stream`` is a binary file object."""
    if length <= 0:
        raise core.FactorError(tr("فایلی ارسال نشد."))
    if length > MAX_UPLOAD:
        raise core.FactorError(tr("حجم فایل بیش از حد مجاز است (حداکثر {} مگابایت).", MAX_UPLOAD >> 20), 413)
    directory = ensure_dir()
    part = os.path.join(directory, ".upload-{}.part".format(secrets.token_hex(4)))
    remaining = length
    try:
        fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as out:
            head = b""
            while remaining > 0:
                chunk = stream.read(min(CHUNK, remaining))
                if not chunk:
                    raise core.FactorError(tr("ارسال فایل ناقص ماند."))
                if len(head) < 16:
                    head += chunk[:16]
                out.write(chunk)
                remaining -= len(chunk)
        if head[:2] == GZIP_MAGIC:
            ext = ".db.gz"
        elif head[:16] == SQLITE_MAGIC:
            ext = ".db"
        else:
            raise core.FactorError(tr("این فایل یک بکاپ XuiFactor نیست."))
        unpacked = _unpack(part)
        try:
            meta = _check(unpacked)
        finally:
            _rm(unpacked)
        name = "uploaded-{}{}".format(time.strftime("%Y%m%d-%H%M%S"), ext)
        os.replace(part, os.path.join(directory, name))
        return {"name": name, "rules": int(meta.get("rules", 0)), "created_at": int(meta.get("created_at", 0) or 0),
                "engine_version": meta.get("engine_version", "")}
    finally:
        _rm(part)


# ----------------------------------------------------------------- restore
def restore_backup(mgr: "core.Manager", ref: str, rebase: bool = True) -> Dict[str, Any]:
    """Replace the factor tables of the current x-ui database with a backup's.

    * client ids are re-mapped by e-mail, so it works on a server whose
      ``client_traffics`` ids differ;
    * with ``rebase`` the traffic baselines are reset to the current counters so
      traffic that happened before the restore is never multiplied again."""
    path = ref if os.path.isabs(ref) and os.path.isfile(ref) else _path(ref)
    conn_ro = core.connect_ro()
    try:
        if "client_traffics" not in core.table_names(conn_ro):
            raise core.FactorError(tr("این دیتابیس، دیتابیس 3x-ui نیست (جدول client_traffics وجود ندارد)."))
    finally:
        conn_ro.close()
    unpacked = _unpack(path)
    with mgr._lock:
        safety = ""
        try:
            meta = _check(unpacked)
            mgr._systemctl("stop", settings.FACTOR_SERVICE)
            try:
                safety = create_backup(mgr, history=False, tag="pre-restore")["name"]
            except core.FactorError:
                safety = ""  # nothing to protect (tables did not exist)
            summary = _apply(unpacked, rebase)
        finally:
            _rm(unpacked)
            mgr.reader.invalidate()
            try:  # always bring the daemon back, even when the restore failed
                mgr._ensure_unit()
                mgr._systemctl("enable", "--now", settings.FACTOR_SERVICE)
            except OSError:
                pass
    summary.update({"safety_backup": safety, "backup_created_at": int(meta.get("created_at", 0) or 0),
                    "backup_engine": meta.get("engine_version", ""), "doctor": mgr.doctor()[-1200:]})
    return summary


def _apply(unpacked: str, rebase: bool) -> Dict[str, Any]:
    src = sqlite3.connect("file:{}?mode=ro".format(unpacked), uri=True)
    dst = sqlite3.connect(core.database_path(), timeout=30, isolation_level=None)
    warnings: List[str] = []
    restored: Dict[str, int] = {}
    skipped = 0
    try:
        dst.execute("PRAGMA busy_timeout = 30000")
        clients = {r[2]: (r[0], r[1], r[3], r[4]) for r in dst.execute("SELECT id, inbound_id, email, up, down FROM client_traffics")}
        inbound_ids = {r[0] for r in dst.execute("SELECT id FROM inbounds")} if "inbounds" in core.table_names(dst) else set()
        tables, indexes = _factor_objects(src)
        scope_rules = {r[0] for r in src.execute("SELECT rule_id FROM xui_factor_scopes")} if any(t == "xui_factor_scopes" for t, _ in tables) else set()
        dst.execute("BEGIN IMMEDIATE")
        try:
            for (name,) in dst.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'xui\\_factor\\_%' ESCAPE '\\'").fetchall():
                if IDENT_RE.match(name):
                    dst.execute('DROP TABLE IF EXISTS "{}"'.format(name))
            for _, sql in tables:
                dst.execute(sql)
            for sql in indexes:
                dst.execute(sql)
            for table, _ in tables:
                cur = src.execute('SELECT * FROM "{}"'.format(table))
                cols = [d[0] for d in cur.description]
                marks = ",".join("?" * len(cols))
                idx = {c: i for i, c in enumerate(cols)}
                total = 0
                while True:
                    rows = cur.fetchmany(BATCH)
                    if not rows:
                        break
                    out = []
                    for row in rows:
                        row = list(row)
                        if table in ("xui_factor_rule_clients", "xui_factor_overrides", "xui_factor_excludes"):
                            found = clients.get(row[idx["email"]])
                            if found is None:
                                skipped += 1
                                continue
                            row[idx["traffic_id"]], row[idx["inbound_id"]] = found[0], found[1]
                            if table == "xui_factor_rule_clients" and rebase:
                                row[idx["last_up"]], row[idx["last_down"]] = found[2], found[3]
                                row[idx["rem_up"]] = row[idx["rem_down"]] = 0
                        elif table == "xui_factor_rules" and row[idx["id"]] not in scope_rules:
                            found = clients.get(row[idx["email"]])
                            if found is not None:
                                row[idx["inbound_id"]] = found[1]
                        elif table == "xui_factor_scopes" and row[idx["inbound_id"]] is not None and row[idx["inbound_id"]] not in inbound_ids:
                            warnings.append(tr("اینباند {} در این سرور وجود ندارد؛ ضریب آن اعمال نمی‌شود تا اینباند ساخته شود.", row[idx["inbound_id"]]))
                        out.append(tuple(row))
                    dst.executemany('INSERT OR IGNORE INTO "{}" VALUES ({})'.format(table, marks), out)
                    total += len(out)
                restored[table] = total
            if "xfbackup_sequence" in {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
                for name, seq in src.execute("SELECT name, seq FROM xfbackup_sequence").fetchall():
                    if IDENT_RE.match(name):
                        if dst.execute("UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name = ?", (seq, name)).rowcount == 0:
                            dst.execute("INSERT INTO sqlite_sequence(name, seq) VALUES (?, ?)", (name, seq))
            dst.execute("COMMIT")
        except Exception:
            dst.execute("ROLLBACK")
            raise
    except sqlite3.Error as exc:
        raise core.FactorError(tr("بازیابی ناموفق بود و دیتابیس بدون تغییر ماند: {}", exc), 500)
    finally:
        src.close()
        dst.close()
    if skipped:
        warnings.append(tr("{} رکورد مربوط به کلاینت‌هایی که در این سرور نیستند نادیده گرفته شد.", skipped))
    return {"restored": restored, "skipped_missing_clients": skipped, "warnings": sorted(set(warnings)), "rebased": rebase}

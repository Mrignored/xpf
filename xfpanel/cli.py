"""Command line front end exposing every panel feature (used by the ``xfp`` script)."""
from __future__ import annotations

import argparse
import getpass
import json
import secrets
import string
import sys
import time
from typing import Any, Dict, List, Optional

from . import __version__, auth, backup, core, settings, update
from .server import validate_credentials

SOURCE_LABEL = {"single": "rule", "scope": "inbound", "override": "override", "override_idle": "override*",
                "excluded": "excluded", "none": "-"}


def human_bytes(value: Optional[int]) -> str:
    size = float(value or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return "{:.0f} {}".format(size, unit) if unit == "B" else "{:.2f} {}".format(size, unit)
        size /= 1024
    return "0 B"


def fmt_time(ts: Optional[int]) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else "-"


def table(headers: List[str], rows: List[List[Any]]) -> str:
    cells = [[str(c) for c in row] for row in rows]
    widths = [max([len(h)] + [len(r[i]) for r in cells]) for i, h in enumerate(headers)]
    line = lambda parts: "  ".join(p.ljust(widths[i]) for i, p in enumerate(parts)).rstrip()  # noqa: E731
    return "\n".join([line(headers), line(["-" * w for w in widths])] + [line(r) for r in cells])


def _print_pages(payload: Dict[str, Any]) -> None:
    print("\npage {}/{}  total {}".format(payload["page"], payload["pages"], payload["total"]))


def cmd_status(mgr: core.Manager, args: argparse.Namespace) -> None:
    data = mgr.reader.overview()
    service = mgr.service_status()
    print("XuiFactor panel {}".format(__version__))
    print("service        : {} (enabled: {})".format(service["active"], service["enabled"]))
    print("factor tables  : {}".format("ok" if data["tables"] else "missing"))
    print("inbound factors: {} active, {} paused".format(data["inbound_scopes_active"], data["inbound_scopes_paused"]))
    print("global factors : {}".format(data["global_scopes"]))
    print("client rules   : {} active, {} paused".format(data["client_rules_active"], data["client_rules_paused"]))
    print("overrides      : {}".format(data["overrides_active"]))
    print("factored users : {} of {}".format(data["factored_clients"], data["clients_total"]))
    print("applications   : {} (24h: {})".format(data["applications"], data["applications_24h"]))
    print("extra traffic  : {} (24h: {})".format(human_bytes(data["extra_bytes"]), human_bytes(data["extra_bytes_24h"])))
    print("last applied   : {}".format(fmt_time(data["last_applied_at"])))


def cmd_inbounds(mgr: core.Manager, args: argparse.Namespace) -> None:
    payload = mgr.reader.list_inbounds(args.page, args.size, args.query or "")
    print(table(["ID", "REMARK", "PROTO", "PORT", "CLIENTS", "FACTOR", "STATE", "CLIENT-RULES"],
                [[i["id"], i["remark"], i["protocol"], i["port"], i["clients"], i["factor"] or "-", i["state"] or "-",
                  i["client_rules"] + i["client_overrides"]] for i in payload["items"]]))
    _print_pages(payload)


def cmd_clients(mgr: core.Manager, args: argparse.Namespace) -> None:
    payload = mgr.reader.list_clients(args.page, args.size, args.query or "", args.inbound, args.filter)
    print(table(["EMAIL", "INBOUND", "FACTOR", "SOURCE", "STATE", "USED"],
                [[c["email"], c["inbound_id"], c["factor"] or "-", SOURCE_LABEL.get(c["source"], c["source"]),
                  c["state"] or "-", human_bytes((c["up"] or 0) + (c["down"] or 0))] for c in payload["items"]]))
    _print_pages(payload)


def cmd_events(mgr: core.Manager, args: argparse.Namespace) -> None:
    payload = mgr.reader.list_events(args.page, args.size, args.kind, args.query or "")
    print(table(["TIME", "TYPE", "RULE", "MESSAGE"],
                [[fmt_time(e["created_at"]), e["type"], e["rule_id"] or "-", e["message"][:110]] for e in payload["items"]]))
    _print_pages(payload)


def _report(result: Dict[str, Any], done: str) -> int:
    print("{}: {} ok, {} failed".format(done, result["ok"], len(result["failed"])))
    for failure in result["failed"]:
        print("  FAILED {}: {}".format(failure["item"], failure["error"]))
    return 1 if result["failed"] else 0


def run(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="xfp-cli", description="XuiFactor panel command line")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="summary of factor state")
    p = sub.add_parser("inbounds", help="list inbounds with their factor")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--size", type=int, default=25)
    p.add_argument("--query", "-q")
    p = sub.add_parser("clients", help="list clients with their effective factor")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--size", type=int, default=25)
    p.add_argument("--query", "-q")
    p.add_argument("--inbound", type=int)
    p.add_argument("--filter", default="all", choices=["all", "factored", "single", "scope", "override", "none", "excluded"])
    p = sub.add_parser("events", help="audit / review events")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--size", type=int, default=25)
    p.add_argument("--kind", default="lifecycle")
    p.add_argument("--query", "-q")

    p = sub.add_parser("set-inbound", help="set factor for whole inbound(s)")
    p.add_argument("factor")
    p.add_argument("ids", nargs="+", type=int)
    p = sub.add_parser("remove-inbound", help="remove factor of inbound(s)")
    p.add_argument("ids", nargs="+", type=int)
    p = sub.add_parser("set-client", help="set factor for a client")
    p.add_argument("email")
    p.add_argument("inbound_id", type=int, nargs="?")
    p.add_argument("factor")
    p = sub.add_parser("remove-client", help="remove factor of client(s)")
    p.add_argument("emails", nargs="+")

    sub.add_parser("clear-all", help="remove every factor")
    sub.add_parser("disable", help="pause all factors and stop the service")
    sub.add_parser("enable", help="start the service and resume factors")
    p = sub.add_parser("purge", help="drop all xui_factor_* tables and remove the service")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--keep-service", action="store_true")
    p = sub.add_parser("backup", help="create a factor backup (small, portable)")
    p.add_argument("--history", action="store_true", help="also include the traffic-applied audit history (can be large)")
    p.add_argument("--full", action="store_true", help="instead copy the WHOLE 3x-ui database (engine backup)")
    sub.add_parser("backups", help="list backups and where they are stored")
    p = sub.add_parser("restore", help="restore factor records from a backup")
    p.add_argument("name", nargs="?", help="backup file name, absolute path, or 'latest' (omit to choose from a list)")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--keep-baselines", action="store_true", help="do not reset traffic baselines to current counters")
    p = sub.add_parser("backup-delete", help="delete a backup file")
    p.add_argument("name")
    p = sub.add_parser("cleanup")
    p.add_argument("--apply", action="store_true")
    sub.add_parser("doctor")

    p = sub.add_parser("credentials", help="set panel username/password")
    p.add_argument("--username")
    p.add_argument("--password-stdin", action="store_true", help="read the new password from stdin")
    p.add_argument("--generate", action="store_true", help="generate a random password")
    p = sub.add_parser("set-port", help="change panel port")
    p.add_argument("port", type=int)
    p = sub.add_parser("set-ssl", help="record certificate paths for the panel")
    p.add_argument("--cert", default="")
    p.add_argument("--key", default="")
    p.add_argument("--domain", default="")
    p = sub.add_parser("update-check", help="look for a newer release on GitHub")
    p.add_argument("--json", action="store_true")
    p.add_argument("--force", action="store_true", help="ignore the cache")
    p.add_argument("--repo", action="store_true", help="only print the configured owner/repo")
    p = sub.add_parser("set-repo", help="set the GitHub repository (owner/name) updates come from")
    p.add_argument("slug")
    sub.add_parser("show-config", help="print panel connection settings (no secrets)")
    sub.add_parser("serve", help="run the web panel in the foreground")

    args = parser.parse_args(argv)
    mgr = core.Manager()
    try:
        return dispatch(args, mgr)
    except core.FactorError as exc:
        print("error: {}".format(exc.message), file=sys.stderr)
        return 1


def dispatch(args: argparse.Namespace, mgr: core.Manager) -> int:  # noqa: C901 - flat command switch
    cmd = args.cmd
    if cmd == "status":
        cmd_status(mgr, args)
    elif cmd == "inbounds":
        cmd_inbounds(mgr, args)
    elif cmd == "clients":
        cmd_clients(mgr, args)
    elif cmd == "events":
        cmd_events(mgr, args)
    elif cmd == "set-inbound":
        factor = core.parse_factor(args.factor)
        return _report(mgr.bulk(args.ids, lambda i: mgr.set_inbound(i, factor)), "inbound factor set")
    elif cmd == "remove-inbound":
        return _report(mgr.bulk(args.ids, mgr.remove_inbound), "inbound factor removed")
    elif cmd == "set-client":
        result = mgr.set_client(args.email, args.inbound_id, args.factor)
        print("client {} (inbound {}) factor {} via {}".format(result["email"], result["inbound_id"], result["factor"], result["mode"]))
    elif cmd == "remove-client":
        return _report(mgr.bulk(args.emails, mgr.remove_client), "client factor removed")
    elif cmd == "clear-all":
        result = mgr.clear_all()
        print("all factors cleared (overrides removed: {})".format(result["overrides_removed"]))
        for warning in result["warnings"]:
            print("  warning: " + warning)
    elif cmd == "disable":
        mgr.disable_factor()
        print("factor disabled: rules paused and service stopped")
    elif cmd == "enable":
        mgr.enable_factor()
        print("factor enabled: service started and rules resumed")
    elif cmd == "purge":
        if not args.yes:
            if input("This drops ALL XuiFactor data from the x-ui database. Type DELETE to continue: ").strip() != "DELETE":
                print("aborted")
                return 1
        result = mgr.purge(not args.keep_service)
        print("dropped tables: {}".format(", ".join(result["dropped"]) or "none"))
        print("service removed: {}".format(result["service_removed"]))
        if result["backup"]:
            print(result["backup"])
    elif cmd == "backup":
        if args.full:
            result = backup.create_full_backup(mgr)
            print("Full database backup saved to: {}".format(result["path"]))
        else:
            result = backup.create_backup(mgr, history=args.history)
            print("Factor backup saved to: {}".format(result["path"]))
            print("Size: {}   rules: {}   clients tracked: {}".format(human_bytes(result["size"]), result["tables"].get("xui_factor_rules", 0),
                                                                   result["tables"].get("xui_factor_rule_clients", 0)))
            print("Restore with: xfp restore {}".format(result["name"]))
    elif cmd == "backups":
        data = backup.list_backups()
        print("Factor backups directory : {}".format(data["factor_dir"]))
        print("Full DB backups directory: {}\n".format(data["full_dir"]))
        if data["items"]:
            print(table(["#", "KIND", "SIZE", "DATE", "FILE"], [[i + 1, b["kind"], human_bytes(b["size"]), fmt_time(b["mtime"]), b["name"]]
                                                                for i, b in enumerate(data["items"])]))
        else:
            print("(no backups yet)")
    elif cmd == "restore":
        return restore_command(mgr, args)
    elif cmd == "backup-delete":
        backup.delete_backup(args.name)
        print("deleted {}".format(args.name))
    elif cmd == "cleanup":
        print(mgr.cleanup(dry_run=not args.apply))
    elif cmd == "doctor":
        print(mgr.doctor())
    elif cmd == "credentials":
        return set_credentials(args)
    elif cmd == "set-port":
        if not 1 <= args.port <= 65535:
            raise core.FactorError("port must be between 1 and 65535")
        cfg = settings.load_panel_config()
        cfg["port"] = args.port
        settings.save_panel_config(settings.ensure_secret(cfg))
        print("panel port set to {} (restart the panel service to apply)".format(args.port))
    elif cmd == "set-ssl":
        cfg = settings.load_panel_config()
        cfg.update({"cert": args.cert, "key": args.key, "ssl_domain": args.domain})
        settings.save_panel_config(settings.ensure_secret(cfg))
    elif cmd == "update-check":
        if args.repo:
            print(update.repo())
            return 0
        info = update.CHECKER.check(force=args.force)
        if args.json:
            print(json.dumps(info, ensure_ascii=False))
        elif info["error"]:
            print("error: " + info["error"], file=sys.stderr)
            return 1
        else:
            print("installed: {}   latest: {}   {}".format(info["current"], info["latest"],
                                                          "UPDATE AVAILABLE" if info["newer"] else "up to date"))
    elif cmd == "set-repo":
        if not update.SLUG_RE.match(args.slug) or args.slug == update.PLACEHOLDER:
            raise core.FactorError("use the form owner/repo, e.g. myname/xui-factor-panel")
        with open(update.repo_file(), "w", encoding="utf-8") as fh:
            fh.write(args.slug + "\n")
        print("update source set to https://github.com/{}".format(args.slug))
    elif cmd == "show-config":
        cfg = settings.load_panel_config()
        print(json.dumps({k: cfg.get(k) for k in ("host", "port", "username", "cert", "key", "ssl_domain")}, indent=2))
    elif cmd == "serve":
        from .server import serve
        serve()
    return 0


def restore_command(mgr: core.Manager, args: argparse.Namespace) -> int:
    data = backup.list_backups()
    factor = [b for b in data["items"] if b["kind"] == "factor"]
    ref = args.name
    if not ref:
        if not factor:
            print("No factor backups in {}".format(data["factor_dir"]))
            return 1
        print("Backups are stored in: {}\n".format(data["factor_dir"]))
        print(table(["#", "SIZE", "DATE", "FILE"], [[i + 1, human_bytes(b["size"]), fmt_time(b["mtime"]), b["name"]] for i, b in enumerate(factor)]))
        choice = input("\nNumber to restore (or a full path, empty = cancel): ").strip()
        if not choice:
            return 1
        ref = factor[int(choice) - 1]["name"] if choice.isdigit() and 0 < int(choice) <= len(factor) else choice
    elif ref == "latest":
        if not factor:
            raise core.FactorError("no factor backups found in {}".format(data["factor_dir"]))
        ref = factor[0]["name"]
    if not args.yes and input("Replace ALL current factor records with '{}'? Type YES: ".format(ref)).strip() != "YES":
        print("aborted")
        return 1
    result = backup.restore_backup(mgr, ref, rebase=not args.keep_baselines)
    print("Restored: {}".format(", ".join("{}={}".format(k.replace("xui_factor_", ""), v) for k, v in result["restored"].items())))
    if result["safety_backup"]:
        print("Previous state saved as: {}/{}".format(data["factor_dir"], result["safety_backup"]))
    for warning in result["warnings"]:
        print("warning: " + warning)
    print("Traffic baselines were {}.".format("reset to current counters" if result["rebased"] else "kept from the backup"))
    return 0


def set_credentials(args: argparse.Namespace) -> int:
    cfg = settings.load_panel_config()
    username = args.username or cfg.get("username") or "admin"
    password = None  # type: Optional[str]
    if args.generate:
        alphabet = string.ascii_letters + string.digits
        password = "".join(secrets.choice(alphabet) for _ in range(14))
    elif args.password_stdin:
        password = sys.stdin.readline().rstrip("\n")
    elif sys.stdin.isatty():
        password = getpass.getpass("New password (empty = keep current): ") or None
        if password and getpass.getpass("Repeat password: ") != password:
            raise core.FactorError("passwords do not match")
    if password is None and not cfg.get("password_hash"):
        raise core.FactorError("a password is required for the first setup")
    validate_credentials(username, password)
    cfg["username"] = username
    if password:
        cfg["password_hash"] = auth.hash_password(password)
    cfg["secret"] = ""  # rotate: logs everyone out
    settings.save_panel_config(settings.ensure_secret(cfg))
    print("username: {}".format(username))
    if args.generate:
        print("password: {}".format(password))
    else:
        print("password: {}".format("updated" if password else "unchanged"))
    return 0

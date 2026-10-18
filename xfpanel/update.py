"""Update checks against the project's GitHub releases (standard library only).

Nothing here touches the disk or the network on its own: the panel asks only when the
operator opens the dashboard (cached for hours) or presses the check button."""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

from . import __version__, core
from .i18n import tr

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLACEHOLDER = "OWNER/REPO"
SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$")
VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")
CACHE_TTL = 6 * 3600
MIN_FORCE_INTERVAL = 15
MAX_RESPONSE = 2 << 20
UPDATE_LOG = os.environ.get("XFP_UPDATE_LOG", "/var/log/xfp-update.log")


def repo_file() -> str:
    return os.environ.get("XFP_REPO_FILE") or os.path.join(APP_DIR, "REPO")


def repo() -> str:
    """``owner/name`` of the GitHub repository updates are published in ('' = not configured)."""
    slug = os.environ.get("XFP_REPO", "").strip()
    if not slug:
        try:
            with open(repo_file(), "r", encoding="utf-8") as fh:
                slug = fh.read().strip()
        except OSError:
            slug = ""
    return slug if slug != PLACEHOLDER and SLUG_RE.match(slug) else ""


def parse_version(text: str) -> Optional[Tuple[int, int, int, int, str]]:
    """Sort key for ``v1.2.3`` / ``1.2.3-beta``; a pre-release sorts below its release."""
    match = VERSION_RE.match((text or "").strip())
    if not match:
        return None
    major, minor, patch, pre = match.group(1), match.group(2), match.group(3), match.group(4)
    return (int(major), int(minor), int(patch), 0 if pre else 1, pre or "")


def is_newer(candidate: str, current: str) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    return a is not None and b is not None and a > b


def valid_tag(tag: str) -> bool:
    return bool(tag) and len(tag) < 64 and tag == tag.strip() and parse_version(tag) is not None


def fetch_json(url: str) -> Any:
    if not url.startswith("https://api.github.com/repos/"):
        raise core.FactorError(tr("آدرس بروزرسانی نامعتبر است."))
    request = urllib.request.Request(url, headers={"User-Agent": "xui-factor-panel/" + __version__,
                                                   "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=8) as resp:  # noqa: S310 - fixed https host checked above
            return json.loads(resp.read(MAX_RESPONSE).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise core.FactorError(tr("ارتباط با GitHub برقرار نشد: {}", "HTTP {}".format(exc.code)))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise core.FactorError(tr("ارتباط با GitHub برقرار نشد: {}", getattr(exc, "reason", exc)))


def latest_release(slug: str) -> Optional[Dict[str, Any]]:
    """Newest published release, falling back to the highest version tag."""
    data = fetch_json("https://api.github.com/repos/{}/releases/latest".format(slug))
    if isinstance(data, dict) and valid_tag(str(data.get("tag_name", ""))):
        return {"tag": data["tag_name"], "url": data.get("html_url", ""), "notes": str(data.get("body") or "")[:2000]}
    tags = fetch_json("https://api.github.com/repos/{}/tags?per_page=50".format(slug))
    names = [str(t.get("name", "")) for t in tags or [] if isinstance(t, dict) and valid_tag(str(t.get("name", "")))]
    if not names:
        return None
    best = max(names, key=lambda n: parse_version(n))  # type: ignore[arg-type,return-value]
    return {"tag": best, "url": "https://github.com/{}/releases/tag/{}".format(slug, best), "notes": ""}


class Checker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._result: Optional[Dict[str, Any]] = None
        self._at = 0.0

    def check(self, force: bool = False) -> Dict[str, Any]:
        with self._lock:
            age = time.monotonic() - self._at
            if self._result is not None and (age < MIN_FORCE_INTERVAL or (not force and age < CACHE_TTL)):
                return dict(self._result, cached=True)
            result = self._fresh()
            if not result["error"]:
                self._result, self._at = result, time.monotonic()
            return result

    @staticmethod
    def _fresh() -> Dict[str, Any]:
        out: Dict[str, Any] = {"current": __version__, "latest": "", "tag": "", "newer": False, "url": "", "notes": "",
                               "configured": False, "error": "", "checked_at": int(time.time()), "cached": False,
                               "repo": repo(), "log": UPDATE_LOG}
        if not out["repo"]:
            out["error"] = tr("منبع بروزرسانی (مخزن GitHub) تنظیم نشده است. با «xfp set-repo کاربر/مخزن» تنظیم کنید.")
            return out
        out["configured"] = True
        try:
            release = latest_release(out["repo"])
        except core.FactorError as exc:
            out["error"] = exc.message
            return out
        if release is None:
            out["error"] = tr("هنوز نسخه‌ای (Release یا Tag) در مخزن منتشر نشده است.")
            return out
        out.update(tag=release["tag"], latest=release["tag"].lstrip("v"), url=release["url"], notes=release["notes"],
                   newer=is_newer(release["tag"], __version__))
        return out


CHECKER = Checker()

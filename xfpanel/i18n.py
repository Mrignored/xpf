"""Tiny gettext-style translation layer.

The Persian text itself is the message key, so the code stays readable and Persian
needs no table. English lives in :mod:`xfpanel.messages_en`. The active language is
per thread: the web server sets it from each request, the command line defaults to
English (override with ``XFP_LANG=fa``)."""
from __future__ import annotations

import os
import threading
from typing import Any

from .messages_en import EN

_local = threading.local()
SUPPORTED = ("fa", "en")


def default_lang() -> str:
    value = os.environ.get("XFP_LANG", "en").lower()
    return value if value in SUPPORTED else "en"


def set_lang(value: Any) -> None:
    value = str(value or "").lower()
    _local.lang = value if value in SUPPORTED else None


def get_lang() -> str:
    return getattr(_local, "lang", None) or default_lang()


def tr(text: str, *args: Any) -> str:
    """Translate ``text`` (Persian source) to the active language and apply ``str.format``."""
    if get_lang() == "en":
        text = EN.get(text, text)
    return text.format(*args) if args else text

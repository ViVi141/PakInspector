# -*- coding: utf-8 -*-
"""Load UI strings from JSON locale files (no extra dependencies)."""

from __future__ import annotations

import json
import locale
from importlib import resources
from typing import Any, Dict


SUPPORTED_LANGS = ("en", "zh_CN")


def default_language() -> str:
    """Pick en or zh_CN from OS locale; unknown locales fall back to en."""
    # Windows CPython has no locale.LC_MESSAGES (POSIX-only).
    lc_messages = getattr(locale, "LC_MESSAGES", None)
    if lc_messages is not None:
        try:
            pair = locale.getlocale(lc_messages)
            if pair and pair[0]:
                code = pair[0].lower()
                if code.startswith("zh"):
                    return "zh_CN"
        except (TypeError, ValueError, OSError, AttributeError):
            pass
    try:
        loc = locale.getdefaultlocale()
        if loc and loc[0]:
            code = loc[0].lower()
            if code.startswith("zh"):
                return "zh_CN"
    except (TypeError, ValueError, OSError):
        pass
    return "en"


def _load_table(lang: str) -> Dict[str, str]:
    if lang not in SUPPORTED_LANGS:
        lang = "en"
    name = "%s.json" % lang
    try:
        path = resources.files("pakinspector").joinpath("locales", name)
        raw = path.read_bytes()
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        return {}
    out: Dict[str, str] = {}
    for key, value in data.items():
        if isinstance(key, str) and isinstance(value, str):
            out[key] = value
    return out


def _merge_fallback(primary: Dict[str, str], fallback: Dict[str, str]) -> Dict[str, str]:
    merged = dict(fallback)
    merged.update(primary)
    return merged


class I18n:
    """Runtime string table with optional format kwargs."""

    def __init__(self, lang: str) -> None:
        self._lang = lang if lang in SUPPORTED_LANGS else "en"
        self._en = _load_table("en")
        primary = _load_table(self._lang)
        self._strings = _merge_fallback(primary, self._en)

    @property
    def lang(self) -> str:
        return self._lang

    def set_language(self, lang: str) -> None:
        """Switch UI language; reloads merged tables."""
        if lang not in SUPPORTED_LANGS:
            lang = "en"
        self._lang = lang
        self._en = _load_table("en")
        primary = _load_table(self._lang)
        self._strings = _merge_fallback(primary, self._en)

    def tr(self, key: str, **kwargs: Any) -> str:
        template = self._strings.get(key)
        if template is None:
            template = self._en.get(key, key)
        if kwargs:
            return template.format(**kwargs)
        return template

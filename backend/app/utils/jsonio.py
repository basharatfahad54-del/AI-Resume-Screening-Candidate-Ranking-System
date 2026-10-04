"""Tolerant JSON helpers for TEXT-backed JSON columns.

The schema stores structured fields (skill lists, section maps, explanations)
as ``Text`` rather than native JSON types. That keeps the exact same schema
working on SQLite and PostgreSQL without dialect-specific column types, but it
means every read has to survive a hand-edited or partially written value.

These helpers never raise: a corrupt value degrades to the supplied default so
one bad row cannot break a whole listing endpoint.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["dumps", "loads_dict", "loads_list", "loads_raw"]


def dumps(value: Any) -> str:
    """Serialise to compact JSON, never raising."""
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return "[]" if isinstance(value, list) else "{}"


def loads_raw(value: str | None, default: Any = None) -> Any:
    if value is None or value == "":
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def loads_list(value: str | list | None, default: list | None = None) -> list:
    """Read a JSON array column, coercing a scalar into a single-item list."""
    fallback = [] if default is None else list(default)
    if isinstance(value, list):
        return value
    if value is None or value == "":
        return fallback
    parsed = loads_raw(value)
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, (str, int, float)):
        return [parsed]
    if isinstance(parsed, dict):
        return [parsed]
    return fallback


def loads_dict(value: str | dict | None, default: dict | None = None) -> dict:
    fallback = {} if default is None else dict(default)
    if isinstance(value, dict):
        return value
    if value is None or value == "":
        return fallback
    parsed = loads_raw(value)
    if isinstance(parsed, dict):
        return parsed
    return fallback

"""Shared schema helpers.

The JSON-shaped columns (``required_skills``, ``employment_history``, ...) are
stored as ``Text`` holding a JSON document so the schema stays portable across
SQLite and Postgres. That means a response model validated straight from an ORM
object receives a JSON *string* where it expects a list.

Rather than hand-decoding every field in every router, these validators accept
both shapes. The rest of the codebase can keep using
``Model.model_validate(orm_object)`` and stay readable.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from pydantic import BeforeValidator


def coerce_str_list(value: Any) -> Any:
    """Accept a list, a JSON array string, or ``None``."""
    if value is None or isinstance(value, list):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                # Not JSON after all - treat it as a single comma-separated value
                # rather than failing the whole response.
                return [item.strip() for item in text.split(",") if item.strip()]
            return [str(item) for item in parsed] if isinstance(parsed, list) else [str(parsed)]
        return [value]
    return value


def coerce_obj_list(value: Any) -> Any:
    """Accept a list of dicts or a JSON array string of dicts."""
    if value is None or isinstance(value, list):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return value


def coerce_str(value: Any) -> Any:
    """Accept a dict or a JSON object string (``{}`` when empty or corrupt)."""
    if value is None or isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return value


#: ``list[str]`` column stored as JSON text.
StrList = Annotated[list[str], BeforeValidator(coerce_str_list)]
#: ``list[dict]`` column stored as JSON text.
ObjList = Annotated[list[dict[str, Any]], BeforeValidator(coerce_obj_list)]
#: ``dict`` column stored as JSON text.
ObjDict = Annotated[dict[str, Any], BeforeValidator(coerce_str)]


__all__ = ["ObjDict", "ObjList", "StrList", "coerce_obj_list", "coerce_str", "coerce_str_list"]
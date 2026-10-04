"""Optional LLM integration for structured extraction and the assistant.

This module is intentionally *thin and defensive*:

* It is a no-op unless ``LLM_PROVIDER=openai`` and an API key is configured,
  so the whole application runs offline with heuristic extraction.
* Any text sent to a third party is PII-redacted first (``app.utils.text``),
  because resumes contain phone numbers and home addresses.
* Every call is time-boxed, retried at most once, and returns ``None`` on
  failure - an LLM outage degrades quality, it never breaks screening.

The provider is OpenAI-compatible (chat completions), so OpenAI, Azure OpenAI,
Ollama and vLLM all work by changing ``OPENAI_BASE_URL``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.utils.text import redact_pii

logger = get_logger(__name__)

_TIMEOUT_SECONDS = 25.0
_MAX_OUTPUT_TOKENS = 1200
_JSON_BLOCK = re.compile(r"```(?:json)?\s*(?P<body>[\[{].*?[\]}])\s*```", re.DOTALL)


@dataclass(frozen=True, slots=True)
class LLMStatus:
    enabled: bool
    provider: str
    model: str
    detail: str


def llm_status() -> LLMStatus:
    if settings.llm_provider == "none":
        return LLMStatus(False, "none", settings.llm_model, "LLM disabled; using heuristic extraction")
    if not settings.openai_api_key:
        return LLMStatus(False, settings.llm_provider, settings.llm_model, "OPENAI_API_KEY is not set")
    return LLMStatus(True, settings.llm_provider, settings.llm_model, "enabled")


def is_enabled() -> bool:
    return llm_status().enabled


def _client():
    try:
        from openai import AsyncOpenAI
    except ImportError:  # pragma: no cover - optional dependency
        logger.warning("LLM_PROVIDER is set but the 'openai' package is not installed")
        return None
    return AsyncOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        timeout=_TIMEOUT_SECONDS,
        max_retries=1,
    )


async def complete_json(
    system_prompt: str,
    user_payload: dict[str, Any],
    *,
    max_tokens: int = _MAX_OUTPUT_TOKENS,
) -> dict[str, Any] | list[Any] | None:
    """Ask the model for a JSON object/array. Returns ``None`` on any failure."""
    if not is_enabled():
        return None
    client = _client()
    if client is None:
        return None

    # Redact before egress: never send phone numbers or emails to a vendor.
    safe_payload = _redact_payload(user_payload)
    try:
        response = await client.chat.completions.create(
            model=settings.llm_model,
            temperature=0,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(safe_payload, ensure_ascii=False)[:24000]},
            ],
        )
    except Exception as exc:  # noqa: BLE001 - degrade gracefully
        logger.warning("LLM call failed (%s); falling back to heuristics", exc)
        return None

    content = response.choices[0].message.content if response.choices else ""
    return _parse_json(content)


def _redact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, str):
            # Contact details are extracted locally with regex; the model does
            # not need them, so they never leave the deployment.
            clean[key] = value if key in {"skills_hint"} else redact_pii(value, keep_email=False)
        elif isinstance(value, dict):
            clean[key] = _redact_payload(value)
        elif isinstance(value, list):
            clean[key] = [_redact_payload(item) if isinstance(item, dict) else item for item in value]
        else:
            clean[key] = value
    return clean


def _parse_json(content: str | None) -> dict[str, Any] | list[Any] | None:
    if not content:
        return None
    text = content.strip()
    block = _JSON_BLOCK.search(text)
    if block:
        text = block.group("body")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Models sometimes wrap JSON in prose; recover the outermost object/array.
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    logger.warning("LLM response was not valid JSON")
    return None


def coerce_str_list(value: Any, *, limit: int = 40) -> list[str]:
    """Tolerant coercion of an LLM field into a clean list of strings."""
    if value is None:
        return []
    if isinstance(value, str):
        items = re.split(r"[,;\n]|\s{2,}", value)
    elif isinstance(value, (list, tuple)):
        items = [str(item) for item in value]
    else:
        return []
    seen: dict[str, str] = {}
    for item in items:
        cleaned = re.sub(r"^[\s\-\*\u2022\d\.\)]+", "", str(item)).strip(" ,;\u2022\"'")
        if 1 < len(cleaned) <= 120:
            seen.setdefault(cleaned.lower(), cleaned)
    return list(seen.values())[:limit]

"""Text normalisation helpers shared by the NLP pipeline and the API layer."""

from __future__ import annotations

import re
import unicodedata

__all__ = [
    "collapse_whitespace",
    "mask_email",
    "redact_pii",
    "snippet",
    "titlecase_keywords",
    "truncate",
]

_WHITESPACE = re.compile(r"[ \t\x0b\f\r]+")
_BLANK_LINES = re.compile(r"\n{3,}")
_BULLET_PREFIX = re.compile(r"^\s*(?:[-*\u2022\u25cf\u25aa\u2013\u2014>]+|\d+[.)])\s*")
_SOFT_HYPHEN = re.compile(r"(\w)-\n(\w)")

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)[\s.-]?)?\d{3,4}[\s.-]?\d{3,4}(?:[\s.-]?\d{2,4})?")
_URL = re.compile(r"https?://\S+|www\.\S+")

_ACRONYM_SMALL = {
    "ai": "AI",
    "ml": "ML",
    "dl": "DL",
    "nlp": "NLP",
    "llm": "LLM",
    "llms": "LLMs",
    "api": "API",
    "apis": "APIs",
    "sql": "SQL",
    "aws": "AWS",
    "gcp": "GCP",
    "ui": "UI",
    "ux": "UX",
    "qa": "QA",
    "hr": "HR",
    "bi": "BI",
    "etl": "ETL",
    "ci": "CI",
    "cd": "CD",
    "os": "OS",
    "db": "DB",
    "js": "JS",
    "ts": "TS",
    "c": "C",
    "r": "R",
    "go": "Go",
    "k8s": "K8s",
    "gpu": "GPU",
    "nn": "NN",
    "pr": "PR",
    "vc": "VC",
}


def collapse_whitespace(text: str) -> str:
    """Trim trailing spaces, join wrapped lines and cap blank runs."""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WHITESPACE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANK_LINES.sub("\n\n", text).strip()


def snippet(text: str, start: int, end: int, *, padding: int = 60, limit: int = 220) -> str:
    """Extract a readable window around ``[start, end)`` of ``text``."""
    if not text:
        return ""
    left = max(0, start - padding)
    right = min(len(text), end + padding)
    window = text[left:right]
    window = _BULLET_PREFIX.sub("", window.splitlines()[0] if left else window)
    window = " ".join(window.split())
    if left > 0:
        window = "..." + window.lstrip()
    if right < len(text):
        window = window.rstrip() + "..."
    return truncate(window, limit)


def truncate(text: str, limit: int, *, suffix: str = "...") -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return text[: max(0, limit - len(suffix))].rstrip() + suffix


def dehyphenate(text: str) -> str:
    """Re-join words split across lines by a PDF hyphen, e.g. ``machine-\\nlearn``."""
    return _SOFT_HYPHEN.sub(r"\1\2", text or "")


def strip_bullets(line: str) -> str:
    return _BULLET_PREFIX.sub("", line or "").strip()


def titlecase_keywords(text: str) -> str:
    """Title-case a comma separated skill list, preserving known acronyms."""
    cleaned = collapse_whitespace(text)
    if not cleaned:
        return ""
    parts = [part.strip() for part in re.split(r"[,;|/\n]|(?<=\w)\s{2,}", cleaned) if part.strip()]
    rendered: list[str] = []
    for part in parts:
        lowered = part.lower()
        if lowered in _ACRONYM_SMALL:
            rendered.append(_ACRONYM_SMALL[lowered])
            continue
        words = []
        for word in part.split():
            lowered_word = word.lower()
            if lowered_word in _ACRONYM_SMALL:
                words.append(_ACRONYM_SMALL[lowered_word])
            elif "-" in word:
                words.append("-".join(w.capitalize() for w in word.split("-") if w))
            elif word.isupper() and len(word) <= 5:
                words.append(word)
            else:
                words.append(lowered_word.capitalize())
        rendered.append(" ".join(words))
    return ", ".join(rendered)


def mask_email(email: str | None) -> str:
    """Mask an email for logs: ``ahmed.khan@example.com`` -> ``a***@example.com``."""
    if not email or "@" not in email:
        return "***"
    local, _, domain = email.partition("@")
    head = local[:1] if local else ""
    return f"{head}***@{domain}"


def redact_pii(text: str, *, keep_email: bool = False) -> str:
    """Strip obvious PII from free text.

    Used when text is sent to an external LLM provider or written to logs, so a
    vendor request never carries a phone number or home address verbatim.
    """
    if not text:
        return ""
    redacted = _URL.sub("[url]", text)
    if not keep_email:
        redacted = _EMAIL.sub("[email]", redacted)
    redacted = _PHONE.sub("[phone]", redacted)
    return collapse_whitespace(redacted)


def normalize_unicode(text: str) -> str:
    """NFKC-normalise and strip zero-width characters common in PDF output."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    return text.replace("\u200b", "").replace("\ufeff", "").replace("\u00ad", "")

"""Document text cleaning.

PDF and DOCX extraction produces noisy text: broken ligatures, hard wrapped
lines, running headers/footers, page numbers, bullet glyphs and column bleed.
Cleaning here is deliberately conservative - it removes artefacts that are
*never* semantic content and never rewrites the candidate's own words, because
the cleaned string is also stored as evidence and shown to recruiters.
"""

from __future__ import annotations

import re
import unicodedata

from app.nlp.skill_taxonomy import normalize_text

__all__ = [
    "clean_document_text",
    "clean_lines",
    "dehyphenate",
    "first_meaningful_line",
    "is_email_like",
    "looks_like_contact_line",
    "split_sentences",
    "strip_bullets",
    "strip_running_headers",
    "to_match_text",
]

_LIGATURES = {
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
    "\ufb05": "st",
    "\ufb06": "st",
}

_BULLET = re.compile(r"^[\s\u00a0]*[\u2022\u25cf\u25aa\u25e6\u2043\u2219\u00b7\-\u2013\u2014>*]+[\s\u00a0]*")
_PAGE_NOISE = re.compile(
    r"^(?:page\s+)?\d+\s*(?:of\s*\d+)?\.?$",
    re.IGNORECASE,
)
_EMAIL_NOISE = re.compile(r"^\S+@\S+$")
_URL_NOISE = re.compile(r"^https?://\S+$", re.IGNORECASE)
_CONTACT_BAR = re.compile(r"^(?:[\w.+-]+@[\w.-]+|(?:\+?\d[\d\s().-]{7,}\d)|(?:https?://|www\.)\S+)$")

# Lines that repeat on every page of a resume carry no signal.
_RUNNING_HEADER_HINT = re.compile(
    r"(?:curriculum\s*vitae|résumé|resume\s+of|page\s+\d+|confidential)",
    re.IGNORECASE,
)

_MULTISPACE = re.compile(r"[ \t\x0b\f\r]+")
_SOFT_HYPHEN_BREAK = re.compile(r"(\w)[-‐‑‒–]\n(\w)")
_MULTI_NEWLINE = re.compile(r"\n{3,}")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _apply_ligatures(text: str) -> str:
    for ligature, replacement in _LIGATURES.items():
        text = text.replace(ligature, replacement)
    return text


def dehyphenate(text: str) -> str:
    """Re-join words split across a line break by a hyphen.

    PDF extraction frequently turns ``machine-`` + newline + ``learning`` into
    two tokens, which breaks skill detection at exactly the wrong place.
    """
    return _SOFT_HYPHEN_BREAK.sub(r"\1\2", text or "")


def strip_bullets(line: str) -> str:
    """Remove a leading list marker (``-``, ``*``, ``1)``) from a line."""
    return _BULLET.sub("", line or "").strip()


def strip_running_headers(lines: list[str]) -> list[str]:
    """Drop artefacts that repeat across pages of a document."""
    if not lines:
        return []
    total = len(lines)
    seen: dict[str, int] = {}
    for line in lines:
        key = normalize_text(line)
        if key and len(key) < 90:
            seen[key] = seen.get(key, 0) + 1

    kept: list[str] = []
    for index, line in enumerate(lines):
        key = normalize_text(line)
        stripped = line.strip()
        if not stripped:
            kept.append("")
            continue
        if _PAGE_NOISE.match(stripped):
            continue
        if _URL_NOISE.match(stripped):
            continue
        # A short line repeated on many pages is a header/footer.
        if len(key) < 60 and seen.get(key, 0) >= 3 and total > 25:
            continue
        if seen.get(key, 0) == 1 and _RUNNING_HEADER_HINT.search(stripped) and index in (0, 1, total - 1):
            continue
        kept.append(stripped)
    return kept


def clean_lines(raw: str) -> list[str]:
    """Normalise a raw extracted document into clean, un-prefixed lines."""
    if not raw:
        return []

    text = unicodedata.normalize("NFKC", raw)
    text = _apply_ligatures(text)
    text = text.replace("\u200b", "").replace("\ufeff", "").replace("\u00ad", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _SOFT_HYPHEN_BREAK.sub(r"\1\2", text)
    text = _MULTISPACE.sub(" ", text)

    raw_lines = [_BULLET.sub("", line).rstrip() for line in text.split("\n")]
    cleaned = strip_running_headers(raw_lines)

    # Drop trailing whitespace-only runs but keep single blank separators.
    result: list[str] = []
    blanks = 0
    for line in cleaned:
        if line.strip():
            blanks = 0
            result.append(line.strip())
        else:
            blanks += 1
            if blanks == 1:
                result.append("")
    return _MULTI_NEWLINE.sub("\n\n", "\n".join(result)).strip().split("\n")


def clean_document_text(raw: str) -> str:
    """Full cleaning pass producing the canonical text stored on a record."""
    return "\n".join(clean_lines(raw)).strip()


def to_match_text(text: str) -> str:
    """Lower-cased, accent-free, punctuation-light form used for similarity.

    The taxonomy's ``normalize_text`` is reused so the matching view and the
    skill index always agree on tokenisation.
    """
    if not text:
        return ""
    return normalize_text(text)


def split_sentences(text: str, *, min_length: int = 25) -> list[str]:
    """Split normalised text into sentence-ish units for evidence snippets."""
    if not text:
        return []
    sentences: list[str] = []
    for chunk in text.split("\n"):
        chunk = chunk.strip()
        if not chunk:
            continue
        for sentence in _SENTENCE_SPLIT.split(chunk):
            sentence = sentence.strip(" -*\u2022\t")
            if len(sentence) >= min_length:
                sentences.append(sentence)
    return sentences


def looks_like_contact_line(line: str) -> bool:
    """True when a line is a pure contact strip (email / phone / URL only)."""
    stripped = line.strip()
    if not stripped or len(stripped) > 200:
        return False
    parts = [part.strip() for part in re.split(r"\s*[|•·]\s*|\s{3,}", stripped) if part.strip()]
    return bool(parts) and all(_CONTACT_BAR.match(part) for part in parts)


def is_email_like(value: str) -> bool:
    return bool(_EMAIL_NOISE.match(value.strip()))


def first_meaningful_line(lines: list[str], *, skip: int = 0) -> str:
    for line in lines[skip:]:
        candidate = line.strip()
        if candidate and not _PAGE_NOISE.match(candidate):
            return candidate
    return ""

"""Skill and certification extraction with provenance.

Two extraction channels are combined:

1. **Explicit** - a dedicated Skills section. Terms listed there are stated
   claims, so they get high confidence.
2. **Implicit** - taxonomy mentions anywhere else in the document (experience
   bullets, projects). These are *evidenced* claims, so confidence is lower and
   each one keeps the snippet it was found in.

Every extracted skill carries the evidence snippet. That is what lets the
ranking engine tell a recruiter *why* it believes someone has a skill, and it
means a parsing mistake is auditable rather than invisible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.nlp import cleaning
from app.nlp.skill_taxonomy import (
    find_skill_mentions,
    normalize_skill,
    normalize_text,
    skill_by_normalized_name,
    slugify_unknown,
)
from app.utils.text import snippet, truncate

__all__ = ["ExtractedSkill", "extract_certifications", "extract_skills"]

# Separators used inside a Skills section entry list.
_SPLIT = re.compile(r"[,;|•·/]|\s{2,}|\s+\+\s+|\s{3,}")
# A skills-section line is short; a sentence is not a skill list.
_MAX_LINE = 220
_MAX_SKILL_LEN = 60
_YEARS_NEAR_SKILL = re.compile(r"(\d{1,2}(?:\.\d)?)\s*\+?\s*(?:years?|yrs?|yr)", re.IGNORECASE)

_CERT_MARKERS = (
    "certified", "certification", "certificate", "licensed", "license", "credential",
    "associate", "professional", "diploma",
)


@dataclass(slots=True)
class ExtractedSkill:
    display_name: str
    normalized_name: str
    category: str
    confidence: float
    years_experience: float | None = None
    is_certified: bool = False
    evidence: list[str] = field(default_factory=list)
    source: str = "explicit"  # explicit | implicit | certification
    mention_count: int = 1

    def merge(self, other: "ExtractedSkill") -> "ExtractedSkill":
        """Combine two observations of the same canonical skill."""
        self.confidence = max(self.confidence, other.confidence)
        self.mention_count += other.mention_count
        self.years_experience = max(self.years_experience or 0.0, other.years_experience or 0.0) or None
        self.is_certified = self.is_certified or other.is_certified
        for item in other.evidence:
            if item not in self.evidence:
                self.evidence.append(item)
        if other.source == "explicit":
            self.source = "explicit"
        return self


def _parse_skill_entry(entry: str) -> tuple[str, float | None]:
    """Split "Python (3 years)" into ("Python", 3.0)."""
    entry = entry.strip(" -*\u2022\t[]()")
    years: float | None = None
    match = _YEARS_NEAR_SKILL.search(entry)
    if match:
        try:
            years = float(match.group(1))
        except ValueError:  # pragma: no cover
            years = None
        entry = _YEARS_NEAR_SKILL.sub("", entry).strip(" -,()")
    # Trailing proficiency noise such as "JavaScript (expert)".
    entry = re.sub(
        r"\((?:beginner|intermediate|advanced|expert|basic|proficient|fluent)[^)]*\)",
        "",
        entry,
        flags=re.IGNORECASE,
    ).strip(" -,()")
    return entry, years


def _looks_like_skill_entry(entry: str) -> bool:
    if not entry or len(entry) > _MAX_SKILL_LEN:
        return False
    tokens = entry.split()
    if len(tokens) > 5:
        return False
    if entry.endswith((".", "!", "?")) and len(tokens) > 2:
        return False
    normalized = normalize_text(entry)
    if not normalized or len(normalized) < 2:
        return False
    if re.search(r"\b(?:and|or|with|the|for)\b", normalized) and len(tokens) > 3:
        return False
    return True


def _iter_candidate_entries(text: str) -> list[str]:
    """Yield plausible skill entries from a skills-section body."""
    entries: list[str] = []
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line or len(line) > _MAX_LINE:
            continue
        # Split "Languages: Python, Java | Tools: Docker" into its parts.
        for chunk in re.split(r"(?<=[a-z0-9)\]])\s*[|]\s*", line):
            label, _, remainder = chunk.partition(":")
            pieces = [chunk] if not remainder.strip() else [remainder]
            for piece in pieces:
                for entry in _SPLIT.split(piece):
                    cleaned, _years = _parse_skill_entry(entry)
                    if _looks_like_skill_entry(cleaned):
                        entries.append(cleaned)
    return entries


def extract_skills(
    lines: list[str],
    skills_section: str = "",
    *,
    max_per_skill: int = 3,
) -> list[ExtractedSkill]:
    """Extract skills from an explicit skills section plus free text."""
    found: dict[str, ExtractedSkill] = {}

    # ---- Channel 1: explicit skills section ------------------------------- #
    for entry in _iter_candidate_entries(skills_section):
        normalized, category, display = normalize_skill(entry)
        _, years = _parse_skill_entry(entry)
        existing = found.get(normalized)
        item = ExtractedSkill(
            display_name=display,
            normalized_name=normalized,
            category=category,
            confidence=0.95,
            years_experience=years,
            evidence=[entry],
            source="explicit",
        )
        if existing:
            existing.merge(item)
        else:
            found[normalized] = item

    # ---- Channel 2: taxonomy mentions across the whole document ---------- #
    for line in lines:
        if len(line) > 600:
            line = line[:600]
        lowered = normalize_text(line)
        for surface, normalized_name, start, end in find_skill_mentions(lowered):
            definition_normalized, category, display = normalize_skill(surface)
            # A canonical slug can arrive via a "related" alias; prefer the taxonomy entry.
            canonical = normalized_name or definition_normalized
            if category == "other" and definition_normalized != "other":
                category = definition_normalized
            years_match = _YEARS_NEAR_SKILL.search(line)
            years: float | None = None
            if years_match:
                try:
                    years = float(years_match.group(1))
                except ValueError:  # pragma: no cover
                    years = None
            item = ExtractedSkill(
                display_name=display,
                normalized_name=canonical,
                category=category,
                # Longer surface forms are less likely to be an incidental word match.
                confidence=0.7 if len(surface.split()) > 1 else 0.6,
                years_experience=years,
                evidence=[snippet(line, start, end, padding=40, limit=180)],
                source="implicit",
            )
            existing = found.get(canonical)
            if existing:
                existing.merge(item)
            else:
                found[canonical] = item
            # Cap evidence volume so one repetitive resume cannot bloat the row.
            item.evidence = item.evidence[:max_per_skill]

    # Skill names carry no sentiment, but drop tokens that are clearly noise.
    noisy = re.compile(r"^(?:experience|knowledge|skills?|familiarity|proficiency|expertise|etc)$")
    result = [
        item
        for item in found.values()
        if not noisy.match(normalize_text(item.display_name)) and len(item.normalized_name) > 1
    ]
    result.sort(key=lambda item: (-item.confidence, -item.mention_count, item.display_name))
    return result


# ``[ \t]`` rather than ``\s`` on purpose: ``\s`` matches newlines, which let a
# match run from one bullet to the next and invent certifications such as
# "Machine Learning Specialty\nCertified".
_CERT_PATTERNS = (
    re.compile(r"\b((?:aws|azure|google cloud|gcp|databricks|tensorflow|microsoft|cisco|huawei|oracle|salesforce)[^,;|\n]{0,60}?(?:certified|certification|certificate|cert)\w*)", re.IGNORECASE),
    re.compile(r"\b([A-Z][A-Za-z0-9&/\.\- ]{2,60}?[ \t]+(?:Certified|Certification|Certificate)\w*)"),
    re.compile(r"\b((?:Certified[ \t]+)?(?:scrum[ \t]?master|csm|pmp|cissp|ccna|cfa|istqb|cka|ckad|tf dev)[\w \t\-]{0,30})", re.IGNORECASE),
)


def extract_certifications(lines: list[str], section_text: str = "") -> list[str]:
    """Find certification names.

    Three channels are combined: explicit regex patterns, taxonomy skills in
    the ``certification`` category (so "Azure AI Engineer" is recognised without
    needing the word "certified"), and the certifications section.
    """
    names: dict[str, str] = {}
    source = section_text.strip() or "\n".join(lines)
    blocks = [source] + [line for line in lines if _looks_like_certification_line(line)]

    for block in blocks:
        if not block or len(block) > 4000:
            continue
        for pattern in _CERT_PATTERNS:
            for match in pattern.finditer(block):
                candidate = truncate(match.group(1).strip(" ,;|-\u2022"), 120)
                if len(candidate) < 4:
                    continue
                names.setdefault(slugify_unknown(candidate), candidate)

    # A skills/certifications section listing certification-like entries.
    for line in section_text.split("\n"):
        stripped = line.strip()
        if not stripped or len(stripped) > _MAX_LINE:
            continue
        for entry in _SPLIT.split(stripped):
            cleaned, _years = _parse_skill_entry(entry)
            if cleaned and len(cleaned) <= 80 and _looks_like_certification_entry(cleaned):
                names.setdefault(slugify_unknown(cleaned), cleaned)

    # Taxonomy certifications mentioned anywhere in the document. Keyed through
    # ``slugify_unknown`` like every other channel: mixing in ``normalized_name``
    # here put two entries with identical text under different keys, so the same
    # certification was returned twice.
    by_name = skill_by_normalized_name()
    for line in lines:
        if not line or len(line) > 600:
            continue
        for _surface, normalized_name, _start, _end in find_skill_mentions(normalize_text(line)[:600]):
            definition = by_name.get(normalized_name)
            if definition and definition.category == "certification":
                names.setdefault(slugify_unknown(definition.name), definition.name)

    # Keep only the most specific name when one cert name overlaps another
    # ("AWS Certified" is redundant next to "AWS Certified Machine Learning",
    # and "Machine Learning Specialty Certified" is the same cert mis-parsed).
    ordered = sorted(names.values(), key=lambda value: (-len(value), value.casefold()))
    result: list[str] = []
    for candidate in ordered:
        lowered = normalize_text(candidate)
        # Overlap in either direction: the longer name wins, so a shorter
        # duplicate cannot survive as its own entry.
        if any(
            lowered != normalize_text(kept)
            and (lowered in normalize_text(kept) or normalize_text(kept) in lowered)
            for kept in result
        ):
            continue
        result.append(candidate)
    return result


def _looks_like_certification_line(line: str) -> bool:
    lowered = normalize_text(line)
    return any(marker in lowered for marker in _CERT_MARKERS)


def _looks_like_certification_entry(entry: str) -> bool:
    lowered = normalize_text(entry)
    if any(marker in lowered for marker in _CERT_MARKERS):
        return True
    _normalized, category, _display = normalize_skill(entry)
    return category == "certification"

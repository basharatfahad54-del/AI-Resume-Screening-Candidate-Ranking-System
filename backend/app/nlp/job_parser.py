"""Job description parsing (PRD section 6).

Recruiters paste or upload a job description; the system must turn it into a
structured requirement set. The approach is section-aware heuristics:

* "Requirements"/"Minimum qualifications" -> ``required_skills``
* "Preferred"/"Nice to have"/"Plus"          -> ``preferred_skills``
* "Certifications" sections                    -> ``certifications``
* "Responsibilities"/"What you will do"       -> ``responsibilities``
* "Soft skills"/"Competencies"                 -> ``soft_skills``

Skill terms come from the shared taxonomy, so a job asking for "ML" and a
resume claiming "Machine Learning" resolve to the same canonical skill.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.nlp import cleaning
from app.nlp.skill_extractor import extract_certifications
from app.nlp.skill_taxonomy import (
    find_skill_mentions,
    normalize_text,
    skill_by_normalized_name,
)
from app.schemas.job import JobExtractionResult
from app.utils.text import truncate

__all__ = ["ParsedJob", "parse_job_description"]

_REQUIRED_HEADINGS = (
    "requirements", "required", "minimum qualifications", "minimum requirements",
    "must have", "must have skills", "essential", "basic qualifications", "skills required",
    "what you need", "you have", "qualifications",
)
_PREFERRED_HEADINGS = (
    "preferred", "nice to have", "nice to have skills", "plus", "desirable",
    "bonus", "preferred qualifications", "extra credit", "good to have", "advantageous",
)
_RESPONSIBILITY_HEADINGS = (
    "responsibilities", "what you will do", "the role", "duties", "about the role",
    "key responsibilities", "your impact", "day to day", "day-to-day", "what you'll do",
)
_SOFT_HEADINGS = ("soft skills", "competencies", "personal attributes", "interpersonal", "culture")
_CERT_HEADINGS = ("certifications", "certificates", "licenses", "credentials")

_SECTION_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("required_skills", _REQUIRED_HEADINGS),
    ("preferred_skills", _PREFERRED_HEADINGS),
    ("responsibilities", _RESPONSIBILITY_HEADINGS),
    ("soft_skills", _SOFT_HEADINGS),
    ("certifications", _CERT_HEADINGS),
)

# Order in which a heading is matched against the buckets above. "Preferred
# qualifications" also contains "qualifications" (a required keyword), so the
# preferred bucket has to be tested first.
_CLASSIFICATION_ORDER: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("preferred_skills", _PREFERRED_HEADINGS),
    ("certifications", _CERT_HEADINGS),
    ("soft_skills", _SOFT_HEADINGS),
    ("responsibilities", _RESPONSIBILITY_HEADINGS),
    ("required_skills", _REQUIRED_HEADINGS),
)

_EXPERIENCE_PATTERNS = (
    re.compile(r"(\d{1,2}(?:\.\d)?)\s*\+?\s*(?:-|\s)?\s*years?", re.IGNORECASE),
    re.compile(r"(?:at\s+least|minimum(?:\s+of)?|min\.?)\s*(\d{1,2}(?:\.\d)?)\s*years?", re.IGNORECASE),
    re.compile(r"(\d{1,2})\s*(?:-|–|to)\s*(\d{1,2})\s*years?", re.IGNORECASE),
    re.compile(r"(\d{1,2})\s*years?", re.IGNORECASE),
)

_EMPLOYMENT_TYPES = (
    "full-time", "full time", "part-time", "part time", "contract", "contractor",
    "temporary", "internship", "intern", "freelance", "remote", "hybrid", "on-site", "onsite",
)

_LOCATION_HINTS = (
    "location", "based in", "office", "work from", "on-site", "onsite", "hybrid",
    "remote", "relocation", "commute",
)

_SPLIT = re.compile(r"[,;|•·]|\s{2,}")

# "(required)", "(nice to have)", "(must have)" style qualifiers that are
# metadata on a bullet, not part of the skill name.
_TRAILING_QUALIFIER = re.compile(
    r"\s*\((?:required|must(?:\s+have)?|mandatory|essential|preferred|nice\s+to\s+have|"
    r"desirable|bonus|plus|optional|strong|good\s+to\s+have)\)\s*$",
    re.IGNORECASE,
)
_CONJUNCTION = re.compile(r"\s+(?:and|or|/|,)\s+", re.IGNORECASE)


@dataclass(slots=True)
class ParsedJob:
    title: str | None = None
    department: str | None = None
    location: str | None = None
    employment_type: str | None = None
    experience_required_years: float = 0.0
    education_required: str | None = None
    required_skills: list[str] = field(default_factory=list)
    preferred_skills: list[str] = field(default_factory=list)
    certifications: list[str] = field(default_factory=list)
    responsibilities: list[str] = field(default_factory=list)
    soft_skills: list[str] = field(default_factory=list)
    raw_text: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_schema(self) -> JobExtractionResult:
        return JobExtractionResult(
            title=self.title,
            department=self.department,
            location=self.location,
            employment_type=self.employment_type,
            experience_required_years=self.experience_required_years,
            education_required=self.education_required,
            required_skills=self.required_skills,
            preferred_skills=self.preferred_skills,
            certifications=self.certifications,
            responsibilities=self.responsibilities,
            soft_skills=self.soft_skills,
            raw_text=self.raw_text,
        )


def _heading_key(line: str) -> str | None:
    """Classify a line as a job-description section heading.

    Two guards matter more than the keyword list:

    * list items are never headings - otherwise "Python (required)" matches
      the *required* keyword and silently discards the skill;
    * a parenthesised qualifier ("nice to have") marks a bullet, not a title.

    Order is also significant: "Preferred qualifications" contains the word
    "qualifications", so the preferred bucket must be tested first.
    """
    stripped = cleaning.strip_bullets(line)
    if not stripped:
        return None
    stripped = re.sub(r"^\s*\d+[.)]\s*", "", stripped).strip()
    if not stripped or len(stripped) > 70:
        return None
    if "(" in stripped or ")" in stripped:
        return None
    if stripped.endswith((".", ";")):
        return None

    label = stripped.rstrip(":").strip()
    words = label.split()
    if not label or len(words) > 5:
        return None
    normalized = normalize_text(label)
    if not normalized:
        return None

    for key, keywords in _CLASSIFICATION_ORDER:
        for keyword in keywords:
            token = normalize_text(keyword)
            if not token:
                continue
            if normalized == token or normalized.startswith(f"{token} "):
                return key
            # "Key Responsibilities" / "Technical Requirements".
            if normalized.endswith(f" {token}") and len(words) - len(token.split()) <= 2:
                return key
    return None


def _blocks(lines: list[str]) -> dict[str, list[str]]:
    """Split a job description into requirement buckets by heading.

    Accumulates body lines under the most recent recognised heading. Anything
    before the first heading lands in ``"other"``, which is still mined for
    skills, experience and education signals.
    """
    buckets: dict[str, list[str]] = {key: [] for key, _ in _SECTION_KEYWORDS}
    buckets["other"] = []
    current = "other"
    found_heading = False

    for line in lines:
        key = _heading_key(line)
        if key:
            found_heading = True
            current = key
            # "Requirements: Python, SQL" carries content on the heading line.
            _, separator, remainder = line.partition(":")
            if separator and remainder.strip():
                buckets[key].append(remainder.strip())
            continue
        if line.strip():
            buckets[current].append(line)

    if not found_heading:
        # A single unstructured blob: treat the whole thing as one body.
        buckets["other"].extend(line for line in lines if line.strip())
    return buckets


def _terms_from(text: str) -> list[str]:
    """Extract candidate skill terms from a requirements block.

    Free text is kept verbatim: a job may legitimately require a tool that is
    not in the taxonomy, and losing it would silently weaken the match.
    """
    terms: dict[str, str] = {}
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped or len(stripped) > 400:
            continue
        for chunk in _SPLIT.split(stripped):
            candidate = cleaning.strip_bullets(chunk)
            candidate = re.sub(r"^\s*\d+[.)]\s*", "", candidate)
            candidate = _TRAILING_QUALIFIER.sub("", candidate).strip(" ,;\u2022")
            if not candidate or len(candidate) > 60:
                continue
            normalized = normalize_text(candidate)
            if not normalized or len(normalized) < 2:
                continue
            if not _is_skill_term(candidate):
                continue
            terms.setdefault(normalized, candidate)
    return list(terms.values())


def _requirement_skills(block: str, limit: int) -> list[str]:
    """Canonical skill names for a requirements block, in first-seen order.

    A requirement bullet is rarely one skill. "Strong PyTorch and SQL skills"
    and "4+ years of experience with Python" each name real skills buried in
    filler, and storing the whole bullet as a single requirement creates a
    requirement no candidate can ever satisfy - which silently depresses every
    score. So each line is mined for taxonomy mentions first.

    A bullet that names no known skill is still kept verbatim: a job may
    legitimately require a tool absent from the taxonomy, and dropping it would
    weaken the match. A bullet that *does* name known skills is dropped in favour
    of them, which keeps "Python and SomeHouseFramework" from losing the
    off-taxonomy half.
    """
    ordered: dict[str, str] = {}

    for line in block.split("\n"):
        stripped = line.strip()
        if not stripped or len(stripped) > 400:
            continue

        # Harvested from the whole line, so a bullet that is an eligibility
        # criterion rather than a skill still contributes its skills.
        for name in _known_skills(stripped):
            ordered.setdefault(normalize_text(name), name)

        for chunk in _SPLIT.split(stripped):
            candidate = cleaning.strip_bullets(chunk)
            candidate = re.sub(r"^\s*\d+[.)]\s*", "", candidate)
            candidate = _TRAILING_QUALIFIER.sub("", candidate).strip(" ,;\u2022")
            if not candidate or len(candidate) > 60:
                continue
            if _known_skills(candidate):
                continue
            if not _is_skill_term(candidate):
                continue
            normalized = normalize_text(candidate)
            if len(normalized) > 1:
                ordered.setdefault(normalized, candidate)

    return list(ordered.values())[:limit]


def _taxonomy_forms() -> set[str]:
    """Every surface form of every taxonomy skill, lower-cased and normalised.

    Keys are the underscore slugs, so the space-separated form has to be added
    explicitly or nothing matches.
    """
    forms: set[str] = set()
    for normalized_name, definition in skill_by_normalized_name().items():
        forms.add(normalized_name)
        forms.add(normalized_name.replace("_", " "))
        forms.add(normalize_text(definition.name))
    return forms


def _drop_compound_terms(terms: list[str]) -> list[str]:
    """Remove conjunctions whose every part is already a tracked skill.

    "Machine Learning and Deep Learning" must not become one requirement that
    no candidate could ever satisfy; the two real skills are emitted instead.
    Retained as a safety net for off-taxonomy text, where a conjunction can
    still survive ``_requirement_skills``.
    """
    forms = _taxonomy_forms()
    kept: list[str] = []
    for term in terms:
        parts = [part.strip() for part in _CONJUNCTION.split(term) if part.strip()]
        if len(parts) > 1 and all(normalize_text(part) in forms for part in parts):
            continue
        kept.append(term)
    return kept


# Requirement bullets that describe something other than a skill. Emitting
# "3+ years of experience" as a required skill would create a requirement no
# candidate can ever satisfy and would depress every score.
_NON_SKILL_TERM = re.compile(
    r"\b(?:years?|months?|degree|degrees|bachelor|master|phd|doctrine|diploma|university|college|"
    r"salary|range|per annum|usd|pkr|eur|apply|email|cv|resume|contact|interview|availability|"
    r"immediately|notice period|work authorization|visa|relocation)\b",
    re.IGNORECASE,
)
_EXPERIENCE_BULLET = re.compile(r"^\d+\s*\+?\s*(?:-\s*\d+\s*)?(?:years?|months?)", re.IGNORECASE)


def _is_skill_term(candidate: str) -> bool:
    """Reject bullets that are eligibility criteria rather than skills."""
    if not candidate or len(candidate) > 60:
        return False
    if _EXPERIENCE_BULLET.match(candidate):
        return False
    if _NON_SKILL_TERM.search(candidate):
        return False
    return True


def _known_skills(text: str) -> list[str]:
    """Canonical taxonomy skills mentioned in ``text``, in first-seen order."""
    found: dict[str, str] = {}
    by_name = skill_by_normalized_name()
    for _surface, normalized_name, _start, _end in find_skill_mentions(normalize_text(text)):
        definition = by_name.get(normalized_name)
        if definition:
            found.setdefault(normalized_name, definition.name)
    return list(found.values())


def _merge_terms(primary: list[str], secondary: list[str]) -> list[str]:
    seen = {normalize_text(term) for term in primary}
    merged = list(primary)
    for term in secondary:
        key = normalize_text(term)
        if key and key not in seen:
            seen.add(key)
            merged.append(term)
    return merged


def _split_requirement_lines(text: str) -> tuple[list[str], list[str]]:
    """Split a requirements block into required and preferred terms.

    Handles the very common single-block layout where each bullet is tagged
    inline, e.g. "Python (required), Docker (nice to have)".
    """
    required: list[str] = []
    preferred: list[str] = []
    for line in text.split("\n"):
        lowered = normalize_text(line)
        if not lowered:
            continue
        is_preferred = any(
            marker in lowered for marker in ("preferred", "nice to have", "bonus", "plus", "desirable")
        )
        is_required = any(marker in lowered for marker in ("required", "must have", "essential", "mandatory"))
        if is_preferred and not is_required:
            preferred.append(line)
        elif is_required or not is_preferred:
            required.append(line)
        else:  # pragma: no cover - defensive
            preferred.append(line)
    return required, preferred


def _extract_experience_years(text: str) -> float:
    best = 0.0
    for pattern in _EXPERIENCE_PATTERNS:
        for match in pattern.finditer(text):
            if match.re.groups >= 2 and match.group(2):
                try:
                    best = max(best, float(match.group(1)))
                except ValueError:
                    continue
                continue
            try:
                value = float(match.group(1))
            except (ValueError, IndexError):
                continue
            # Ignore absurd matches (e.g. "50 years") and year-like tokens.
            if 0.5 <= value <= 30:
                best = max(best, value)
    return round(best, 1)


def _extract_title(lines: list[str]) -> str | None:
    for line in lines[:8]:
        stripped = line.strip()
        if not stripped or len(stripped) > 120:
            continue
        lowered = normalize_text(stripped)
        if any(lowered.endswith(suffix) for suffix in ("job description", "job posting", "position", "role", "jd")):
            cleaned = re.sub(
                r"\s*(?:job description|job posting|position|role|jd)\s*$", "", stripped, flags=re.IGNORECASE
            ).strip(" -–—:|")
            if cleaned:
                return cleaned
        if any(hint in lowered for hint in ("engineer", "developer", "scientist", "analyst", "manager", "designer", "lead", "intern")):
            return stripped
    return None


def _extract_department(lines: list[str]) -> str | None:
    for line in lines[:10]:
        match = re.match(r"^(?:department|team|division|function)\s*[:\-]\s*(.+)$", line.strip(), re.IGNORECASE)
        if match:
            return match.group(1).strip()[:150]
    return None


def _extract_location(lines: list[str]) -> str | None:
    for line in lines[:12]:
        match = re.match(r"^(?:location|based in|office|work location)\s*[:\-]\s*(.+)$", line.strip(), re.IGNORECASE)
        if match:
            value = match.group(1).strip()
            if 2 <= len(value) <= 120:
                return value
    for line in lines[:12]:
        lowered = normalize_text(line)
        if any(hint in lowered for hint in _LOCATION_HINTS) and "," in line:
            return line.strip()[:150]
    return None


def _extract_employment_type(lines: list[str]) -> str | None:
    for line in lines[:12]:
        lowered = normalize_text(line)
        for candidate in _EMPLOYMENT_TYPES:
            if candidate in lowered:
                return candidate.title() if "-" not in candidate else candidate.title()
    return None


def _extract_education_required(text: str) -> str | None:
    from app.nlp.education import degree_rank

    candidates: list[str] = []
    for line in text.split("\n"):
        lowered = normalize_text(line)
        if not lowered:
            continue
        if any(token in lowered for token in ("degree", "education", "qualification", "bachelor", "master", "phd", "diploma")):
            match = re.search(
                r"(bachelor(?:'s)?|master(?:'s)?|ph\.?d|doctorate|mba|b\.?sc|m\.?sc|b\.?tech|m\.?tech)[^,\n]{0,60}",
                line,
                re.IGNORECASE,
            )
            if match and degree_rank(match.group(0)) > 0:
                candidates.append(match.group(0).strip())
    if not candidates:
        return None
    return max(candidates, key=degree_rank)[:120]


def parse_job_description(text: str) -> ParsedJob:
    """Extract a structured requirement set from raw job description text."""
    cleaned = cleaning.clean_document_text(text)
    lines = cleaning.clean_lines(cleaned)
    if not lines:
        return ParsedJob(raw_text="", warnings=["Job description was empty."])

    buckets = _blocks(lines)
    required_block = "\n".join(buckets["required_skills"])
    preferred_block = "\n".join(buckets["preferred_skills"])
    inline_required, inline_preferred = _split_requirement_lines(required_block)

    required_terms = _requirement_skills(
        "\n".join((required_block, *inline_required)), limit=40
    )
    required_terms = _drop_compound_terms(required_terms)

    preferred_terms = _requirement_skills(
        "\n".join((preferred_block, *inline_preferred)), limit=30
    )
    preferred_terms = _drop_compound_terms(preferred_terms)

    certifications = extract_certifications(lines, "\n".join(buckets["certifications"]))[:20]
    responsibilities = [
        truncate(cleaning.strip_bullets(line), 300)
        for line in "\n".join(buckets["responsibilities"]).split("\n")
        if line.strip()
    ][:25]
    soft_skills = _terms_from("\n".join(buckets["soft_skills"]))[:20]

    # Free text anywhere may still name required skills.
    if not required_terms:
        required_terms = _known_skills(cleaned)[:25]

    experience = _extract_experience_years(
        "\n".join((required_block, preferred_block, "\n".join(buckets["other"][:6])))
    )
    education_required = _extract_education_required(
        "\n".join((required_block, preferred_block, "\n".join(buckets["other"][:6])))
    )

    warnings: list[str] = []
    if not required_terms:
        warnings.append("No required skills were detected. Please review and add them manually.")
    if experience == 0.0:
        warnings.append("Experience requirement was not detected; please set it manually.")

    return ParsedJob(
        title=_extract_title(lines),
        department=_extract_department(lines),
        location=_extract_location(lines),
        employment_type=_extract_employment_type(lines),
        experience_required_years=experience,
        education_required=education_required,
        required_skills=required_terms,
        preferred_skills=preferred_terms,
        certifications=certifications,
        responsibilities=responsibilities,
        soft_skills=soft_skills,
        raw_text=cleaned,
        warnings=warnings,
    )

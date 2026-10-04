"""Education parsing and degree-level comparison.

The ``education`` signal in the ranking engine has to answer one question:
"does this candidate's highest qualification meet or exceed what the job asks
for?" That requires a *ranking* over heterogeneous degree strings, which is
what ``degree_rank`` provides.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.nlp.skill_taxonomy import normalize_text

__all__ = [
    "EducationInfo",
    "degree_rank",
    "extract_education",
    "meets_education_requirement",
    "tidy_degree_fields",
]


@dataclass(slots=True)
class EducationInfo:
    degree: str | None = None
    field: str | None = None
    institution: str | None = None
    graduation_year: int | None = None
    rank: int = 0
    evidence: str | None = None


# Ordered from lowest to highest. Numeric rank drives the education score.
_DEGREE_PATTERNS: tuple[tuple[str, int], ...] = (
    ("doctor of philosophy", 6),
    ("ph d", 6),
    ("phd", 6),
    ("doctorate", 6),
    ("doctoral", 6),
    ("doctor of engineering", 6),
    ("dphil", 6),
    ("master of science", 5),
    ("master of engineering", 5),
    ("master of arts", 5),
    ("master of business administration", 5),
    ("masters", 5),
    ("master", 5),
    ("msc", 5),
    ("ms", 5),
    ("mba", 5),
    ("meng", 5),
    ("mtech", 5),
    ("mca", 5),
    ("ma", 5),
    ("m sc", 5),
    ("bachelor of science", 4),
    ("bachelor of engineering", 4),
    ("bachelor of technology", 4),
    ("bachelor of arts", 4),
    ("bachelor", 4),
    ("bachelors", 4),
    ("bsc", 4),
    ("bs", 4),
    ("be", 4),
    ("btech", 4),
    ("bca", 4),
    ("b eng", 4),
    ("b sc", 4),
    ("b tech", 4),
    ("associate", 3),
    ("diploma", 2),
    ("certificate", 1),
    ("high school", 1),
    ("secondary", 1),
    ("intermediate", 2),
    ("fsc", 2),
    ("hsc", 2),
    ("ssc", 1),
    ("matric", 1),
)

_FIELDS = (
    "computer science", "software engineering", "computer engineering", "information technology",
    "artificial intelligence", "machine learning", "data science", "statistics", "mathematics",
    "electronics", "electrical engineering", "mechanical engineering", "civil engineering",
    "business administration", "management sciences", "finance", "accounting", "marketing",
    "psychology", "sociology", "biology", "chemistry", "physics", "economics",
)

_INSTITUTION_HINTS = (
    "university", "universidad", "college", "institute", "institut", "school", "academy",
    "polytechnic", "campus", "faculty", "dept of", "department of",
)


def degree_rank(degree: str | None) -> int:
    """Map a free-text degree onto a 0-6 ordinal scale.

    Returns 0 when the text is not recognisable as a qualification, which the
    scorer treats as "unknown" rather than "failed".
    """
    if not degree:
        return 0
    normalized = f" {_normalize_degree_text(degree)} "
    best = 0
    for token, rank in _DEGREE_PATTERNS:
        if f" {token} " in normalized:
            best = max(best, rank)
    return best


def _normalize_degree_text(text: str) -> str:
    """Normalise a degree string for matching.

    Removes the dots from abbreviations so ``B.S.`` and ``BSc`` collapse to the
    same token. Without this, the most common way of writing a degree in the
    English-speaking world is invisible to the matcher and silently scores zero.
    """
    normalized = normalize_text(text)
    # "b.s." -> "bs", "ph.d." -> "phd", "b.sc" -> "bsc"
    normalized = re.sub(r"(?<=[a-z])\.", "", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _detect_field(line: str) -> str | None:
    normalized = normalize_text(line)
    for field in _FIELDS:
        if field in normalized:
            return field.title()
    return None


def _extract_degree_token(line: str) -> str | None:
    """Return just the qualification from a line such as
    ``"B.S. Computer Science, University of California Davis, 2014"``.

    Storing the whole line as the degree would put the university and the year
    into the degree field, which then renders badly and double-counts when the
    education component is compared against a requirement.
    """
    head = re.split(r"\s*[,;|]\s*|\s+-\s+|\s+at\s+", line, maxsplit=1)[0].strip()
    return head[:120] or None


def _detect_institution(line: str) -> str | None:
    normalized = normalize_text(line)
    if not any(hint in normalized for hint in _INSTITUTION_HINTS):
        return None
    # Drop a leading degree token, keep the institution part.
    cleaned = line.strip()
    cleaned = re.sub(
        r"^(?:bachelor|master|ph\.?d\.?|doctorate|msc|bsc|b\.?s\.?|m\.?s\.?|mba|btech|bca)\b[^,]*,\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    # "MS Computer Science at Stanford University" - the degree precedes the
    # institution with no comma to split on. Only treat " at " as a separator when
    # what precedes it really is a qualification, otherwise "University of Texas
    # at Austin" loses half its name.
    if " at " in cleaned:
        head, _, tail = cleaned.partition(" at ")
        if degree_rank(head):
            cleaned = tail
    cleaned = cleaned.strip(" ,-–—")
    return cleaned[:200] if cleaned else None


_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")


def extract_education(section_text: str, full_text: str = "") -> EducationInfo:
    """Extract the highest qualification from an education section.

    Falls back to scanning the whole document when no education section was
    detected, since many resumes put education at the very bottom under a
    heading the detector missed.
    """
    source = section_text.strip() or full_text
    lines = [line.strip() for line in source.split("\n") if line.strip()]
    if not lines:
        return EducationInfo()

    best: EducationInfo | None = None
    for line in lines[:40]:
        rank = degree_rank(line)
        year_match = _YEAR.search(line)
        year = int(year_match.group(1)) if year_match else None
        if year and not 1950 <= year <= 2100:
            year = None

        candidate = EducationInfo(
            degree=_extract_degree_token(line) if rank else None,
            field=_detect_field(line),
            institution=_detect_institution(line),
            graduation_year=year,
            rank=rank,
            evidence=line[:300],
        )
        if not best or candidate.rank > best.rank or (
            candidate.rank == best.rank
            and candidate.graduation_year
            and (best.graduation_year is None or candidate.graduation_year > best.graduation_year)
        ):
            best = candidate

    result = best or EducationInfo()

    # A separate line often holds the field of study ("in Computer Science").
    if not result.field:
        result.field = next((_detect_field(line) for line in lines[:20] if _detect_field(line)), None)
    # Institution may live on its own line.
    if not result.institution:
        result.institution = next(
            (inst for inst in (_detect_institution(line) for line in lines[:20]) if inst), None
        )
    # Graduation year may sit apart from the degree.
    if not result.graduation_year:
        years = [int(year) for year in _YEAR.findall(" ".join(lines[:20])) if 1950 <= int(year) <= 2100]
        if years:
            result.graduation_year = max(years)

    if result.degree and result.field and result.field.lower() not in normalize_text(result.degree):
        result.degree = f"{result.degree} ({result.field})"[:200]
    return result


_TRAILING_YEAR = re.compile(r"[,\s(\[]*\b(?:19|20)\d{2}\b[\s)\]]*\s*$")


def _strip_year(value: str) -> str:
    """Remove a trailing graduation year from a degree/institution string."""
    return _TRAILING_YEAR.sub("", value).strip(" ,;-").strip()


def tidy_degree_fields(info: EducationInfo) -> EducationInfo:
    """Split a combined education line into discrete fields.

    "MS Computer Science, NUST, 2019" should surface as
    degree="MS Computer Science", institution="NUST", year=2019 - not one
    string that would render badly in the UI and break education scoring.
    """
    if not info.degree:
        return info
    degree = _strip_year(info.degree)
    institution = info.institution
    if institution:
        institution = _strip_year(institution)
        # Pull the institution out of the degree string when it is embedded there.
        marker = f", {institution}"
        if marker in degree:
            degree = degree.split(marker, 1)[0].strip(" ,;-")
    info.degree = degree[:200] or None
    info.institution = institution[:200] if institution else None
    return info


_REQUIREMENT_TOKENS = (
    ("phd", 6), ("doctorate", 6), ("doctoral", 6),
    ("master", 5), ("msc", 5), ("ms", 5), ("mba", 5), ("m tech", 5), ("mtech", 5), ("meng", 5), ("mca", 5),
    ("bachelor", 4), ("bsc", 4), ("bs", 4), ("b tech", 4), ("btech", 4), ("bca", 4), ("b e", 4), ("be", 4),
    ("associate", 3), ("diploma", 2), ("high school", 1), ("secondary", 1),
)


def meets_education_requirement(candidate_degree: str | None, requirement: str | None) -> bool:
    """Whether a candidate's qualification satisfies a stated requirement.

    An empty/unknown requirement is treated as satisfied - a job that says
    nothing about education should not penalise anyone.
    """
    if not requirement or not normalize_text(requirement):
        return True
    required_rank = degree_rank(requirement)
    if required_rank == 0:
        # Fall back to a keyword scan for phrasing like "Bachelor's degree in CS".
        normalized = f" {normalize_text(requirement)} "
        required_rank = next((rank for token, rank in _REQUIREMENT_TOKENS if f" {token} " in normalized), 0)
    if required_rank == 0:
        return True
    return degree_rank(candidate_degree) >= required_rank

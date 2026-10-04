"""Resume section detection.

Resumes have no standard, but they reliably use a small set of headings. This
module maps headings to a canonical section key so the extractor can look for
"where skills are listed" instead of guessing. Heading matching is fuzzy enough
to survive real variants ("WORK EXPERIENCE", "Professional Experience",
"Employment History", "Career History", ...).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.nlp.skill_taxonomy import normalize_text

__all__ = ["SECTION_KEYS", "SectionSpan", "detect_sections", "section_bounds"]

# Canonical section -> keywords that identify it. Order matters: the first
# section whose keyword matches wins, so put specific keys before generic ones.
SECTION_KEYS: dict[str, tuple[str, ...]] = {
    "summary": ("summary", "professional summary", "profile", "objective", "about me", "personal statement"),
    "skills": (
        "skills",
        "technical skills",
        "core skills",
        "key skills",
        "skills and tools",
        "technologies",
        "tech stack",
        "competencies",
        "expertise",
        "areas of expertise",
    ),
    "experience": (
        "experience",
        "work experience",
        "professional experience",
        "employment history",
        "career history",
        "work history",
        "professional background",
        "relevant experience",
    ),
    "projects": (
        "projects",
        "personal projects",
        "academic projects",
        "selected projects",
        "research projects",
        "portfolio",
    ),
    "education": (
        "education",
        "academic background",
        "academic qualifications",
        "educational background",
        "education and training",
        "qualifications",
        "academic credentials",
    ),
    "certifications": (
        "certifications",
        "certification",
        "licenses and certifications",
        "licenses",
        "courses",
        "training",
        "professional development",
    ),
    "achievements": (
        "achievements",
        "awards",
        "accomplishments",
        "honors and awards",
        "publications",
    ),
    "languages": ("languages", "language proficiency", "spoken languages"),
    "interests": ("interests", "hobbies", "activities", "extracurricular"),
    "contact": ("contact", "contact information", "contact details", "personal details"),
    "references": ("references", "referees"),
}

_HEADING_MAX_WORDS = 4
_ALIAS_TO_KEY: dict[str, str] = {
    normalize_text(alias): key for key, aliases in SECTION_KEYS.items() for alias in aliases
}

_COLON_SPLIT = re.compile(r"^\s*(?P<label>[A-Za-z][A-Za-z &/,'()\-\.]{1,60}?)\s*:\s*(?P<content>\S.*)$")
_COLON_SPLIT_EMPTY = re.compile(r"^\s*(?P<label>[A-Za-z][A-Za-z &/,'()\-\.]{1,60}?)\s*:\s*$")

# Labels that may carry their content on the heading line ("Skills: Python,
# SQL"). Every other heading must own its line, otherwise a sub-label inside a
# Skills block ("Languages: Python, Java") is mistaken for a section boundary
# and truncates the block above it - which loses every listed skill.
_INLINE_CONTENT_LABELS: frozenset[str] = frozenset(
    normalize_text(alias)
    for aliases in (
        SECTION_KEYS["skills"],
        ("tech stack", "technologies", "toolkit", "software", "expertise"),
    )
    for alias in aliases
)


@dataclass(frozen=True, slots=True)
class SectionSpan:
    key: str
    heading: str
    start_line: int
    end_line: int
    text: str
    inline_content: str | None = None

    @property
    def line_count(self) -> int:
        return max(0, self.end_line - self.start_line)


def _heading_key_of(candidate: str) -> str | None:
    return _ALIAS_TO_KEY.get(normalize_text(candidate))


def _inline_content_of(line: str) -> str | None:
    """Content trailing a heading on the same line, if the label allows it."""
    inline = _COLON_SPLIT.match(line.strip())
    if inline and _heading_key_of(inline.group("label")) in _INLINE_CONTENT_LABELS:
        return inline.group("content").strip()
    return None


def _match_heading(line: str) -> str | None:
    """Return the canonical section key when ``line`` looks like a heading."""
    stripped = line.strip()
    if not stripped or len(stripped) > 80:
        return None
    # A heading is short and mostly letters; reject sentences and bullets.
    if len(stripped.split()) > _HEADING_MAX_WORDS + 2:
        return None
    letters = sum(ch.isalpha() for ch in stripped)
    if letters < max(3, len(stripped) * 0.5):
        return None

    # "Languages: Python, Java, SQL" is a sub-label inside a skills block, not
    # a section boundary. Only skills-family labels may carry content inline.
    inline = _COLON_SPLIT.match(stripped)
    if inline:
        key = _heading_key_of(inline.group("label"))
        return key if key in _INLINE_CONTENT_LABELS else None

    # "Skills:" with nothing after the colon is a plain heading.
    trailing_colon = _COLON_SPLIT_EMPTY.match(stripped)
    if trailing_colon:
        return _heading_key_of(trailing_colon.group("label"))

    return _heading_key_of(stripped.rstrip(":").strip())


def detect_sections(lines: list[str]) -> list[SectionSpan]:
    """Locate every recognised section in ``lines``.

    Sections run until the next recognised heading or the end of the document.
    Unrecognised content before the first heading is kept under the synthetic
    ``"preamble"`` key, which is where names and contact details usually live.
    """
    if not lines:
        return []

    marks: list[tuple[int, str, str]] = []
    for index, line in enumerate(lines):
        key = _match_heading(line)
        if key:
            marks.append((index, key, line.strip()))

    spans: list[SectionSpan] = []
    if marks and marks[0][0] > 0:
        preamble = "\n".join(lines[: marks[0][0]]).strip()
        if preamble:
            spans.append(
                SectionSpan(
                    key="preamble",
                    heading="(header)",
                    start_line=0,
                    end_line=marks[0][0],
                    text=preamble,
                )
            )

    for position, (index, key, heading) in enumerate(marks):
        end = marks[position + 1][0] if position + 1 < len(marks) else len(lines)
        inline = _inline_content_of(heading)
        body_lines = list(lines[index + 1 : end])
        if inline:
            # "Skills: Python, SQL" - the heading line carries the content.
            body_lines.insert(0, inline)
        body = "\n".join(body_lines).strip()
        spans.append(
            SectionSpan(
                key=key,
                heading=heading,
                start_line=index,
                end_line=end,
                text=body,
                inline_content=inline,
            )
        )
    return spans


def section_bounds(spans: list[SectionSpan]) -> dict[str, tuple[int, int]]:
    return {span.key: (span.start_line, span.end_line) for span in spans}


def sections_to_dict(spans: list[SectionSpan]) -> dict[str, str]:
    """Serialise sections for the ``Candidate.sections`` JSON column.

    Resumes sometimes repeat a section ("SKILLS" at the top and "TECHNICAL
    SKILLS" at the bottom). Occurrences are concatenated rather than
    overwritten so no listed skill is lost.
    """
    out: dict[str, str] = {}
    for span in spans:
        if not span.text:
            continue
        # Keep stored sections compact - they are context, not the raw document.
        body = span.text[:4000]
        out[span.key] = f"{out[span.key]}\n{body}" if span.key in out else body
    return out

"""Resume parsing orchestrator (PRD sections 7 and 8).

Pipeline
--------
    text -> cleaning -> section detection -> information extraction
         -> skill normalisation -> candidate profile

The output is a :class:`ParsedResume`, a plain dataclass with no ORM or I/O
dependencies, so the exact same pipeline is exercised by unit tests, by the
notebooks under ``ml/`` and by the API.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.nlp import cleaning, contact, dates, education, sections, skill_extractor
from app.nlp.document import ExtractedDocument
from app.nlp.skill_extractor import ExtractedSkill

__all__ = ["ParsedResume", "parse_resume"]


@dataclass(slots=True)
class ParsedResume:
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None
    current_title: str | None = None
    total_experience_years: float = 0.0
    relevant_experience_years: float = 0.0
    highest_degree: str | None = None
    degree_field: str | None = None
    institution: str | None = None
    graduation_year: int | None = None
    certifications: list[str] = field(default_factory=list)
    previous_positions: list[str] = field(default_factory=list)
    employment_history: list[dict[str, object]] = field(default_factory=list)
    skills: list[ExtractedSkill] = field(default_factory=list)
    section_map: dict[str, str] = field(default_factory=dict)
    raw_text: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def skill_names(self) -> list[str]:
        return [skill.display_name for skill in self.skills]


# Titles that indicate the role is engineering/analytics, i.e. relevant to a
# technical job posting, used for the "relevant experience" estimate.
_RELEVANT_HINTS = (
    "engineer", "developer", "scientist", "analyst", "architect", "data", "machine learning",
    "ai", "ml", "research", "software", "programmer", "consultant", "automation",
)


def _section_text(section_map: dict[str, str], *keys: str) -> str:
    return "\n".join(section_map.get(key, "") for key in keys if section_map.get(key))


def _extract_employment_history(experience_text: str) -> tuple[list[dict], list[str]]:
    """Group experience lines into role entries with dates.

    Returns ``(entries, previous_positions)``. A new entry starts on a line
    carrying a date range; subsequent non-empty lines are treated as
    responsibilities of that role.
    """
    entries: list[dict] = []
    titles: list[str] = []
    if not experience_text.strip():
        return entries, titles

    current: dict | None = None
    for raw_line in experience_text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        parsed_range = dates.parse_date_range(line)
        title_part = line
        company: str | None = None

        if parsed_range:
            # Split "Senior Engineer, Acme (Jan 2020 - Present)" into parts.
            head = line[: parsed_range.raw and line.index(parsed_range.raw) or len(line)].strip()
            head = head.strip(" ,|()\u2022-–—")
            title_part, company = _split_title_company(head)
        elif current is None:
            if dates.looks_like_experience_header(line):
                title_part, company = _split_title_company(line)
            else:
                continue
        elif not current["title"] and not current["company"]:
            title_part, company = _split_title_company(line)

        if title_part and current is not None and not current["title"]:
            current["title"] = title_part
        elif title_part and current is not None and title_part.lower() != current["title"].lower():
            current["bullets"].append(line)

        if parsed_range or current is None:
            current = {
                "title": title_part or None,
                "company": company,
                "start": parsed_range.as_dict()["start"] if parsed_range else None,
                "end": parsed_range.as_dict()["end"] if parsed_range else None,
                "months": parsed_range.months if parsed_range else None,
                "is_current": parsed_range.is_current if parsed_range else False,
                "raw": line[:400],
                "bullets": [],
            }
            entries.append(current)
            if title_part and title_part not in titles:
                titles.append(title_part)

    for entry in entries:
        entry["bullets"] = entry["bullets"][:12]
    return entries, titles


_COMPANY_SPLIT = (" at ", " @ ", " - ", " | ", ", ", " for ")


def _split_title_company(line: str) -> tuple[str, str | None]:
    for separator in _COMPANY_SPLIT:
        if separator in line:
            head, _, tail = line.partition(separator)
            head, tail = head.strip(), tail.strip()
            if head and tail and len(head) <= 80 and len(tail) <= 80:
                if tail[0].isupper() or separator in (" at ", " @ "):
                    return head, tail
    return line.strip() or None, None


def _relevant_experience(entries: list[dict], full_text: str) -> float:
    """Months spent in roles whose title looks engineering/data oriented."""
    relevant_months = sum(
        int(entry.get("months") or 0)
        for entry in entries
        if dates.looks_like_experience_header(str(entry.get("title") or ""))
        and any(hint in (entry.get("title") or "").lower() for hint in _RELEVANT_HINTS)
    )
    if relevant_months:
        return round(relevant_months / 12.0, 2)
    # No dated technical roles: fall back to overall tenure but never claim more.
    total = dates.sum_experience_years([str(entry.get("raw") or "") for entry in entries])
    if any(hint in full_text.lower() for hint in ("engineer", "developer", "data", "analyst", "scientist")):
        return total
    return 0.0


def parse_resume(document: ExtractedDocument) -> ParsedResume:
    """Run the full parse pipeline over an extracted document."""
    text = document.text
    lines = cleaning.clean_lines(text)
    spans = sections.detect_sections(lines)
    section_map = sections.sections_to_dict(spans)

    experience_text = _section_text(section_map, "experience")
    education_text = _section_text(section_map, "education")
    skills_text = _section_text(section_map, "skills")

    info = contact.extract_contact(lines, text, experience_text)

    edu = education.tidy_degree_fields(education.extract_education(education_text, text))
    skills = skill_extractor.extract_skills(lines, skills_text)
    certifications = skill_extractor.extract_certifications(lines, _section_text(section_map, "certifications"))

    entries, titles = _extract_employment_history(experience_text)

    total_years = dates.sum_experience_years(lines)
    if not total_years and entries:
        total_years = round(sum(int(entry.get("months") or 0) for entry in entries) / 12.0, 2)

    warnings = list(document.warnings) + list(info.warnings)
    if not sections.detect_sections(lines):
        warnings.append("No standard sections were detected; extraction quality may be reduced.")
    if not skills:
        warnings.append("No skills were detected in this resume.")
    if total_years == 0.0:
        warnings.append("Employment dates could not be parsed; please verify total experience.")

    current_title = info.current_title
    if not current_title and entries and entries[0].get("title"):
        current_title = str(entries[0]["title"])

    return ParsedResume(
        full_name=info.full_name,
        email=info.email,
        phone=info.phone,
        location=info.location,
        linkedin_url=info.linkedin_url,
        github_url=info.github_url,
        portfolio_url=info.portfolio_url,
        current_title=current_title,
        total_experience_years=total_years,
        relevant_experience_years=_relevant_experience(entries, text),
        highest_degree=edu.degree,
        degree_field=edu.field,
        institution=edu.institution,
        graduation_year=edu.graduation_year,
        certifications=certifications,
        previous_positions=titles,
        employment_history=entries,
        skills=skills,
        section_map=section_map,
        raw_text=text,
        warnings=warnings,
    )

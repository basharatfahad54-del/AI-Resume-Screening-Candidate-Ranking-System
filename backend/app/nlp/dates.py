"""Date parsing and experience-duration computation.

Recruiters care about *how long* someone has done relevant work, and resumes
express tenure in wildly inconsistent ways::

    Jan 2020 - Present          Mar 2019 – 2022
    2019/06 to 2021/08          2020 - Current
    06/2019 - 08/2021           Summer 2018 - Fall 2019

This module parses those into ``(start, end)`` month pairs and sums durations
with **overlap merging**, so two concurrent roles do not double-count. The
result is a defensible number rather than the naive "sum every date I found".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from app.nlp.skill_taxonomy import normalize_text

__all__ = ["DateRange", "MONTHS", "parse_date_range", "sum_experience_years", "today_ym"]

MONTHS: dict[str, int] = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

_MONTH_ALT = "|".join(sorted(MONTHS, key=len, reverse=True))
_PRESENT = r"(?:present|current|curr(?:ent)?|now|today|to\s+date|ongoing|continuing)"

# "Jan 2020 - Present", "January 2019 – March 2021"
_RANGE_MONTH_YEAR = re.compile(
    rf"\b(?P<sm>{_MONTH_ALT})[\s.,]*'?(?P<sy>\d{{2,4}})\b"
    rf"\s*(?:-|–|—|to|until|till|through|thru)\s*"
    rf"(?:(?P<em>{_MONTH_ALT})[\s.,]*'?(?P<ey>\d{{2,4}})\b|(?P<present>{_PRESENT}))",
    re.IGNORECASE,
)
# "2019 - 2021", "2019/06 - 2021/08"
_RANGE_YEAR = re.compile(r"\b(?P<sy>(?:19|20)\d{2})\s*(?:-|–|—|to|until|till)\s*(?P<ey>(?:19|20)\d{2})\b")
# "2021 - Present", "2019 to Current". Kept separate from _RANGE_YEAR because
# the end is an open-ended keyword rather than a year; without this pattern the
# candidate's current role - the longest one - contributes nothing to their
# total experience.
_RANGE_YEAR_PRESENT = re.compile(
    rf"\b(?P<sy>(?:19|20)\d{{2}})\s*(?:-|–|—|to|until|till|thru|through)\s*(?P<present>{_PRESENT})\b",
    re.IGNORECASE,
)
# "Mar 2019 - 2022": a start month with only a start/end year. Needs its own
# pattern because _RANGE_YEAR_MONTH demands a month on both sides.
_RANGE_MONTH_YEAR_ONLY = re.compile(
    rf"\b(?P<sm>{_MONTH_ALT})[\s.,]*'?(?P<sy>\d{{2,4}})\b"
    rf"\s*(?:-|–|—|to|until|till)\s*(?P<ey>(?:19|20)\d{{2}})\b",
    re.IGNORECASE,
)
_RANGE_YEAR_MONTH = re.compile(
    r"\b(?P<sy>(?:19|20)\d{2})\s*[/\-.]\s*(?P<sm>0?[1-9]|1[0-2])\s*"
    r"(?:-|–|—|to|until|till)\s*"
    r"(?:(?P<ey>(?:19|20)\d{2})\s*[/\-.]\s*(?P<em>0?[1-9]|1[0-2])"
    rf"|(?P<present>{_PRESENT}))",
    re.IGNORECASE,
)
# "06/2019 - 08/2021" (month first, uncommon but used)
_RANGE_DMY = re.compile(
    r"\b(?P<sd>0?[1-9]|[12]\d|3[01])\s*[/\-.]\s*(?P<sy>(?:19|20)\d{2})\s*"
    r"(?:-|–|—|to|until|till)\s*"
    r"(?:(?P<ed>0?[1-9]|[12]\d|3[01])\s*[/\-.]\s*(?P<ey>(?:19|20)\d{2})"
    rf"|(?P<present>{_PRESENT}))",
    re.IGNORECASE,
)
# Single dates, used for graduation years and isolated stamps.
_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")

MIN_YEAR = 1970
MAX_YEAR = date.today().year + 1


@dataclass(frozen=True, slots=True)
class DateRange:
    start: tuple[int, int]
    end: tuple[int, int]
    raw: str
    is_current: bool = False

    @property
    def months(self) -> int:
        (sy, sm), (ey, em) = self.start, self.end
        return max(0, (ey - sy) * 12 + (em - sm))

    @property
    def years(self) -> float:
        return round(self.months / 12.0, 2)

    def as_dict(self) -> dict[str, object]:
        return {
            "start": f"{self.start[0]:04d}-{self.start[1]:02d}",
            "end": None if self.is_current else f"{self.end[0]:04d}-{self.end[1]:02d}",
            "months": self.months,
            "raw": self.raw,
        }


def today_ym() -> tuple[int, int]:
    now = date.today()
    return now.year, now.month


def _clean_year(raw: str) -> int | None:
    value = int(raw)
    if value < 100:
        value += 2000 if value < 70 else 1900
    return value if MIN_YEAR <= value <= MAX_YEAR else None


def _month_of(token: str | None) -> int | None:
    if not token:
        return None
    return MONTHS.get(token.strip().lower()[:9])


def parse_date_range(line: str) -> DateRange | None:
    """Parse the first employment date range found in ``line``."""
    if not line or len(line) > 400:
        return None

    match = _RANGE_MONTH_YEAR.search(line)
    if match:
        year = _clean_year(match.group("sy"))
        month = _month_of(match.group("sm"))
        if year and month:
            if match.group("present"):
                end, current = today_ym(), True
            else:
                end_year = _clean_year(match.group("ey"))
                end_month = _month_of(match.group("em"))
                if not end_year or not end_month:
                    return None
                end, current = (end_year, end_month), False
            start, finish = (year, month), end
            if finish < start:
                start, finish = finish, start
            return DateRange(start=start, end=finish, raw=match.group(0), is_current=current)

    match = _RANGE_MONTH_YEAR_ONLY.search(line)
    if match:
        start_year = _clean_year(match.group("sy"))
        end_year = _clean_year(match.group("ey"))
        start_month = _month_of(match.group("sm"))
        if start_year and end_year and start_month and end_year >= start_year:
            return DateRange(
                start=(start_year, start_month),
                end=(end_year, 12),
                raw=match.group(0),
                is_current=False,
            )

    match = _RANGE_YEAR_MONTH.search(line)
    if match:
        year = _clean_year(match.group("sy"))
        month = int(match.group("sm"))
        if year and month:
            if match.group("present"):
                end, current = today_ym(), True
            else:
                end_year = _clean_year(match.group("ey"))
                end_month = int(match.group("em"))
                if not end_year or not end_month:
                    return None
                end, current = (end_year, end_month), False
            start, finish = (year, month), end
            if finish < start:
                start, finish = finish, start
            return DateRange(start=start, end=finish, raw=match.group(0), is_current=current)

    match = _RANGE_YEAR.search(line)
    if match:
        start_year = _clean_year(match.group("sy"))
        end_year = _clean_year(match.group("ey"))
        if start_year and end_year and end_year >= start_year:
            return DateRange(
                start=(start_year, 1),
                end=(end_year, 12),
                raw=match.group(0),
                is_current=False,
            )

    match = _RANGE_YEAR_PRESENT.search(line)
    if match:
        start_year = _clean_year(match.group("sy"))
        if start_year:
            # Only the year is known, so January is assumed; counting from
            # January can slightly overstate tenure, which is the safer error
            # than understating a current role's length.
            return DateRange(
                start=(start_year, 1),
                end=today_ym(),
                raw=match.group(0),
                is_current=True,
            )

    match = _RANGE_DMY.search(line)
    if match:
        start_year = _clean_year(match.group("sy"))
        start_month = int(match.group("sd"))
        if start_year and 1 <= start_month <= 12:
            if match.group("present"):
                end, current = today_ym(), True
            else:
                end_year = _clean_year(match.group("ey"))
                end_month = int(match.group("ed"))
                if not end_year or not 1 <= end_month <= 12:
                    return None
                end, current = (end_year, end_month), False
            start, finish = (start_year, start_month), end
            if finish < start:
                start, finish = finish, start
            return DateRange(start=start, end=finish, raw=match.group(0), is_current=current)

    return None


def _merge_overlaps(ranges: list[DateRange]) -> list[DateRange]:
    """Merge overlapping ranges so concurrent roles are counted once."""
    if not ranges:
        return []
    ordered = sorted(ranges, key=lambda item: (item.start, item.end))
    merged = [ordered[0]]
    for item in ordered[1:]:
        last = merged[-1]
        if item.start <= last.end:
            if item.end > last.end:
                merged[-1] = DateRange(
                    start=last.start, end=item.end, raw=last.raw, is_current=last.is_current or item.is_current
                )
        else:
            merged.append(item)
    return merged


def sum_experience_years(lines: list[str], *, max_total: float = 50.0) -> float:
    """Total distinct years of employment mentioned across ``lines``.

    Overlapping tenures are merged, so a concurrent internship does not inflate
    a senior candidate's experience. The result is capped at ``max_total`` to
    reject nonsense parses.
    """
    ranges = [parsed for parsed in (parse_date_range(line) for line in lines) if parsed]
    if not ranges:
        return 0.0
    total_months = sum(item.months for item in _merge_overlaps(ranges))
    return round(min(total_months / 12.0, max_total), 2)


def find_graduation_years(text: str) -> list[int]:
    """Candidate graduation years mentioned in ``text``."""
    years: list[int] = []
    for token in _YEAR.findall(text or ""):
        year = _clean_year(token)
        if year and 1950 <= year <= date.today().year + 6:
            years.append(year)
    return sorted(set(years))


def looks_like_experience_header(line: str) -> bool:
    """Whether a line plausibly introduces a role (title @ company)."""
    normalized = normalize_text(line)
    if not normalized or len(normalized) < 4:
        return False
    markers = (
        " at ", " @ ", " engineer", " developer", " scientist", " analyst", " manager",
        " designer", " architect", " consultant", " specialist", " lead", " intern",
        " professor", " researcher", " associate", " technician", " officer",
    )
    return any(marker in normalized for marker in markers)

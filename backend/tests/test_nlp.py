"""Unit tests for the text parsers.

These cover the normalisations that were wrong in ways an integration test only
reveals indirectly: a dotted degree abbreviation silently scored zero on the
education component, and a "YYYY - Present" range was dropped entirely, so a
current job contributed no experience at all.
"""

from __future__ import annotations

import pytest

from app.nlp import dates, education


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("B.S. Computer Science", 4),
        ("B.Sc Computer Science", 4),
        ("BSc Computer Science", 4),
        ("Bachelor of Science", 4),
        ("B Tech Mechanical Engineering", 4),
        ("M.S. in Data Science", 5),
        ("MS Data Science", 5),
        ("MBA", 5),
        ("Ph.D. Physics", 6),
        ("PhD Physics", 6),
        ("High School", 1),
        ("", 0),
        (None, 0),
    ],
)
def test_degree_rank_handles_dotted_abbreviations(text: str | None, expected: int) -> None:
    """``B.S.`` is the most common spelling of a degree and must not score zero."""
    assert education.degree_rank(text) == expected


def test_education_line_is_split_into_fields() -> None:
    info = education.tidy_degree_fields(
        education.extract_education("B.S. Computer Science, University of California Davis, 2014")
    )
    assert info.degree == "B.S. Computer Science"
    assert info.field == "Computer Science"
    assert info.institution == "University of California Davis"
    assert info.graduation_year == 2014


def test_institution_keeps_at_inside_its_own_name() -> None:
    """"University of Texas at Austin" must not be cut at its internal " at "."""
    info = education.tidy_degree_fields(
        education.extract_education("B.S. Statistics, University of Texas at Austin, 2016")
    )
    assert info.institution == "University of Texas at Austin"
    assert info.degree == "B.S. Statistics"


def test_degree_without_comma_keeps_only_the_degree() -> None:
    info = education.tidy_degree_fields(
        education.extract_education("MS Computer Science at Stanford University")
    )
    assert info.degree == "MS Computer Science"
    assert info.institution == "Stanford University"


def test_highest_qualification_wins_over_listing_order() -> None:
    info = education.tidy_degree_fields(
        education.extract_education(
            "B.S. Computer Science, UC Davis, 2014\nM.S. Data Science, Stanford University, 2019"
        )
    )
    assert info.degree == "M.S. Data Science"
    assert info.rank == 5


def test_current_role_contributes_experience() -> None:
    parsed = dates.parse_date_range("2021 - Present")
    assert parsed is not None
    assert parsed.start == (2021, 1)
    assert parsed.is_current
    assert parsed.months > 0


def test_year_only_range_is_understood() -> None:
    parsed = dates.parse_date_range("Mar 2019 - 2022")
    assert parsed is not None
    assert parsed.start == (2019, 3)
    # A bare end year runs to December: "2019 - 2022" covers the whole of 2022,
    # not a single month of it.
    assert parsed.end == (2022, 12)
    assert not parsed.is_current
    assert parsed.months == 45


def test_unparseable_range_is_reported_as_unknown() -> None:
    assert dates.parse_date_range("somewhen") is None
"""NLP pipeline: document parsing, cleaning, sections, extraction, taxonomy.

Entry points
------------
* :func:`app.nlp.document.extract_text`  - bytes/PDF/DOCX/TXT -> clean text
* :func:`app.nlp.resume_parser.parse_resume` - text -> :class:`ParsedResume`
* :func:`app.nlp.job_parser.parse_job_description` - text -> :class:`ParsedJob`
* :mod:`app.nlp.skill_taxonomy`         - canonical skills + alias mapping

All modules in this package are pure: no database, no network, no settings
mutation. That is what makes the pipeline reproducible in notebooks and unit
testable without fixtures on disk.
"""

from app.nlp import (  # noqa: F401
    cleaning,
    contact,
    dates,
    document,
    education,
    job_parser,
    resume_parser,
    sections,
    skill_extractor,
    skill_taxonomy,
)

__all__ = [
    "cleaning",
    "contact",
    "dates",
    "document",
    "education",
    "job_parser",
    "resume_parser",
    "sections",
    "skill_extractor",
    "skill_taxonomy",
]

"""Explanation and evidence generation (PRD section 13).

A score a recruiter cannot interrogate is not usable in a hiring process. Every
match therefore ships with:

* a per-requirement breakdown (``MatchEvidence`` rows) recording the status,
  the confidence, the weight and the resume snippet that justified it;
* a human-readable ``MatchExplanation`` grouping strong matches, gaps and
  weak signals;
* an explicit reminder that the score is a screening aid.

Nothing in this module invents a fact. Every string is derived from the inputs
that produced the score.
"""

from __future__ import annotations

from app.ml.scoring import (
    STATUS_MATCHED,
    STATUS_MISSING,
    STATUS_WEAK,
    JobProfile,
    MatchComputation,
    SkillMatch,
)
from app.nlp.education import degree_rank
from app.utils.text import truncate

__all__ = ["build_evidence_rows", "build_explanation"]

_COMPONENT_LABEL = {
    "required_skills": "Required skill",
    "preferred_skills": "Preferred skill",
    "certification": "Certification",
}

SCREENING_DISCLAIMER = (
    "Screening aid only. This score is generated from resume text and job "
    "requirements and must not be treated as a hiring decision."
)


def _percent(value: float) -> str:
    return f"{round(value * 100)}%"


def _format_years(value: float) -> str:
    return f"{value:g} years"


def build_evidence_rows(computation: MatchComputation) -> list[dict[str, object]]:
    """Flatten a computation into persistable ``MatchEvidence`` dictionaries.

    Weights are the *effective* contribution of each item to its component, so
    the UI can show which requirement actually moved the score.
    """
    rows: list[dict[str, object]] = []

    def add_matches(matches: list[SkillMatch], component: str) -> None:
        total = len(matches) or 1
        for item in matches:
            rows.append(
                {
                    "component": component,
                    "label": item.term,
                    "detail": _detail_for(item),
                    "status": item.status,
                    "weight": round(item.contribution / total, 4),
                    "snippet": item.evidence,
                }
            )

    add_matches(computation.required_matches, "required_skills")
    add_matches(computation.preferred_matches, "preferred_skills")
    add_matches(computation.certification_matches, "certifications")

    rows.append(
        {
            "component": "experience",
            "label": "Experience",
            "detail": _experience_detail(computation),
            "status": STATUS_MATCHED if computation.experience_score >= 0.999 else (
                STATUS_MISSING if computation.experience_score <= 0.001 else STATUS_WEAK
            ),
            "weight": round(computation.weights.get("experience", 0.0), 4),
            "snippet": None,
        }
    )

    if computation.applicable.get("education"):
        rows.append(
            {
                "component": "education",
                "label": "Education",
                "detail": _education_detail(computation),
                "status": STATUS_MATCHED if computation.education_score >= 0.999 else (
                    STATUS_MISSING if computation.education_score <= 0.001 else STATUS_WEAK
                ),
                "weight": round(computation.weights.get("education", 0.0), 4),
                "snippet": None,
            }
        )

    if computation.applicable.get("semantic"):
        rows.append(
            {
                "component": "semantic",
                "label": "Semantic similarity",
                "detail": (
                    f"Overall similarity between the job description and the resume is "
                    f"{_percent(computation.semantic_score)} after rescaling."
                ),
                "status": STATUS_MATCHED if computation.semantic_score >= 0.6 else STATUS_WEAK,
                "weight": round(computation.weights.get("semantic", 0.0), 4),
                "snippet": None,
            }
        )

    return rows


def _detail_for(item: SkillMatch) -> str:
    if item.status == STATUS_MATCHED:
        base = f"Found in the resume (confidence {_percent(item.confidence)})"
        if item.years_experience:
            base += f", ~{item.years_experience:g} years claimed"
        return base
    if item.status == STATUS_WEAK:
        if item.similarity is not None and item.confidence < 0.9:
            return (
                "Not listed explicitly; the resume text is semantically similar "
                f"({_percent(item.similarity)}), so this is unconfirmed"
            )
        return (
            f"Mentioned in the resume with low confidence ({_percent(item.confidence)}); "
            "treat as unconfirmed"
        )
    if item.similarity is not None and item.similarity > 0:
        return f"No evidence of this skill (text similarity only {_percent(item.similarity)})"
    return "No evidence of this skill was found in the resume"


def _experience_detail(computation: MatchComputation) -> str:
    if not computation.applicable.get("experience"):
        return "This job does not state a minimum experience requirement"
    score = computation.experience_score
    if score >= 0.999:
        return "Meets or exceeds the required experience"
    if score <= 0.001:
        return "No dated experience was detected in the resume"
    return f"Meets approximately {_percent(score)} of the required experience"


def _education_detail(computation: MatchComputation) -> str:
    if not computation.applicable.get("education"):
        return "Education could not be evaluated"
    score = computation.education_score
    if score >= 0.999:
        return "Highest qualification meets or exceeds the requirement"
    if score <= 0.001:
        return "Highest qualification is below the stated requirement"
    return f"Highest qualification is {_percent(score)} of the way to the requirement"


def build_explanation(job: JobProfile, computation: MatchComputation) -> dict[str, object]:
    """Assemble the ``MatchExplanation`` payload stored on ``MatchResult``.

    Every string is derived from the computation that produced the score, so
    the narrative can never drift from the numbers.
    """
    strong: list[str] = [item.term for item in computation.matched_required]
    missing: list[str] = [item.term for item in computation.missing_required]
    weak: list[str] = [item.term for item in computation.weak_required]
    preferred: list[str] = [item.term for item in computation.preferred_matches if item.is_satisfied]

    headline = (
        f"{_percent(computation.overall_score)} overall match for {job.title}. "
        f"{len(strong)} of {len(computation.required_matches)} required skills were found explicitly"
        if computation.required_matches
        else f"{_percent(computation.overall_score)} overall match for {job.title}."
    )

    explanation: dict[str, object] = {
        "summary": headline,
        "strong_matches": strong,
        "preferred_matches": preferred,
        "missing_required": missing,
        "weak_signals": weak,
        "experience_note": _experience_note(job, computation),
        "education_note": _education_note(job, computation),
        "certification_note": _certification_note(computation),
        "semantic_note": _semantic_note(computation),
        "recommendation": SCREENING_DISCLAIMER,
    }

    notes = list(computation.notes)
    if weak:
        notes.append(
            "Weak signals are semantic or low-confidence matches. Confirm them with the "
            "candidate before treating them as skills."
        )
    if missing:
        notes.append(
            "Missing requirements may still be present but phrased differently; review the "
            "original resume."
        )
    explanation["notes"] = notes
    return explanation


def _experience_note(job: JobProfile, computation: MatchComputation) -> str | None:
    if not computation.applicable.get("experience"):
        return None
    required = job.experience_required_years
    have = required * computation.experience_score
    if computation.experience_score >= 0.999:
        return f"At least {_format_years(required)} of relevant experience detected"
    if have <= 0:
        return "No dated experience could be parsed from the resume"
    return (
        f"Approximately {_format_years(round(have, 1))} of relevant experience detected "
        f"against {_format_years(required)} required"
    )


def _education_note(job: JobProfile, computation: MatchComputation) -> str | None:
    if not job.education_required:
        return None
    if not computation.applicable.get("education"):
        return (
            f"This job asks for {truncate(job.education_required, 80)}, but no qualification "
            "could be detected in the resume, so education did not affect the score."
        )
    if computation.education_score >= 0.999:
        return f"Qualification meets the {truncate(job.education_required, 60)} requirement"
    return f"Detected qualification is below the {truncate(job.education_required, 60)} requirement"


def _certification_note(computation: MatchComputation) -> str | None:
    if not computation.certification_matches:
        return None
    held = [item.term for item in computation.certification_matches if item.is_satisfied]
    if not held:
        return "None of the preferred certifications were found"
    return f"Certifications matched: {', '.join(held)}"


def _semantic_note(computation: MatchComputation) -> str | None:
    if not computation.applicable.get("semantic"):
        return None
    return (
        f"Resume and job description are {_percent(computation.semantic_score)} similar "
        "overall after rescaling"
    )


def describe_degree(degree: str | None) -> str:
    """Human label for a degree, used in comparison views."""
    if not degree:
        return "Not detected"
    rank = degree_rank(degree)
    label = {1: "Secondary", 2: "Diploma", 3: "Associate", 4: "Bachelor's", 5: "Master's", 6: "Doctorate"}.get(
        rank, "Unrecognised"
    )
    return f"{truncate(degree, 80)} ({label})"

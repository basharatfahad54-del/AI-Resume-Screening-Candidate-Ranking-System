"""Screening assistant (PRD section 17) and dashboard read models.

Two hard rules, both enforced here rather than in the route:

1. **Read only.** Nothing in this module writes to candidates, jobs or matches
   except the conversation transcript. A chat turn must never be able to
   shortlist, delete or rescore anyone.
2. **Every number comes from the database.** The assistant answers from stored
   matches and parsed profiles; it never recomputes a score, so it cannot
   contradict the ranking screen.

Intent routing happens before any model call: the deterministic intents cover
the questions recruiters actually ask, which keeps the common path free of
latency, cost and hallucination risk. The LLM is a fallback for free-form
questions and receives PII-redacted, aggregate-only context.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.logging import get_logger
from app.models import Candidate, CandidateSkill, Conversation, Job, MatchResult, ProcessingStatus, Skill
from app.nlp.llm import complete_json, is_enabled, llm_status
from app.schemas.assistant import AssistantQuery, AssistantResponse, TablePayload
from app.services import matching as matching_service
from app.utils.jsonio import dumps, loads_dict, loads_list

logger = get_logger(__name__)

__all__ = ["AssistantError", "answer", "assistant_status"]

_MAX_CONTEXT_CANDIDATES = 25
_DISCLAIMER = (
    "Decision-support only. These results come from resume text and job "
    "requirements; the decision to progress or reject a candidate stays with a "
    "human reviewer."
)


class AssistantError(Exception):
    """The question could not be answered."""


@dataclass(frozen=True, slots=True)
class Intent:
    name: str
    pattern: re.Pattern[str]


# Order matters: the first match wins, so specific intents precede general ones.
_INTENTS: tuple[Intent, ...] = (
    Intent("top_candidates", re.compile(r"\btop\b|\bbest\s+match|\bhighest\s+match|\brank(?:ed|ing)?\b", re.I)),
    Intent("missing_skills", re.compile(r"\bmissing\b|\black(?:s|ing)?\b|\bdon'?t have\b|\bwithout\b|\bgap\b", re.I)),
    Intent("shortlist", re.compile(r"\bshortlist|\bshortlisted\b", re.I)),
    Intent("compare", re.compile(r"\bcompare\b|\bversus\b|\bvs\.?\b|\bdifference\b", re.I)),
    Intent("candidate_detail", re.compile(r"\bwho is\b|\bprofile\b|\bresume\b|\bcandidate\b", re.I)),
    Intent("job_summary", re.compile(r"\bjob\b|\brole\b|\bposition\b|\brequirement", re.I)),
    Intent("counts", re.compile(r"\bhow many\b|\bcount\b|\bnumber of\b|\btotal\b", re.I)),
)


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
async def answer(
    session: AsyncSession, query: AssistantQuery, *, user_id: int | None = None
) -> AssistantResponse:
    """Answer a screening question about the stored data."""
    text = query.message.strip()
    if not text:
        raise AssistantError("A question is required.")

    intent = _detect_intent(text)
    job = await _resolve_job(session, query.job_id, text)

    handler = {
        "top_candidates": _answer_top,
        "missing_skills": _answer_missing,
        "shortlist": _answer_shortlist,
        "compare": _answer_compare,
        "candidate_detail": _answer_candidate,
        "job_summary": _answer_job,
        "counts": _answer_counts,
    }.get(intent)

    if handler is not None:
        response = await handler(session, text, query, job)
    elif is_enabled():
        response = await _answer_with_llm(session, text, query, job)
    else:
        response = _answer_fallback(text)

    response.conversation_id = await _record(session, response, query, user_id, intent)
    response.suggestions = _suggestions(intent)
    return response


def _detect_intent(text: str) -> str | None:
    for intent in _INTENTS:
        if intent.pattern.search(text):
            return intent.name
    return None


async def _resolve_job(session: AsyncSession, job_id: int | None, text: str) -> Job | None:
    """Use the explicit job, else the only job, else try to spot a title mention."""
    if job_id is not None:
        return (await session.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()

    jobs = list(
        (
            await session.execute(
                select(Job).where(Job.is_active.is_(True)).order_by(Job.id)
            )
        )
        .scalars()
        .all()
    )
    if len(jobs) == 1:
        return jobs[0]

    lowered = text.lower()
    for job in jobs:
        title = (job.title or "").lower()
        if title and (title in lowered or title.split()[0] in lowered):
            return job
    return None


def _no_job() -> AssistantResponse:
    return AssistantResponse(
        answer=(
            "I need a job to answer that. Open a job and ask again, or tell me "
            "which position you mean."
        ),
        intent="job_required",
        suggestions=_suggestions("top_candidates"),
    )


# --------------------------------------------------------------------------- #
# Intent handlers - all read-only
# --------------------------------------------------------------------------- #
async def _answer_top(
    session: AsyncSession, text: str, query: AssistantQuery, job: Job | None
) -> AssistantResponse:
    if job is None:
        return _no_job()
    pairs, _weights = await matching_service.load_rankings(
        session, job, sort_by=_sort_field(text), limit=query.top_k
    )
    if not pairs:
        return AssistantResponse(
            answer=f"No candidates have been scored against {job.title!r} yet.",
            intent="top_candidates",
            tables=[],
        )

    rows = [
        [
            index,
            candidate.full_name,
            round(match.overall_score, 4),
            round(match.required_skills_score, 4),
            round(match.experience_score, 4),
            "yes" if candidate.shortlisted else "no",
        ]
        for index, (candidate, match) in enumerate(pairs, start=1)
    ]
    leader = pairs[0]
    return AssistantResponse(
        answer=(
            f"{len(pairs)} candidates scored against {job.title!r}. "
            f"{leader[0].full_name} leads at {leader[1].overall_score:.0%} overall, with "
            f"{leader[1].required_skills_score:.0%} of the required skills matched. "
            f"Scores rank resume evidence only."
        ),
        intent="top_candidates",
        mode="structured_query",
        tables=[
            TablePayload(
                title=f"Top {len(rows)} candidates for {job.title}",
                columns=[
                    "Rank",
                    "Candidate",
                    "Overall",
                    "Required skills",
                    "Experience",
                    "Shortlisted",
                ],
                rows=rows,
            )
        ],
        sources=[{"type": "match_results", "job_id": job.id, "rows": len(rows)}],
    )


async def _answer_missing(
    session: AsyncSession, text: str, query: AssistantQuery, job: Job | None
) -> AssistantResponse:
    if job is None:
        return _no_job()
    pairs, _weights = await matching_service.load_rankings(
        session, job, sort_by="required_skills", limit=query.top_k
    )
    if not pairs:
        return AssistantResponse(
            answer=f"No candidates have been scored against {job.title!r} yet.",
            intent="missing_skills",
        )

    rows: list[list[object]] = []
    leaders: list[str] = []
    for candidate, match in pairs:
        explanation = loads_dict(match.explanation)
        missing = [str(term) for term in explanation.get("missing_required", []) or []]
        weak = [str(term) for term in explanation.get("weak_signals", []) or []]
        if missing:
            leaders.append(candidate.full_name)
        rows.append([candidate.full_name, ", ".join(missing) or "none", ", ".join(weak) or "none"])

    if not leaders:
        return AssistantResponse(
            answer=(
                f"None of the {len(pairs)} scored candidates is missing a required skill for "
                f"{job.title!r}. Verify against the original resumes before relying on that."
            ),
            intent="missing_skills",
            tables=[
                TablePayload(
                    title="Missing requirements",
                    columns=["Candidate", "Missing required", "Weak signals"],
                    rows=rows,
                )
            ],
        )
    return AssistantResponse(
        answer=(
            f"{len(leaders)} of {len(pairs)} candidates are missing at least one required skill "
            f"for {job.title!r}. A missing skill may simply be phrased differently - check the "
            f"resume before treating it as absent."
        ),
        intent="missing_skills",
        mode="structured_query",
        tables=[
            TablePayload(
                title=f"Missing requirements for {job.title}",
                columns=["Candidate", "Missing required", "Weak signals"],
                rows=rows,
            )
        ],
        sources=[{"type": "match_explanations", "job_id": job.id}],
    )


async def _answer_shortlist(
    session: AsyncSession, text: str, query: AssistantQuery, job: Job | None
) -> AssistantResponse:
    stmt = (
        select(Candidate)
        .where(Candidate.is_deleted.is_(False), Candidate.shortlisted.is_(True))
        .options(selectinload(Candidate.skills).selectinload(CandidateSkill.skill))
        .order_by(Candidate.updated_at.desc())
        .limit(query.top_k)
    )
    candidates = (await session.execute(stmt)).scalars().unique().all()
    if not candidates:
        return AssistantResponse(
            answer="Nobody has been shortlisted yet.", intent="shortlist"
        )
    rows = [
        [
            candidate.full_name,
            candidate.current_title or "-",
            candidate.total_experience_years,
            ", ".join(link.skill.name for link in candidate.skills[:6] if link.skill) or "-",
        ]
        for candidate in candidates
    ]
    return AssistantResponse(
        answer=(
            f"{len(candidates)} candidate(s) shortlisted"
            + (f" for {job.title!r}" if job else "")
            + "."
        ),
        intent="shortlist",
        mode="structured_query",
        tables=[
            TablePayload(
                title="Shortlisted candidates",
                columns=["Name", "Current title", "Years experience", "Top skills"],
                rows=rows,
            )
        ],
    )


async def _answer_compare(
    session: AsyncSession, text: str, query: AssistantQuery, job: Job | None
) -> AssistantResponse:
    names = _named_candidates(text)
    if len(names) < 2:
        return AssistantResponse(
            answer=(
                "Name two candidates to compare, for example \"compare Ahmed and Bilal\". "
                "I will show the differences in their extracted data, not a recommendation."
            ),
            intent="compare",
        )

    found: list[Candidate] = []
    for name in names:
        pattern = f"%{name.strip()}%"
        candidate = (
            await session.execute(
                select(Candidate)
                .where(Candidate.is_deleted.is_(False), Candidate.full_name.ilike(pattern))
                .options(selectinload(Candidate.skills).selectinload(CandidateSkill.skill))
                .limit(1)
            )
        ).scalars().first()
        if candidate is not None:
            found.append(candidate)

    if len(found) < 2:
        return AssistantResponse(
            answer=(
                "I could not find two matching candidates"
                + (f" for {job.title!r}" if job else "")
                + ". Check the names, or compare them from the candidates page."
            ),
            intent="compare",
        )

    comparison = await matching_service.compare_candidates(
        session,
        candidate_ids=[candidate.id for candidate in found],
        job_id=job.id if job else None,
    )
    return AssistantResponse(
        answer=(
            f"Compared {', '.join(comparison.columns)} on "
            f"{', '.join(row.label for row in comparison.rows)}. This is a factual "
            f"comparison of extracted data only."
        ),
        intent="compare",
        mode="structured_query",
        tables=[
            TablePayload(
                title="Comparison",
                columns=["Criterion", *comparison.columns],
                rows=[[row.label, *[row.values.get(name) for name in comparison.columns]] for row in comparison.rows],
            )
        ],
    )


async def _answer_candidate(
    session: AsyncSession, text: str, query: AssistantQuery, job: Job | None
) -> AssistantResponse:
    name = _named_candidates(text)
    stmt = select(Candidate).where(Candidate.is_deleted.is_(False))
    if name:
        stmt = stmt.where(Candidate.full_name.ilike(f"%{name[0].strip()}%"))
    elif job is not None:
        stmt = stmt.join(MatchResult, MatchResult.candidate_id == Candidate.id).where(
            MatchResult.job_id == job.id
        )
    candidate = (
        await session.execute(
            stmt.options(selectinload(Candidate.skills).selectinload(CandidateSkill.skill)).limit(1)
        )
    ).scalars().first()

    if candidate is None:
        return AssistantResponse(
            answer="I could not find that candidate. Check the name, or search from the candidates page.",
            intent="candidate_detail",
        )

    profile = matching_service.candidate_profile(candidate)
    rows: list[list[object]] = [
        ["Current title", candidate.current_title or "-"],
        ["Location", candidate.location or "-"],
        ["Email", candidate.email or "-"],
        ["Total experience (years)", candidate.total_experience_years],
        ["Highest degree", candidate.highest_degree or "-"],
        ["Institution", candidate.institution or "-"],
        ["Certifications", ", ".join(loads_list(candidate.certifications)) or "-"],
        ["Skills found", ", ".join(signal.display_name for signal in profile.skills) or "-"],
        ["Processing status", getattr(candidate.status, "value", candidate.status)],
    ]

    match_stmt = select(MatchResult).where(MatchResult.candidate_id == candidate.id)
    if job is not None:
        match_stmt = match_stmt.where(MatchResult.job_id == job.id)
    match = (await session.execute(match_stmt.order_by(MatchResult.created_at.desc()).limit(1))).scalar_one_or_none()

    tables = [TablePayload(title=f"Profile: {candidate.full_name}", columns=["Field", "Value"], rows=rows)]
    answer_text = (
        f"{candidate.full_name}: {candidate.current_title or 'title not detected'}, "
        f"{candidate.total_experience_years or 0:g} years total experience, "
        f"{len(profile.skills)} skills on file."
    )
    if match is not None and job is not None:
        explanation = loads_dict(match.explanation)
        tables.append(
            TablePayload(
                title=f"Match against {job.title}",
                columns=["Component", "Score"],
                rows=[
                    ["Overall", round(match.overall_score, 4)],
                    ["Required skills", round(match.required_skills_score, 4)],
                    ["Experience", round(match.experience_score, 4)],
                    ["Preferred skills", round(match.preferred_skills_score, 4)],
                    ["Semantic", round(match.semantic_score, 4)],
                ],
            )
        )
        answer_text += (
            f" Scored {match.overall_score:.0%} against {job.title!r}: "
            f"{explanation.get('summary', '')}"
        )
    return AssistantResponse(
        answer=answer_text,
        intent="candidate_detail",
        mode="structured_query",
        tables=tables,
    )


async def _answer_job(
    session: AsyncSession, text: str, query: AssistantQuery, job: Job | None
) -> AssistantResponse:
    if job is None:
        jobs = list(
            (
                await session.execute(
                    select(Job).where(Job.is_active.is_(True)).order_by(Job.title).limit(20)
                )
            )
            .scalars()
            .all()
        )
        if not jobs:
            return AssistantResponse(answer="There are no active jobs yet.", intent="job_summary")
        return AssistantResponse(
            answer=f"{len(jobs)} active job(s): " + ", ".join(job_row.title for job_row in jobs),
            intent="job_summary",
            mode="structured_query",
            tables=[
                TablePayload(
                    title="Active jobs",
                    columns=["Id", "Title", "Department", "Location"],
                    rows=[
                        [row.id, row.title, row.department or "-", row.location or "-"]
                        for row in jobs
                    ],
                )
            ],
        )

    profile = matching_service.job_profile(job)
    scored = int(
        (
            await session.execute(
                select(func.count(MatchResult.id)).where(MatchResult.job_id == job.id)
            )
        ).scalar_one()
        or 0
    )
    rows: list[list[object]] = [
        ["Title", job.title],
        ["Department", job.department or "-"],
        ["Location", job.location or "-"],
        ["Employment type", job.employment_type or "-"],
        ["Experience required (years)", job.experience_required_years or 0],
        ["Education required", job.education_required or "not specified"],
        ["Required skills", ", ".join(profile.required_skills) or "-"],
        ["Preferred skills", ", ".join(profile.preferred_skills) or "-"],
        ["Certifications", ", ".join(profile.certifications) or "-"],
        ["Candidates scored", scored],
    ]
    return AssistantResponse(
        answer=(
            f"{job.title} needs {job.experience_required_years or 0:g} years of experience and "
            f"{len(profile.required_skills)} required skills. {scored} candidate(s) scored so far."
        ),
        intent="job_summary",
        mode="structured_query",
        tables=[TablePayload(title=f"Job: {job.title}", columns=["Field", "Value"], rows=rows)],
        sources=[{"type": "jobs", "job_id": job.id}],
    )


async def _answer_counts(
    session: AsyncSession, text: str, query: AssistantQuery, job: Job | None
) -> AssistantResponse:
    live = Candidate.is_deleted.is_(False)
    total = int((await session.execute(select(func.count(Candidate.id)).where(live))).scalar_one() or 0)
    parsed = int(
        (
            await session.execute(
                select(func.count(Candidate.id)).where(
                    live, Candidate.status == ProcessingStatus.parsed
                )
            )
        ).scalar_one()
        or 0
    )
    jobs = int(
        (await session.execute(select(func.count(Job.id)).where(Job.is_active.is_(True)))).scalar_one()
        or 0
    )
    skills = int((await session.execute(select(func.count(Skill.id)))).scalar_one() or 0)
    return AssistantResponse(
        answer=(
            f"{total} candidate(s) on file, {parsed} parsed successfully, {jobs} active job(s), "
            f"and {skills} skills in the taxonomy."
        ),
        intent="counts",
        mode="structured_query",
        tables=[
            TablePayload(
                title="Counts",
                columns=["Metric", "Value"],
                rows=[
                    ["Candidates", total],
                    ["Parsed", parsed],
                    ["Active jobs", jobs],
                    ["Taxonomy skills", skills],
                ],
            )
        ],
    )


# --------------------------------------------------------------------------- #
# LLM fallback
# --------------------------------------------------------------------------- #
async def _answer_with_llm(
    session: AsyncSession, text: str, query: AssistantQuery, job: Job | None
) -> AssistantResponse:
    """Free-form question: hand the model aggregate, PII-free context only.

    Candidate names, emails and raw text are never included. The model is asked
    for JSON so the answer and its citations stay separable, and a failure falls
    back to the deterministic reply rather than surfacing an error to a
    recruiter mid-screening.
    """
    context = await _aggregate_context(session, job)
    status = llm_status()
    result = await complete_json(
        system_prompt=(
            "You are a screening assistant for a resume screening tool. Answer only from the "
            "supplied aggregates. Never invent candidate names, scores or skills. Never "
            "recommend hiring, rejecting or ranking a person, and never infer age, gender, "
            "ethnicity, nationality, disability or any protected characteristic. If the data "
            "does not answer the question, say so. Return JSON with keys 'answer' (string) and "
            "'sources' (array of strings)."
        ),
        user_payload={"question": text, "data": context},
    )

    if not isinstance(result, dict) or not result.get("answer"):
        logger.info("LLM assistant returned no usable payload; using the safe fallback")
        return _answer_fallback(text)

    return AssistantResponse(
        answer=str(result["answer"])[:4000],
        mode="llm",
        intent="freeform",
        sources=[{"provider": status.provider, "model": status.model}],
        tables=[],
    )


async def _aggregate_context(session: AsyncSession, job: Job | None) -> dict[str, object]:
    """Counts and skill frequencies only - no personal data."""
    live = Candidate.is_deleted.is_(False)
    top_skills = (
        await session.execute(
            select(Skill.name, func.count(CandidateSkill.id))
            .join(CandidateSkill, CandidateSkill.skill_id == Skill.id)
            .join(Candidate, Candidate.id == CandidateSkill.candidate_id)
            .where(live)
            .group_by(Skill.name)
            .order_by(func.count(CandidateSkill.id).desc())
            .limit(20)
        )
    ).all()

    context: dict[str, object] = {
        "candidate_count": int(
            (await session.execute(select(func.count(Candidate.id)).where(live))).scalar_one() or 0
        ),
        "most_common_skills": [{"skill": name, "candidates": int(count)} for name, count in top_skills],
    }
    if job is not None:
        pairs, weights = await matching_service.load_rankings(session, job, limit=_MAX_CONTEXT_CANDIDATES)
        if weights is not None:
            context["scoring_weights"] = weights.model_dump()
        context["job"] = {
            "title": job.title,
            "required_skills": loads_list(job.required_skills),
            "preferred_skills": loads_list(job.preferred_skills),
            "experience_required_years": job.experience_required_years,
        }
        context["score_summary"] = {
            "scored": len(pairs),
            "average": round(sum(match.overall_score for _c, match in pairs) / len(pairs), 4)
            if pairs
            else 0.0,
            "required_skill_average": round(
                sum(match.required_skills_score for _c, match in pairs) / len(pairs), 4
            )
            if pairs
            else 0.0,
        }
    return context


def _answer_fallback(text: str) -> AssistantResponse:
    return AssistantResponse(
        answer=(
            "I could not map that to a screening question"
            + (f" (you asked: {text[:120]!r})" if text else "")
            + ". I can rank candidates, list missing requirements, show the shortlist, "
            "compare two people, summarise a job or a candidate, or give counts."
        ),
        intent="unknown",
        mode="structured_query",
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
_SORT_HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bskill", re.I), "required_skills"),
    (re.compile(r"\bexperien", re.I), "experience"),
    (re.compile(r"\beducat|degree", re.I), "education"),
    (re.compile(r"\bsemantic|similar|relevan", re.I), "semantic"),
    (re.compile(r"\bcertif", re.I), "certifications"),
)


def _sort_field(text: str) -> str:
    for pattern, field in _SORT_HINTS:
        if pattern.search(text):
            return field
    return "overall"


def _named_candidates(text: str) -> list[str]:
    """Best-effort capture of the names mentioned in a question."""
    lowered = text.lower()
    for marker in ("compare ", "compare between ", "difference between ", " vs "):
        if marker in lowered:
            tail = lowered.split(marker, 1)[1]
            tail = tail.split("?")[0]
            parts = re.split(r"\s*(?:,|\band\b|\bversus\b|\bvs\.?\b)\s*", tail)
            return [part.strip(" .") for part in parts if len(part.strip(" .")) > 1][:2]
    quoted = re.findall(r"[\"']([^\"']{2,60})[\"']", text)
    if quoted:
        return quoted[:2]
    return []


_SUGGESTIONS: dict[str, list[str]] = {
    "top_candidates": ["Who is missing the most required skills?", "Which candidates are shortlisted?"],
    "missing_skills": ["Show me the top ranked candidates", "Summarise this job"],
    "shortlist": ["Who are the top candidates?", "Compare two candidates"],
    "compare": ["Who is missing the most required skills?", "Summarise this job"],
    "candidate_detail": ["Who are the top candidates?", "How many candidates are there?"],
    "job_summary": ["Who are the top candidates for this job?", "Which candidates are shortlisted?"],
    "counts": ["Who are the top candidates?", "Summarise this job"],
    "job_required": ["Who are the top candidates?", "Summarise a job"],
}


def _suggestions(intent: str | None) -> list[str]:
    return _SUGGESTIONS.get(intent or "", _SUGGESTIONS["counts"])


async def _record(
    session: AsyncSession,
    response: AssistantResponse,
    query: AssistantQuery,
    user_id: int | None,
    intent: str | None,
) -> int | None:
    """Append the turn to the conversation transcript.

    Transcript only - an assistant turn is not an audit-worthy state change, so
    the answer text is stored but the candidate and match tables are untouched.
    """
    conversation: Conversation | None = None
    if query.conversation_id is not None:
        conversation = (
            await session.execute(
                select(Conversation).where(Conversation.id == query.conversation_id)
            )
        ).scalar_one_or_none()

    if conversation is None:
        conversation = Conversation(
            user_id=user_id,
            job_id=query.job_id,
            title=query.message.strip()[:120],
            messages="[]",
        )
        session.add(conversation)
        await session.flush()

    history = loads_list(conversation.messages)
    history.append({"role": "user", "content": query.message.strip()[:2000]})
    history.append({"role": "assistant", "content": response.answer[:4000], "intent": intent})
    conversation.messages = dumps(history[-40:])
    await session.flush()
    return conversation.id


def assistant_status() -> dict[str, object]:
    status = llm_status()
    return {
        "llm_enabled": status.enabled,
        "provider": status.provider,
        "model": status.model,
        "detail": status.detail,
        "intents": [intent.name for intent in _INTENTS],
        "disclaimer": _DISCLAIMER,
    }
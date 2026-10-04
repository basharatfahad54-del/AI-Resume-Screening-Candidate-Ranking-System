"""Deterministic synthetic corpus of jobs, candidates and graded relevance labels.

Why synthetic
-------------
The project must be demonstrable, testable and shareable without collecting or
shipping real resumes. Every record is generated from a seeded RNG, every name
is a placeholder, and no field can identify a real person.

How the labels are made meaningful
----------------------------------
Each candidate receives a *latent* skill depth vector over every role family.
Only part of that depth is visible in the CSV:

* ``declared_skills`` - what the candidate claims, and what the rule-based
  engine can match exactly;
* ``hidden_skills`` - expertise mentioned only in the free-text ``summary``.

The relevance grade is computed from the **latent** depth, so a pair can be
relevant even when the declared list misses the requirement. That is the whole
point of the experiment: the interpretable baseline only sees declared skills,
while a text-aware learned model can recover the hidden evidence. Labels are
therefore learnable but noisy, exactly like real screening data.

Protected attributes (``gender``, ``birth_year``) are generated, written to the
CSV for fairness auditing, and never used as features or in the label function.
``ml/tests/test_fairness.py`` enforces both invariants.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from random import Random

from .backend_bridge import degree_rank

__all__ = [
    "CANDIDATE_FIELDS",
    "JOB_FIELDS",
    "LABEL_FIELDS",
    "PROTECTED_CANDIDATE_FIELDS",
    "Candidate",
    "Corpus",
    "Job",
    "Label",
    "ValidationError",
    "generate_corpus",
    "load_corpus",
    "write_corpus",
]

# --------------------------------------------------------------------------- #
# Taxonomy
# --------------------------------------------------------------------------- #
#: Role families with their realistic skill pools, ordered by frequency so a
#: truncated sample keeps a natural long tail.
ROLE_FAMILIES: dict[str, dict[str, object]] = {
    "backend": {
        "label": "Backend Engineer",
        "core": ["python", "sql", "postgresql", "redis", "docker", "rest api", "kafka"],
        "extra": ["celery", "rabbitmq", "graphql", "terraform", "aws", "django", "fastapi"],
    },
    "frontend": {
        "label": "Frontend Engineer",
        "core": ["typescript", "react", "css", "html", "vite", "testing library", "accessibility"],
        "extra": ["next.js", "redux", "tailwind", "web performance", "storybook", "figma"],
    },
    "data": {
        "label": "Data Scientist",
        "core": ["python", "pandas", "sql", "statistics", "machine learning", "data cleaning"],
        "extra": ["spark", "dbt", "airflow", "experimentation", "tableau", "hugging face"],
    },
    "ml": {
        "label": "Machine Learning Engineer",
        "core": ["python", "pytorch", "scikit-learn", "model evaluation", "feature engineering", "mlops"],
        "extra": ["transformers", "mlflow", "ray", "cuda", "vector search", "data versioning"],
    },
    "devops": {
        "label": "Platform Engineer",
        "core": ["linux", "docker", "kubernetes", "terraform", "ci/cd", "monitoring", "aws"],
        "extra": ["ansible", "prometheus", "grafana", "helm", "incident response", "gcp"],
    },
    "security": {
        "label": "Security Engineer",
        "core": ["threat modelling", "python", "penetration testing", "iam", "cryptography", "incident response"],
        "extra": ["burp suite", "static analysis", "owasp", "soc", "zero trust", "fuzzing"],
    },
}

CERTIFICATIONS: dict[str, list[str]] = {
    "backend": ["AWS Solutions Architect", "Certified Kubernetes Administrator"],
    "frontend": ["Google UX Certificate", "Meta Frontend Developer"],
    "data": ["TensorFlow Developer Certificate", "Tableau Desktop Specialist"],
    "ml": ["AWS Machine Learning Specialty", "Deep Learning Specialization"],
    "devops": ["CKA", "Certified Terraform Associate"],
    "security": ["OSCP", "CompTIA Security+"],
}

DEGREES = ["high school", "associate", "bachelor", "master", "phd"]
#: Degrees each family treats as a sensible floor, used when generating jobs.
TYPICAL_DEGREE = {
    "backend": "bachelor",
    "frontend": "bachelor",
    "data": "master",
    "ml": "master",
    "devops": "bachelor",
    "security": "bachelor",
}

GENDERS = ["female", "male", "non-binary", "undisclosed"]
FIRST_NAMES = [
    "Alex", "Bailey", "Casey", "Devon", "Ellis", "Finley", "Gray", "Harper", "Indigo", "Jules",
    "Kai", "Logan", "Marlowe", "Noel", "Oakley", "Parker", "Quinn", "Reese", "Sage", "Tatum",
]
LAST_NAMES = [
    "Alder", "Brook", "Cedar", "Dune", "Ember", "Fern", "Grove", "Heath", "Isla", "Juniper",
    "Kestrel", "Linden", "Mesa", "North", "Orchard", "Prairie", "Quarry", "Ridge", "Slate", "Thicket",
]
#: Neutral filler so generated summaries read like real prose.
SUMMARY_TEMPLATES = [
    "Delivered {n} production releases across {domain}, partnering with product and design.",
    "Owned {domain} for {n} years; reduced operational toil through automation and observability.",
    "Built and maintained services in {domain}; mentored {n} junior engineers.",
    "Led a {n}-person {domain} team through a platform migration with zero downtime.",
    "Focused on {domain} quality, documentation and measurable business outcomes.",
]
DOMAIN_PHRASES = {
    "backend": "backend services", "frontend": "frontend interfaces", "data": "analytics",
    "ml": "machine learning systems", "devops": "developer platforms", "security": "security posture",
}
PROJECT_PHRASES = {
    "backend": "an order pipeline handling 12k requests per second",
    "frontend": "a design-system driven component library",
    "data": "a self-serve metrics layer used by every product team",
    "ml": "a ranking model that lifted conversion by 8%",
    "devops": "a multi-region deployment pipeline with automated rollback",
    "security": "a threat model and detection pipeline for the production estate",
}

JOB_FIELDS = [
    "job_id", "title", "role_family", "required_skills", "preferred_skills",
    "certifications", "experience_required_years", "education_required", "description",
]
CANDIDATE_FIELDS = [
    "candidate_id", "full_name", "role_family", "declared_skills", "hidden_skills",
    "years_experience", "highest_degree", "certifications", "summary",
    "gender", "birth_year",
]
LABEL_FIELDS = ["job_id", "candidate_id", "relevance"]
#: Written for auditing only; never fed to the feature extractor or the labels.
PROTECTED_CANDIDATE_FIELDS = ("gender", "birth_year")

#: Multi-valued cells are pipe separated so the CSVs stay dependency free.
_DELIMITER = "|"


# --------------------------------------------------------------------------- #
# Records
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Job:
    job_id: int
    title: str
    role_family: str
    required_skills: tuple[str, ...]
    preferred_skills: tuple[str, ...]
    certifications: tuple[str, ...]
    experience_required_years: float
    education_required: str | None
    description: str

    @property
    def text(self) -> str:
        """Single document used for embedding and TF-IDF."""
        return " ".join(
            part
            for part in (
                self.title,
                self.role_family,
                self.description,
                " ".join(self.required_skills),
                " ".join(self.preferred_skills),
                " ".join(self.certifications),
                self.education_required or "",
            )
            if part
        )


@dataclass(frozen=True, slots=True)
class Candidate:
    candidate_id: int
    full_name: str
    role_family: str
    declared_skills: tuple[str, ...]
    hidden_skills: tuple[str, ...]
    years_experience: float
    highest_degree: str | None
    certifications: tuple[str, ...]
    summary: str
    gender: str
    birth_year: int

    @property
    def text(self) -> str:
        """Single document used for embedding and TF-IDF."""
        return " ".join(
            part
            for part in (
                self.summary,
                " ".join(self.declared_skills),
                " ".join(self.certifications),
                f"{self.years_experience:g} years of experience",
                self.highest_degree or "",
            )
            if part
        )


@dataclass(frozen=True, slots=True)
class Label:
    """Graded relevance of one candidate for one job (0 = irrelevant)."""

    job_id: int
    candidate_id: int
    relevance: int


@dataclass(slots=True)
class Corpus:
    jobs: list[Job] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    labels: list[Label] = field(default_factory=list)

    @property
    def job_ids(self) -> list[int]:
        return [job.job_id for job in self.jobs]

    @property
    def candidate_ids(self) -> list[int]:
        return [candidate.candidate_id for candidate in self.candidates]

    def job(self, job_id: int) -> Job:
        for job in self.jobs:
            if job.job_id == job_id:
                return job
        raise KeyError(job_id)

    def candidate(self, candidate_id: int) -> Candidate:
        for candidate in self.candidates:
            if candidate.candidate_id == candidate_id:
                return candidate
        raise KeyError(candidate_id)

    def labels_for(self, job_id: int) -> list[Label]:
        return [label for label in self.labels if label.job_id == job_id]

    def grade_distribution(self) -> dict[int, int]:
        counts: dict[int, int] = {}
        for label in self.labels:
            counts[label.relevance] = counts.get(label.relevance, 0) + 1
        return dict(sorted(counts.items()))

    def positive_rate(self) -> float:
        if not self.labels:
            return 0.0
        return sum(1 for label in self.labels if label.relevance > 0) / len(self.labels)


class ValidationError(ValueError):
    """Raised when a corpus file is missing fields or breaks an invariant."""


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #
def _sample(rng: Random, pool: Sequence[str], count: int) -> list[str]:
    return sorted(rng.sample(list(pool), min(count, len(pool))))


def _years(rng: Random) -> float:
    return round(rng.uniform(0.5, 18.0), 1)


def _make_job(rng: Random, job_id: int, families: Sequence[str]) -> Job:
    family = rng.choice(families)
    spec = ROLE_FAMILIES[family]
    core = list(spec["core"])  # type: ignore[arg-type]
    extra = list(spec["extra"])  # type: ignore[arg-type]
    required = _sample(rng, core, rng.randint(4, 5)) + _sample(rng, extra, rng.randint(2, 3))
    preferred = _sample(rng, extra, rng.randint(2, 3))
    certifications: list[str] = []
    if rng.random() < 0.45:
        certifications = _sample(rng, CERTIFICATIONS[family], 1)
    required_years = float(rng.choice([1, 2, 3, 4, 5, 6, 8]))
    education = TYPICAL_DEGREE[family] if rng.random() < 0.75 else None
    domain = DOMAIN_PHRASES[family]
    description = (
        f"We are hiring a {spec['label']} to own {domain}. You will deliver "
        f"{PROJECT_PHRASES[family]} and partner with cross-functional peers. "
        f"Required: {', '.join(required)}. Nice to have: {', '.join(preferred)}."
    )
    return Job(
        job_id=job_id,
        title=f"{spec['label']} ({rng.choice(['Remote', 'Hybrid', 'On-site'])}) #{job_id:03d}",
        role_family=family,
        required_skills=tuple(sorted(set(required))),
        preferred_skills=tuple(sorted(set(preferred))),
        certifications=tuple(certifications),
        experience_required_years=required_years,
        education_required=education,
        description=description,
    )


def _make_candidate(rng: Random, candidate_id: int, families: Sequence[str]) -> Candidate:
    family = rng.choice(families)
    spec = ROLE_FAMILIES[family]
    pool = list(spec["core"]) + list(spec["extra"])  # type: ignore[arg-type]
    declared = _sample(rng, pool, rng.randint(4, 7))
    hidden = _sample(rng, pool, rng.randint(2, 5))
    declared = [skill for skill in declared if skill not in hidden] or declared[:2]
    degree = rng.choice(DEGREES[2:]) if rng.random() < 0.85 else rng.choice(DEGREES[:2])
    certifications = _sample(rng, CERTIFICATIONS[family], rng.randint(0, 2))
    projects = rng.randint(2, 9)
    summary = " ".join(
        rng.choice(SUMMARY_TEMPLATES).format(
            n=projects,
            domain=DOMAIN_PHRASES[family],
        )
        for _ in range(1)
    )
    # Hidden expertise is only ever stated in prose, never in the skill list.
    summary = f"{summary} Hands-on with {', '.join(hidden)}."
    return Candidate(
        candidate_id=candidate_id,
        full_name=(
            f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)} "
            f"({candidate_id:04d})"
        ),
        role_family=family,
        declared_skills=tuple(declared),
        hidden_skills=tuple(hidden),
        years_experience=_years(rng),
        highest_degree=degree,
        certifications=tuple(certifications),
        summary=summary,
        gender=rng.choice(GENDERS),
        birth_year=rng.randint(1970, 2004),
    )


def _latent_depth(
    rng: Random, candidate: Candidate, family: str, spec: dict[str, object]
) -> dict[str, float]:
    """Per-skill competence the candidate *actually* has for ``family``.

    Three realistic effects make this differ from the declared list:

    * **Self-representation bias.** Some candidates understate and some
      overstate what they can do, so a declared skill is not always full
      competence. The bias is latent: it is never written to the CSV, which is
      exactly why a name-match engine cannot rank perfectly here.
    * **Undeclared expertise.** Competence mentioned only in the summary.
    * **Unmentioned competence.** Occasionally real but invisible in the text,
      which is the irreducible noise floor of any screening model.
    """
    pool = list(spec["core"]) + list(spec["extra"])  # type: ignore[arg-type]
    off_family_penalty = 0.55 if candidate.role_family != family else 1.0
    bias = rng.uniform(-0.45, 0.45)
    depth: dict[str, float] = {}
    for skill in pool:
        if skill in candidate.declared_skills:
            value = 1.0 + bias
        elif skill in candidate.hidden_skills:
            value = 0.85 + bias * 0.5
        else:
            value = rng.choice([0.0, 0.0, 0.0, 0.3])
        depth[skill] = max(0.0, min(1.0, value * off_family_penalty))
    return depth


def _relevance(
    rng: Random, job: Job, candidate: Candidate, depth: dict[str, float]
) -> int:
    """Grade 0-3 from latent depth, experience and education fit.

    The aggregation is deliberately *not* a plain average. Requirements are
    weighted with descending importance and the skill term is squared, because a
    single unevidenced hard requirement is what disqualifies a candidate in
    practice. A pair whose strongest required skill is still weak is capped at
    grade 1 regardless of how many other boxes it ticks - otherwise "average
    match" would rank a well-rounded generalist above a specialist who meets
    every hard requirement.
    """
    if job.required_skills:
        values = [depth.get(skill, 0.0) for skill in job.required_skills]
        weights = [1.0 / (1.0 + 0.25 * index) for index in range(len(values))]
        skill_fit = sum(value * weight for value, weight in zip(values, weights, strict=True)) / sum(
            weights
        )
        strongest = max(values)
    else:
        skill_fit = 0.0
        strongest = 0.0
    if job.preferred_skills:
        preferred_fit = sum(depth.get(skill, 0.0) for skill in job.preferred_skills) / len(
            job.preferred_skills
        )
    else:
        preferred_fit = 0.0
    required_years = job.experience_required_years or 0.0
    years_fit = min(1.0, candidate.years_experience / required_years) if required_years else 1.0
    if job.education_required:
        gap = degree_rank(job.education_required) - degree_rank(candidate.highest_degree)
        degree_fit = max(0.0, 1.0 - 0.4 * gap)
    else:
        degree_fit = 1.0

    fit = (
        0.68 * (skill_fit**2)
        + 0.12 * preferred_fit
        + 0.12 * years_fit
        + 0.08 * degree_fit
    )
    if strongest < 0.45:
        fit = min(fit, 0.25)
    # Idiosyncratic hiring noise: some pairs are mislabelled in any real corpus.
    fit += rng.gauss(0.0, 0.05)
    fit = max(0.0, min(1.0, fit))
    if fit < 0.18:
        return 0
    if fit < 0.32:
        return 1
    if fit < 0.46:
        return 2
    return 3


def generate_corpus(
    *,
    jobs: int = 48,
    candidates: int = 240,
    seed: int = 20240517,
    families: Sequence[str] | None = None,
    include_every_pair: bool = True,
) -> Corpus:
    """Build a reproducible corpus.

    ``include_every_pair`` labels every job against every candidate, which is
    what a real recruiter portal does (one pool, many openings). Set it to
    ``False`` for a sparse pairwise matrix.
    """
    known = tuple(families or ROLE_FAMILIES)
    unknown = [family for family in known if family not in ROLE_FAMILIES]
    if unknown:
        raise ValidationError(f"Unknown role families: {unknown}")

    rng = Random(seed)
    job_records = [_make_job(rng, index, known) for index in range(1, jobs + 1)]
    candidate_records = [
        _make_candidate(rng, index, known) for index in range(1, candidates + 1)
    ]

    labels: list[Label] = []
    for job in job_records:
        spec = ROLE_FAMILIES[job.role_family]
        for candidate in candidate_records:
            depth = _latent_depth(rng, candidate, job.role_family, spec)
            labels.append(Label(job.job_id, candidate.candidate_id, _relevance(rng, job, candidate, depth)))

    corpus = Corpus(jobs=job_records, candidates=candidate_records, labels=labels)
    validate_corpus(corpus)
    return corpus


# --------------------------------------------------------------------------- #
# Serialisation
# --------------------------------------------------------------------------- #
def _join(values: Iterable[str]) -> str:
    return _DELIMITER.join(values)


def _split(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in (value or "").split(_DELIMITER) if item.strip())


def _write_csv(path: Path, fieldnames: Sequence[str], rows: Iterable[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _job_row(job: Job) -> dict[str, object]:
    row = asdict(job)
    for key in ("required_skills", "preferred_skills", "certifications"):
        row[key] = _join(getattr(job, key))
    row["education_required"] = job.education_required or ""
    row["experience_required_years"] = f"{job.experience_required_years:g}"
    return row


def _candidate_row(candidate: Candidate) -> dict[str, object]:
    row = asdict(candidate)
    for key in ("declared_skills", "hidden_skills", "certifications"):
        row[key] = _join(getattr(candidate, key))
    row["years_experience"] = f"{candidate.years_experience:g}"
    row["highest_degree"] = candidate.highest_degree or ""
    return row


def write_corpus(corpus: Corpus, directory: Path) -> dict[str, Path]:
    """Write ``jobs.csv``, ``candidates.csv`` and ``labels.csv``."""
    directory = Path(directory)
    jobs_path = directory / "jobs.csv"
    candidates_path = directory / "candidates.csv"
    labels_path = directory / "labels.csv"
    _write_csv(jobs_path, JOB_FIELDS, (_job_row(job) for job in corpus.jobs))
    _write_csv(candidates_path, CANDIDATE_FIELDS, (_candidate_row(c) for c in corpus.candidates))
    _write_csv(
        labels_path,
        LABEL_FIELDS,
        ({"job_id": label.job_id, "candidate_id": label.candidate_id, "relevance": label.relevance} for label in corpus.labels),
    )
    manifest_path = directory / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "jobs": len(corpus.jobs),
                "candidates": len(corpus.candidates),
                "labels": len(corpus.labels),
                "grade_distribution": {str(key): value for key, value in corpus.grade_distribution().items()},
                "positive_rate": round(corpus.positive_rate(), 4),
                "role_families": sorted(ROLE_FAMILIES),
                "protected_fields_excluded_from_features": list(PROTECTED_CANDIDATE_FIELDS),
                "provenance": "synthetic - generated by talentmatch_ml.dataset.generate_corpus",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {"jobs": jobs_path, "candidates": candidates_path, "labels": labels_path, "manifest": manifest_path}


def _read_csv(path: Path, required: Sequence[str]) -> list[dict[str, str]]:
    if not path.exists():
        raise ValidationError(f"Missing dataset file: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [name for name in required if name not in (reader.fieldnames or [])]
        if missing:
            raise ValidationError(f"{path.name} is missing columns: {missing}")
        return [row for row in reader]


def load_corpus(directory: Path) -> Corpus:
    """Read and validate a corpus previously written by :func:`write_corpus`."""
    directory = Path(directory)
    jobs = [
        Job(
            job_id=int(row["job_id"]),
            title=row["title"],
            role_family=row["role_family"],
            required_skills=_split(row["required_skills"]),
            preferred_skills=_split(row["preferred_skills"]),
            certifications=_split(row["certifications"]),
            experience_required_years=float(row["experience_required_years"] or 0),
            education_required=row["education_required"] or None,
            description=row["description"],
        )
        for row in _read_csv(directory / "jobs.csv", JOB_FIELDS)
    ]
    candidates = [
        Candidate(
            candidate_id=int(row["candidate_id"]),
            full_name=row["full_name"],
            role_family=row["role_family"],
            declared_skills=_split(row["declared_skills"]),
            hidden_skills=_split(row["hidden_skills"]),
            years_experience=float(row["years_experience"] or 0),
            highest_degree=row["highest_degree"] or None,
            certifications=_split(row["certifications"]),
            summary=row["summary"],
            gender=row["gender"],
            birth_year=int(row["birth_year"] or 0),
        )
        for row in _read_csv(directory / "candidates.csv", CANDIDATE_FIELDS)
    ]
    labels = [
        Label(
            job_id=int(row["job_id"]),
            candidate_id=int(row["candidate_id"]),
            relevance=int(row["relevance"]),
        )
        for row in _read_csv(directory / "labels.csv", LABEL_FIELDS)
    ]
    corpus = Corpus(jobs=jobs, candidates=candidates, labels=labels)
    validate_corpus(corpus)
    return corpus


def validate_corpus(corpus: Corpus) -> None:
    """Fail fast on the invariants every downstream stage relies on."""
    if not corpus.jobs:
        raise ValidationError("Corpus contains no jobs")
    if not corpus.candidates:
        raise ValidationError("Corpus contains no candidates")
    if not corpus.labels:
        raise ValidationError("Corpus contains no labels")

    job_ids = {job.job_id for job in corpus.jobs}
    candidate_ids = {candidate.candidate_id for candidate in corpus.candidates}
    if len(job_ids) != len(corpus.jobs):
        raise ValidationError("Duplicate job_id values")
    if len(candidate_ids) != len(corpus.candidates):
        raise ValidationError("Duplicate candidate_id values")

    for job in corpus.jobs:
        if job.role_family not in ROLE_FAMILIES:
            raise ValidationError(f"Job {job.job_id} has unknown role_family {job.role_family!r}")
        if not job.required_skills:
            raise ValidationError(f"Job {job.job_id} lists no required skills")

    seen: set[tuple[int, int]] = set()
    for label in corpus.labels:
        if label.job_id not in job_ids:
            raise ValidationError(f"Label references unknown job {label.job_id}")
        if label.candidate_id not in candidate_ids:
            raise ValidationError(f"Label references unknown candidate {label.candidate_id}")
        if label.relevance not in (0, 1, 2, 3):
            raise ValidationError(f"Relevance must be 0-3, got {label.relevance}")
        key = (label.job_id, label.candidate_id)
        if key in seen:
            raise ValidationError(f"Duplicate label for {key}")
        seen.add(key)

    per_job: dict[int, int] = {}
    for label in corpus.labels:
        per_job[label.job_id] = per_job.get(label.job_id, 0) + 1
    if len(set(per_job.values())) != 1:
        raise ValidationError("Every job must be labelled against the same number of candidates")
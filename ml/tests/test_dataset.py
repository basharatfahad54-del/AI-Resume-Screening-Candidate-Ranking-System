"""Dataset generation, serialisation and invariant tests."""

from __future__ import annotations

from dataclasses import replace

import pytest

from talentmatch_ml.dataset import (
    PROTECTED_CANDIDATE_FIELDS,
    Corpus,
    Label,
    ValidationError,
    generate_corpus,
    load_corpus,
    validate_corpus,
    write_corpus,
)


@pytest.fixture(scope="module")
def corpus() -> Corpus:
    return generate_corpus(jobs=8, candidates=20, seed=99)


def test_generation_is_deterministic() -> None:
    first = generate_corpus(jobs=6, candidates=12, seed=5)
    second = generate_corpus(jobs=6, candidates=12, seed=5)
    assert [job.required_skills for job in first.jobs] == [
        job.required_skills for job in second.jobs
    ]
    assert first.grade_distribution() == second.grade_distribution()
    assert [label.relevance for label in first.labels] == [
        label.relevance for label in second.labels
    ]


def test_different_seeds_differ() -> None:
    first = generate_corpus(jobs=6, candidates=12, seed=5)
    second = generate_corpus(jobs=6, candidates=12, seed=6)
    assert [label.relevance for label in first.labels] != [
        label.relevance for label in second.labels
    ]


def test_every_job_is_labelled_against_every_candidate(corpus: Corpus) -> None:
    expected = len(corpus.jobs) * len(corpus.candidates)
    assert len(corpus.labels) == expected
    for job in corpus.jobs:
        assert len(corpus.labels_for(job.job_id)) == len(corpus.candidates)


def test_grades_are_in_range_and_include_all_levels(corpus: Corpus) -> None:
    distribution = corpus.grade_distribution()
    assert set(distribution) == {0, 1, 2, 3}
    assert all(0 <= grade <= 3 for grade in distribution)


def test_names_are_placeholders_not_people(corpus: Corpus) -> None:
    # Synthetic identities only: a placeholder token plus a numeric id.
    assert all("(0" in candidate.full_name for candidate in corpus.candidates)


def test_hidden_skills_never_appear_in_declared_list(corpus: Corpus) -> None:
    for candidate in corpus.candidates:
        assert not set(candidate.hidden_skills) & set(candidate.declared_skills)


def test_hidden_skills_are_only_mentioned_in_prose(corpus: Corpus) -> None:
    for candidate in corpus.candidates:
        for skill in candidate.hidden_skills:
            assert skill.lower() in candidate.summary.lower()


def test_csv_round_trip(tmp_path, corpus: Corpus) -> None:
    paths = write_corpus(corpus, tmp_path)
    assert set(paths) == {"jobs", "candidates", "labels", "manifest"}
    reloaded = load_corpus(tmp_path)
    assert len(reloaded.jobs) == len(corpus.jobs)
    assert len(reloaded.candidates) == len(corpus.candidates)
    assert reloaded.grade_distribution() == corpus.grade_distribution()
    assert reloaded.job(corpus.jobs[0].job_id).required_skills == (
        corpus.jobs[0].required_skills
    )
    assert reloaded.candidate(corpus.candidates[0].candidate_id).summary == (
        corpus.candidates[0].summary
    )


def test_manifest_records_provenance(tmp_path, corpus: Corpus) -> None:
    write_corpus(corpus, tmp_path)
    manifest = (tmp_path / "manifest.json").read_text(encoding="utf-8")
    assert "synthetic" in manifest
    for field in PROTECTED_CANDIDATE_FIELDS:
        assert field in manifest


def test_validation_rejects_out_of_range_grade(corpus: Corpus) -> None:
    broken = Corpus(
        jobs=corpus.jobs[:1],
        candidates=corpus.candidates[:1],
        labels=[Label(corpus.jobs[0].job_id, corpus.candidates[0].candidate_id, 7)],
    )
    with pytest.raises(ValidationError, match="Relevance must be"):
        validate_corpus(broken)


def test_validation_rejects_unknown_reference(corpus: Corpus) -> None:
    broken = Corpus(
        jobs=corpus.jobs[:1],
        candidates=corpus.candidates[:1],
        labels=[Label(corpus.jobs[0].job_id, 9999, 1)],
    )
    with pytest.raises(ValidationError, match="unknown candidate"):
        validate_corpus(broken)


def test_validation_rejects_duplicate_pair(corpus: Corpus) -> None:
    label = corpus.labels[0]
    broken = Corpus(
        jobs=corpus.jobs[:1],
        candidates=corpus.candidates[:1],
        labels=[label, label],
    )
    with pytest.raises(ValidationError, match="Duplicate label"):
        validate_corpus(broken)


def test_validation_rejects_unknown_role_family(corpus: Corpus) -> None:
    broken_job = replace(corpus.jobs[0], role_family="wizard")
    broken = Corpus(
        jobs=[broken_job],
        candidates=corpus.candidates[:1],
        labels=corpus.labels[:1],
    )
    with pytest.raises(ValidationError, match="unknown role_family"):
        validate_corpus(broken)


def test_generate_rejects_unknown_family() -> None:
    with pytest.raises(ValidationError, match="Unknown role families"):
        generate_corpus(jobs=2, candidates=2, families=("backend", "wizard"))
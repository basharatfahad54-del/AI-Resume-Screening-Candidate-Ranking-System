"""Parity with the backend scoring engine.

If the offline pipeline and the live API ever disagree about embeddings or
scores, every metric in ``ml/runs`` describes a system nobody ships. These tests
pin that agreement.
"""

from __future__ import annotations

import numpy as np

from talentmatch_ml.backend_bridge import backend


def _backend():
    return backend()


def test_bridge_points_at_the_backend_package() -> None:
    symbols = _backend()
    for name in ("compute_match", "HashingEmbedder", "ScoringWeights", "degree_rank"):
        assert name in symbols


def test_hashing_embedder_is_bit_identical_to_the_backend() -> None:
    from talentmatch_ml.features import FeatureBuilder

    symbols = _backend()
    reference = symbols["HashingEmbedder"](384)
    texts = [
        "Senior backend engineer with python, postgresql and kafka experience",
        "Product designer with no engineering background",
    ]
    from_bridge = FeatureBuilder().embedder.encode(texts)
    from_backend = reference.encode(texts)
    assert from_bridge.shape == from_backend.shape == (2, 384)
    assert np.array_equal(from_bridge, from_backend)


def test_embeddings_are_l2_normalised() -> None:
    symbols = _backend()
    vectors = symbols["HashingEmbedder"](384).encode(["python kafka", "python kafka"])
    norms = np.linalg.norm(vectors, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-5)


def test_feature_components_match_a_direct_backend_call() -> None:
    from talentmatch_ml.dataset import generate_corpus
    from talentmatch_ml.features import FeatureBuilder, _skill_signals

    symbols = _backend()
    corpus = generate_corpus(jobs=4, candidates=12, seed=3)
    builder = FeatureBuilder().fit(corpus)
    job = corpus.jobs[0]
    candidate = corpus.candidates[0]
    _vector, components = builder.featurise(job, candidate)

    job_profile = symbols["JobProfile"](
        job_id=job.job_id,
        title=job.title,
        required_skills=tuple(job.required_skills),
        preferred_skills=tuple(job.preferred_skills),
        certifications=tuple(job.certifications),
        experience_required_years=job.experience_required_years,
        education_required=job.education_required,
        text=job.text,
        embedding=builder._embeddings.get(job.job_id),
    )
    candidate_profile = symbols["CandidateProfile"](
        candidate_id=candidate.candidate_id,
        full_name=candidate.full_name,
        skills=_skill_signals(candidate),
        total_experience_years=candidate.years_experience,
        relevant_experience_years=candidate.years_experience,
        highest_degree=candidate.highest_degree,
        certifications=tuple(candidate.certifications),
        text=candidate.text,
        embedding=builder._embeddings.get(-candidate.candidate_id),
    )
    computation = symbols["compute_match"](
        job_profile, candidate_profile, symbols["ScoringWeights"]()
    )
    assert components["overall_score"] == computation.overall_score
    assert components["required_skills_score"] == computation.required_skills_score
    assert components["experience_score"] == computation.experience_score
    assert components["education_score"] == computation.education_score
    assert components["semantic_score"] == computation.semantic_score
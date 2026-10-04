"""Feature contract tests: shape, ordering, determinism and no leakage."""

from __future__ import annotations

import numpy as np
import pytest

from talentmatch_ml.dataset import PROTECTED_CANDIDATE_FIELDS, generate_corpus
from talentmatch_ml.features import FORBIDDEN_FIELDS, FeatureBuilder, build_feature_table, feature_names


@pytest.fixture(scope="module")
def corpus():
    return generate_corpus(jobs=6, candidates=15, seed=21)


@pytest.fixture(scope="module")
def table(corpus):
    return build_feature_table(corpus)


def test_shape_matches_labels_and_columns(table) -> None:
    assert len(table) == table.X.shape[0]
    assert table.names == feature_names()
    assert table.X.shape[1] == len(table.names)
    assert table.X.shape[0] == len(table.grades) == len(table.groups)


def test_features_are_finite(table) -> None:
    assert np.isfinite(table.X).all()


def test_grouped_scores_preserve_one_entry_per_pair(table) -> None:
    grouped = table.per_query_scores(np.arange(len(table), dtype=float))
    assert sum(len(rows) for rows in grouped.values()) == len(table)
    assert set(grouped) == set(int(value) for value in table.groups)


def test_binary_label_uses_grade_threshold(table) -> None:
    expected = (table.grades >= 2).astype(np.int32)
    assert np.array_equal(table.binary_labels, expected)


def test_subset_keeps_rows_aligned(table) -> None:
    mask = np.zeros(len(table), dtype=bool)
    mask[::3] = True
    subset = table.subset(mask)
    assert len(subset) == int(mask.sum())
    assert np.array_equal(subset.X, table.X[mask])
    assert subset.job_ids == [job for job, keep in zip(table.job_ids, mask, strict=True) if keep]


def test_builder_is_deterministic(corpus) -> None:
    first = FeatureBuilder().fit(corpus)
    second = FeatureBuilder().fit(corpus)
    job, candidate = corpus.jobs[0], corpus.candidates[0]
    vector_a, _ = first.featurise(job, candidate)
    vector_b, _ = second.featurise(job, candidate)
    assert np.allclose(vector_a, vector_b)


def test_components_include_engine_outputs(corpus) -> None:
    builder = FeatureBuilder().fit(corpus)
    _vector, components = builder.featurise(corpus.jobs[0], corpus.candidates[0])
    for key in ("overall_score", "required_skills_score", "semantic_score", "tfidf_cosine"):
        assert key in components
        assert 0.0 <= components[key] <= 1.0


def test_forbidden_fields_are_declared() -> None:
    assert set(FORBIDDEN_FIELDS) == set(PROTECTED_CANDIDATE_FIELDS)


def test_protected_attributes_do_not_change_features(corpus) -> None:
    """Flipping gender and birth year must leave the feature vector untouched."""
    from dataclasses import replace

    builder = FeatureBuilder().fit(corpus)
    job = corpus.jobs[0]
    original = corpus.candidates[0]
    altered = replace(original, gender="non-binary", birth_year=1971 + (original.birth_year % 20))
    vector_a, _ = builder.featurise(job, original)
    vector_b, _ = builder.featurise(job, altered)
    assert np.allclose(vector_a, vector_b)


def test_candidate_text_is_built_for_downstream_channels(corpus) -> None:
    builder = FeatureBuilder().fit(corpus)
    _vector, components = builder.featurise(corpus.jobs[0], corpus.candidates[0])
    assert components["candidate_text_length"] > 0
    assert components["hashed_cosine"] != 0.0
"""Fairness invariants for the offline pipeline.

Two rules are enforced mechanically rather than by review:

1. no protected attribute is ever a feature column;
2. protected attributes cannot influence a label, so a model cannot learn a
   shortcut that the audit would then be unable to detect.

The audit itself does read protected attributes - that is the point of it - and
``test_audit_reports_subgroups`` proves the audit still works.
"""

from __future__ import annotations

from dataclasses import replace
from random import Random

import numpy as np

from talentmatch_ml.dataset import (
    CANDIDATE_FIELDS,
    PROTECTED_CANDIDATE_FIELDS,
    generate_corpus,
)
from talentmatch_ml.features import build_feature_table, feature_names
from talentmatch_ml.report import fairness_audit


def _corpus():
    return generate_corpus(jobs=10, candidates=40, seed=4242)


def test_protected_fields_are_absent_from_the_feature_contract() -> None:
    columns = set(feature_names())
    for field in PROTECTED_CANDIDATE_FIELDS:
        assert field not in columns
    # No column may smuggle a protected value under another name either.
    assert not any("gender" in column or "age" in column or "birth" in column for column in columns)


def test_protected_fields_are_still_present_in_the_dataset() -> None:
    # Otherwise the audit would have nothing to compare against.
    for field in PROTECTED_CANDIDATE_FIELDS:
        assert field in CANDIDATE_FIELDS


def test_labels_are_invariant_to_protected_attributes() -> None:
    """The label function is a pure function of skills, experience and education."""
    from talentmatch_ml import dataset as dataset_module

    corpus = generate_corpus(jobs=3, candidates=8, seed=7)
    job = corpus.jobs[0]
    spec = dataset_module.ROLE_FAMILIES[job.role_family]
    original = corpus.candidates[0]
    altered = replace(original, gender="non-binary", birth_year=1999)
    assert original.gender != altered.gender and original.birth_year != altered.birth_year

    state = Random(1234).getstate()
    rng_a = Random(1234)
    rng_a.setstate(state)
    depth_a = dataset_module._latent_depth(rng_a, original, job.role_family, spec)
    grade_a = dataset_module._relevance(rng_a, job, original, depth_a)

    rng_b = Random(1234)
    rng_b.setstate(state)
    depth_b = dataset_module._latent_depth(rng_b, altered, job.role_family, spec)
    grade_b = dataset_module._relevance(rng_b, job, altered, depth_b)

    assert depth_a == depth_b
    assert grade_a == grade_b


def test_features_are_invariant_to_protected_attributes() -> None:
    corpus = _corpus()
    flipped = replace(corpus, candidates=[replace(c, gender="non-binary", birth_year=1955) for c in corpus.candidates])
    first = build_feature_table(corpus)
    second = build_feature_table(flipped)
    assert np.allclose(first.X, second.X)


def test_audit_reports_subgroups() -> None:
    corpus = generate_corpus(jobs=14, candidates=80, seed=99)
    table = build_feature_table(corpus)
    rng = np.random.default_rng(0)
    scores = rng.random(len(table))
    rows = fairness_audit(table, scores, corpus)
    assert rows
    assert any(row.subgroup.startswith("gender=") for row in rows)
    assert any(row.subgroup.startswith("age=") for row in rows)
    assert all(0.0 <= row.ndcg_at_5 <= 1.0 for row in rows)
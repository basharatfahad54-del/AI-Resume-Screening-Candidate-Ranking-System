"""TalentMatch ML: an offline, reproducible reranking and evaluation pipeline.

The package never touches the production database. It reuses the *same*
interpretable scoring engine the API serves (``app.ml.scoring``) as its feature
source, then learns a calibrated reranker on top of those features and measures
whether the learned model actually ranks better than the rule-based baseline.

Sub-modules
------------
``backend_bridge``
    Imports the FastAPI application package without duplicating its logic.
``dataset``
    Deterministic synthetic corpus generation plus CSV load/validate helpers.
``features``
    Turns (job, candidate) pairs into a numeric feature matrix.
``metrics``
    Ranking and classification metrics with bootstrap confidence intervals.
``train`` / ``evaluate`` / ``report``
    Training, held-out evaluation and human-readable reporting.
``cli``
    ``python -m talentmatch_ml.cli`` entry point.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "1.0.0"
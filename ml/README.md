# TalentMatch ML

Offline, reproducible reranking and evaluation for TalentMatch AI. This package
is **not** part of the request path: the API serves the interpretable rule-based
scorer, and this pipeline exists to answer one question honestly - *would a
learned model rank candidates better?*

## What it does

1. **Generates a synthetic corpus** (`dataset.py`) - jobs, candidates and graded
   relevance labels, from a fixed seed. No real resumes, no personal data.
2. **Featurises every pair** (`features.py`) using the *same* scoring engine the
   API serves, plus coverage statistics and two text-similarity channels.
3. **Evaluates a learned reranker** against that engine with job-grouped
   cross-validation (`train.py`), with a paired bootstrap significance test.
4. **Audits** ranking quality across protected subgroups (`report.py`).
5. **Writes** `metrics.json` plus a human-readable `report.md` per run.

## Running it

```bash
# From the repository root, using the project virtualenv.
set PYTHONPATH=ml\src                     # Windows
export PYTHONPATH=ml/src                   # macOS / Linux
.\.venv\Scripts\python.exe -m talentmatch_ml.cli all --jobs 48 --candidates 240
```

Output lands in `ml/data/sample/` (dataset + manifest) and
`ml/runs/<model>-seed<seed>-<timestamp>/` (`report.md`, `metrics.json`,
`reranker_<model>.joblib`).

Useful variations:

```bash
# JSON metrics only, no artefacts
python -m talentmatch_ml.cli evaluate --model logistic --folds 5

# More folds, different corpus size
python -m talentmatch_ml.cli all --jobs 80 --candidates 300 --folds 8

# Regenerate the dataset on its own
python -m talentmatch_ml.cli synthesize --dataset ml/data/sample
```

Install as a package instead of setting `PYTHONPATH`:

```bash
pip install -r ml/requirements.txt        # once: brings in wheel for the build
pip install -e ml --no-build-isolation   # also installs the talentmatch-ml command
talentmatch-ml evaluate                  # no PYTHONPATH needed from here on
```

`--no-build-isolation` keeps the install offline, which means `wheel` and
`setuptools` must already be in the environment; `ml/requirements.txt` lists
`wheel` for exactly that reason. Without the flag, pip builds in an isolated
environment and downloads them itself.

Tests:

```bash
python -m pytest ml -q
```

## Why the labels are interesting

Each candidate has a **latent skill depth** per role family, while the dataset
only exposes part of it:

| Signal | Visible to the engine? |
| --- | --- |
| declared skills | yes - exact name match |
| skills mentioned only in the summary | only through text similarity |
| self-representation bias (understating/overstating) | no |
| unmentioned competence | no |

Relevance grades are computed from the latent depth, so the rule-based engine -
which can only match declared names - is genuinely handicapped, while a
text-aware model has something to learn. Without that gap the experiment would
be vacuous: the baseline would be perfect and any reranker would tie.

## Current result

On the default corpus (`--jobs 48 --candidates 240`), the gradient-boosted
reranker is **statistically tied** with the shipped rule-based ranking on
NDCG@5 (paired bootstrap over 48 jobs, p > 0.05), while being clearly better
calibrated point-wise (Brier 0.04 vs 0.10).

That is the decision this pipeline exists to inform: **keep the interpretable
scorer in production.** A model that ties on ranking quality while adding a
dependency, an artifact to version and an uninspectable failure mode is a net
loss. The artifact is still saved so the comparison can be re-run the moment a
real corpus - or a neural encoder - is available.

## Responsible use

- Features and labels never read `gender` or `birth_year`; both invariants are
  enforced by `ml/tests/test_fairness.py`, not by review.
- The fairness audit does read them, which is the only legitimate use: it
  compares ranking quality per subgroup so proxy discrimination stays visible.
- Outputs are decision support for a human reviewer. Nothing here rejects a
  candidate, and no score should be treated as a hiring decision on its own.
- The corpus is synthetic. These numbers describe a simulation, not real hiring
  outcomes.

## Layout

```
ml/
  src/talentmatch_ml/
    backend_bridge.py   imports app.ml.* from backend/ so features cannot drift
    dataset.py          synthetic corpus generation + CSV load/validate
    features.py         (job, candidate) -> feature matrix
    metrics.py          NDCG/MAP/precision/recall + bootstrap + paired test
    train.py            grouped CV, models, artifacts
    report.py           markdown report + fairness audit
    cli.py              command line entry point
  tests/                57 tests: determinism, invariants, parity, fairness
  data/sample/          generated corpus (gitignored)
  runs/                 generated artefacts (gitignored)
```
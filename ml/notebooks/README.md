# Notebooks

Deliberately empty. Two reasons:

1. **Reproducibility.** Every number this project reports comes from
   `python -m talentmatch_ml.cli`, which is seeded, versioned and re-runnable in
   CI. A notebook that cannot be diffed in a pull request is not a result, it is
   an anecdote.
2. **No hidden state.** Notebooks are the easiest place for a protected
   attribute to sneak into a feature matrix, because nothing enforces the
   contract.

If you want to explore interactively, do it against the same code paths:

```python
import sys
sys.path.insert(0, "../src")

from talentmatch_ml.dataset import generate_corpus
from talentmatch_ml.features import build_feature_table
from talentmatch_ml.train import train_model

corpus = generate_corpus(jobs=20, candidates=80, seed=7)
table = build_feature_table(corpus)
result = train_model(table, model_name="gbm", seed=7, folds=5)
print(result.test_report.ranking)
```

To keep an artifact for later inspection:

```python
import joblib
from talentmatch_ml.train import load_artifact, save_artifact

save_artifact(result, table, Path("../runs/exploration"))
artifact = load_artifact("../runs/exploration/reranker_gbm.joblib")
artifact.predict_proba(table.X[:5])
```

If a notebook ever becomes necessary, add it here with its seed and the command
that reproduces it in the first cell.
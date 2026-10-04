# Model card: TalentMatch ranking

**What this is:** a deterministic, weighted scoring engine that ranks candidates
against one job description. **What this is not:** a model that decides who gets
hired. It produces an ordered list with evidence for a human reviewer.

- **Version:** 1.0.0
- **Code:** `backend/app/ml/scoring.py`, `backend/app/ml/embeddings.py`
- **Artifacts:** none. There is no serialized model to load, drift or roll back.
- **Evaluation code:** [`ml/`](../ml/README.md)
- **Policy:** [responsible-ai.md](responsible-ai.md)

## Mechanism

For each (job, candidate) pair the engine computes six components in `[0, 1]`,
multiplies each by its weight, sums, and scales to `[0, 100]`.

| Component | Default weight | Signal |
| --- | --- | --- |
| Required skills | 0.40 | Fraction of required skills matched, plus partial credit for semantically close but undeclared skills |
| Experience | 0.20 | Years of experience against the requirement, saturating at it |
| Education | 0.15 | Highest level held against the level required, ordinal |
| Preferred skills | 0.10 | Fraction of preferred skills matched |
| Semantic | 0.10 | Cosine similarity between embedded job and resume text |
| Certifications | 0.05 | Fraction of required certifications held |

Weights are stored as data in `ScoringWeight` profiles, are admin-managed, and
must sum to 1.0. Changing them changes everyone's results, which is why the
change is audited and why out-of-range profiles are rejected rather than
normalised.

### Component behaviour worth knowing

- **Skill matching** normalises names before comparison, so "React.js", "React JS"
  and "React" collapse to one skill. A required skill that is not declared can
  still earn partial credit when cosine similarity between the *whole resume* and
  that skill name reaches `SEMANTIC_SKILL_THRESHOLD = 0.45`. This recovers
  experience that was never written as a skill, and it is also the component most
  sensitive to the embedding provider.
- **Experience** saturates: exceeding the requirement stops adding score, so a
  20-year applicant does not outrank a well-matched 7-year applicant on volume
  alone.
- **Education** compares ordinal levels only. Field of study and institution are
  not scored.
- **Semantic** is the only component that depends on the provider
  (`EMBEDDING_PROVIDER`). With the default `hashing` provider it measures
  lexical overlap, not meaning.

### Output

Every ranking response carries the component values, the matched, partially
matched and missing skills, human-readable reasons, the evidence rows and a
disclaimer. `GET /api/matches/{id}` returns the raw evidence for one pair, which
is the endpoint to use when a reviewer disputes a score.

## Evaluation

### The rule-based engine against the offline reranker

Numbers below are the default experiment: 48 synthetic jobs x 240 candidates =
11,520 graded pairs, 5-fold `GroupKFold` grouped by job, out-of-fold predictions
for every pair. Reproduce with:

```bash
python -m talentmatch_ml.cli all --jobs 48 --candidates 240 --seed 20240517 --model gbm --folds 5
```

| Metric | Rule-based (shipped) | GBM reranker |
| --- | --- | --- |
| NDCG@5 | **0.9277** | 0.9229 |
| MAP@5 | 0.9751 | **0.9756** |
| Precision@5 | 0.9792 | 0.9792 |
| Recall@5 | **0.1202** | 0.1202 |
| MRR@5 | 1.0000 | 1.0000 |
| ROC-AUC | 0.9046 | **0.9534** |
| PR-AUC | 0.7954 | **0.8895** |
| Brier score | 0.1030 | **0.0405** |

Paired bootstrap over the 48 job queries on NDCG@5: mean difference **-0.0048**,
95% CI **[-0.0210, +0.0107]**, **p = 0.511** - a tie. MAP@5, precision@5 and
NDCG@10 also tie.

**Decision: ship the rule-based engine.** The reranker is materially better
calibrated point-wise (Brier 0.040 vs 0.103) but that does not translate into a
better shortlist, which is the only thing the product uses it for. A tie is not a
reason to add a model artifact to the request path. Full report:
`ml/runs/<run>/report.md`.

### Fairness audit

From the same run, NDCG@5 by subgroup:

| Subgroup | Pairs | Positive rate | NDCG@5 |
| --- | --- | --- | --- |
| gender=female | 2928 | 0.190 | 0.9204 |
| gender=male | 2928 | 0.163 | 0.9156 |
| gender=non-binary | 3024 | 0.170 | 0.9143 |
| gender=undisclosed | 2640 | 0.171 | 0.9146 |
| age=under 25 | 1008 | 0.220 | 0.9250 |
| age=25-31 | 2112 | 0.160 | 0.9159 |
| age=32-39 | 3168 | 0.171 | 0.9191 |
| age=40+ | 5232 | 0.171 | 0.9206 |

Spread is small, which is expected: the production engine never reads a
protected attribute. Read this as confirmation that no gross leak exists, not as
evidence of fairness. See [responsible-ai.md](responsible-ai.md#fairness-audit)
for what the audit cannot tell you.

## Intended use

Ranking and explaining candidate pools for a human reviewer, on data the
organisation already has a lawful basis to process. Appropriate uses include
shortlist ordering, highlighting gaps for a reviewer to question, and comparing
candidates against an explicit role definition.

**Out of scope, and rejected by design:**

- Automatic rejection, screening-out, or any action taken without a human.
- Ranking by protected or sensitive attributes, or by anything inferred to stand
  in for them.
- Ranking candidates where no job description exists, i.e. scoring people
  against an imagined norm.
- Using the score as the sole or dominant input to a decision.

## Limitations

1. **Competence that is not written down is invisible.** Skill matching is
   name-based; the semantic fallback only recovers literal or lexically close
   evidence. Candidates who describe work without naming the technology are
   penalised.
2. **Experience is approximated by years.** Career breaks, contract work,
   overlapping roles and relevance of past work are not modelled.
3. **Education is ordinal**, and institution and field are ignored.
4. **Recency is ignored.** An outdated resume scores like a current one if the
   text matches.
5. **The default `hashing` embedder is lexical, not semantic.** It does not
   understand paraphrase. Use `sentence_transformers` or `openai` for real
   semantic matching, accepting that both send text off the host.
6. **Weights encode a policy.** The defaults assume skill coverage matters most.
   For a role where it does not, an admin should change the profile, and the
   change is visible in the audit log.
7. **The evaluation corpus is synthetic.** It verifies that the pipeline runs,
   that features cannot leak protected attributes, and that the shipped scorer is
   hard to beat on its own terms. It says nothing about real hiring outcomes.
8. **No drift monitoring.** Scores are recomputed on demand; nothing tracks
   whether the applicant pool or the labour market shifted. A production
   deployment should log component distributions over time.

## Operational notes

- Switching `EMBEDDING_PROVIDER` changes every vector. Recompute rankings for
  affected jobs afterwards or scores will be internally inconsistent.
- Raising `SEMANTIC_SKILL_THRESHOLD` credits fewer unmatched skills; lowering it
  credits more, and admits more false matches. It is a global constant with a
  documented value, not a tuned secret.
- Bcrypt cost, token lifetimes and rate limits are configuration, not model
  parameters; see [security.md](security.md).

## Change log

| Version | Change | Reason |
| --- | --- | --- |
| 1.0.0 | Initial engine: six components, editable weight profiles, evidence rows per pair, partial credit above cosine 0.45 | Baseline for the offline reranking experiment |
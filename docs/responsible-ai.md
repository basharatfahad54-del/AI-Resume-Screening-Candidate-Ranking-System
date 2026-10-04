# Responsible AI

Recruitment software decides who gets read. This document states what the system
is allowed to do, what it refuses to do, and how those rules are enforced rather
than promised.

## The core commitment

**TalentMatch ranks and explains. It never rejects.**

There is no automatic rejection, no auto-screening-out, no silent cut-off, and
no "top 10%" auto-advance. Every ranking is advice to a human who remains
responsible for the decision. Three parts of the product exist specifically to
keep that true:

- `POST /api/candidates/{id}/shortlist` is the only path to a shortlist, it is
  always an explicit request, and nothing in the pipeline sets it from a score.
- The shortlist flag lives on the candidate record, not derived from a rank, so
  "top by score" never silently becomes "shortlisted".
- Every score response carries a disclaimer saying so in words.

If a future change makes the system act on a candidate without a human in the
loop, it is not a feature request; it is a violation of this document.

## Why the scorer is not a learned model

The offline experiment in [`ml/`](../ml/README.md) exists to answer whether a
learned reranker beats the shipped scorer. On the default corpus it is
statistically tied on NDCG@5 (p = 0.511) while being better calibrated
point-wise.

The conclusion was to keep the interpretable scorer, for reasons that are as much
about accountability as about accuracy:

1. **A score a reviewer cannot interrogate is worse than no score.** The
   rule-based model returns six named components, the skills that matched and
   the ones that did not. A recruiter can disagree with one component and be
   right. "The model scored them 71" is not an argument.
2. **A tie is not a win.** Adding an artifact to version, a dependency to track
   and an uninspectable failure mode for no measured ranking gain is a net loss.
3. **Auditable beats clever.** When a candidate disputes a decision, six
   weighted components can be explained in a sentence.

The artifact is still saved so the comparison can be re-run the moment a real
corpus exists. If a reranker ever wins by a margin that survives significance
testing, the decision can be revisited with evidence rather than taste.

## Protected attributes

`gender` and `birth_year` appear in the synthetic ML corpus for one reason: to
audit whether ranking quality differs across groups. They are **never** ranking
features, and this is a test, not a convention.

| Rule | Enforcement |
| --- | --- |
| Protected attributes never enter the feature matrix | `ml/tests/test_fairness.py` fails if a protected column reaches `FeatureTable` |
| Protected attributes never influence labels | Same test asserts label construction cannot read them |
| Production scoring never sees them | The API's scoring engine takes job requirements and candidate skills only; there is no code path that passes demographic data to it |
| The fairness audit is allowed to read them | `ml/src/talentmatch_ml/report.py` - that is the only legitimate use |

Removing a protected attribute is a one-line change in the corpus generator.
Keeping it out of the feature matrix is a test that fails the build.

Proxy discrimination is the harder problem and this system does not solve it.
Removing `gender` and `birth_year` does not remove postcode, name, graduation
year, career gaps, school prestige or the language a resume is written in, all of
which can act as proxies. The audit exists to keep that visible rather than to
certify fairness.

## Fairness audit

`report.py` writes a subgroup table to every run report: for each gender and age
band, the number of pairs, the positive rate, NDCG@5, precision@5 and the mean
score, for both the shipped scorer and the candidate reranker. Subgroups with
fewer than 30 pairs or fewer than 3 distinct jobs are reported as
*"not enough rows to audit"* rather than as a number, because a five-row estimate
invites over-reading.

Read it as a **smoke alarm, not a certificate**:

- A large gap in ranking quality between groups is a prompt to investigate the
  pipeline, not proof of discrimination.
- On the synthetic corpus the data-generating process *is* the ground truth, so
  the audit measures whether the scorer reproduces a known ranking. It cannot
  tell you how the model behaves on real applicants.
- The real deployment question is whether any observed gap has a lawful,
  job-related explanation that a human can articulate.

## Known limitations of the ranking

Stated so nobody over-reads a score:

- **Skill matching is name-based, with a semantic fallback.** Declared skills
  match after normalisation, so "React.js" and "React" are the same skill. An
  unmatched required skill earns partial credit when the whole resume is close
  to that skill (cosine >= 0.45). Competence that is never written down is
  invisible, and that is a real penalty for candidates who undersell themselves.
- **Experience is a number.** Years of experience are treated as a quantity that
  saturates at the requirement. Career breaks, contract work and overlapping
  roles are approximated.
- **Education is ordinal.** Level is compared against level. Field of study and
  institution are not weighted.
- **Text similarity depends on the provider.** The `hashing` embedder is a
  deterministic lexical fallback, not a language model. It is honest about
  literal overlap and blind to paraphrase. `sentence_transformers` and `openai`
  are meaningfully better and both send text off the host.
- **Recency and gaps are not modelled.** A 2015 resume scores like a 2025
  resume if the text matches.
- **Weights are a policy choice.** Changing them changes everyone's ranking,
  which is why only an admin can, and why the change is audited.

## Human oversight

- Scores are advisory. The UI shows the components and the evidence, not just the
  total, so a reviewer can see *why*.
- Ranking views, exports, weight changes, user changes and purges are written to
  the audit log with actor, IP and user agent.
- Deleting candidate data is available to admins and audited, so a correction
  request can be honoured.
- Nothing in the product ranks on protected attributes, and the ML pipeline
  exists to keep that honest as the code changes.

## Data minimisation

- The ML corpus is **synthetic**. No real resume was used to produce any number
  in this repository.
- Resumes are stored only as long as the retention window (`RETENTION_DAYS`,
  default 365) and are reachable only through an authorised endpoint.
- The offline pipeline never ships in the API container (`.dockerignore`), so
  training code cannot run on production data by accident.

## If you deploy this

Do these before pointing it at real applicants:

1. Replace `SECRET_KEY`, set a unique `ADMIN_PASSWORD`, then remove it from the
   environment. See [deployment.md](deployment.md).
2. Run the offline pipeline on a **real, consented** corpus and read
   `ml/runs/<run>/report.md` before trusting any metric. The synthetic numbers
   here are a smoke test of the machinery, not evidence about your candidates.
3. Re-check the fairness audit with the real corpus, per protected group your
   jurisdiction requires you to consider.
4. Decide the embedding provider deliberately: `hashing` keeps data on the host,
   the hosted options do not.
5. Get legal review for your jurisdiction. Employment and AI regulation moves
   quickly, and this document is engineering guidance, not legal advice.
6. Tell candidates a ranking tool is in use, and give them a way to ask for a
   human review. Most regimes now expect both.
# P1 Knowledge Layer — Ingestion and Hybrid Retrieval Design

**Date:** 2026-11-04
**Owner:** P1 (domain, knowledge & memory)
**Covers:** L4–L6 — ingestion, retrieval, and the before/after retrieval experiment
**Status:** draft for P1 to own. Every number here was measured on this corpus, not estimated.

---

## 1. Intent

The incident commander needs the right runbook in front of it before it reasons about an
incident. This document decides how the 18 authored runbooks become a searchable index, how
retrieval quality is measured, and what the one graded before/after change is.

It exists because the obvious design — chunk at 350 tokens, embed, measure Recall@5 — was
measured against this corpus and does not survive contact with it.

---

## 2. What the corpus actually is

Measured 2026-11-04 via `ingest_runbooks()` over `data/runbooks/`:

| | |
|---|---|
| Documents | 18 — 17 `cause`, 1 `symptom` |
| Evidence status | 14 `structural`, 2 `hypothesis`, 2 `verified` |
| Labelled queries in front matter | 71 |
| Whole corpus | 6,416 words ≈ 8.3k tokens |
| Median document body | 342 words ≈ 444 tokens |
| H2 sections | 97 (median **43 words** ≈ 56 tokens, max 246 words) |
| Sections above a ~500-token budget | **0** |

Two consequences, and they drive most of this document:

**A runbook is already chunk-sized.** The median body is ~444 tokens. A 350-token chunking
target would produce roughly one chunk per file, and the "overlap only when splitting a long
section" rule would never fire, because nothing is long enough to split. Chunking is therefore
deferred (§4, D6), not configured.

**The corpus is small enough that top-5 is 28% of it.** Any metric that asks "is the answer in
the top 5 of 18?" is a weak filter. This is the single most important fact about measuring
retrieval here.

---

## 3. The baseline, measured before building anything

A ~60-line stdlib BM25 (no dependencies) over the 71 front-matter queries, scored at document
level. Gold label = the runbook the query was authored in.

| Index text | Recall@5 | Recall@1 | MRR |
|---|---|---|---|
| Body only | 0.944 | 0.634 | 0.770 |
| **Body + `symptom` + `service` + `fault_id`** | **0.972** | 0.648 | 0.788 |
| Body + `queries` — **the contamination case** | 1.000 | 0.958 | 0.979 |
| Random baseline (5 of 18) | 0.278 | — | — |

### 3.1 Recall@5 is already saturated

0.972 is 69 of 71. The headroom available to *any* retrieval improvement is **two queries**.
A change that moves two queries is not a demonstrable improvement; it is inside the noise of
an embedding-model choice. Reporting Recall@5 alone would produce "no measurable change",
which is true and nearly uninformative.

### 3.2 Section-level retrieval does not help — measured, not assumed

An early recommendation in this project's discussion was to retrieve sections rather than
documents, on the theory that 97 units make a harder and more useful task. Measured:

| Retrieval unit | Recall@5 |
|---|---|
| Document | 0.972 |
| Section, collapsed to parent document (115 units) | **0.958** |

Section retrieval is *worse*. With 18 documents, a document's evidence is spread across ~6
short sections, and competing documents' sections rank in between. Pooling at document level
wins. The hypothesis was wrong and is recorded here so nobody re-proposes it.

### 3.3 Where the headroom actually is

Splitting the 71 queries by how much of their vocabulary appears in their gold document:

| Subset | n | Recall@5 | **Recall@1** |
|---|---|---|---|
| Easy (overlap > 0.60) | 50 | 1.000 | 0.740 |
| **Hard (overlap ≤ 0.60)** | 21 | 0.905 | **0.429** |

On low-overlap queries BM25 puts the right runbook first **43%** of the time. That is the gap
semantic retrieval exists to close. The clearest single case:

```
'assistant feels sluggish but works'   overlap 0.00   BM25 rank 13 of 18
```

Zero shared vocabulary with its runbook. No lexical method can find it; an embedding can.
Recall@5 cannot see this, because rank 13 and rank 6 are both just "a miss".

---

## 4. Decisions

### D1 — Retrieval unit is the whole document

Measured in §3.2. Chunk-level retrieval is reconsidered only if the corpus grows past ~50
documents or a single runbook exceeds ~800 tokens.

### D2 — Index text is body + `symptom` + `service` + `fault_id`, and never `queries`

Worth +2.8 points of Recall@5 over body alone (§3). `severity` and `status` are **filters and
trust signals, not index text** — a sev1 runbook must not rank higher merely for containing
the word "sev1".

### D3 — `queries` stays in front matter; the guard moves to the index

One source of truth. The queries are authored beside the runbook, which is why there are 71 of
them, and splitting them into a second file would create drift.

The leak is not duplication, it is indexing. `test_ingestion_does_not_include_frontmatter_queries`
checks `document.body`, which never contained them — the leak happens one layer later, when an
indexer concatenates metadata. **A new guard asserts no query string appears in any index
entry's text.** This is the fifth failure named in `tests/test_guards.py`'s docstring and the
only one with no guard today.

### D4 — The evaluation query set comes from someone who did not author the corpus

The 71 front-matter queries are contaminated by construction: same author, same sitting, same
vocabulary as the documents they retrieve. Their median query/document overlap is 0.75. They
are a **development set**.

P1 cannot fix this by trying harder — P1 wrote all 18 runbooks. The graded measurement needs
~25 queries written by a teammate given only the fault list and service names, runbooks
unopened. This is the rule already in `CONTRIBUTING.md` — *the person tuning the agent must not
write the held-out cases* — applied to retrieval. **P4 is the natural author.**

Until that set exists, every number in this document is a development number and is labelled
as such in any report. How P4's queries get their gold labels is D12.

### D5 — Report Recall@5 as required, and Recall@1 + MRR because they discriminate

Recall@5 is the graded metric and it is reported. It is reported *alongside* Recall@1 and MRR
and alongside the saturation argument in §3.1, because a 2-query ceiling is a property of an
18-document corpus and not of the retrieval method. Per-query results are published so a
reader can see which queries moved.

### D6 — No chunking yet

Nothing in the corpus exceeds the budget (§2). A `chunking.py` would be written, tested and
defended without changing a single retrieval result. Revisit when D1's thresholds are crossed.

### D7 — Lexical: BM25 via `rank-bm25`; our own tokenizer

*My call, reversible.* Scoring is standard and not where this project's risk lives. The
tokenizer **is** ours, because it carries one requirement: `paymentFailure` must also index as
`payment` + `failure`, or exact-identifier queries and prose queries cannot both work. The
stdlib BM25 from the spike is the fallback if the dependency proves awkward.

### D8 — Semantic: `sentence-transformers` behind an optional extra

`all-MiniLM-L6-v2` (80 MB model, but torch is ~2 GB). It goes in
`[project.optional-dependencies]`, **not** the default dependencies, so CI's `lint`, `test` and
`guards` jobs do not install torch on every run. Tests needing it use `pytest.importorskip`
with a reason, matching the existing pending-guard convention.

### D9 — Fusion: Reciprocal Rank Fusion, k₀ = 60

Scores from BM25 and cosine similarity are on incomparable scales. RRF uses ranks only, which
is the point. k₀ = 60 is the conventional default and is recorded as a default, not a finding.

### D10 — In-memory index; no vector database

18 documents and 8.3k tokens. Chroma, persistence and a server are deferred behind the
retriever interface, which is the only thing the commander sees.

### D11 — No LLM anywhere in the retrieval layer

Answer synthesis, citation validation and grounding checks are P2/P3 graded territory. P1's
layer must be measurable with no model in the loop, or a retrieval regression and a generation
regression become indistinguishable.

### D12 — Gold labels: authored fault-first by P4, and a gold *set* not a single document

P4 chooses a fault from the fault list, writes a query describing it, and ships the
`(query -> fault_id)` mapping **with** the query set. The label therefore exists before any
retrieval runs and cannot be influenced by seeing output. P1 does not label P4's queries.

Gold is a **set**: the intended fault plus any alternates P4 marks acceptable. Recall@k counts a
hit when *any* member of the gold set appears in the top k. This is required by the corpus, not
a convenience — `'ads fail to load on product pages'` matches `ad-failure`, `ad-high-cpu` and
`ad-manual-gc` on the evidence available to a retriever, and the one `symptom` runbook carries an
explicit `candidates` list naming the causes it narrows to. A single-gold metric would score
correct-but-not-intended retrievals as failures and understate quality on the whole `ad/*` family.

The committed mapping is the ruler, and §7's hash covers it.

### D13 — Retrieval does not filter or re-rank on `status`

Every matching runbook is returned with its `status` attached; the commander decides how much to
trust each one. 14 of 18 runbooks are `structural` and 2 are `hypothesis`, so filtering would
drop or demote most of the corpus and bake a trust policy into the index.

Keeping them separate is what makes them separately measurable: with status coupled to ranking, a
retrieval regression and a trust-policy change are indistinguishable in the numbers. This matches
the separation already set out in `docs/memory-policy.md`, where status is recorded against
evidence rather than used to hide it.

---

## 5. Module layout

```
packages/oncall-knowledge/src/oncall_knowledge/
├── schema.py          exists — typed front matter
├── validator.py       exists — corpus validation
├── ingestion.py       exists — discover + validate, root-pinned
├── indexing/
│   ├── lexical.py     BM25 + the camelCase-aware tokenizer
│   ├── semantic.py    embeddings (optional dependency)
│   └── hybrid.py      RRF fusion
├── retrieval.py       the ONE interface the commander calls
├── evaluation.py      Recall@k, MRR, per-query output
└── inspection.py      CLI: show me what a query retrieves and why
```

`retrieval.py` is the only public surface. Everything under `indexing/` is internal and may be
replaced — that is what makes D10 a deferral rather than a commitment.

---

## 6. The graded experiment

One change, everything else frozen.

| | Chunking | Lexical | Semantic | Fusion |
|---|---|---|---|---|
| **A — baseline** | none (D6) | BM25 | — | — |
| **B — one change** | none | BM25 | MiniLM | RRF |

Frozen across both: the corpus, the index text (D2), the query set, the gold labels, and the
metric code. Reported: Recall@5, Recall@1, MRR, per-query ranks, and the queries that improved
or regressed.

Run on the development set *and* on P4's set (D4, labelled per D12), reported separately. The expectation on the
record now, so it can be wrong: **Recall@5 moves by ~0 on the development set and visibly on
the hard subset.** If B improves the development Recall@5 by a lot, suspect contamination
before celebrating.

---

## 7. Guards this design adds

| Guard | Catches |
|---|---|
| No query string appears in any index entry (D3) | The 0.972 → 1.000 self-match |
| Every index entry's `source_path` is under `data/` | Indexing the eval tree |
| The experiment record carries a hash of (query, gold) pairs | A silently edited ruler between A and B |

The third is the one this project keeps needing: a changed eval set produces a changed number
with no error anywhere.

---

## 8. Non-goals and anti-goals

**Non-goals:** persistence, a vector database, reranking, query expansion, multi-hop retrieval,
chunking, answer generation.

**Anti-goals** — these would mean failure even with good numbers:

- A Recall@5 improvement that comes from the index seeing the queries.
- A retrieval layer that cannot be scored without an API key.
- Chunking parameters tuned before anything in the corpus needs chunking.
- A single aggregate number with no per-query output, which cannot support or refute any claim
  about *why* retrieval improved.

---

## 9. Left to the builder

- Tokenizer details beyond the camelCase requirement (stemming, stopwords, metric-name handling
  like `demo_payment_transactions_total`).
- Whether `IncidentContext` (incident id / service / symptom / suspected fault) is parsed in
  `retrieval.py` or handed in already structured by the commander. The incident ID must not
  reach the index either way — no runbook mentions one.
- `inspection.py`'s output format.
- Whether to return neighbouring context with a hit once D1's thresholds are crossed.

## 10. Open questions for P1

1. Does P4 accept authoring the 25 blind queries, and when?
2. Do the 8 unwritten seed postmortems land before the experiment? 18 → 26 documents does more
   for metric discrimination than any retrieval change, but it also changes the corpus mid-
   experiment, so it happens strictly before A or strictly after B.

---

## 11. Calls made while drafting, for P1 to check

A builder review of this document found seven places where a wrong guess would change the
plan. Three went back to P1 (§10). These four were settled here and are reversible:

| Call | Why |
|---|---|
| The experiment record is one JSON file per run under `artifacts/retrieval/`, carrying the config id, the model id, the (query, gold) set hash, and every per-query rank | The hash guard in §7 needs somewhere to live, and per-query ranks are the only way to defend a claim about *which* queries improved |
| `retrieve()` returns ranked results carrying `document_id`, `status`, the fused score, **and each retriever's own rank** | Without the component ranks, an RRF result cannot be debugged — you cannot tell whether a hit came from BM25 or the embedding |
| The embedding model name **and revision** are pinned and recorded in every run record; vectors are L2-normalised | An unpinned model silently changes the ruler between run A and run B, which is §7's failure mode in a different costume |
| Each retriever contributes its top 20 into RRF | Deeper than the reported k=5 so fusion has material to reorder; shallow enough to stay fast on 18 documents |

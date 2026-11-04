# P1 Ingestion and Hybrid Retrieval Implementation Plan

> **Execution:** this plan is executed **natively, task by task, by P1** — `CLAUDE.md`
> bars subagent-driven development on this repository, so `superpowers:subagent-driven-development`
> does not apply here. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the 18 validated runbooks into a measurable hybrid retrieval layer, and run the
graded before/after experiment on it.

**Architecture:** One shared index-text function feeds two independent retrievers — BM25 over
tokens and cosine over embeddings — whose *rankings* (never their scores) are fused by Reciprocal
Rank Fusion. Everything is scored through one `search(query, k)` callable, so the baseline, the
hybrid, and the experiment runner all exercise the same interface.

**Tech Stack:** Python 3.12, `rank-bm25`, `sentence-transformers` (optional extra), pytest.

**Spec:** `docs/superpowers/specs/2026-11-04-p1-ingestion-and-retrieval-design.md`

## Global Constraints

- `oncall_knowledge` may import **only** `oncall_contracts` from the workspace. Enforced by
  `tests/test_guards.py::test_dependency_direction`.
- Index text is **body + `symptom` + `service` + `fault_id`**. Never `queries` (D2/D3). Never
  `severity` or `status` — those are filters and trust signals, not index text.
- **No LLM, no API key, anywhere in this package** (D11).
- No chunking. The retrieval unit is the whole document (D1/D6).
- Gold is a **set**; a hit is any member of the set appearing in the top k (D12).
- Retrieval never filters or re-ranks on `status` (D13).
- RRF uses `k0 = 60`, with each retriever contributing its **top 20** (§11).
- Python >= 3.12, ruff line-length 100. `make lint && make test` before every commit.
- New test files need unique basenames — pytest runs `--import-mode=importlib`.
- Experiment records go to `artifacts/retrieval/`.

## Review Focus

Five failure modes the spec implies that no task's happy path would catch. Each has a test in
the task that owns the code.

1. **A missing `sentence-transformers` must fail loudly, not fall back to lexical-only.** A
   silent fallback makes config B score exactly like config A, and the experiment reports "no
   improvement" instead of "it never ran". Task 5.
2. **Score ties must break deterministically.** Two documents on an identical score, ordered by
   dict insertion, make every A/B delta partly noise. Task 2.
3. **An empty or whitespace-only query must not crash** and must return an empty ranking rather
   than an arbitrary one. Task 2.
4. **A query with zero corpus overlap** scores 0.0 against everything; the ranking must still be
   deterministic and `rank_of_first_gold` must report a miss, not rank 0. Tasks 2 and 3.
5. **Comparing two runs scored on different query sets must refuse.** The hash exists so a
   silently edited ruler cannot produce a quietly meaningless delta. Task 7.

---

### Task 1: Tokenizer and index text

The foundation both retrievers share. If they index different text, the experiment compares two
things at once.

**Files:**
- Create: `packages/oncall-knowledge/src/oncall_knowledge/indexing/__init__.py`
- Create: `packages/oncall-knowledge/src/oncall_knowledge/indexing/text.py`
- Test: `packages/oncall-knowledge/tests/test_index_text.py`

**Interfaces:**
- Consumes: `RunbookDocument`, `RunbookMetadata` from `oncall_knowledge.schema`.
- Produces: `tokenize(text: str) -> list[str]`, `index_text(document: RunbookDocument) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
def test_camel_case_identifiers_also_index_as_words():
    tokens = tokenize("paymentFailure")
    assert "paymentfailure" in tokens   # exact-identifier queries
    assert "payment" in tokens          # prose queries
    assert "failure" in tokens

def test_metric_names_split_on_underscores():
    tokens = tokenize("demo_payment_transactions_total")
    assert "payment" in tokens
    assert "transactions" in tokens

def test_tokenizing_is_case_insensitive():
    assert tokenize("PaymentFailure") == tokenize("paymentfailure")

def test_index_text_carries_the_retrieval_metadata():
    doc = next(d for d in ingest_runbooks() if d.document_id == "payment-failure")
    text = index_text(doc)
    assert doc.metadata.symptom in text
    assert doc.metadata.service in text
    assert doc.metadata.fault_id in text

def test_index_text_never_contains_a_labelled_query():
    """D3: the leak that takes Recall@1 from 0.65 to 0.96."""
    for doc in ingest_runbooks():
        for query in doc.metadata.queries:
            assert query not in index_text(doc)

def test_index_text_is_exactly_the_four_permitted_fields():
    """Token sets, so the join format stays the implementer's choice.

    NOT `status not in index_text(doc)`: bodies legitimately discuss "verified",
    "hypothesis" and sev1-sev3 in their Severity and Provenance sections --
    3, 3 and 19 of them respectively. Measured. The invariant is about what
    index_text ADDS, not which words the prose happens to use.
    """
    for doc in ingest_runbooks():
        permitted = " ".join(p for p in (doc.body, doc.metadata.symptom,
                                         doc.metadata.service, doc.metadata.fault_id) if p)
        assert set(tokenize(index_text(doc))) == set(tokenize(permitted))

def test_a_runbook_without_a_fault_id_does_not_index_the_word_none():
    """checkout-error-rate-elevated is the symptom runbook: fault_id is None.
    Interpolating it would collide with "none", the no-op action's name.
    """
    doc = next(d for d in ingest_runbooks() if d.metadata.fault_id is None)
    assert "none" not in tokenize(index_text(doc))
```

The drafted file is `packages/oncall-knowledge/tests/test_index_text.py` — 9 tests, already
confirmed failing with `ModuleNotFoundError: No module named 'oncall_knowledge.indexing'`.

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run pytest packages/oncall-knowledge/tests/test_index_text.py -v`
Expected: FAIL — `ModuleNotFoundError: oncall_knowledge.indexing`

- [ ] **Step 3: Implement `tokenize` and `index_text` in `indexing/text.py`**

`tokenize` lowercases, splits on any non-alphanumeric-non-underscore run, emits each raw token,
and additionally emits camelCase sub-parts and underscore sub-parts. `index_text` joins body,
`symptom`, `service` and `fault_id` with spaces and nothing else.

- [ ] **Step 4: Run the tests and confirm they pass**

- [ ] **Step 5: `make lint && make test`, then commit**

```bash
git add packages/oncall-knowledge/src/oncall_knowledge/indexing packages/oncall-knowledge/tests/test_index_text.py
git commit -m "feat(knowledge): add the shared tokenizer and index text"
```

---

### Task 2: Lexical index, and the guard that protects it

**Files:**
- Create: `packages/oncall-knowledge/src/oncall_knowledge/indexing/lexical.py`
- Modify: `packages/oncall-knowledge/pyproject.toml` — add `rank-bm25>=0.2.2`
- Modify: `tests/test_guards.py`
- Test: `packages/oncall-knowledge/tests/test_lexical_index.py`

**Interfaces:**
- Consumes: `tokenize`, `index_text` from Task 1.
- Produces:
  - `IndexEntry` — frozen dataclass: `document_id: str`, `text: str`, `status: str`,
    `source_path: str`
  - `LexicalIndex.build(documents: Sequence[RunbookDocument]) -> LexicalIndex`
  - `LexicalIndex.entries -> tuple[IndexEntry, ...]`
  - `LexicalIndex.search(query: str, k: int) -> list[tuple[str, float]]` — `(document_id, score)`,
    highest first, **tie-broken by `document_id` ascending**

- [ ] **Step 1: Write the failing tests**

```python
def test_exact_identifier_query_finds_its_runbook():
    index = LexicalIndex.build(ingest_runbooks())
    assert index.search("paymentFailure", k=1)[0][0] == "payment-failure"

def test_ties_break_deterministically():
    """Review Focus 2: insertion-ordered ties make every A/B delta partly noise."""
    index = LexicalIndex.build(ingest_runbooks())
    once = index.search("checkout 422", k=5)
    again = LexicalIndex.build(ingest_runbooks()).search("checkout 422", k=5)
    assert once == again

def test_empty_query_returns_no_results():
    """Review Focus 3."""
    index = LexicalIndex.build(ingest_runbooks())
    assert index.search("", k=5) == []
    assert index.search("   ", k=5) == []

def test_zero_overlap_query_returns_k_results_with_zero_scores():
    """Review Focus 4: a miss must still be an ordered, reproducible ranking."""
    index = LexicalIndex.build(ingest_runbooks())
    results = index.search("zzzz qqqq", k=5)
    assert len(results) == 5
    assert all(score == 0.0 for _, score in results)

def test_entries_carry_status_without_indexing_it():
    index = LexicalIndex.build(ingest_runbooks())
    assert {e.status for e in index.entries} <= {"verified", "structural", "hypothesis"}
```

And the second guard from spec §7 — every indexed entry came from `data/`, so the eval tree can
never be indexed:

```python
def test_every_index_entry_comes_from_the_data_tree() -> None:
    """Retrieval must never be able to index the answers."""
    from oncall_knowledge.indexing.lexical import LexicalIndex
    from oncall_knowledge.ingestion import ingest_runbooks

    for entry in LexicalIndex.build(ingest_runbooks()).entries:
        parts = Path(entry.source_path).resolve().parts
        assert "data" in parts
        assert "eval" not in parts
```

And in `tests/test_guards.py` — this is the guard for the fifth failure in that file's docstring:

```python
def test_the_index_never_contains_a_labelled_query() -> None:
    """A query copied into its own index entry matches itself: Recall@1 0.65 -> 0.96.

    Measured. test_corpus_validates checks the BODY, which never held them; the
    leak is one layer later, when an indexer concatenates metadata.
    """
    from oncall_knowledge.indexing.lexical import LexicalIndex
    from oncall_knowledge.ingestion import ingest_runbooks

    documents = ingest_runbooks()
    index = LexicalIndex.build(documents)
    by_id = {d.document_id: d for d in documents}
    for entry in index.entries:
        for query in by_id[entry.document_id].metadata.queries:
            assert query not in entry.text, f"{entry.document_id} indexes its own query"
```

- [ ] **Step 2: Run both test files and confirm they fail**

- [ ] **Step 3: Add the dependency**

Run: `uv add --package oncall-knowledge 'rank-bm25>=0.2.2'`

- [ ] **Step 4: Implement `IndexEntry` and `LexicalIndex` in `indexing/lexical.py`**

Wrap `rank_bm25.BM25Okapi` over `[tokenize(index_text(d)) for d in documents]`. `search` returns
`sorted(..., key=lambda r: (-score, document_id))[:k]`, and short-circuits to `[]` when
`tokenize(query)` is empty.

- [ ] **Step 5: Run both test files and `make guards`; confirm all pass**

- [ ] **Step 6: Commit**

```bash
git commit -am "feat(knowledge): add the BM25 index and guard it against query self-match"
```

---

### Task 3: Evaluation metrics and the case loaders

**Files:**
- Create: `packages/oncall-knowledge/src/oncall_knowledge/evaluation.py`
- Test: `packages/oncall-knowledge/tests/test_evaluation.py`

**Interfaces:**
- Produces:
  - `QueryCase` — frozen dataclass: `query: str`, `gold: frozenset[str]`
  - `dev_cases(documents: Sequence[RunbookDocument]) -> list[QueryCase]` — one case per
    front-matter query, `gold = frozenset({document_id})`
  - `load_cases(path: Path) -> list[QueryCase]` — P4's YAML: a `queries` list of
    `{query, gold}` where `gold` is a string or list of `fault_id`s
  - `rank_of_first_gold(ranked_ids: Sequence[str], gold: frozenset[str]) -> int | None` —
    1-based, `None` for a miss
  - `CaseResult` — `query: str`, `gold: frozenset[str]`, `rank: int | None`, `top: tuple[str, ...]`
  - `EvalResult` — `recall_at_1: float`, `recall_at_5: float`, `mrr: float`,
    `cases: tuple[CaseResult, ...]`, `query_set_hash: str`, `n_cases: int`
  - `evaluate(search: Callable[[str, int], list[tuple[str, float]]], cases, *, k: int = 5, depth: int = 20) -> EvalResult`
  - `query_set_hash(cases: Sequence[QueryCase]) -> str` — SHA-256 over the sorted
    `(query, sorted(gold))` pairs

- [ ] **Step 1: Write the failing tests**

```python
CASES = [QueryCase("q1", frozenset({"a"})), QueryCase("q2", frozenset({"b", "c"}))]

def test_rank_is_one_based_and_none_for_a_miss():
    assert rank_of_first_gold(["x", "a"], frozenset({"a"})) == 2
    assert rank_of_first_gold(["x", "y"], frozenset({"a"})) is None

def test_any_gold_member_counts_as_a_hit():
    """D12: 'ads fail to load' fairly matches three ad runbooks."""
    assert rank_of_first_gold(["c", "b"], frozenset({"b", "c"})) == 1

def test_metrics_on_a_known_ranking():
    def search(query, k):
        return [("a", 1.0)] if query == "q1" else [("z", 1.0)]
    result = evaluate(search, CASES, k=5)
    assert result.recall_at_1 == 0.5
    assert result.mrr == 0.5

def test_dev_cases_cover_every_labelled_query():
    documents = ingest_runbooks()
    assert len(dev_cases(documents)) == sum(len(d.metadata.queries) for d in documents)

def test_query_set_hash_changes_when_a_gold_label_changes():
    """Review Focus 5's mechanism."""
    other = [CASES[0], QueryCase("q2", frozenset({"b"}))]
    assert query_set_hash(CASES) != query_set_hash(other)

def test_query_set_hash_is_order_independent():
    assert query_set_hash(CASES) == query_set_hash(list(reversed(CASES)))
```

- [ ] **Step 2: Run and confirm failure**

- [ ] **Step 3: Implement `evaluation.py`**

`evaluate` calls `search(case.query, depth)` once per case, truncates to `k` for the reported
metrics, and keeps the full `depth` ranking only for `rank` so MRR is not censored at 5.

- [ ] **Step 4: Run and confirm pass**

- [ ] **Step 5: `make lint && make test`, then commit**

---

### Task 4: Experiment A — the baseline, recorded

**Files:**
- Create: `packages/oncall-knowledge/src/oncall_knowledge/experiment.py`
- Test: `packages/oncall-knowledge/tests/test_experiment.py`

**Interfaces:**
- Consumes: Tasks 2 and 3.
- Produces:
  - `build_record(config_id: str, result: EvalResult, *, model_id: str | None) -> dict`
  - `write_record(record: dict, out_dir: Path = Path("artifacts/retrieval")) -> Path`
  - `main(argv: Sequence[str] | None = None) -> int` — `--config {A,B}`, `--cases {dev,<path>}`,
    `--out <dir>`

The record carries `config_id`, `model_id`, `query_set_hash`, `recall_at_1`, `recall_at_5`,
`mrr`, `n_cases`, and a `cases` list of `{query, gold, rank, top}` (§11).

- [ ] **Step 1: Write the failing tests**

```python
def test_baseline_reproduces_the_measured_recall():
    """The stdlib spike measured Recall@5 = 0.972 and Recall@1 = 0.648 on this
    corpus. rank-bm25 scores slightly differently, so this pins a floor, not the
    exact figure. If this drops, retrieval regressed or the corpus moved.
    """
    documents = ingest_runbooks()
    index = LexicalIndex.build(documents)
    result = evaluate(index.search, dev_cases(documents))
    assert result.recall_at_5 >= 0.95
    assert result.recall_at_1 >= 0.60

def test_record_carries_the_query_set_hash_and_every_case():
    ...
    assert record["query_set_hash"]
    assert len(record["cases"]) == result.n_cases

def test_write_record_names_the_file_after_the_config(tmp_path):
    path = write_record(record, out_dir=tmp_path)
    assert path.parent == tmp_path
    assert "A" in path.name
```

- [ ] **Step 2: Run and confirm failure**
- [ ] **Step 3: Implement `experiment.py`**
- [ ] **Step 4: Run and confirm pass**

- [ ] **Step 5: Record the baseline for real and read it**

Run: `uv run python -m oncall_knowledge.experiment --config A --cases dev`
Expected: a file under `artifacts/retrieval/`. Read the printed Recall@5 and compare it to 0.972.

- [ ] **Step 6: Commit — include the record**

```bash
git add artifacts/retrieval
git commit -am "feat(knowledge): record the lexical retrieval baseline"
```

---

### Task 5: Semantic index, behind an optional extra

**Files:**
- Create: `packages/oncall-knowledge/src/oncall_knowledge/indexing/semantic.py`
- Modify: `packages/oncall-knowledge/pyproject.toml` — `[project.optional-dependencies] semantic`
- Test: `packages/oncall-knowledge/tests/test_semantic_index.py`

**Interfaces:**
- Produces:
  - `SemanticUnavailableError(RuntimeError)`
  - `SemanticIndex.MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"`, `MODEL_REVISION: str`
  - `SemanticIndex.build(documents) -> SemanticIndex`
  - `SemanticIndex.search(query: str, k: int) -> list[tuple[str, float]]`, same contract as
    `LexicalIndex.search`

- [ ] **Step 1: Write the failing tests**

```python
def test_missing_dependency_raises_instead_of_falling_back(monkeypatch):
    """Review Focus 1. A silent fallback makes config B score exactly like
    config A, and the experiment reports 'no improvement' for 'it never ran'.
    """
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    with pytest.raises(SemanticUnavailableError, match="uv sync --extra semantic"):
        SemanticIndex.build(ingest_runbooks())

def test_the_model_is_pinned():
    assert SemanticIndex.MODEL_REVISION  # recorded in every run record

def test_finds_the_paraphrase_bm25_misses():
    """'assistant feels sluggish but works' has ZERO lexical overlap with its
    runbook and sits at BM25 rank 13 of 18. This is the whole case for embeddings.
    """
    pytest.importorskip("sentence_transformers")
    index = SemanticIndex.build(ingest_runbooks())
    top = [doc_id for doc_id, _ in index.search("assistant feels sluggish but works", k=5)]
    assert "ai-slow-response" in top
```

- [ ] **Step 2: Run; confirm the first two fail and the third fails or skips**

- [ ] **Step 3: Add the optional extra**

Run: `uv add --package oncall-knowledge --optional semantic 'sentence-transformers>=5.0' 'numpy>=2.0'`
It must **not** land in the default dependencies — CI's `lint`, `test` and `guards` jobs would
then install torch on every run.

- [ ] **Step 4: Implement `semantic.py`**

Import `sentence_transformers` inside `build` and translate `ImportError` into
`SemanticUnavailableError` naming the exact remedy. Encode `index_text(d)` per document,
L2-normalise, and score by dot product. Same `(-score, document_id)` tie-break as Task 2.

- [ ] **Step 5: `uv sync --extra semantic`, run the tests, confirm all three pass**

- [ ] **Step 6: Commit**

---

### Task 6: RRF fusion and the public retriever

**Files:**
- Create: `packages/oncall-knowledge/src/oncall_knowledge/indexing/hybrid.py`
- Modify: `packages/oncall-knowledge/src/oncall_knowledge/retrieval.py` (currently empty)
- Test: `packages/oncall-knowledge/tests/test_hybrid.py`
- Test: `packages/oncall-knowledge/tests/test_retrieval.py` (currently empty)

**Interfaces:**
- Produces:
  - `rrf(rankings: Sequence[Sequence[str]], *, k0: int = 60) -> list[tuple[str, float]]`
  - `RetrievalResult` — frozen dataclass: `document_id: str`, `status: str`, `score: float`,
    `lexical_rank: int | None`, `semantic_rank: int | None`
  - `Retriever.build(documents, *, semantic: bool) -> Retriever`
  - `Retriever.retrieve(query: str, k: int = 5) -> list[RetrievalResult]`
  - `Retriever.search(query: str, k: int) -> list[tuple[str, float]]` — the plain callable
    `evaluate` takes

- [ ] **Step 1: Write the failing tests**

```python
def test_rrf_uses_ranks_not_scores():
    """Two rankings that agree on 'b' put it first even though it is never rank 1."""
    fused = [doc_id for doc_id, _ in rrf([["a", "b"], ["c", "b"]])]
    assert fused[0] == "b"

def test_rrf_matches_the_formula():
    fused = dict(rrf([["a", "b"]], k0=60))
    assert fused["a"] == pytest.approx(1 / 61)
    assert fused["b"] == pytest.approx(1 / 62)

def test_results_expose_each_retriever_rank():
    """§11: an RRF hit you cannot attribute is an RRF hit you cannot debug."""
    r = Retriever.build(ingest_runbooks(), semantic=False).retrieve("paymentFailure")
    assert r[0].lexical_rank == 1
    assert r[0].semantic_rank is None

def test_retrieval_does_not_filter_on_status():
    """D13: 2 of 18 runbooks are `hypothesis`; retrieval exposes, never hides."""
    retriever = Retriever.build(ingest_runbooks(), semantic=False)
    documents = ingest_runbooks()
    for doc in (d for d in documents if d.metadata.status == "hypothesis"):
        top = {r.document_id for q in doc.metadata.queries for r in retriever.retrieve(q, k=5)}
        assert doc.document_id in top, f"{doc.document_id} is unreachable by its own queries"
```

- [ ] **Step 2: Run and confirm failure**

- [ ] **Step 3: Implement `rrf` in `hybrid.py` and `Retriever` in `retrieval.py`**

`Retriever.build(semantic=False)` holds only the lexical index — that is config A. With
`semantic=True` it holds both, asks each for its top 20, and fuses. Component ranks come from the
pre-fusion rankings.

- [ ] **Step 4: Run and confirm pass**
- [ ] **Step 5: `make lint && make test`, then commit**

---

### Task 7: Experiment B and the before/after report

**Files:**
- Modify: `packages/oncall-knowledge/src/oncall_knowledge/experiment.py`
- Test: `packages/oncall-knowledge/tests/test_experiment_compare.py`

**Interfaces:**
- Produces: `compare(before: dict, after: dict) -> dict` with `deltas`, `improved` and
  `regressed` lists of queries. Raises `ValueError` on mismatched `query_set_hash`.

- [ ] **Step 1: Write the failing tests**

```python
def test_comparing_different_query_sets_refuses():
    """Review Focus 5. A silently edited ruler produces a quietly meaningless delta."""
    with pytest.raises(ValueError, match="query_set_hash"):
        compare({"query_set_hash": "aaa", ...}, {"query_set_hash": "bbb", ...})

def test_compare_names_the_queries_that_moved():
    report = compare(before, after)
    assert "q-improved" in report["improved"]
    assert "q-regressed" in report["regressed"]
```

- [ ] **Step 2: Run and confirm failure**
- [ ] **Step 3: Implement `compare` and wire `--config B` to the semantic retriever**
- [ ] **Step 4: Run and confirm pass**

- [ ] **Step 5: Run the actual experiment**

```bash
uv run python -m oncall_knowledge.experiment --config A --cases dev
uv run python -m oncall_knowledge.experiment --config B --cases dev
```

Expected, on the record from the spec's §6: Recall@5 moves by roughly nothing, and the hard
low-overlap queries move visibly. **If development Recall@5 jumps, suspect contamination before
celebrating.**

- [ ] **Step 6: Commit both records and write the finding up in the spec's own words**

---

### Task 8: `inspection.py` — see what a query retrieves and why

**Files:**
- Create: `packages/oncall-knowledge/src/oncall_knowledge/inspection.py`
- Test: `packages/oncall-knowledge/tests/test_inspection.py`

- [ ] **Step 1: Write the failing test**

```python
def test_inspection_prints_the_ranks_that_produced_the_hit(capsys):
    main(["--query", "checkout requests are returning 422", "--top-k", "3"])
    out = capsys.readouterr().out
    assert "checkout-error-rate-elevated" in out
    assert "lexical_rank" in out
    assert "status" in out
```

- [ ] **Step 2: Run and confirm failure**
- [ ] **Step 3: Implement `main(argv) -> int`** — `--query`, `--top-k`, `--semantic`
- [ ] **Step 4: Run and confirm pass**
- [ ] **Step 5: `make lint && make test`, then commit**

---

## Not in this plan

- P4's query set (D12). Tasks 3 and 7 accept `--cases <path>`, so it plugs in with no code
  change. Until it exists, every number produced here is a **development** number.
- The 8 unwritten seed postmortems. They change the corpus, so they land strictly before Task 4
  or strictly after Task 7 (spec §10).
- Chunking, persistence, a vector database, reranking, answer generation (spec §8).

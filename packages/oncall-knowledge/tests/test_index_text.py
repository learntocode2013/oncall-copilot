"""Task 1: the text both retrievers index, and the tokenizer they share.

Two invariants are load-bearing here.

The tokenizer has to serve two query families at once. `paymentFailure` is how
the flag is named and how an engineer would paste it; "payment failure" is how a
customer would describe it. Both must reach the same runbook, so an identifier
indexes as itself AND as its parts.

The index text must never contain a runbook's own labelled queries. Measured on
this corpus: including them takes Recall@1 from 0.648 to 0.958 and Recall@5 to
1.000, because each document then matches its own query verbatim. Nothing raises.
That is the fifth failure named in tests/test_guards.py's docstring.
"""

import pytest
from oncall_knowledge.indexing.text import index_text, tokenize
from oncall_knowledge.ingestion import ingest_runbooks


@pytest.fixture(scope="module")
def documents():
    return ingest_runbooks()


# --- the tokenizer -------------------------------------------------------


def test_camel_case_identifiers_also_index_as_words():
    """An exact-identifier query and a prose query must reach the same runbook."""
    tokens = tokenize("paymentFailure")

    assert "paymentfailure" in tokens
    assert "payment" in tokens
    assert "failure" in tokens


def test_metric_names_split_on_underscores():
    """PromQL names appear in runbook bodies and in engineers' queries."""
    tokens = tokenize("demo_payment_transactions_total")

    assert "payment" in tokens
    assert "transactions" in tokens


def test_tokenizing_is_case_insensitive():
    """A leading capital must not change the result.

    Deliberately NOT `tokenize("PaymentFailure") == tokenize("paymentfailure")`:
    the first has a camelCase boundary and splits into payment + failure, the
    second has none and cannot. Those two are correctly unequal.
    """
    tokens = tokenize("paymentFailure")

    assert tokens, "a stub returning None would pass an equality-only assertion"
    assert tokenize("PaymentFailure") == tokens


def test_punctuation_is_not_a_token():
    assert tokenize("checkout returns HTTP 422!") == ["checkout", "returns", "http", "422"]


def test_empty_text_yields_no_tokens():
    assert tokenize("") == []
    assert tokenize("   \n\t ") == []


# --- the index text ------------------------------------------------------


def test_index_text_carries_the_retrieval_metadata(documents):
    """Worth +2.8 points of Recall@5 over the body alone. Measured."""
    doc = next(d for d in documents if d.document_id == "payment-failure")

    text = index_text(doc)

    assert doc.metadata.symptom in text
    assert doc.metadata.service in text
    assert doc.metadata.fault_id in text


def test_index_text_never_contains_a_labelled_query(documents):
    """The self-match leak. This is the one test in this file that must never be
    relaxed: every other assertion here describes a choice, this one describes a
    measurement that invalidates the whole experiment.
    """
    for doc in documents:
        text = index_text(doc)
        for query in doc.metadata.queries:
            assert query not in text, f"{doc.document_id} would index its own query: {query!r}"


def test_index_text_is_exactly_the_four_permitted_fields(documents):
    """Compared as token sets, so the join format stays the implementer's choice.

    Asserting `status not in index_text(doc)` would be wrong: bodies legitimately
    discuss "verified", "hypothesis" and sev1-sev3 in their Severity and
    Provenance sections. The invariant is about what index_text ADDS, not about
    which words the prose happens to use.
    """
    for doc in documents:
        permitted = " ".join(
            part
            for part in (
                doc.body,
                doc.metadata.symptom,
                doc.metadata.service,
                doc.metadata.fault_id,
            )
            if part
        )

        assert set(tokenize(index_text(doc))) == set(tokenize(permitted)), doc.document_id


def test_a_runbook_without_a_fault_id_does_not_index_the_word_none(documents):
    """`checkout-error-rate-elevated` is a symptom runbook: fault_id is None.

    Interpolating it would put the literal token "none" in the index, and "none"
    is also the name of the documented no-op action, so the two would collide.
    """
    doc = next(d for d in documents if d.metadata.fault_id is None)

    assert "none" not in tokenize(index_text(doc))

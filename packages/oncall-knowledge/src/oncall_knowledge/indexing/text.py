"""The text both retrievers index, and the tokenizer they share.

If the lexical and semantic indexes ever read different text, the experiment
compares two changes at once and its before/after number means nothing. That is
why index_text lives here rather than inside either index.
"""

import re

from oncall_knowledge.schema import RunbookDocument

# Runs of word characters. Underscores are kept here so a pasted metric name
# survives as one token; they are split into parts below.
_WORD = re.compile(r"[A-Za-z0-9_]+")

# A lowercase-or-digit followed by an uppercase: the camelCase seam. "HTTP" has
# no such seam and correctly stays whole.
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def tokenize(text: str) -> list[str]:
    """Lowercase tokens, with compound identifiers emitted whole AND in parts.

    `paymentFailure` is how the flag is named and how an engineer would paste it;
    "payment failure" is how a customer describes the same incident. Both must
    reach the same runbook, so an identifier indexes as itself and as its pieces.
    Same for `demo_payment_transactions_total`.

    Parts are emitted only when a split actually happened, so an ordinary word is
    not indexed twice.
    """
    tokens: list[str] = []
    for raw in _WORD.findall(text):
        tokens.append(raw.lower())
        parts = [part for chunk in _CAMEL.split(raw) for part in chunk.split("_") if part]
        if len(parts) > 1:
            tokens.extend(part.lower() for part in parts)
    return tokens


def index_text(document: RunbookDocument) -> str:
    """Body plus the three metadata fields worth indexing. Nothing else.

    `symptom`, `service` and `fault_id` are worth +2.8 points of Recall@5 over
    the body alone, measured on this corpus.

    `queries` is excluded deliberately and permanently: including it takes
    Recall@1 from 0.648 to 0.958, because every document then matches its own
    labelled query verbatim. `severity` and `status` are excluded because they
    are filters and trust signals -- a sev1 runbook must not rank higher for
    containing the word "sev1". `fault_id` is None on symptom runbooks, so it is
    skipped rather than stringified.
    """
    metadata = document.metadata
    return " ".join(
        part
        for part in (document.body, metadata.symptom, metadata.service, metadata.fault_id)
        if part
    )

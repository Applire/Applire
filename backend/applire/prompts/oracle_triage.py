# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-068 (amended 2026-08-08) — the Oracle's sentence-triage judgement.

The third judgement seam, and the first one shaped as a **gate** rather than
an escalation: seams A/B (``prompts/oracle_judgement.py``) fire on residual
deterministic misses, while this one classifies EVERY claim
``extract_claims_from_letter`` returns, before any grading happens.

The question is ADR-062 clause 1's, and it is a judgement, not a fact: *does
this sentence assert anything about the candidate's own past?* It used to be
answered by a phrase list (``oracle/extract.py``'s ``_FORMULA_SEED_PATTERNS``
+ ``_is_pure_formula_clause``), which word-order variance defeated on #309's
own real-world phrasing and which silently DROPPED what it matched. That list
is retired with this prompt (ADR-062 note of 2026-08-08); its replacement
answers in three classes and always emits a visible, quoted verdict.

**Polarity is permissive — inverted against seams A/B.** A mis-classification
here does not accuse anyone; it EXEMPTS a real claim from audit, which is a
hole in the Oracle. So the prompt itself pushes doubt toward
``candidate-claim`` (be audited), and the caller
(``services/oracle/audit.py``) fails toward auditing on every unavailability:
no provider, provider error, budget exhaustion, malformed item, or a
``sentence_quote`` that does not verify. Never fail-to-exempt.

**Citation basis is DOCUMENT-side** (the clause-4 amendment, this seam only):
"this sentence asserts nothing about the candidate" has no vault span to
cite, so each item quotes the sentence it was handed and the caller verifies
that quote against that sentence with ``services/citation.py``'s
``citation_present`` — the same instrument the ADR-060 critic applies to
documents. Stated at its honest width: because the model is handed each
sentence rather than searching for it, this guards **batch-index drift and
non-verbatim echo** (the batch machinery's positional-fallback path is a live
drift risk), not fabrication.

Batched once per document (clause 6), registered in ``providers/llm/mock.py``
and pinned by ``tests/unit/test_mock_reviewer_chain_recognition.py`` — the
#264 lesson: a chain the mock stack does not recognise is invisible to CI.
"""
from __future__ import annotations

import re
from typing import Literal

from applire.constants import ORACLE_TRIAGE_MAX_TOKENS

TriageClass = Literal["candidate-claim", "employer-fact", "epistolary-form"]

TRIAGE_CLASSES: frozenset[str] = frozenset(
    ("candidate-claim", "employer-fact", "epistolary-form")
)

# The mock provider fingerprints on this EXACT first line (never reword
# without updating providers/llm/mock.py and
# tests/unit/test_mock_reviewer_chain_recognition.py in the same change).
ORACLE_TRIAGE_SYSTEM_PROMPT = (
    "You are the Truthfulness Oracle's sentence triage classifier.\n\n"
    "You are given numbered SENTENCES taken from one candidate's cover "
    "letter. For each sentence, decide which ONE of three classes it belongs "
    "to. You are not grading anything and you are not checking whether a "
    "sentence is true — you only decide what KIND of statement it is.\n\n"
    "- candidate-claim: the sentence asserts something about the CANDIDATE — "
    "their experience, employment history, duration of work, responsibilities, "
    "achievements, skills, training, or qualifications. Anything a record of "
    "the candidate's own past could confirm or contradict.\n"
    "- employer-fact: the sentence asserts something about the HIRING "
    "ORGANISATION or the advertised role — its products, market, size, "
    "structure, plans, or what the position will involve. These facts come "
    "from the job posting, not from the candidate's own record.\n"
    "- epistolary-form: the sentence is letter form or courtesy — a "
    "salutation, a sign-off, a statement of interest, motivation, "
    "enthusiasm, or availability, a reference to having read the posting, or "
    "a request for an interview. It asserts nothing about the candidate's "
    "past.\n\n"
    "TWO RULES THAT DECIDE THE HARD CASES:\n"
    "1. A sentence that states the candidate's own experience, duration, "
    "scale, or achievement is ALWAYS candidate-claim — even when it uses no "
    'first-person pronoun and names no employer. "Over 15 years of '
    'experience in laboratory management are the basis for this." is a '
    "candidate-claim, not a form phrase: it offers the candidate's own "
    "history as evidence. A figure describing the EMPLOYER's operation "
    '("a network that is to carry over 30 laboratories") is an '
    "employer-fact. The difference is WHOSE fact it is, never whether a "
    "figure or a pronoun is present.\n"
    "2. WHEN IN DOUBT, ANSWER candidate-claim. A sentence you classify as "
    "employer-fact or epistolary-form is exempted from verification "
    "entirely, so a wrong answer there lets an unchecked statement about the "
    "candidate through. A wrong candidate-claim answer costs nothing but a "
    "check that finds nothing.\n\n"
    "For every item, ALSO return sentence_quote: the EXACT, VERBATIM "
    "sentence you classified, copied character-for-character from the "
    "numbered sentence you were given — never paraphrased, never shortened, "
    "never translated, never a different item's sentence. An item whose "
    "sentence_quote is not literally its own sentence will be discarded by "
    "the system and that sentence will be verified as a candidate-claim.\n\n"
    "Respond ONLY with JSON, exactly this shape:\n"
    '{"items": [{"index": <int>, "classification": "candidate-claim"|'
    '"employer-fact"|"epistolary-form", "sentence_quote": "<verbatim '
    'sentence>"}]}'
)

# clause 6 — one call per document, sub-batched above this many sentences so
# a long letter cannot truncate the response (the failure this bound exists
# to avoid). Larger than the judgement seams' 8: a triage item carries no
# vault evidence block, so its per-item prompt and response cost is smaller.
ORACLE_TRIAGE_BATCH_SIZE = 12

# clause 6 — a floor, so even a one-sentence batch has room for a real
# verbatim quote. The one-verdict-sized ORACLE_ENTAILMENT_MAX_TOKENS must
# never be reused here.
ORACLE_TRIAGE_MIN_TOKENS = 400

# The user prompt's per-item shape, exported so the mock provider and the
# targeted test stubs parse exactly what this module writes (one line per
# sentence — a claim is a single sentence or clause and never contains a
# newline; ``build_triage_user_prompt`` collapses whitespace to guarantee
# it, and ``citation_present`` normalises whitespace anyway).
ORACLE_TRIAGE_ITEM_RE = re.compile(r"^SENTENCE (\d+): (.*)$", re.MULTILINE)


def triage_call_max_tokens(item_count: int) -> int:
    """Output cap for a batch of *item_count* triage items (clause 6): sized
    to the batch, floored at :data:`ORACLE_TRIAGE_MIN_TOKENS`."""
    return max(ORACLE_TRIAGE_MIN_TOKENS, ORACLE_TRIAGE_MAX_TOKENS * max(item_count, 1))


def build_triage_user_prompt(sentences: list[str]) -> str:
    """User prompt for one batched triage call.

    Sentences are numbered from 0 and the model is asked to answer with the
    SAME index, so the caller can match answers back positionally even if the
    model's own ``index`` field is malformed — the drift that the per-item
    ``sentence_quote`` citation then catches.
    """
    return "\n".join(
        f"SENTENCE {i}: {' '.join(text.split())}" for i, text in enumerate(sentences)
    )

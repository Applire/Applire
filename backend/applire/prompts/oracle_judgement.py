# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-068 — the Oracle's bounded equivalence judgement.

The deterministic layer (``services/oracle/matchers``) is a literal-surface
matcher: it can prove a claim traces to the vault, but it cannot tell a
faithful RESTATEMENT — the same fact in another language, or in different
words — from a genuine miss. Two narrow, batched, fail-safe seams hand that
one question to a model at the Oracle's own deterministic boundary (never
upstream of it, never able to overrule a red flag — ADR-052 §2 still holds):

- ``cross_language``: the claim's language differs from the vault's own
  dominant language (ADR-068 clause 2a) and the deterministic literal match
  missed — is the CLAIM a faithful restatement of some VAULT unit in the
  OTHER language?
- ``restatement``: an unanchored figure/clause's only vault backing is owned
  by a position the letter's own prose never names, and the wording overlap
  is below the discriminating floor — does the CLAIM genuinely restate that
  owned evidence, or is the number/content borrowed from an unrelated fact?

Both questions collapse to the SAME judgement shape (a claim, a short list of
candidate vault spans, and a yes/no/uncertain + citation), so both share one
system prompt and one batched call per document (clause 6) — never one call
per claim (the #264 lesson: an LLM chain unrecognised by the mock stack is
invisible to CI's fastest job, so this prompt is registered in
``providers/llm/mock.py`` and pinned by
``tests/unit/test_mock_reviewer_chain_recognition.py``).

**Faithfulness includes ALTITUDE** (ADR-068 Consequences): a rendering that
raises seniority, scope, or ownership above what the vault evidence
states — "Sachbearbeiter" rendered as "Manager", "Mitarbeit an X" rendered as
"led X" — is NOT an equivalent restatement, even when every content word
otherwise lines up. The system prompt states this explicitly; it is the one
judgement call this seam exists to make correctly, not just "same topic?".

**Fail-safe, always.** A judgement call answers at most "this claim
corresponds" or "it does not" — the caller (``services/oracle/audit.py``)
decides what verdict that implies, and an unavailable/malformed/uncited
answer NEVER produces an accusation on its own (ADR-068 clause 3).
"""
from __future__ import annotations

from typing import Literal

from applire.constants import ORACLE_JUDGEMENT_MAX_TOKENS

JudgementMode = Literal["cross_language", "restatement"]

# The mock provider fingerprints on this EXACT first line (never reword
# without updating providers/llm/mock.py and
# tests/unit/test_mock_reviewer_chain_recognition.py in the same change).
ORACLE_JUDGEMENT_SYSTEM_PROMPT = (
    "You are the Truthfulness Oracle's equivalence judge.\n\n"
    "For each numbered item you are given a CLAIM (one sentence or clause "
    "from a candidate's job-application document) and one or more numbered "
    "spans of VAULT EVIDENCE (the candidate's own verified profile data). "
    "Decide whether the CLAIM is a faithful equivalent or restatement of the "
    "VAULT EVIDENCE.\n\n"
    "Each item also states a MODE:\n"
    "- cross_language: the CLAIM and the VAULT EVIDENCE may be written in "
    "different languages (e.g. one in English, one in German). A correct "
    "translation of the same fact IS a faithful restatement — do not "
    "penalise language alone.\n"
    "- restatement: the CLAIM and the VAULT EVIDENCE are typically the same "
    "language, but rendered as different content or blended with wording "
    "the evidence does not support. Judge whether the CLAIM's own content "
    "genuinely comes from this evidence, not merely whether a number "
    "matches.\n\n"
    "FAITHFULNESS INCLUDES ALTITUDE: a CLAIM that raises seniority, scope, "
    "responsibility, or ownership above what the VAULT EVIDENCE states is "
    "NOT equivalent, even when the topic and any figures line up. For "
    "example, evidence describing a \"Sachbearbeiter\" (case handler) role "
    "rendered as \"Manager\" is not equivalent; evidence describing "
    "\"Mitarbeit an X\" (contributed to X) rendered as \"led X\" is not "
    "equivalent. A claim that stays at or below the evidence's own altitude "
    "IS equivalent.\n\n"
    "For each item, decide corresponds:\n"
    '- true: the CLAIM is a faithful equivalent/restatement of the VAULT '
    "EVIDENCE, at the same altitude or lower.\n"
    '- false: the CLAIM is NOT a faithful restatement of the VAULT '
    "EVIDENCE (wrong content, or raised altitude).\n"
    '- "uncertain": you cannot tell either way from the evidence given.\n\n'
    "For every item, ALSO return vault_quote: the EXACT, VERBATIM span from "
    "the VAULT EVIDENCE you relied on to decide — copied character-for-"
    "character from the numbered evidence you were given, never paraphrased, "
    "never invented, never translated. This applies whether corresponds is "
    "true, false, or uncertain — you must always cite the specific evidence "
    "span you compared the claim against. An item whose vault_quote is not "
    "literally present in its own VAULT EVIDENCE will be discarded by the "
    "system.\n\n"
    "Respond ONLY with JSON, exactly this shape:\n"
    '{"items": [{"index": <int>, "corresponds": true|false|"uncertain", '
    '"vault_quote": "<verbatim span from the vault evidence you relied on>"}'
    "]}"
)

# clause 6 — never one call per claim; a batch call carries at most this many
# items (the response-shape's per-item cost is small, but an unbounded batch
# risks truncation, which is exactly the failure this bound exists to avoid).
ORACLE_JUDGEMENT_BATCH_SIZE = 8

# clause 6 — the existing ORACLE_ENTAILMENT_MAX_TOKENS=200 is sized for ONE
# verdict with no citation span; a judgement batch must NOT reuse it.
ORACLE_JUDGEMENT_MIN_TOKENS = 400


def judgement_call_max_tokens(item_count: int) -> int:
    """Output cap for a batch of *item_count* judgement items (clause 6):
    120 tokens/item, floored at 400 so even a single-item batch has enough
    room for a real vault_quote span."""
    return max(ORACLE_JUDGEMENT_MIN_TOKENS, ORACLE_JUDGEMENT_MAX_TOKENS * max(item_count, 1))


def build_judgement_user_prompt(
    items: list[tuple[str, list[str], JudgementMode]],
) -> str:
    """User prompt for one batched judgement call.

    *items* is (claim_text, candidate vault unit texts, mode) tuples, ordered
    — the model is asked to answer with the SAME 0-based index so the caller
    can match responses back to candidates positionally even if the model's
    own "index" field is malformed or missing.
    """
    blocks: list[str] = []
    for i, (claim_text, unit_texts, mode) in enumerate(items):
        evidence_lines = "\n".join(
            f"  [{j}] {text}" for j, text in enumerate(unit_texts)
        ) or "  (no candidate evidence)"
        blocks.append(
            f"ITEM {i} (mode: {mode}):\n"
            f"CLAIM: {claim_text}\n"
            f"VAULT EVIDENCE:\n{evidence_lines}"
        )
    return "\n\n".join(blocks)

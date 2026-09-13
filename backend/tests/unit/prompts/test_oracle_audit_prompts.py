# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""M5.7.1 — pin ``applire.prompts.oracle_audit`` byte-identical to the inline
prompt text ``services/oracle/extract.py`` and ``services/oracle/audit.py``
built before the move. String-level assertions plus one wiring check on the
real ``_entailment`` call site; no LLM.
"""
import pytest

from applire.prompts.oracle_audit import (
    ENTAILMENT_SYSTEM_PROMPT,
    SEGMENT_PROSE_SYSTEM_PROMPT,
    build_entailment_prompt,
    build_segment_prose_prompt,
)
from applire.schemas.oracle import ClaimVerdict
from applire.services.oracle.audit import (
    _EntailmentBudget,
    _entailment,
)
from applire.services.oracle.matchers.vault import EvidenceUnit

# ── segmentation prompt (extract.py's _segment_prose_llm) ───────────────────

_GOLDEN_SEGMENT_SYSTEM = (
    "Split the following resume/cover-letter prose into its individual factual "
    "claims (one short statement each). Return STRICT JSON: "
    '{"claims": ["...", "..."]}. Do not rephrase, do not add or drop content — '
    "segment only."
)


def test_segment_system_prompt_byte_identical_to_pre_move_text():
    assert SEGMENT_PROSE_SYSTEM_PROMPT == _GOLDEN_SEGMENT_SYSTEM


def test_build_segment_prose_prompt_is_payload_only():
    assert build_segment_prose_prompt("some prose") == "TEXT:\nsome prose"


def test_segment_prompt_concatenation_matches_pre_move_single_argument():
    """system + prompt reconstructs the exact string the old single-argument
    ``_SEGMENT_PROMPT.format(text=...)`` call produced."""
    text = "Led migrations across three teams."
    old_single_argument_prompt = (
        "Split the following resume/cover-letter prose into its individual factual "
        "claims (one short statement each). Return STRICT JSON: "
        '{"claims": ["...", "..."]}. Do not rephrase, do not add or drop content — '
        "segment only.\n\nTEXT:\n" + text
    )
    reconstructed = SEGMENT_PROSE_SYSTEM_PROMPT + "\n\n" + build_segment_prose_prompt(text)
    assert reconstructed == old_single_argument_prompt


# ── entailment prompt (audit.py's _entailment, #404 retrofit) ───────────────

_GOLDEN_ENTAILMENT_SYSTEM = (
    "You are a strict verification function for job-application claims.\n"
    "Compare the DOCUMENT CLAIM against the PROFILE EVIDENCE and return "
    'STRICT JSON: {"verdict": "grounded" | "inflated" | "unbacked" | "unverifiable"}.\n'
    "- grounded: the evidence supports the claim as stated\n"
    "- inflated: the evidence is aspirational or weaker than the claim's rendering "
    "(e.g. a target presented as an achieved result)\n"
    "- unbacked: the evidence does not contain or contradicts the claim\n"
    "- unverifiable: subjective, or the evidence cannot decide it"
)


def test_entailment_system_prompt_byte_identical_to_pre_move_text():
    assert ENTAILMENT_SYSTEM_PROMPT == _GOLDEN_ENTAILMENT_SYSTEM


def test_build_entailment_prompt_byte_identical_to_pre_move_format():
    evidence = "- worked at Acme for 4 years"
    claim = "Led the Acme migration."
    assert (
        build_entailment_prompt(evidence, claim)
        == f"PROFILE EVIDENCE:\n{evidence}\n\nDOCUMENT CLAIM:\n{claim}"
    )


class _SpyProvider:
    def __init__(self, response: dict):
        self.calls: list[dict] = []
        self._response = response

    async def aparse_json(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        return self._response


_FALLBACK = ClaimVerdict(verdict="unverifiable", checker="entailment", detail="fallback")


@pytest.mark.asyncio
async def test_entailment_call_site_uses_the_moved_prompt_module():
    """Wiring check: audit.py's real ``_entailment`` call site now sources
    its system/prompt from the moved ``applire.prompts.oracle_audit``
    constants, not a re-inlined copy."""
    spy = _SpyProvider({"verdict": "grounded"})
    unit = EvidenceUnit(path="work[0]", text="Led the Acme migration.", text_norm="led the acme migration.")
    verdict = await _entailment(
        "Led the Acme migration.",
        [unit],
        spy,
        _EntailmentBudget(limit=1),
        _FALLBACK,
    )
    assert len(spy.calls) == 1
    assert spy.calls[0]["system"] == ENTAILMENT_SYSTEM_PROMPT
    assert spy.calls[0]["prompt"] == build_entailment_prompt(
        "- Led the Acme migration.", "Led the Acme migration."
    )
    assert verdict.verdict == "grounded"

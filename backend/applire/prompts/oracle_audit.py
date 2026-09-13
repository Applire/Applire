# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Truthfulness Oracle prompts moved out of ``services/oracle/`` (M5.7.1
inline-prompt move) — placement changes only; every rendered string stays
byte-identical to what shipped before the move (pinned by the corresponding
unit tests).

  * :data:`SEGMENT_PROSE_SYSTEM_PROMPT` / :func:`build_segment_prose_prompt`
    — ``services/oracle/extract.py``'s ``_segment_prose_llm`` (ADR-047
    bounded-output-by-contract prose-segmentation fallback, US248
    audit-any-document). Previously the standing instruction and the payload
    both travelled as ONE ``prompt=`` argument
    (``_SEGMENT_PROMPT.format(text=text)``) — the same shape the #404
    retrofit fixed for the entailment call below, fixed here too: the
    instruction is now ``system=`` and only the text to segment is
    ``prompt=``. Text unchanged, placement only.

  * :data:`ENTAILMENT_SYSTEM_PROMPT` / :func:`build_entailment_prompt` —
    ``services/oracle/audit.py``'s ``_entailment`` (ADR-052, the narrow
    bounded entailment call for deterministically-undecided figure-free
    claims). Moved from that module's own ``_ENTAILMENT_SYSTEM_PROMPT`` /
    ``_ENTAILMENT_USER_PROMPT`` constants, introduced there by the #404
    retrofit (2026-08-01): that call originally had NO ``system=`` argument
    at all, so ``MockLLMProvider.aparse_json``'s fingerprint strategy (which
    inspects ``system``, never ``prompt``) could never recognise it and the
    mock stack silently never exercised a real entailment verdict shape
    (``tests/unit/test_mock_reviewer_chain_recognition.py`` pins the fix).
    Reachability of this call (M5.7.1 enumeration,
    ``Documents/Runs/Nougat/build-3/w/oracle-audit-callers.md``): live and
    intentional from the agent-door ``audit_document`` MCP tool
    (``entailment=True`` default, ``backend/applire/mcp/server.py``);
    explicitly disabled (``entailment=False``) on the CV/cover-letter
    generation path (``services/oracle/selfaudit.py``, ADR-068 clause 7
    scoping) so threading a judgement-seam provider into generation does not
    silently reactivate this older, broader mechanism there too.
"""

SEGMENT_PROSE_SYSTEM_PROMPT = (
    "Split the following resume/cover-letter prose into its individual factual "
    "claims (one short statement each). Return STRICT JSON: "
    '{"claims": ["...", "..."]}. Do not rephrase, do not add or drop content — '
    "segment only."
)


def build_segment_prose_prompt(text: str) -> str:
    """Payload only — the standing instruction travels as
    :data:`SEGMENT_PROSE_SYSTEM_PROMPT` (``system=``)."""
    return f"TEXT:\n{text}"


ENTAILMENT_SYSTEM_PROMPT = (
    "You are a strict verification function for job-application claims.\n"
    "Compare the DOCUMENT CLAIM against the PROFILE EVIDENCE and return "
    'STRICT JSON: {"verdict": "grounded" | "inflated" | "unbacked" | "unverifiable"}.\n'
    "- grounded: the evidence supports the claim as stated\n"
    "- inflated: the evidence is aspirational or weaker than the claim's rendering "
    "(e.g. a target presented as an achieved result)\n"
    "- unbacked: the evidence does not contain or contradicts the claim\n"
    "- unverifiable: subjective, or the evidence cannot decide it"
)


def build_entailment_prompt(evidence: str, claim: str) -> str:
    return f"PROFILE EVIDENCE:\n{evidence}\n\nDOCUMENT CLAIM:\n{claim}"

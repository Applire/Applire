# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""US246 — pre-delivery truthfulness self-audit (ADR-052 §4).

Called by the CV and cover-letter generation pipelines inside the same commit
that persists the artifact and its ATS report, so re-generation replaces the
report atomically and "ready implies report available" (the E037 PQ #2
lesson) holds for the truthfulness panel too.

**The deterministic core is still hermetic; judgement is now amended in
(ADR-068 clause 7, 2026-08-01).** The original invariant here was "no
entailment provider — generation must stay LLM-free after the writer
finishes". ADR-068's two bounded equivalence seams (cross-language +
restatement, ``services/oracle/audit.py``) change that: the deterministic
layer still runs FIRST and UNCHANGED (nothing here alters ``audit_document``'s
own red-flag ordering — ADR-052 §2 still holds), but when a caller supplies a
``provider`` the judgement pass now runs too, batched, after it. Generation
NEVER fails or blocks on a judgement problem either way: with ``provider=
None`` (the default — a caller that hasn't opted in, or genuinely wants the
old LLM-free guarantee) any seam that triggers takes the clause-3 fail-safe
path and is counted in ``judgement_unavailable``, exactly as if the provider
call itself had failed. A failure ANYWHERE in this function NEVER raises and
NEVER blocks delivery — it leaves the report NULL (ADR-040 attestation stays
the gate).

**Scoping deviation (found during implementation, not in the ADR-068 clause
text):** ``audit_document``'s ``provider`` argument was ALREADY shared by an
older, broader mechanism — the narrow ``_entailment`` fallback (ADR-052) for
undecided figure-free claims, previously reachable only from the agent-door
tool because generation always called this function with ``provider=None``.
Threading a real provider here for the two new seams would have silently
reactivated that older mechanism for EVERY CV/letter generation too — extra
latency/cost the "no added latency/cost" half of the original invariant never
agreed to, confirmed by several pre-existing tests whose mocked providers
assert an exact call count/sequence and broke under the extra call. This
module therefore calls ``audit_document(..., entailment=False)`` — ``provider``
here powers ONLY the cross-language/restatement judgement seams.
"""
from __future__ import annotations

import logging
from typing import Any

from applire.services.oracle.audit import audit_document

logger = logging.getLogger(__name__)


async def build_self_audit_report(
    profile_json: dict[str, Any] | None,
    *,
    tailored_data: dict[str, Any] | None = None,
    letter_data: dict[str, Any] | None = None,
    provider: Any | None = None,
    document_language: str | None = None,
) -> dict[str, Any] | None:
    """Truthfulness report as a JSONB-safe dict, or None on any failure.

    ``provider`` (ADR-068 clause 7) is optional — omit it (or pass ``None``)
    to keep the pre-ADR-068 deterministic-only behaviour; the two judgement
    seams then always resolve to their clause-3 fail-safe verdict when
    triggered. ``document_language`` is this document's own generation
    language (``None`` keeps the cross-language seam off regardless of
    ``provider``) — see the CV/cover-letter callers in ``services/cv.py`` /
    ``services/cover_letter.py`` for which value they thread through and why.
    """
    try:
        if tailored_data is not None:
            report = await audit_document(
                "cv",
                profile_json or {},
                tailored_data=tailored_data,
                provider=provider,
                document_language=document_language,
                # ADR-068 clause 7 scoping (flagged deviation, see
                # audit_document's own docstring): keep the OLDER, broader
                # entailment mechanism off during generation — ``provider``
                # here powers ONLY the two new bounded judgement seams.
                entailment=False,
            )
        elif letter_data is not None:
            report = await audit_document(
                "cover_letter",
                profile_json or {},
                letter_data=letter_data,
                provider=provider,
                document_language=document_language,
                entailment=False,
            )
        else:
            return None
        return report.model_dump(mode="json")
    except Exception:
        logger.exception("Truthfulness self-audit failed — report left NULL")
        return None

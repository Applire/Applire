# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""US246 — pre-delivery truthfulness self-audit (ADR-052 §4).

Called by the CV and cover-letter generation pipelines inside the same commit
that persists the artifact and its ATS report, so re-generation replaces the
report atomically and "ready implies report available" (the E037 PQ #2
lesson) holds for the truthfulness panel too.

The self-audit is deterministic-only (no entailment provider): generation
must stay LLM-free after the writer finishes, hermetic in CI, and free of
added latency/cost. The narrow entailment path remains available on the
agent-door ``audit_document`` tool. A failure NEVER raises and NEVER blocks
delivery — it leaves the report NULL (ADR-040 attestation stays the gate).
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
) -> dict[str, Any] | None:
    """Truthfulness report as a JSONB-safe dict, or None on any failure."""
    try:
        if tailored_data is not None:
            report = await audit_document(
                "cv", profile_json or {}, tailored_data=tailored_data
            )
        elif letter_data is not None:
            report = await audit_document(
                "cover_letter", profile_json or {}, letter_data=letter_data
            )
        else:
            return None
        return report.model_dump(mode="json")
    except Exception:
        logger.exception("Truthfulness self-audit failed — report left NULL")
        return None

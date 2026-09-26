# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The truthfulness verdict summary on the ATS-report envelope (ADR-058 amended
2026-09-26, ruling A-1; Agent #676, founder UAT finding F-12).

The human sees every red Oracle verdict in the review panel's group 1; the agent
on the guided pipeline read ``get_cv_ats_report`` / ``get_cover_letter_ats_report``
and saw none. One function, called by both doors' shared service
(``services.cv.get_cv_ats_report`` / ``services.cover_letter.get_cover_letter_ats_report``),
so REST and MCP serialise the same object (ADR-066).

``flagged`` is the SAME selection the panel renders — ``review_state._flagged_claims``
over the persisted report, with the ATS report's ``claimable_concepts`` — so the
panel and the agent cannot disagree about what is red. No LLM call, no re-audit:
a summary of what is persisted (staleness inherited, SF-ORACLE.9).
"""
from __future__ import annotations

import logging

from applire.schemas.ats import TruthfulnessSummary
from applire.schemas.oracle import TruthfulnessReport
from applire.services.review_state import _flagged_claims

logger = logging.getLogger(__name__)


def _unavailable() -> TruthfulnessSummary:
    return TruthfulnessSummary(available=False)


def summarize(truth_report: dict | None, ats_report: dict | None) -> TruthfulnessSummary:
    """Summary of a row's persisted truthfulness report.

    Unknown is never clean: a missing, empty or unvalidatable report yields
    ``available=False`` with ``flagged`` / ``stop_and_fix`` = ``None``.
    """
    if not truth_report or not isinstance(truth_report, dict):
        return _unavailable()
    try:
        validated = TruthfulnessReport.model_validate(truth_report)
    except Exception:
        logger.warning("Stored truthfulness report is malformed — summary unavailable")
        return _unavailable()
    keywords = ((ats_report or {}).get("keywords") or {}) if isinstance(ats_report, dict) else {}
    claimable = list(keywords.get("claimable_concepts") or [])
    flagged = len(_flagged_claims(truth_report, claimable))
    return TruthfulnessSummary(
        available=True,
        counts=dict(validated.counts),
        flagged=flagged,
        stop_and_fix=flagged > 0,
        unverifiable_dominated=validated.unverifiable_dominated,
    )

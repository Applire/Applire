# Copyright (C) 2024-2026 Tobias Rosenbaum
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""ADR-060 — the outcome critic's persisted verdict (#322, amended 2026-07-31).

One engine, two mounts (ADR-066): the same report shape is persisted on
``GeneratedCV.critic_report`` (Pass A — assembled single-document coherence)
and ``GeneratedCoverLetter.critic_report`` (Pass B — cross-document
coherence). A separate report shape from ``ATSReport``/``TruthfulnessReport``
(own field, own endpoint) rather than a new key folded into either — the
critic judges something neither of those checks, and keeping it a distinct,
additive surface means it can never be confused with a correctness/grounding
verdict, and can never gate on the same field a stricter consumer might key
off of.

``CriticAdvisory.changed`` is pinned to the literal ``False`` (SF-CRITIC.5):
the type itself makes "this pass could have set changed=True" inexpressible —
the only way to alter that would be to change the schema, never a call site.
"""

import uuid
from typing import Literal, Optional

from pydantic import BaseModel, Field


class CriticAdvisory(BaseModel):
    """One coherence finding — always advisory, never a rewrite (ADR-060
    clause 5/amendments). ``cv_state``/``cv_detail``/``letter_state`` are the
    literal, citation-verified snippets the finding rests on (SF-CRITIC.2/.6:
    "state the fact it rests on so a user can adjudicate at a glance");
    ``message`` is the localized narrative built deterministically from those
    facts (see ``services/outcome_critic.py:_build_advisory``) — DE/EN parity
    is a construction guarantee (both are always populated), never a per-call
    accident of which language the model happened to answer in.

    ``kind`` (2026-07-31, third ADR-060 amendment):
    - ``letter_only`` — the letter states something the CV never mentions
    - ``letter_richer`` — both mention it; only the letter carries the depth
    - ``numeric_inconsistency`` — the two documents state different figures
      for the same quantity (cross-document)
    - ``internal_inconsistency`` — one document contradicts itself (Pass A:
      a summary broader than its own detail; ``cv_detail`` holds the second
      span)
    """

    concept: str
    kind: Literal[
        "letter_only",
        "letter_richer",
        "numeric_inconsistency",
        "internal_inconsistency",
    ] = "letter_only"
    # The CV's own verbatim mention, or None when the concept is entirely
    # absent from the CV (the plain letter-only shape).
    cv_state: Optional[str] = None
    # Pass A internal findings: the second CV span the first one overreaches
    # (e.g. the detail bullet a summary claim is broader than).
    cv_detail: Optional[str] = None
    # The letter's verbatim mention the finding is about (None on Pass A —
    # there is no letter at the CV mount).
    letter_state: Optional[str] = None
    changed: Literal[False] = False
    message: dict[str, str] = Field(default_factory=dict)  # {"de": ..., "en": ...}


class OutcomeCriticReport(BaseModel):
    """Persisted on ``GeneratedCV.critic_report`` (Pass A) and
    ``GeneratedCoverLetter.critic_report`` (Pass B).

    ``ran``/``reason`` deliberately distinguish "did not run" from "ran and
    found nothing" from "ran but the judgement call errored" (SF-CRITIC.1) —
    collapsing any two of these into the same shape is the exact blind-control
    failure this pass exists to avoid repeating.

    reason ∈ {None, "disabled", "missing_letter", "missing_cv",
    "missing_ledger", "judgement_error"}. ``None`` only when ``ran`` is True
    and at least the judgement call itself completed (``advisories`` may
    still be empty — the model found nothing worth surfacing). The pre-
    2026-07-31 ``no_candidates`` short-circuit is retired deliberately
    (SF-CRITIC.9: 0 candidates ≠ nothing wrong); old persisted rows may still
    carry it.

    ``dropped_citations`` (SF-CRITIC.11): findings the model returned whose
    quoted spans failed literal verification against the documents — dropped,
    never surfaced, but COUNTED so a run with gutted recall is readable from
    the persisted report alone.
    """

    ran: bool
    reason: Optional[str] = None
    # Which mount produced this report: "cv" (Pass A) or "letter" (Pass B).
    # None on pre-2026-07-31 rows.
    mount: Optional[str] = None
    advisories: list[CriticAdvisory] = Field(default_factory=list)
    dropped_citations: int = 0


class OutcomeCriticReportResponse(BaseModel):
    """API envelope — mirror of ``ATSReportResponse``/``TruthfulnessReportResponse``:
    ``report`` is null until generation + the critic pass complete (or for a
    pre-Tiramisu row that predates this field)."""

    document_id: uuid.UUID
    status: str                  # generation status of the underlying document
    report: Optional[OutcomeCriticReport] = None

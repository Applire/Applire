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

"""ADR-060 Pass B — the outcome critic's persisted verdict (#322).

A separate report shape from ``ATSReport``/``TruthfulnessReport`` (own field,
own endpoint, own MCP tool) rather than a new key folded into either — Pass B
judges something neither of those checks (cross-document coherence), and
keeping it a distinct, additive surface means it can never be confused with a
correctness/grounding verdict, and can never gate on the same field a stricter
consumer might key off of.

``CriticAdvisory.changed`` is pinned to the literal ``False`` (SF-CRITIC.5):
the type itself makes "this pass could have set changed=True" inexpressible —
the only way to alter that would be to change the schema, never a call site.
"""

import uuid
from typing import Literal, Optional

from pydantic import BaseModel, Field


class CriticAdvisory(BaseModel):
    """One cross-document finding — always advisory, never a rewrite (ADR-060
    clause 5/amendment). ``cv_state``/``letter_state`` are the literal, verbatim
    snippets the finding rests on (SF-CRITIC.2/.6: "state the fact it rests on
    so a user can adjudicate at a glance"); ``message`` is the localized
    narrative built deterministically from those facts (see
    ``services/outcome_critic.py:_build_advisory``) — DE/EN parity is a
    construction guarantee (both are always populated), never a per-call
    accident of which language the model happened to answer in.
    """

    concept: str
    # The CV's own verbatim mention, or None when the concept is entirely
    # absent from the CV (the plain letter-only shape).
    cv_state: Optional[str] = None
    # The letter's verbatim mention that the finding is about.
    letter_state: str
    changed: Literal[False] = False
    message: dict[str, str] = Field(default_factory=dict)  # {"de": ..., "en": ...}


class OutcomeCriticReport(BaseModel):
    """Persisted on ``GeneratedCoverLetter.critic_report``.

    ``ran``/``reason`` deliberately distinguish "did not run" from "ran and
    found nothing" from "ran but the judgement call errored" (SF-CRITIC.1) —
    collapsing any two of these into the same shape is the exact blind-control
    failure this pass exists to avoid repeating.

    reason ∈ {None, "disabled", "missing_letter", "missing_cv",
    "missing_ledger", "no_candidates", "judgement_error"}. ``None`` only when
    ``ran`` is True and at least the judgement call itself completed
    (``advisories`` may still be empty — the model found nothing worth
    surfacing).
    """

    ran: bool
    reason: Optional[str] = None
    advisories: list[CriticAdvisory] = Field(default_factory=list)


class OutcomeCriticReportResponse(BaseModel):
    """API envelope — mirror of ``ATSReportResponse``/``TruthfulnessReportResponse``:
    ``report`` is null until generation + the critic pass complete (or for a
    pre-Tiramisu row that predates this field)."""

    document_id: uuid.UUID
    status: str                  # generation status of the underlying document
    report: Optional[OutcomeCriticReport] = None

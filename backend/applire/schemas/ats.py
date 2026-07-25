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

import uuid
from typing import Literal, Optional

from pydantic import BaseModel


class ATSCheck(BaseModel):
    id: str                      # stable machine id, e.g. "contact-name", "work-2", "reading-order"
    status: Literal["pass", "fail"]
    details: Optional[str] = None  # human-readable EN diagnostic; frontend translates labels by id
    # E042 follow-up (ADR-038 chrome discipline): machine-readable variant of `details`
    # for user-facing bands the frontend localises (currently the page-length band).
    # `details` stays populated as the EN fallback — persisted legacy reports and the
    # agent channel read it. Both None for pure EN diagnostics.
    details_key: Optional[str] = None
    details_params: Optional[dict[str, int | str]] = None


class ATSKeywordCoverage(BaseModel):
    present: list[str] = []
    missing: list[str] = []  # back-compat: the full missing list (claimable + honest-gap)
    # US203 (ADR-048): a missing keyword the candidate HAS per the Keyword Ledger
    # (should have been surfaced) vs one they genuinely lack (an honest gap — never
    # something to fabricate). When no ledger is available all missing default to honest-gap.
    missing_claimable: list[str] = []
    missing_honest_gap: list[str] = []
    # ADR-048 amended 2026-07-03 (#117), the fourth quadrant: a keyword PRESENT in the
    # document but NOT claimable per the ledger — an unsupported claim (e.g. typed in
    # via the section editor). Surfaced as a truthfulness warning, never silently
    # counted as ordinary coverage. Empty when no ledger is available (cannot judge).
    present_unsupported: list[str] = []
    # E048/US266 (#249 option b): EVERY claimable ledger entry's surface forms
    # (concept name included), regardless of whether the term is present or
    # missing in the document — unlike `missing_claimable`, which only covers
    # the absent subset. Lets the frontend join the Truthfulness Oracle's
    # per-skill "unbacked" verdict against the Keyword Ledger's (possibly
    # adjacency-only) claimable classification, without a new endpoint or any
    # change to the Oracle's own verdict taxonomy. Empty when no ledger is
    # available (legacy back-compat).
    claimable_concepts: list[str] = []
    # #260 — pre-generation keyword-liability check (agent-door + report
    # parity): the concept names of every Keyword Ledger entry that is a JD
    # HARD REQUIREMENT, claimable (WILL be echoed by the generator), but
    # carries no narrative evidence anywhere in the vault (bare skills-list
    # entry only). Distinct axis from `claimable_concepts`/#249's "related"
    # state — literal vault presence vs narrative depth — and additive: a
    # concept may appear in both lists without contradiction. Empty when no
    # ledger is available, or the ledger predates #260 (no `narrative_backed`
    # key — back-compat default is "backed", so nothing is flagged).
    keyword_liability_concepts: list[str] = []


class ATSReport(BaseModel):
    version: int = 1
    document: Literal["cv", "cover_letter"]
    checks: list[ATSCheck]
    keywords: ATSKeywordCoverage
    # convenience counts — NOT a score (ADR-039/ADR-035): the UI shows the list, never a percentage
    passed: int
    failed: int


class ATSReportResponse(BaseModel):
    document_id: uuid.UUID
    status: str                  # generation status of the underlying document
    report: Optional[ATSReport] = None   # null while pending/failed or when the audit engine errored

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

from pydantic import BaseModel, Field


class ATSCheck(BaseModel):
    id: str                      # stable machine id, e.g. "contact-name", "work-2", "reading-order"
    # E057/ADR-079 clause 4: a THIRD status, `not_applicable`, for a check that
    # genuinely cannot be evaluated on the artefact (e.g. the page-length band
    # on a `.docx` export, which has no fixed pagination until a renderer lays
    # it out). Counted in its own bucket by `_finish()` — never folded into
    # `passed`/`failed`, and never silently absent, which is worse: an absent
    # check is invisible to both counters and reads as a clean, complete audit
    # of something that was never examined (the #634 failure class).
    # Producers, in the order they arrived: `_page_band_not_applicable` on the
    # `.docx` export (E057, the widening's original reason), and — since
    # ADR-039 amended 2026-09-04 (#563 part D / #542) — `terminal-review` and
    # `narrative-evidence` on the PRIMARY report, which is the first time the
    # PDF-side report can carry this status at all. A renderer that only ever
    # met it on the `.docx` report must be re-read.
    status: Literal["pass", "fail", "not_applicable"]
    details: Optional[str] = None  # human-readable EN diagnostic; frontend translates labels by id
    # E042 follow-up (ADR-038 chrome discipline): machine-readable variant of `details`
    # for user-facing bands the frontend localises (currently the page-length band).
    # `details` stays populated as the EN fallback — persisted legacy reports and the
    # agent channel read it. Both None for pure EN diagnostics.
    details_key: Optional[str] = None
    details_params: Optional[dict[str, int | str]] = None
    # E056/ADR-077 clause 5: structured driver for a fail band — machine-
    # readable on every door (an agent reading only pass/fail must not be the
    # design assumption). Two keys exist: {"pinned_facts": N} on page-length,
    # and {"concepts": N} on `narrative-evidence` (ADR-039 amended 2026-09-04).
    # Read the key, never assume the single one.
    driver: Optional[dict[str, int]] = None


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


class PinnedFactReportEntry(BaseModel):
    """E056/ADR-077 clauses 3+5 — one fact pin's measured fate on this
    document. `present` is the normalized-containment FACT scoped to the
    pinned entry's tailored twin (never document-wide); a stale pin is
    surfaced here with present=False, never silently dropped."""

    pin_id: str
    entry_type: str
    quote: str
    present: bool
    stale: bool = False
    # ADR-077 clause 2 / SF-PIN.6: a truth floor (letter_figure_guard et al.)
    # deleted the carrier — correct by hierarchy (truth > pin), never silent.
    removed_by_truth_floor: bool = False
    # ADR-077 amended 2026-08-26 (#580): the Keyword Ledger's DO-NOT-CLAIM concepts
    # the pinned quote carries as whole tokens — a FACT about the quote (the same
    # fact that keeps the reviewer from demanding it), never a statement about
    # why the pin is absent. `[]` = measured, no conflict (or no ledger);
    # `None` = the report predates the field and was never measured — a legacy
    # report must not read as "genuinely clean" (adversarial pass 2026-08-26).
    ledger_conflict: list[str] | None = None


class ATSReport(BaseModel):
    version: int = 1
    document: Literal["cv", "cover_letter"]
    checks: list[ATSCheck]
    keywords: ATSKeywordCoverage
    # convenience counts — NOT a score (ADR-039/ADR-035): the UI shows the list, never a percentage
    # E057/ADR-079 clause 4: both EXCLUDE not_applicable checks by construction
    # (_finish sums on status equality) — a not_applicable check is neither a
    # pass nor a fail, at this layer or any layer that reports a total.
    passed: int
    failed: int
    # E057/ADR-079 clause 4: the not_applicable bucket, counted separately —
    # same back-compat shape as `PinnedFactReportEntry.ledger_conflict` below,
    # deliberately not the same shape as `passed`/`failed` (plain, required
    # `int`). Those two are safe as required fields because every report ever
    # persisted was computed by a version of `_finish()` that populated them;
    # `not_applicable` did not exist before this field, so a persisted report
    # from before this change has no key for it at all.
    #   * `None` = the report predates this field — audited under the old
    #     two-value pass/fail vocabulary, where `not_applicable` could not
    #     have been assigned to any check. Never read as "confirmed zero";
    #     the schema never gave that report the chance to say so.
    #   * an int (including `0`) = computed by `_finish()` under the current
    #     three-value schema. `_finish` populates this on every call, so any
    #     report produced from here on always carries a real, measured count
    #     — `0` genuinely means "audited, and nothing came back not_applicable".
    not_applicable: Optional[int] = None
    # E056/ADR-077: per-pin presence measurement (ship-and-report, never a
    # gate). None = audited without pin context (legacy reports, no pins).
    pinned_facts: Optional[list[PinnedFactReportEntry]] = None


class ATSReportResponse(BaseModel):
    document_id: uuid.UUID
    status: str                  # generation status of the underlying document
    report: Optional[ATSReport] = None   # null while pending/failed or when the audit engine errored

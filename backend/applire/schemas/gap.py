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

import logging
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from applire.schemas.gap_cluster import GapClusterSchema

logger = logging.getLogger(__name__)


class RequirementBreakdownItem(BaseModel):
    requirement: str
    source: str  # "required" | "nice_to_have"
    status: str  # "direct" | "partial" | "gap" (=unknown) | "denied" (ADR-048 am. 2026-07-27)
    slot: float
    earned: float
    reason: str = ""


class KeywordLedgerEntry(BaseModel):
    """One JD expectation in the Keyword Ledger (ADR-048, E037)."""

    concept: str
    surface_forms: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)  # required | nice_to_have | keyword
    fit_weight: float  # 1.0 required / 0.5 nice_to_have / 0.0 keyword-only
    status: str  # "direct" | "partial" | "gap" (=unknown) | "denied" (ADR-048 am. 2026-07-27)
    evidence: str = ""
    claimable: bool
    # #260 — pre-generation keyword-liability check: is this concept
    # substantiated by a NARRATIVE somewhere in the vault (a work/project
    # bullet, an achievement, a signature story) — never just the bare
    # skills list. Defaults True so a legacy row persisted before #260 (no
    # key at all) is never mistaken for a liability.
    narrative_backed: bool = True
    # ADR-064 — mirrors DeniedConcept.denial_level (the durable home is
    # ProfileMetadata.denied_concepts; this is the ledger's read-only mirror,
    # rewritten by _enforce_denial_stance on every rebuild). None whenever
    # status != "denied": a direct/partial/gap entry was never denied at any
    # level. Never independently settable to a meaningful value on a
    # non-denied entry — see the invariant enforced below.
    denial_level: Literal["direct", "partial"] | None = None
    # ADR-048 amended 2026-07-27 / 2026-08-13 — WHAT makes this entry `partial`:
    # the profile's own name for the DIFFERENT capability standing in for the JD's
    # term (the posting asks for TOGAF, the profile has arc42). Present iff the
    # row is claimable AND status == "partial" (the lifecycle invariant; see
    # keyword_ledger.is_positioning_only).
    #
    # Added to this schema 2026-08-13, and it is a fix, not an extension:
    # `services/gap.py`'s `_LEDGER_PUBLISHABLE_KEYS` has listed the field as
    # publishable since ADR-048, but this model declared neither the field nor
    # `extra="allow"` — so `GapAnalysisResponse.model_validate(...)` silently
    # stripped it from EVERY API response, on every status. An allowlist a schema
    # below it defeats is a control that cannot fire; the agent channel and the
    # frontend have never once seen the field the ledger works to compute.
    adjacent_evidence: str | None = None
    # ADR-069 — the bar facet of a quantified scope requirement (team size,
    # budget): {kind, value, value_max, comparator, quote, level,
    # candidate_values, cited_entry, attested}. ``attested`` (ADR-070) is the
    # code-verified {entry, quote, unit} citation of vault prose bearing on the
    # bar — the only material render_scope_positioning_block delivers to the
    # writers. None on every ordinary concept entry.
    # A bar-carrying entry is EXEMPT from all coverage machinery by predicate
    # (keyword_ledger.is_scope_entry) and its status moves only via the
    # gap-analysis judgement seam or elicited testimony — never via literal
    # corpus presence.
    bar: dict | None = None

    @model_validator(mode="after")
    def _denial_level_only_when_denied(self) -> "KeywordLedgerEntry":
        """M2 finding-fix (2026-07-29): a raising validator on a
        PERSISTED-READ path (this schema validates `GapAnalysis.keyword_ledger`
        rows coming back OUT of the database) means any future write path that
        ever produces this inconsistency takes down every GET of that gap
        analysis with a 500 — no production path does today, but every OTHER
        back-compat concern in this schema degrades instead (`narrative_backed`
        defaults True for a legacy row with no key at all). Match that: drop
        the inconsistent `denial_level` and log a warning so the anomaly is
        observable without taking the endpoint down.
        """
        if self.denial_level is not None and self.status != "denied":
            logger.warning(
                "KeywordLedgerEntry: dropping inconsistent denial_level=%r on "
                "concept=%r — status=%r is not 'denied' (denial_level may only "
                "be set alongside status == 'denied')",
                self.denial_level, self.concept, self.status,
            )
            self.denial_level = None
        return self


class GapAnalysisResponse(BaseModel):
    id: uuid.UUID
    job_analysis_id: uuid.UUID
    profile_id: uuid.UUID
    match_score: float | None = Field(default=None, ge=0.0, le=1.0)
    critical_gaps: list[str]
    minor_gaps: list[str]
    strengths: list[str]
    keyword_gaps: list[str]
    category_a: list[str] = Field(default_factory=list)
    category_b: list[str] = Field(default_factory=list)
    category_c: list[str] = Field(default_factory=list)
    keyword_ledger: list[KeywordLedgerEntry] = Field(default_factory=list)
    gap_clusters: list[GapClusterSchema] = Field(default_factory=list)
    requirement_breakdown: list[RequirementBreakdownItem] = Field(default_factory=list)
    # #260 — derived, never independently settable: every keyword_ledger entry
    # that is a JD hard requirement, claimable (will be echoed by the
    # generator), but carries no narrative evidence anywhere in the vault.
    # Populated by `_derive_keyword_liabilities` below from `keyword_ledger`
    # itself, so it can never drift from the ledger it summarises — one
    # source, read on the SAME response the gaps page and the `analyze_gaps`
    # MCP tool already return (no new endpoint/tool for agent-door parity).
    keyword_liabilities: list[KeywordLedgerEntry] = Field(default_factory=list)
    # ADR-074 (#526) — derived, never independently settable: every JD HARD
    # requirement Applire holds nothing on and never asked the candidate about
    # (not claimable, no evidence, no adjacent capability, no stated limit).
    # Such a requirement has no truthful expression in a cover letter, so it is
    # excluded from generation and told to the CANDIDATE instead — as a fact
    # about US ("you were never asked"), never as a deficiency in them.
    #
    # Scoped per APPLICATION and DERIVED rather than snapshotted onto a
    # generated document: the state belongs to the (job, gap analysis) pair and
    # exists before any document does. Deriving it here means it cannot drift
    # past a post-interview recompute, and it disappears by itself once the
    # candidate is asked and answers. Same #260 `keyword_liabilities` pattern —
    # one source, read on the SAME response the gaps page and the `analyze_gaps`
    # MCP tool already return, so no new endpoint and no new tool (ADR-056 §4).
    unasked_requirements: list[KeywordLedgerEntry] = Field(default_factory=list)
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("keyword_ledger", mode="before")
    @classmethod
    def _ledger_none_to_empty(cls, v):
        # Legacy gap_analyses rows (pre-E037) have NULL keyword_ledger.
        return v or []

    @model_validator(mode="after")
    def _derive_keyword_liabilities(self) -> "GapAnalysisResponse":
        # Local import: services/keyword_ledger.py has no reverse dependency
        # on schemas/gap.py, but keeping the import local mirrors this
        # codebase's established cycle-avoidance convention elsewhere.
        from applire.services.keyword_ledger import keyword_liabilities as _compute
        from applire.services.keyword_ledger import unasked_hard_requirements

        entries = [e.model_dump() for e in self.keyword_ledger]
        self.keyword_liabilities = [
            KeywordLedgerEntry(**e) for e in _compute(entries)
        ]
        # ADR-074 — same derivation discipline, same single source.
        self.unasked_requirements = [
            KeywordLedgerEntry(**e) for e in unasked_hard_requirements(entries)
        ]
        return self


class KeywordLiabilityDowngradeRequest(BaseModel):
    """Body for POST /api/job/{job_id}/gaps/liabilities/downgrade (#260
    exit b) — the candidate's own choice to drop a keyword-liability
    concept rather than tell its story via resolve_gap (exit a)."""

    concept: str


_GAP_JOB_STATUS = Literal["pending", "processing", "ready", "failed", "expired"]


class GapJobResponse(BaseModel):
    """Response for POST /api/job/{job_id}/gap-jobs — the async gap-analysis handle.

    The kick-off returns immediately; the heavy LLM analysis runs in a background task
    (so the gaps screen can't block ~2 min or 504 fragilely). Poll
    GET /api/job/{job_id}/gap-jobs/{gap_job_id} until status is ``ready`` or ``failed``.
    """

    gap_job_id: uuid.UUID
    status: _GAP_JOB_STATUS


class GapJobStatusResponse(BaseModel):
    """Response for GET /api/job/{job_id}/gap-jobs/{gap_job_id} (async gap-analysis poll).

    ``result`` carries the same GapAnalysisResponse the synchronous path returned,
    populated when status == ``ready``. On ``failed``, ``error_code`` is a stable machine
    code the frontend localizes — the raw provider text is never surfaced.
    """

    gap_job_id: uuid.UUID
    status: _GAP_JOB_STATUS
    error_code: str | None = None
    result: GapAnalysisResponse | None = None

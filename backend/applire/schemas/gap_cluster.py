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

from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field, field_validator

#: The keys the CLUSTERING MODEL writes. Everything else on a persisted cluster
#: (``outcome``, ``coverage``) is Applire's record (ADR-089 clause 3) and is
#: never taken from a model response; ``budget_remaining`` is derived at
#: response time and never persisted (ruling C-2, 2026-09-23).
LLM_CLUSTER_KEYS = frozenset({"id", "label", "category", "gaps", "jd_skills", "jd_context"})

CoverageValue = Literal["open", "partly_covered", "covered", "declined"]


class GapClusterOutcome(BaseModel):
    """ADR-089 clause 3 — what the interview doors have done with a cluster,
    as FACTS only: counts, member strings, session ids. Never answer or
    question text (the analysis row is not retention-scoped; transcripts live
    in ``interview_sessions`` and expire there)."""

    asked: int = 0
    covered: list[str] = Field(default_factory=list)
    declined: list[str] = Field(default_factory=list)
    session_ids: list[str] = Field(default_factory=list)


class GapClusterMemberStatus(BaseModel):
    """RULING C-1 — one requirement chip on a gap card and its colour source."""

    member: str
    status: Literal["covered", "partial", "gap", "declined"]


class GapClusterSchema(BaseModel):
    """One gap cluster as it is persisted on ``gap_analyses.gap_clusters``.

    ``category`` is **derived, not solicited** (#675 line 60, prompt
    ``gap_clustering`` v2): it is "C" when any member gap came from the
    analysis's Category C list, else "B" — a fact about which input list the
    member came from, which the caller already holds (ADR-062 clause 1). The
    clustering prompt used to state that rule and the model broke it; the
    default here exists so a model response that omits the field validates,
    and ``services/gap.py::_reconcile_cluster_categories`` then sets it.
    Readers (``interview_graph.plan_clusters``, the gaps screen, the agent
    door) see the same two letters as before.

    ADR-089 clause 3 — ``gaps`` holds the cluster's OPEN members only;
    ``outcome.covered`` / ``outcome.declined`` hold the rest (a reader that
    needs every member goes through ``gap_coverage.all_members``). ``coverage``
    is the derived state the gaps page renders. A legacy row (pre-ADR-089)
    carries neither: ``outcome`` reads empty, and ``coverage`` is ``None`` here
    and derived from the row's own ledger by ``GapAnalysisResponse``.
    ``budget_remaining`` (ruling C-2) is ``gap_coverage.remaining_budget`` of
    this cluster — the same number the session turn reports as
    ``cluster_coverage.budget_remaining``. ``member_statuses`` (ruling C-1) is
    ``gap_coverage.member_statuses`` over every member against the row's own
    ledger, filled by ``GapAnalysisResponse``. Both are derived at response
    time and never persisted.
    """

    id: str
    label: str
    category: Literal["B", "C"] = "B"
    gaps: list[str]
    jd_skills: list[str]
    jd_context: str
    outcome: GapClusterOutcome = Field(default_factory=GapClusterOutcome)
    coverage: CoverageValue | None = None
    member_statuses: list[GapClusterMemberStatus] = Field(default_factory=list)

    @field_validator("outcome", mode="before")
    @classmethod
    def _tolerant_outcome(cls, v: Any) -> Any:
        # A persisted-read path: a malformed record degrades to its normalised
        # reading (the same one every gap_coverage function uses), never a 500.
        from applire.services.gap_coverage import _outcome_of

        return _outcome_of({"outcome": v})

    @field_validator("coverage", mode="before")
    @classmethod
    def _known_coverage_or_none(cls, v: Any) -> Any:
        from applire.services.gap_coverage import COVERAGE_VALUES

        return v if v in COVERAGE_VALUES else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def budget_remaining(self) -> int:
        from applire.services.gap_coverage import remaining_budget

        return remaining_budget({"outcome": self.outcome.model_dump()})

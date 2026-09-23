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

"""The per-gap coverage record (ADR-089) — CONTRACT SKELETON.

This module is the seam between the gap analysis (`services/gap.py`, which
carries clusters and their record from one analysis row to the next) and the
interview doors (`services/session.py`, `mcp/server.py`, which ask questions
and record what each answer covered). Both sides import from here; neither
re-derives any of these facts on its own (ADR-066: one implementation per
capability).

ADR-062 clause 1/6 declaration: every function here computes FACTS — ledger
status lookups, list membership, counts. None interprets prose, none calls a
model.

Shape of one persisted `gap_clusters` entry after ADR-089::

    {
      "id": str, "label": str, "category": "B" | "C",
      "gaps": [str],            # OPEN members only (ledger status gap/partial)
      "jd_skills": [str], "jd_context": str,
      "outcome": {
        "asked": int,           # answered turns on this cluster, across sessions
        "covered": [str],       # members whose ledger row is `direct`
        "declined": [str],      # members that are recorded denials
        "session_ids": [str],   # sessions that asked it (transcripts live there)
      },
      "coverage": "open" | "partly_covered" | "covered" | "declined",
    }

No answer or question text is ever stored here (ADR-089 clause 3: the
analysis row is not TTL-scoped; transcripts expire with `interview_sessions`).

STATUS: signatures and docstrings are the agreed contract (main session,
2026-09-23). Package A implements the bodies; package B calls them. Changing a
signature is a RULING, not a local edit — message the main session.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from applire.constants import INTERVIEW_MAX_QUESTIONS_PER_GAP

MemberFact = Literal["covered", "declined", "open"]
CoverageStatus = Literal["open", "partly_covered", "covered", "declined"]


@dataclass(frozen=True)
class AnswerScope:
    """What an answer-driven recompute may treat as TOUCHED (ADR-089 clause 5).

    ``cluster_ids`` — the clusters this session worked (their members, as
    carried on the previous row, are touched). ``answers`` — every candidate
    answer of this session; any requirement whose surface forms appear in one
    of them is touched too (a self-correction outside the answered cluster).

    ``AnswerScope()`` (both empty) is the `/gaps/refresh` case: answer-driven,
    nothing touched — only a denial or the vault floor may lower a row.
    ``analyze_gaps(..., answer_scope=None)`` is the non-answer path (first
    analysis, JD change): fresh, no merge.
    """

    cluster_ids: tuple[str, ...] = ()
    answers: tuple[str, ...] = ()


def empty_outcome() -> dict[str, Any]:
    """The record of a cluster nobody has asked yet."""
    return {"asked": 0, "covered": [], "declined": [], "session_ids": []}


def all_members(cluster: dict[str, Any]) -> list[str]:
    """Every member of a cluster — open ``gaps`` + ``outcome.covered`` +
    ``outcome.declined``, order-preserving, deduplicated by the ledger
    normaliser.

    The ONE way to read a cluster's full term vocabulary. Readers that need it
    (never ``gaps`` alone — that fails open): ``choice_grounding._cluster_terms``
    and ``constituent_evidence``, ``cv_gap_hints._merge_cluster_duplicates``,
    the frontend ``LiabilityPanel.findOwningCluster`` (mirrors this in TS).
    """
    raise NotImplementedError


def classify_members(
    members: list[str],
    keyword_ledger: list[dict[str, Any]] | None,
    denied_concepts: list[dict[str, Any]] | None,
) -> dict[str, MemberFact]:
    """Per-member fact, in ADR-089 clause 2 precedence:

    1. ``declined`` — the member is a recorded denial (``is_denied_concept``
       over ``denied_concepts``); checked FIRST, so a denial worded with the
       JD's own term never reads as covered.
    2. ``covered`` — a ledger row matching the member (``keyword_ledger._matches``
       over ``concept`` + ``surface_forms``) has ``status == "direct"``.
       When several rows match, ALL must be ``direct`` (the #207 rule
       ``filter_answered_concepts`` already applies).
    3. ``open`` — otherwise (``gap``, ``partial``, or no matching row).

    A member that matches NO ledger row is reported ``open`` here; dropping
    orphans is the recompute's job (``carry_forward_clusters``), not this one's.
    """
    raise NotImplementedError


def member_matches_ledger(member: str, keyword_ledger: list[dict[str, Any]] | None) -> bool:
    """True when at least one ledger row matches ``member`` (used to drop
    orphaned members on carry-forward, ADR-089 clause 4)."""
    raise NotImplementedError


def derive_coverage(cluster: dict[str, Any], keyword_ledger: list[dict[str, Any]] | None) -> CoverageStatus:
    """ADR-089 clause 3, from the members' ledger statuses:

    * ``covered``  — every member ``direct`` or declined, at least one ``direct``;
    * ``declined`` — every member declined;
    * ``partly_covered`` — at least one member ``direct``/``partial``/declined,
      but not all ``direct``/declined (a Category B cluster starts here);
    * ``open`` — every member ``gap`` (or unmatched).
    """
    raise NotImplementedError


def remaining_budget(cluster: dict[str, Any], per_gap: int = INTERVIEW_MAX_QUESTIONS_PER_GAP) -> int:
    """``per_gap - outcome.asked``, floored at 0 (ADR-089 clause 1)."""
    raise NotImplementedError


def is_askable(cluster: dict[str, Any], per_gap: int = INTERVIEW_MAX_QUESTIONS_PER_GAP) -> bool:
    """Budget left AND coverage not ``covered``/``declined``."""
    raise NotImplementedError


def apply_turn_outcome(
    cluster: dict[str, Any],
    member_facts: dict[str, MemberFact],
    *,
    session_id: str,
    keyword_ledger: list[dict[str, Any]] | None,
    charge: bool = True,
) -> dict[str, Any]:
    """PURE. A new cluster dict after one answered turn: ``outcome.asked``
    += 1 when ``charge``; ``covered``/``declined`` updated from
    ``member_facts``; ``gaps`` = the open members; ``session_ids`` gains
    ``session_id`` (deduplicated); ``coverage`` re-derived. Never mutates
    the input (JSONB tracking: callers reassign the whole list)."""
    raise NotImplementedError


def refresh_cluster_from_ledger(
    cluster: dict[str, Any],
    keyword_ledger: list[dict[str, Any]] | None,
    denied_concepts: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """PURE. Re-split a CARRIED cluster's members against a new ledger
    (ADR-089 clause 4): orphans (``member_matches_ledger`` False) dropped;
    the rest re-classified into ``gaps``/``covered``/``declined``;
    ``outcome.asked``/``session_ids`` kept; ``coverage`` re-derived.
    Returns ``None`` when no member survives (the cluster is dropped).
    The B/C ``category`` is re-derived by the caller with the same #675
    line-60 fact ``gap._reconcile_cluster_categories`` uses."""
    raise NotImplementedError


async def record_turn_outcome(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    fallback_gap_analysis_id: uuid.UUID | None,
    cluster_id: str,
    member_facts: dict[str, MemberFact],
    session_id: str,
    charge: bool = True,
) -> dict[str, Any] | None:
    """Persist one answered turn's outcome (ADR-089 clause 3).

    Target row: the job's latest non-deleted ``GapAnalysis`` whose
    ``gap_clusters`` carries ``cluster_id``; else the row
    ``fallback_gap_analysis_id`` (the session's own). Reassigns the WHOLE
    ``gap_clusters`` list (plain JSON column, not a MutableList) and
    FLUSHES — never commits: the caller's turn owns the transaction
    (session.py's one-commit-per-turn rule, #179). Returns the updated
    cluster dict, or ``None`` when no row carries the cluster.
    """
    raise NotImplementedError

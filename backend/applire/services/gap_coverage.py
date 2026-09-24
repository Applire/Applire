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

"""The per-gap coverage record (ADR-089).

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

Two readings of "a member", one instrument each (ADR-066):

* **Which ledger rows speak for a member** — :func:`_rows_for`: the ledger
  builder's own ``_matches`` (exact or bidirectional substring over the
  normalised string) against each row's ``concept`` AND ``surface_forms``.
  Generous on purpose: a wider match set can only VETO ``covered`` (the #207
  all-rows-direct rule) and only KEEP a member on carry-forward, both of which
  are the fail-open directions.
* **Whether the candidate declined it** — the DECLARED branch of the denial
  predicate (``stance.declared_denial_matches``, the same matcher the ledger
  floor uses before it writes ``status="denied"``, #486), plus a row of the
  member's own name that already carries ``status="denied"``. A member that is
  merely CONTAINED in a denied compound (``Produktion`` inside a denied
  ``"direkte Produktion für Lebensmittelkunden"``) is not a recorded denial of
  that member: the ledger floors or releases it, and its row's status decides.

The #260 keyword-liability rule (RULING A-1, 2026-09-23, ADR-089 clause 2 as
amended): a member whose ledger row is a liability — a required, claimable
concept with no story anywhere in the vault (``keyword_ledger.
keyword_liabilities``) — is OPEN, never covered, until a recompute sees its
story (``narrative_backed``). The open question for a liability is the story,
not the claim, and the shipped "tell the story" exits (the gaps page's
LiabilityPanel, the agent guide's ``resolve_gap``) open a session on exactly
that cluster. The rule lives in ONE predicate, :func:`_is_unstoried_liability`.

Signatures and docstrings are the agreed contract (main session, 2026-09-23):
package A implements, package B calls. Changing a signature is a RULING.
"""
from __future__ import annotations

import copy
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from applire.constants import INTERVIEW_MAX_QUESTIONS_PER_GAP
from applire.services.keyword_ledger import (
    _denied_concept_entries,
    _matches,
    _norm,
    keyword_liabilities,
)
from applire.services.profile.reconcile.stance import declared_denial_matches

logger = logging.getLogger(__name__)

MemberFact = Literal["covered", "declined", "open"]
CoverageStatus = Literal["open", "partly_covered", "covered", "declined"]
#: RULING C-1 — the four-way per-member status the gaps page colours each
#: requirement chip by. ``partial`` + ``gap`` together are exactly
#: :data:`MemberFact`'s ``open``.
MemberStatus = Literal["covered", "partial", "gap", "declined"]

#: Every value ``coverage`` may take — a stored value outside this set is
#: ignored and re-derived (a hand-edited or future row never crashes a reader).
COVERAGE_VALUES: frozenset[str] = frozenset({"open", "partly_covered", "covered", "declined"})


@dataclass(frozen=True)
class AnswerScope:
    """What an answer-driven recompute may treat as TOUCHED (ADR-089 clause 5).

    ``cluster_ids`` — the clusters this session worked; their members, as
    carried on the previous row, are the touched set. Nothing else is: an
    answer that merely MENTIONS a requirement outside those clusters does not
    touch it (ruling M-2, 2026-09-23 — the adversarial pass showed an
    incidental "weekly design review" in an observability answer unlocking an
    unrelated, fully backed requirement to classifier noise). A self-correction
    outside the worked clusters still lowers its requirement through the two
    floors: the recorded denial and the vault-evidence floor.

    ``AnswerScope()`` (both empty) is the `/gaps/refresh` case: answer-driven,
    nothing touched — only a denial or the vault floor may lower a row.
    ``analyze_gaps(..., answer_scope=None)`` is the non-answer path (first
    analysis, JD change): fresh, no merge.
    """

    cluster_ids: tuple[str, ...] = ()


def empty_outcome() -> dict[str, Any]:
    """The record of a cluster nobody has asked yet."""
    return {"asked": 0, "covered": [], "declined": [], "session_ids": []}


# ---------------------------------------------------------------------------
# Private readers — tolerant of every legacy / malformed shape
# ---------------------------------------------------------------------------


def _str_list(value: Any) -> list[str]:
    """``value`` as a list of non-blank strings (anything else dropped)."""
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, str) and _norm(v)]


def _outcome_of(cluster: dict[str, Any]) -> dict[str, Any]:
    """The cluster's ``outcome``, normalised to the four keys (a legacy row
    has none; a malformed one is read as empty, never raised on)."""
    raw = cluster.get("outcome") if isinstance(cluster, dict) else None
    if not isinstance(raw, dict):
        return empty_outcome()
    asked = raw.get("asked")
    if isinstance(asked, bool) or not isinstance(asked, int) or asked < 0:
        asked = 0
    return {
        "asked": asked,
        "covered": _str_list(raw.get("covered")),
        "declined": _str_list(raw.get("declined")),
        "session_ids": [s for s in (raw.get("session_ids") or []) if isinstance(s, str) and s],
    }


def _dedupe(members: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in members:
        key = _norm(m)
        if key and key not in seen:
            seen.add(key)
            out.append(m)
    return out


def _row_names(row: dict[str, Any]) -> list[str]:
    names = [row.get("concept", ""), *(row.get("surface_forms") or [])]
    return [n for n in names if isinstance(n, str) and _norm(n)]


def _rows_for(member: str, keyword_ledger: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Every ledger row that speaks for ``member`` — ``_matches`` over the
    row's ``concept`` + ``surface_forms`` (the ledger builder's own matcher)."""
    key = _norm(member)
    if not key:
        return []
    return [
        row
        for row in keyword_ledger or []
        if isinstance(row, dict) and any(_matches(key, _norm(n)) for n in _row_names(row))
    ]


def _own_rows(member: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The rows whose concept or a surface form IS the member (normalised
    equality) — the member's own requirement, as opposed to a broader or
    narrower row that merely contains it."""
    key = _norm(member)
    return [row for row in rows if key in {_norm(n) for n in _row_names(row)}]


def _is_unstoried_liability(row: dict[str, Any]) -> bool:
    """RULING A-1: a #260 keyword liability (required + claimable + no story in
    the vault) is not covered — its open question is the story. THE one
    predicate, reusing the ledger's own liability slice (ADR-066)."""
    return bool(keyword_liabilities([row]))


def _denial_terms(denied_concepts: list[dict[str, Any]] | list[str] | None) -> list[str]:
    return [concept for concept, _level in _denied_concept_entries(denied_concepts)]


def _is_declined(member: str, rows: list[dict[str, Any]], denials: list[str]) -> bool:
    """A recorded denial of THIS member: a persisted denial that declares it
    (the ledger floor's assert-half matcher, #486), or a row of its own name the
    ledger already wrote as ``denied``."""
    if denials and declared_denial_matches(member, denials):
        return True
    own = _own_rows(member, rows)
    return bool(own) and all(row.get("status") == "denied" for row in own)


def _is_anchored(row: dict[str, Any], member: str) -> bool:
    """The row's own CONCEPT is the member or contains it — the member's own
    requirement, or a narrower one (``5+ years Python experience`` for the
    member ``Python``). A row that matches only through a surface form, or
    whose concept is broader, is not anchored on the member."""
    concept = _norm(row.get("concept", ""))
    key = _norm(member)
    return bool(concept) and bool(key) and key in concept


def _deciding_rows(member: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The rows whose status decides whether ``member`` is covered.

    When the ledger has rows anchored on the member (:func:`_is_anchored` —
    its own row, and any narrower requirement that contains it), those decide,
    and the #207 veto holds among them: a narrower non-direct row still vetoes.
    A row that only lists the member among its surface forms does not decide:
    the broad ``SAP`` row (``partial``) listing ``SAP PP`` (delivery run E1),
    or the unrelated ``Event-driven architecture`` row (an unstoried
    liability) listing ``Event streaming`` (adversarial pass E2) — both
    vetoed a member whose own row was ``direct``, 2026-09-23. When nothing is
    anchored on the member (it is matched only through surface forms or a
    broader row), every matching row is all the ledger has to say, so they
    decide."""
    anchored = [row for row in rows if _is_anchored(row, member)]
    return anchored or rows


def _is_covered(member: str, rows: list[dict[str, Any]]) -> bool:
    """Every deciding row ``direct`` (the #207 rule — a narrower non-direct row
    vetoes, a broader one does not, :func:`_deciding_rows`) and none of them an
    unstoried liability (RULING A-1)."""
    deciding = _deciding_rows(member, rows)
    return bool(deciding) and all(
        row.get("status") == "direct" and not _is_unstoried_liability(row) for row in deciding
    )


def _member_status(
    member: str, keyword_ledger: list[dict[str, Any]] | None, denials: list[str]
) -> MemberStatus:
    """THE per-member classification, in ADR-089 clause 2 precedence — the one
    implementation behind :func:`classify_members` (three-way fact) and
    :func:`member_statuses` (four-way chip status, RULING C-1):

    ``declined`` (a recorded denial of this member) > ``covered`` (every
    matching row ``direct``, none an unstoried liability) > ``partial`` (the
    member's rows hold a claimable status — a ``partial``, an unstoried #260
    liability, a ``direct`` a narrower row vetoes) > ``gap`` (``gap``, or no
    matching row)."""
    rows = _rows_for(member, keyword_ledger)
    if _is_declined(member, rows, denials):
        return "declined"
    if _is_covered(member, rows):
        return "covered"
    if _has_claimable_signal(member, rows):
        return "partial"
    return "gap"


def _has_claimable_signal(member: str, rows: list[dict[str, Any]]) -> bool:
    """Does the ledger hold ANY claimable status for this member — its own rows
    when it has some, else every row that speaks for it? This is what separates
    ``partly_covered`` (a likely match, a liability, a partial) from ``open``."""
    signal_rows = _own_rows(member, rows) or rows
    return any(row.get("status") in ("direct", "partial") for row in signal_rows)


# ---------------------------------------------------------------------------
# Public contract
# ---------------------------------------------------------------------------


def all_members(cluster: dict[str, Any]) -> list[str]:
    """Every member of a cluster — open ``gaps`` + ``outcome.covered`` +
    ``outcome.declined``, order-preserving, deduplicated by the ledger
    normaliser.

    The ONE way to read a cluster's full term vocabulary. Readers that need it
    (never ``gaps`` alone — that fails open): ``choice_grounding._cluster_terms``
    and ``constituent_evidence``, ``cv_gap_hints._merge_cluster_duplicates``,
    the frontend ``LiabilityPanel.findOwningCluster`` (mirrors this in TS).
    """
    if not isinstance(cluster, dict):
        return []
    outcome = _outcome_of(cluster)
    return _dedupe(_str_list(cluster.get("gaps")) + outcome["covered"] + outcome["declined"])


def classify_members(
    members: list[str],
    keyword_ledger: list[dict[str, Any]] | None,
    denied_concepts: list[dict[str, Any]] | None,
) -> dict[str, MemberFact]:
    """Per-member fact, in ADR-089 clause 2 precedence:

    1. ``declined`` — the member is a recorded denial; checked FIRST, so a
       denial worded with the JD's own term never reads as covered. "Recorded
       denial" is the DECLARED branch of the denial predicate
       (``stance.declared_denial_matches`` over ``denied_concepts`` — the
       matcher the ledger floor uses before it asserts ``status="denied"``,
       #486), or a ledger row of the member's own name already ``denied``. A
       member only CONTAINED in a denied compound is not declined: its ledger
       row (floored ``gap`` or released) decides, like every other member.
    2. ``covered`` — a ledger row matching the member (``keyword_ledger._matches``
       over ``concept`` + ``surface_forms``) has ``status == "direct"``.
       When several rows match, ALL deciding rows must be ``direct`` (the #207
       rule ``filter_answered_concepts`` already applies — directional: a
       narrower non-direct row vetoes, a strictly broader one does not, see
       :func:`_deciding_rows`) — and none may be an unstoried #260 liability
       (RULING A-1).
    3. ``open`` — otherwise (``gap``, ``partial``, a liability, or no matching row).

    A member that matches NO ledger row is reported ``open`` here; dropping
    orphans is the recompute's job (``carry_forward_clusters``), not this one's.
    """
    denials = _denial_terms(denied_concepts)
    facts: dict[str, MemberFact] = {}
    for member in members or []:
        if not isinstance(member, str) or not _norm(member):
            continue
        status = _member_status(member, keyword_ledger, denials)
        facts[member] = status if status in ("declined", "covered") else "open"
    return facts


def member_statuses(
    cluster: dict[str, Any],
    keyword_ledger: list[dict[str, Any]] | None,
    denied_concepts: list[dict[str, Any]] | list[str] | None = None,
) -> list[dict[str, str]]:
    """RULING C-1 — ``[{"member", "status"}]`` over EVERY member
    (:func:`all_members` order), ``status`` ∈ covered | partial | gap |
    declined, by the same precedence as :func:`classify_members`
    (:func:`_member_status`). A member the record already holds as declined
    (``outcome.declined``) is ``declined`` whatever the ledger row is worded
    as — the record is the fact of a denial. Derived at response time from the
    row's own ledger; never persisted."""
    if not isinstance(cluster, dict):
        return []
    recorded_declined = {_norm(m) for m in _outcome_of(cluster)["declined"]}
    denials = _denial_terms(denied_concepts)
    return [
        {
            "member": member,
            "status": "declined"
            if _norm(member) in recorded_declined
            else _member_status(member, keyword_ledger, denials),
        }
        for member in all_members(cluster)
    ]


def member_matches_ledger(member: str, keyword_ledger: list[dict[str, Any]] | None) -> bool:
    """True when at least one ledger row matches ``member`` (used to drop
    orphaned members on carry-forward, ADR-089 clause 4)."""
    return bool(_rows_for(member, keyword_ledger))


def derive_coverage(cluster: dict[str, Any], keyword_ledger: list[dict[str, Any]] | None) -> CoverageStatus:
    """ADR-089 clause 3, from the members' ledger statuses:

    * ``covered``  — every member ``direct`` or declined, at least one ``direct``;
    * ``declined`` — every member declined;
    * ``partly_covered`` — at least one member ``direct``/``partial``/declined,
      but not all ``direct``/declined (a Category B cluster starts here);
    * ``open`` — every member ``gap`` (or unmatched).

    The cluster's own split is authoritative for WHICH members are covered or
    declined (``outcome.covered`` / ``outcome.declined`` — written from the
    same ledger by :func:`apply_turn_outcome` / :func:`refresh_cluster_from_ledger`),
    so ``coverage`` can never contradict ``gaps``: a cluster with an open member
    is never ``covered``. The ledger answers the one remaining question — does
    an OPEN member hold any claimable status (a ``partial``, a liability, a
    ``direct`` a narrower row vetoes) — which is what makes it
    ``partly_covered`` rather than ``open``. A cluster with no member at all is
    ``open``.
    """
    if not isinstance(cluster, dict):
        return "open"
    outcome = _outcome_of(cluster)
    declined = {_norm(m) for m in outcome["declined"]}
    covered = {_norm(m) for m in outcome["covered"]}
    states: list[str] = []
    for member in all_members(cluster):
        key = _norm(member)
        if key in declined:
            states.append("declined")
        elif key in covered:
            states.append("direct")
        elif _has_claimable_signal(member, _rows_for(member, keyword_ledger)):
            states.append("partial")
        else:
            states.append("gap")
    if not states:
        return "open"
    if all(s == "declined" for s in states):
        return "declined"
    if all(s in ("direct", "declined") for s in states):
        return "covered"
    if any(s in ("direct", "partial", "declined") for s in states):
        return "partly_covered"
    return "open"


def remaining_budget(cluster: dict[str, Any], per_gap: int = INTERVIEW_MAX_QUESTIONS_PER_GAP) -> int:
    """``per_gap - outcome.asked``, floored at 0 (ADR-089 clause 1)."""
    return max(0, int(per_gap) - _outcome_of(cluster)["asked"])


def stored_or_derived_coverage(
    cluster: dict[str, Any], keyword_ledger: list[dict[str, Any]] | None = None
) -> CoverageStatus:
    """The cluster's persisted ``coverage`` when it is a known value, else
    :func:`derive_coverage` over ``keyword_ledger`` (a legacy row carries none)."""
    stored = cluster.get("coverage") if isinstance(cluster, dict) else None
    if isinstance(stored, str) and stored in COVERAGE_VALUES:
        return stored  # type: ignore[return-value]
    return derive_coverage(cluster, keyword_ledger)


def is_askable(cluster: dict[str, Any], per_gap: int = INTERVIEW_MAX_QUESTIONS_PER_GAP) -> bool:
    """Budget left AND coverage not ``covered``/``declined``.

    A cluster with no open member left is never askable either (there is
    nothing to ask about) — a well-formed record reads ``covered``/``declined``
    in that case anyway; this guards a malformed one.
    """
    if not isinstance(cluster, dict):
        return False
    if remaining_budget(cluster, per_gap) <= 0:
        return False
    if stored_or_derived_coverage(cluster) in ("covered", "declined"):
        return False
    return bool(_str_list(cluster.get("gaps")))


def _with_split(
    cluster: dict[str, Any],
    facts_in_order: list[tuple[str, str]],
    outcome: dict[str, Any],
    keyword_ledger: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """A NEW cluster dict: ``gaps``/``outcome.covered``/``outcome.declined``
    from ``facts_in_order``, ``coverage`` re-derived from the result."""
    new = copy.deepcopy(cluster)
    new["gaps"] = [m for m, f in facts_in_order if f == "open"]
    new["outcome"] = {
        **outcome,
        "covered": [m for m, f in facts_in_order if f == "covered"],
        "declined": [m for m, f in facts_in_order if f == "declined"],
    }
    new["coverage"] = derive_coverage(new, keyword_ledger)
    return new


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
    the input (JSONB tracking: callers reassign the whole list).

    A member ``member_facts`` does not name keeps its previous split (B
    classifies the OPEN members; a covered or declined member stays so until
    the next recompute re-splits the whole cluster). Facts are matched to
    members by the ledger normaliser; a fact about a string that is not a
    member of this cluster is ignored — a turn never adds a member.
    """
    outcome = _outcome_of(cluster)
    facts_by_key = {
        _norm(k): v
        for k, v in (member_facts or {}).items()
        if isinstance(k, str) and v in ("covered", "declined", "open")
    }
    previous_declined = {_norm(m) for m in outcome["declined"]}
    previous_covered = {_norm(m) for m in outcome["covered"]}
    ordered: list[tuple[str, str]] = []
    for member in all_members(cluster):
        key = _norm(member)
        fact = facts_by_key.get(key)
        if fact is None:
            if key in previous_declined:
                fact = "declined"
            elif key in previous_covered:
                fact = "covered"
            else:
                fact = "open"
        ordered.append((member, fact))
    if charge:
        outcome["asked"] = outcome["asked"] + 1
    if isinstance(session_id, str) and session_id and session_id not in outcome["session_ids"]:
        outcome["session_ids"] = [*outcome["session_ids"], session_id]
    return _with_split(cluster, ordered, outcome, keyword_ledger)


def _split_against_ledger(
    cluster: dict[str, Any],
    keyword_ledger: list[dict[str, Any]] | None,
    denied_concepts: list[dict[str, Any]] | None,
    *,
    drop_orphans: bool,
) -> dict[str, Any] | None:
    members = all_members(cluster)
    if drop_orphans and keyword_ledger:
        # An EMPTY ledger is no evidence about any member (fail-open): orphan
        # dropping needs a ledger to be orphaned from.
        surviving = [m for m in members if member_matches_ledger(m, keyword_ledger)]
        for dropped in [m for m in members if m not in surviving]:
            logger.info(
                "gap_coverage: dropped carried member %r from cluster %r — it "
                "matches no row of the new ledger (ADR-089 clause 4)",
                dropped,
                cluster.get("id"),
            )
        members = surviving
    if not members:
        logger.info(
            "gap_coverage: dropped carried cluster %r — no member survives the "
            "new ledger (ADR-089 clause 4)",
            cluster.get("id"),
        )
        return None
    facts = classify_members(members, keyword_ledger, denied_concepts)
    ordered = [(m, facts.get(m, "open")) for m in members]
    return _with_split(cluster, ordered, _outcome_of(cluster), keyword_ledger)


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
    line-60 fact ``gap._reconcile_cluster_categories`` uses.

    An empty/absent ledger drops nothing (no ledger is no evidence).
    """
    if not isinstance(cluster, dict):
        return None
    return _split_against_ledger(cluster, keyword_ledger, denied_concepts, drop_orphans=True)


def resplit_cluster(
    cluster: dict[str, Any],
    keyword_ledger: list[dict[str, Any]] | None,
    denied_concepts: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """PURE. Re-split a cluster against an IN-PLACE ledger change on its own
    row (e.g. the #260 exit-b liability downgrade): members kept (no orphan
    drop — the ledger's rows did not change identity), ``outcome.asked`` /
    ``session_ids`` kept, the split and ``coverage`` re-derived."""
    split = _split_against_ledger(cluster, keyword_ledger, denied_concepts, drop_orphans=False)
    return split if split is not None else copy.deepcopy(cluster)


def initialise_cluster_record(
    cluster: dict[str, Any],
    keyword_ledger: list[dict[str, Any]] | None,
    denied_concepts: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """PURE. A FRESHLY clustered cluster (first analysis, JD change, or an
    appended cluster on carry-forward) with its record initialised: empty
    ``outcome``, members split against this analysis's own ledger so ``gaps``
    holds only open members (ADR-089 clause 3), ``coverage`` derived. Never
    drops a member — a fresh cluster's membership is the clustering's call.
    """
    fresh = {**cluster, "outcome": empty_outcome()}
    split = _split_against_ledger(fresh, keyword_ledger, denied_concepts, drop_orphans=False)
    if split is None:  # a memberless cluster: keep it as the model sent it
        return {**fresh, "gaps": [], "coverage": "open"}
    return split


def cluster_by_id(clusters: Any, cluster_id: str) -> dict[str, Any] | None:
    """The first cluster in a persisted ``gap_clusters`` list with this id."""
    for c in clusters or []:
        if isinstance(c, dict) and c.get("id") == cluster_id:
            return c
    return None


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

    The coverage is derived against the TARGET row's own ledger.
    """
    from applire.models.gap import GapAnalysis  # local: models import nothing from here

    result = await db.execute(
        select(GapAnalysis)
        .where(
            GapAnalysis.job_analysis_id == job_id,
            GapAnalysis.deleted_at.is_(None),
        )
        .order_by(desc(GapAnalysis.created_at))
    )
    target = None
    for row in result.scalars():
        if cluster_by_id(row.gap_clusters, cluster_id) is not None:
            target = row
            break
    if target is None and fallback_gap_analysis_id is not None:
        fallback = await db.get(GapAnalysis, fallback_gap_analysis_id)
        if (
            fallback is not None
            and fallback.deleted_at is None
            and cluster_by_id(fallback.gap_clusters, cluster_id) is not None
        ):
            target = fallback
    if target is None:
        logger.warning(
            "record_turn_outcome: no gap analysis of job %s carries cluster %r — "
            "the turn's outcome is not recorded",
            job_id,
            cluster_id,
        )
        return None

    updated: dict[str, Any] | None = None
    new_clusters: list[Any] = []
    for c in target.gap_clusters or []:
        if updated is None and isinstance(c, dict) and c.get("id") == cluster_id:
            updated = apply_turn_outcome(
                c,
                member_facts,
                session_id=session_id,
                keyword_ledger=target.keyword_ledger,
                charge=charge,
            )
            new_clusters.append(updated)
        else:
            new_clusters.append(c)
    # JSONB tracking gotcha: gap_clusters is a plain _JSON column — only a NEW
    # list object marks the attribute dirty.
    target.gap_clusters = new_clusters
    await db.flush()
    return updated

# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The interview turn's coverage re-judge (ADR-100 cl. 9 amended 2026-11-04;
ADR-089 cl. 2 amended; WP4, Interview #787).

**Why.** ``cluster_coverage.open_concepts`` is the cluster's members that are
not covered (:func:`gap_coverage.classify_members`): covered needs the member's
deciding ledger rows ``direct`` and not a #260 liability. Between an answer and
the completion recompute, the only thing that moved a row was the #188 seam
(``session._upgrade_ledger_for_addressed_gap``): it lifts only a ``gap`` row the
answer names LITERALLY and never moves a claimable ``partial``. The 11-04 E2E
tier (German vault, English posting): the record kept LIMS / ERP / API strategy
/ Integration governance open after the answer that stated them, and MES open
after "Kopplung des Fertigungsleitsystems …" — so the next question re-asked
what had just been answered.

**What.** After an answered turn that changed the vault, ONE call of the gap
classifier — the gap analysis's own ``SYSTEM_PROMPT`` + ``build_user_prompt``
(ADR-066: one judge for "does the vault show R"), with the requirement lists AND
the job-analysis block narrowed to the open members' deciding rows, each in its
own list, and no scope bars (they have their own floor). Code then applies only
what the Fact admits (ADR-100 cl. 1/3):

* a verdict for a row that was asked, ranking ABOVE the row's status
  (gap < partial < direct) — never lowered here (downward moves stay with the
  completion recompute and the denial floors, ADR-089 cl. 5);
* its ``evidence_quote`` resolves in one claimable vault unit
  (``keyword_ledger._verified_backing_path`` = ``quote_in_vault`` + the floor's
  denial and aim stances) **that an answer of THIS session wrote** — the unit
  carries an enrichment receipt (``build_vault_index``'s ``receipt_ids``) of an
  ``interview`` / ``agent_interview`` record with this ``source_session_id``.
  The re-judge exists to read the answers; a lift resting on text the vault held
  before the session is a re-roll of the analysis's own verdict, not news (the
  churn ADR-089 cl. 2 was written against). Measured, the control replay on the
  pre-answer 11-04 vault: claude-sonnet-5-5 lifted ERP / API strategy /
  Integration governance to ``direct`` on ``technologies`` and
  ``professional_summary`` units the luna analysis had graded ``partial`` (2/2
  runs); luna lifted nothing (0/2);
* the ledger builder's own denial floor ran on the fresh row (a denied verdict
  ranks 0), and a member the record already reads ``declined`` is no target.

The lifted row keeps its identity (concept, score slot, JD phrase) and takes the
verdict's claim (status, evidence, backing with ``unit_path``); an adjacency note
that belonged to a ``partial`` is dropped on a lift to ``direct``. A failed call
changes nothing — the member stays open, as before the amendment.

ADR-062 cl. 1: the judgement ("does this span show R") is the classifier's; this
module computes facts only (status ranks, list membership, a verbatim-span check).
"""
from __future__ import annotations

import copy
import logging
from typing import Any

from applire.services import gap_coverage

logger = logging.getLogger(__name__)

#: The prompt-context site — the gap analysis's own (same prompt, same arm).
SITE = "gap_analysis:writer"

_RANK = {"direct": 2, "partial": 1}

#: The ledger lists, strongest source first: a row named by several lists is
#: re-judged under the strongest (the rules differ per list).
_LISTS = (("required", "required_skills"), ("nice_to_have", "nice_to_have_skills"), ("keyword", "keywords"))


def _session_answer_record_ids(profile_json: dict[str, Any], session_id: str) -> set[str]:
    """Ids of the enrichment records this interview session's answers wrote."""
    history = ((profile_json or {}).get("metadata") or {}).get("enrichment_history") or []
    return {
        str(rec.get("id"))
        for rec in history
        if isinstance(rec, dict)
        and rec.get("id")
        and rec.get("source") in ("interview", "agent_interview")
        and session_id
        and str(rec.get("source_session_id") or "") == str(session_id)
    }


def _rank(status: Any) -> int:
    return _RANK.get(status, 0) if isinstance(status, str) else 0


def _targets(
    ledger: list[dict[str, Any]], members: list[str], denied_records: list[dict[str, Any]]
) -> list[int]:
    """Indices of the rows to re-judge: the deciding rows of every member the
    record reads ``open``, that are not already ``direct`` and not denied."""
    from applire.services.keyword_ledger import is_denial_floored

    facts = gap_coverage.classify_members(members, ledger, denied_records)
    picked: list[int] = []
    for member, fact in facts.items():
        if fact != "open":
            continue
        rows = gap_coverage._rows_for(member, ledger)
        for row in gap_coverage._deciding_rows(member, rows):
            if row.get("status") in ("denied", "direct") or is_denial_floored(row):
                continue
            idx = next(i for i, r in enumerate(ledger) if r is row)
            if idx not in picked:
                picked.append(idx)
    return picked


def _narrowed_job(job_inputs: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    job = copy.deepcopy(job_inputs)
    lists: dict[str, list[str]] = {key: [] for _src, key in _LISTS}
    for row in rows:
        sources = row.get("sources") or []
        key = next((k for src, k in _LISTS if src in sources), "required_skills")
        lists[key].append(row.get("concept", ""))
    job.update(lists)
    job["scope_requirements"] = []
    return job


def _lifted_row(existing: dict[str, Any], fresh: dict[str, Any], unit_path: str) -> dict[str, Any]:
    out = copy.deepcopy(existing)
    out["status"] = fresh["status"]
    out["claimable"] = True
    out["evidence"] = fresh.get("evidence", "")
    out["backing"] = {"quote": fresh["backing"]["quote"], "unit_path": unit_path, "instrument": "quote"}
    out.pop("unbacked", None)
    if fresh.get("adjacent_evidence") and fresh["status"] == "partial":
        out["adjacent_evidence"] = fresh["adjacent_evidence"]
    else:
        out.pop("adjacent_evidence", None)
    forms = [*(existing.get("surface_forms") or []), *(fresh.get("surface_forms") or [])]
    out["surface_forms"] = list(dict.fromkeys(f for f in forms if isinstance(f, str) and f.strip())) or [
        out.get("concept", "")
    ]
    return out


async def rejudge_open_members(
    ledger: list[dict[str, Any]],
    members: list[str],
    *,
    job_inputs: dict[str, Any],
    profile_json: dict[str, Any],
    provider: Any,
    session_id: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Re-judge ``members`` (one cluster's) against the post-turn vault.

    Returns ``(ledger, lifted_concepts)`` — a NEW list (the input is not
    mutated); unchanged and ``[]`` when nothing was open, the call failed, or no
    verdict passed the Fact."""
    from applire.prompts import preamble as _prompt_context
    from applire.constants import GAP_ANALYSIS_MAX_TOKENS
    from applire.prompts.gap_analysis import SYSTEM_PROMPT, build_user_prompt
    from applire.services.gap_inference import pre_classify
    from applire.services.keyword_ledger import (
        _annotate_narrative_backed,
        _denied_concept_entries,
        _verified_backing_path,
        build_keyword_ledger,
    )
    from applire.services.profile.reconcile.stance import denial_release_corpus, exclude_unconfirmed
    from applire.services.prompt_view import aim_and_other_lines

    rows = [r for r in ledger or [] if isinstance(r, dict)]
    denied_records = [
        d
        for d in ((profile_json or {}).get("metadata") or {}).get("denied_concepts") or []
        if isinstance(d, dict) and d.get("concept")
    ]
    target_idx = _targets(rows, [m for m in members if m], denied_records)
    if not target_idx:
        return list(ledger or []), []
    targets = [rows[i] for i in target_idx]

    job = _narrowed_job(job_inputs, targets)
    claimable_vault = exclude_unconfirmed(profile_json)
    try:
        pre = pre_classify(job, profile_json)
        prompt = build_user_prompt(job, claimable_vault, pre, None)
        data = await _prompt_context.aparse_json(
            provider, SITE, prompt,
            system=SYSTEM_PROMPT,
            temperature=0.1,
            max_tokens=GAP_ANALYSIS_MAX_TOKENS,
        )
        from applire.services.gap import ledger_input_from_classification

        classifications = (data or {}).get("classifications") or []
        fresh_ledger = build_keyword_ledger(
            [ledger_input_from_classification(c) for c in classifications if isinstance(c, dict)],
            list(job["required_skills"]),
            list(job["nice_to_have_skills"]),
            list(job["keywords"]),
            denied_concepts=denied_records,
            profile_json=profile_json,
        )
        from applire.services.oracle.matchers.vault import build_vault_index

        import types

        answered = _session_answer_record_ids(profile_json, session_id)
        full_index = build_vault_index(claimable_vault)
        index = types.SimpleNamespace(
            units=[u for u in full_index.units if answered.intersection(getattr(u, "receipt_ids", None) or ())]
        )
    except Exception as exc:  # noqa: BLE001 — fail closed on claimability: nothing moves
        logger.warning("interview coverage re-judge failed (%s: %s) — no row lifted", type(exc).__name__, exc)
        return list(ledger or []), []

    denials = [c for c, _lvl in _denied_concept_entries(denied_records)]
    corpus = denial_release_corpus(profile_json) or None
    aims = aim_and_other_lines(profile_json)[0]
    fresh_by_concept = {
        gap_coverage._norm(r.get("concept", "")): r for r in fresh_ledger if isinstance(r, dict)
    }

    out = list(ledger or [])
    lifted: list[str] = []
    for i in target_idx:
        existing = rows[i]
        fresh = fresh_by_concept.get(gap_coverage._norm(existing.get("concept", "")))
        if not fresh or not fresh.get("claimable") or _rank(fresh.get("status")) <= _rank(existing.get("status")):
            continue
        unit_path = _verified_backing_path(fresh, index, denials=denials, corpus=corpus, aims=aims)
        if unit_path is None:
            continue
        pos = next(j for j, r in enumerate(out) if r is existing)
        out[pos] = _annotate_narrative_backed([_lifted_row(existing, fresh, unit_path)], profile_json)[0]
        lifted.append(existing.get("concept", ""))
    if lifted:
        logger.info("interview coverage re-judge lifted %s", lifted)
    return out, lifted

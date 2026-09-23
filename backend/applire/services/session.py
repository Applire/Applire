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

"""
Session service — orchestrates the interview state machine across REST calls.

Turn 1  (POST /api/session):
    resolve mode (auto-detect or override) → check for existing active session (idempotent)
    → [lazy gap analysis for MODE A] → GapDetector → QuestionGenerator → persist state

Turn N  (POST /api/session/{id}/message):
    load state → done-signal check (pre-LLM) → hard-ceiling check
    → ResponseParser → ProfileUpdater → persist profile
    → if gaps remain AND under ceiling: QuestionGenerator → next question
    → else: mark complete with reason

GET /api/session/{id}:
    load session → return SessionStateResponse for agent recovery / pause-resume
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from applire.config import settings
from applire.constants import (
    INTERVIEW_MAX_QUESTIONS_PER_GAP,
    INTERVIEW_SESSION_TTL_DAYS as _SESSION_TTL_DAYS,
    INTERVIEW_TARGET_MIN_GUIDED,
    INTERVIEW_TARGET_MIN_TARGETED,
    MODE_B_COMPLETENESS_THRESHOLD,
)
from applire.models.gap import GapAnalysis
from applire.models.job import JobAnalysis
from applire.utils.language_detection import resolve_jd_language
from applire.models.profile import MasterProfile
from applire.models.session import InterviewSession
from applire.models.user_settings import UserSettings
from applire.services.color_detection import _CE_STUB_USER_ID
from applire.providers.llm.base import LLMProvider
# #480 PR 7 — `EnrichmentRecord`, `ProfileMetadata` and `record_denials` are no
# longer imported here: the interview's last three hand-rolled vault writes (the
# skill confirmation, the probe flag, the denial escalation) are typed acts
# through `commit_ops` now, and the trail is invariant 3's, not this module's.
from applire.schemas.profile import MasterProfileData, render_localized_confirmation
from applire.schemas.session import (
    ClusterCoverage,
    ConfirmationPrompt,
    ConflictSummary,
    InterviewState,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionMessageResponse,
    SessionStateResponse,
)
from applire.services import gap_coverage
from applire.services.ats_audit import _norm as ats_norm
from applire.services.ats_audit import surface_present
from applire.services.gap import analyze_gaps, has_clustering_input
from applire.services.gap_coverage import AnswerScope
from applire.services.interview.budget import derive_hard_ceiling
from applire.services.interview.signals import is_termination_signal
from applire.services.profile.reconcile.stance import (
    denial_release_corpus,
    is_denied_concept,
)
from applire.services.interview.sufficiency import (
    _concept_matches_ledger_key,
    concept_is_required,
    is_interview_sufficient,
)
from applire.services.interview_quant import should_ask_availability
from applire.services.keyword_ledger import (
    assert_claimable_backed,
    reevaluate_gap_ledger_against_vault,
    upgrade_ledger_for_concepts,
)
from applire.services.interview_graph import (
    build_confirmation_clusters,
    build_conflict_clusters,
    build_gate_clusters,
    filter_answered_concepts,
    gap_detector,
    gap_detector_mode_b,
    gap_display_label,
    interpret_conflict_answer,
    interpret_gate_answer,
    is_confirmation_cluster,
    is_conflict_cluster,
    is_gate_cluster,
    question_generator_with_profile,
)
from applire.services.profile.reconcile.interview_bridge import (
    reconcile_interview_turn,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# UI language resolver
# ---------------------------------------------------------------------------


async def get_ui_language(db: AsyncSession) -> str:
    """Resolve the user's UI language for conversational LLM output (ADR-038).

    Reads the CE stub user's settings; returns 'en' when no explicit choice
    exists (``ui_language`` is nullable since the 2026-08-01 amendment — NULL
    means the user never chose). Single seam for the future multi-user (OIDC)
    lookup. Job-scoped conversation should use ``get_conversation_language``
    instead, which adds the JD-language fallback.
    """
    result = await db.execute(
        select(UserSettings).where(UserSettings.user_id == _CE_STUB_USER_ID)
    )
    row = result.scalar_one_or_none()
    return (row.ui_language if row else None) or "en"


async def get_conversation_language(
    db: AsyncSession, job_id: uuid.UUID | str | None = None
) -> str:
    """Resolve the language for job-scoped conversational output.

    ADR-038 amendment 2026-08-01 (#400/#313): an explicitly chosen
    ``ui_language`` always wins; without one (no settings row, or a row
    auto-created by an unrelated settings write — ``ui_language`` NULL), a
    job-scoped conversation follows the language the JD is written in, the
    language the user is demonstrably operating in on a headless/agent-channel
    journey. 'en' remains the last-resort default only when there is no job
    to route on.
    """
    result = await db.execute(
        select(UserSettings).where(UserSettings.user_id == _CE_STUB_USER_ID)
    )
    row = result.scalar_one_or_none()
    if row is not None and row.ui_language:
        return row.ui_language
    if isinstance(job_id, str):
        # Interview state JSONB stores job_id as a string.
        try:
            job_id = uuid.UUID(job_id)
        except ValueError:
            job_id = None
    if job_id is not None:
        job_result = await db.execute(
            select(JobAnalysis).where(JobAnalysis.id == job_id)
        )
        job = job_result.scalar_one_or_none()
        if job is not None:
            return resolve_jd_language(job)
    return "en"


# ---------------------------------------------------------------------------
# US163 — deferred integrity gate injection (ADR-041 amended)
# ---------------------------------------------------------------------------


def _account_name(profile_record: MasterProfile | None) -> str | None:
    if profile_record is None:
        return None
    return ((profile_record.profile_json or {}).get("personal_info") or {}).get("name")


async def _pending_gate_clusters(
    db: AsyncSession, lang: str, account_name: str | None
) -> tuple[list[str], dict, dict]:
    """Build gate-first pseudo-clusters for every open parked gate (US167).

    Returns the GapDetector-shaped ``(ids, categories, clusters_by_id)`` so the
    caller can prepend the gate ids ahead of the JD gaps, mandatory and
    job-irrelevant. Empty when nothing is parked — the no-gate path is unchanged.
    """
    from applire.services.profile import list_open_gates  # lazy: avoid import cycle

    records = await list_open_gates(db)
    if not records:
        return [], {}, {}
    inputs = [
        {
            "upload_id": r.id,
            "gate": r.gate_status,
            "account_name": account_name,
            "cv_name": ((r.staged_extraction or {}).get("personal_info") or {}).get("name"),
        }
        for r in records
    ]
    return build_gate_clusters(inputs, lang)


def _gate_entry(state: InterviewState, cluster_id: str) -> dict | None:
    """The stored gate descriptor for a critical-gaps entry, or None if not a gate."""
    if not is_gate_cluster(cluster_id):
        return None
    return (state.get("gate_clusters") or {}).get(cluster_id)


async def _resolve_gate(
    db: AsyncSession, upload_id: str, action: str, provider: LLMProvider
) -> None:
    """Apply the user's gate decision, tolerating an already-resolved upload.

    The merge reconciles the parked extraction into the master profile via the
    ADR-046 engine, so the interview's provider is threaded through (rather than
    the global factory) — that keeps the merge on the same LLM the session uses
    and makes the path injectable in tests.
    """
    from applire.services.profile import (  # lazy: avoid import cycle
        StagedExtractionAlreadyResolved,
        StagedExtractionNotFound,
        resolve_staged_extraction,
    )

    try:
        await resolve_staged_extraction(
            db, uuid.UUID(upload_id), action=action, provider=provider
        )
    except (StagedExtractionAlreadyResolved, StagedExtractionNotFound):
        # Idempotent on resume / TTL eviction — the gate is no longer open, so
        # there is nothing to apply; advancing past it is the correct behaviour.
        pass


async def _ask_or_complete_at(
    record: InterviewSession,
    state: InterviewState,
    db: AsyncSession,
    provider: LLMProvider,
    next_index: int,
    lang: str,
) -> SessionMessageResponse:
    """Position the session at ``next_index`` and emit its question (gate-aware)
    or complete when no gaps remain. Used after a gate is resolved."""
    skipped = set(state.get("skipped_gaps", []))
    next_index = _next_valid_index(state["critical_gaps"], next_index, skipped)
    state["current_gap_index"] = next_index
    gaps_remaining = _count_remaining(state["critical_gaps"], next_index, skipped)

    profile_record = await _load_profile(state["profile_id"], db)
    if gaps_remaining <= 0:
        return await _complete_session(record, state, db, "gaps_resolved", provider, profile_record)

    next_gap = state["critical_gaps"][next_index]
    gate_entry = _gate_entry(state, next_gap)
    conflict_entry = _conflict_entry(state, next_gap)
    confirmation_entry = _confirmation_entry(state, next_gap)
    if gate_entry is not None:
        next_question = gate_entry["question"]
        next_choices = gate_entry["choices"]
    elif conflict_entry is not None:
        next_question = conflict_entry["question"]
        next_choices = conflict_entry["choices"]
    elif confirmation_entry is not None:
        next_question = confirmation_entry["question"]
        next_choices = confirmation_entry["choices"]
    else:
        next_category = (state.get("gap_categories") or {}).get(next_gap)
        job_context = (
            await _load_job_context(state["job_id"], db)
            if state.get("mode") == "guided"
            else None
        )
        q_data = await _cluster_question(
            state, profile_record.profile_json, provider, db,
            session_id=str(record.id),
            gap_category=next_category, job_context=job_context, lang=lang,
        )
        next_question = q_data["question"]
        next_choices = q_data["choices"]

    state["current_question"] = next_question
    state["current_choices"] = next_choices
    state["messages"].append({"role": "assistant", "content": next_question})
    record.state = state
    record.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return SessionMessageResponse(
        complete=False,
        question=next_question,
        gaps_remaining=gaps_remaining,
        choices=next_choices,
        current_gap_id=_current_gap_id(state),
        addressed_gap_ids=list(state.get("addressed_gaps", [])),
    )


async def _handle_gate_answer(
    record: InterviewSession,
    state: InterviewState,
    db: AsyncSession,
    provider: LLMProvider,
    current_idx: int,
    gate_entry: dict,
    message: str,
) -> SessionMessageResponse:
    """Resolve a deferred Tier-1 gate from the user's answer (US163).

    A clear yes/no merges or discards the parked upload and advances; an unclear
    answer re-asks the same blocking question (safe default: never auto-merge).
    """
    decision = interpret_gate_answer(message)

    if decision == "unclear":
        question = gate_entry["question"]
        choices = gate_entry["choices"]
        state["current_question"] = question
        state["current_choices"] = choices
        state["messages"].append({"role": "assistant", "content": question})
        record.state = state
        record.updated_at = datetime.now(timezone.utc)
        await db.commit()
        gaps_remaining = _count_remaining(
            state["critical_gaps"], current_idx, set(state.get("skipped_gaps", []))
        )
        return SessionMessageResponse(
            complete=False, question=question, gaps_remaining=gaps_remaining, choices=choices,
            current_gap_id=_current_gap_id(state),
            addressed_gap_ids=list(state.get("addressed_gaps", [])),
        )

    action = "merge" if decision == "merge" else "discard"
    await _resolve_gate(db, gate_entry["upload_id"], action, provider)

    current_gap = state["critical_gaps"][current_idx]
    state["addressed_gaps"] = state.get("addressed_gaps", []) + [current_gap]
    state["questions_asked"] = state.get("questions_asked", 0) + 1
    record.questions_asked = state["questions_asked"]

    lang = await get_conversation_language(db, job_id=state.get("job_id"))
    return await _ask_or_complete_at(record, state, db, provider, current_idx + 1, lang)


# ---------------------------------------------------------------------------
# US165 — standalone profile-review interview (no JD): conflict resolution
# ---------------------------------------------------------------------------


def _conflict_entry(state: InterviewState, cluster_id: str) -> dict | None:
    """The stored conflict descriptor for a critical-gaps entry, or None."""
    if not is_conflict_cluster(cluster_id):
        return None
    return (state.get("conflict_clusters") or {}).get(cluster_id)


def _confirmation_entry(state: InterviewState, cluster_id: str) -> dict | None:
    """The stored confirmation descriptor for a critical-gaps entry, or None.

    E037 PQ #4 — an N-option import ambiguity surfaced in the profile-review
    interview (question + per-option buttons), distinct from a 2-value conflict.
    """
    if not is_confirmation_cluster(cluster_id):
        return None
    return (state.get("confirmation_clusters") or {}).get(cluster_id)


async def _resolve_confirmation_safely(
    db: AsyncSession, confirmation_id: str, chosen_option: str
) -> None:
    """Record the user's confirmation choice, tolerating an already-resolved one."""
    from applire.services.profile import resolve_confirmation  # lazy: avoid cycle

    try:
        await resolve_confirmation(confirmation_id, chosen_option, db)
    except LookupError:
        # Idempotent on resume: the confirmation is already gone from pending, so
        # advancing past it is correct.
        pass


async def _handle_confirmation_answer(
    record: InterviewSession,
    state: InterviewState,
    db: AsyncSession,
    provider: LLMProvider,
    current_idx: int,
    confirmation_entry: dict,
    message: str,
) -> SessionMessageResponse:
    """Resolve a pending import-time confirmation from the user's choice (E037 PQ #4).

    Deterministic (no LLM): the user picks one of the engine's options. Any
    non-empty answer records the choice and advances; an empty answer re-asks the
    same question + options (never guess an identity judgement)."""
    options = confirmation_entry.get("options") or confirmation_entry.get("choices") or []
    chosen = (message or "").strip()

    if not chosen:
        question = confirmation_entry["question"]
        state["current_question"] = question
        state["current_choices"] = options
        state["messages"].append({"role": "assistant", "content": question})
        record.state = state
        record.updated_at = datetime.now(timezone.utc)
        await db.commit()
        gaps_remaining = _count_remaining(
            state["critical_gaps"], current_idx, set(state.get("skipped_gaps", []))
        )
        return SessionMessageResponse(
            complete=False, question=question, gaps_remaining=gaps_remaining, choices=options,
            current_gap_id=_current_gap_id(state),
            addressed_gap_ids=list(state.get("addressed_gaps", [])),
        )

    # Adversarial finding (2026-09-09, WP-adv-vault) — founder ruling V-5's
    # resolution turn was wired into `_handle_interview_confirmation_answer`
    # only (a confirmation raised and answered WITHIN the same interview
    # turn). This handler is the OTHER, and more common, resolution route: the
    # standalone profile-review interview (US165) that #686's "Decide now" CTA
    # opens, which is where an engagement confirmation raised by
    # `submit_testimony`, `submit_claims` or a CV import is actually answered
    # — those doors never resolve their own asks in-session. Before this fix,
    # `_resolve_confirmation_safely` below (`profile.resolve_confirmation`) was
    # the ONLY thing that ran: it marks the park resolved and receipts the
    # candidate's raw text, but it is bookkeeping by design
    # (`_apply_resolve_confirmation`'s own docstring) — it never rebuilds the
    # parked op, so the station/project/volunteering V-5's own ADR text
    # describes as "gone" stayed gone on this route, whichever family or door
    # raised it. `context`/`option_keys` reach `confirmation_entry` now
    # (`_open_confirmations` / `build_confirmation_clusters`, fixed alongside
    # this), so the same deterministic turn V-5 built can run here too.
    context = confirmation_entry.get("context") or {}
    if context.get("section") in _ENGAGEMENT_OPS:
        profile_record = await _load_profile(state["profile_id"], db)
        await _apply_engagement_confirmation(
            db, profile_record, context, chosen,
            session_id=str(record.id),
            option_key=resolve_option_key(confirmation_entry, chosen),
        )
    elif context.get("incoming_skill") and resolve_skill_decision(
        confirmation_entry, chosen
    ) is None:
        # #730 — the SKILL arm this route lacks (#674's open line: "a SKILL
        # confirmation answered on the standalone profile-review route resolves
        # nothing"). That line stays open on purpose: this fix adds only what the
        # refusal option needs, which is the REFUSAL half, not the write half.
        # An answer naming none of the options must not spend the park here
        # either, or the candidate's "neither" is swallowed by the bookkeeping
        # resolve below and the ask is gone for good. `distinct`/`merge`
        # answered here still write nothing — unchanged, and still the collector
        # line.
        logger.warning(
            "profile-review confirmation: the answer names none of the options "
            "for incoming skill %r — re-asking, park left open (#730)",
            context.get("incoming_skill"),
        )
        return await _re_ask_unmatched_confirmation(
            record,
            state,
            db,
            current_idx,
            confirmation_entry,
            confirmation_entry["question"],
            options,
            await get_conversation_language(db, job_id=state.get("job_id")),
        )

    await _resolve_confirmation_safely(db, confirmation_entry["confirmation_id"], chosen)

    current_gap = state["critical_gaps"][current_idx]
    state["addressed_gaps"] = state.get("addressed_gaps", []) + [current_gap]
    state["questions_asked"] = state.get("questions_asked", 0) + 1
    record.questions_asked = state["questions_asked"]

    lang = await get_conversation_language(db, job_id=state.get("job_id"))
    return await _ask_or_complete_at(record, state, db, provider, current_idx + 1, lang)


# ADR-066 — the ONE implementation of "which option did the candidate pick"
# moved to `reconcile/confirmations.py` on 2026-09-18 (#723), the module that
# WRITES the option keys, because the applier's family-4 resolution now reads
# them too. Re-exported here: these names are part of this module's surface for
# the interview doors and for `test_669_confirmation_option_keys.py`.
from applire.services.profile.reconcile.confirmations import (  # noqa: E402
    SKILL_OPTION_KEYS as _SKILL_OPTION_KEYS,
    match_skill_decision_text as match_skill_decision_text,
    render_unmatched_answer_hint as render_unmatched_answer_hint,
    resolve_option_key as resolve_option_key,
    resolve_skill_decision as resolve_skill_decision,
)


class ConfirmationAnswerUnmatched(Exception):
    """The answer to a skill-dedupe confirmation named none of its options
    (#730, ADR-063 amended 2026-09-20).

    Raised BEFORE any write, by the applier rather than the door, so a door that
    does not know about this case fails loudly instead of writing. The two doors
    that own the ask (`_handle_interview_confirmation_answer` and the standalone
    profile-review `_handle_confirmation_answer`) catch it and re-ask the same
    question — the empty-answer path's precedent, one step further: an answer
    that is not one of the options is not an answer.
    """


def _skill_confirmation_decision(
    chosen: str,
    option_key: str | None = None,
    *,
    keyed: bool = False,
    pending_conf: dict | None = None,
) -> str | None:
    """Map the user's answer to a skill-dedupe resolution (#187, #669, #730).

    ``option_key`` is the stable key of the option they picked
    (``resolve_option_key``). When present it decides outright — matching stops
    depending on language at all, which is the whole point of ADR-063's
    2026-09-05 amendment. ``keyed`` says the RECORD carries ``option_keys``: an
    answer that named none of them then resolves to ``None``, never to the
    substring matcher (ADR-063 amended 2026-09-20 — the options are the
    identity).

    **The English substring matcher survives only as the back-compat fallback**
    for confirmations persisted before #669 and for the ones the MODEL emits
    (which carry no keys by construction — ``engine._strip_adapter_only``). It
    is the defect the 2026-09-05 amendment names: a German rendering of *"Keep
    the existing skills"* contains neither "keep" nor "existing", so it fell to
    the ``distinct`` default and the vault GAINED a skill the candidate asked it
    to discard. Pinned in ``test_interview_confirmation_resolution.py``.

    **``None`` where this used to return ``"distinct"``** (#730, founder UAT
    2026-09-20). An unrecognised answer no longer writes: the refusal *"Please
    do not add it as a separate skill …"* matched branch 1 on the word it was
    rejecting, and the fallback wrote the skill for every answer the three
    branches missed. The caller re-asks; nothing reaches the vault.

    ``pending_conf`` (adversarial finding, Nougat UAT-fixes batch, 2026-09-20):
    when the caller can hand over the full record, this is a PURE delegation to
    ``confirmations.resolve_skill_decision`` — ADR-066's one implementation,
    computed fresh from the record instead of from the caller's already-derived
    ``option_key``/``keyed``, so a keyless record's answer is matched against
    its own rendered options (never substring-matched as raw free text) exactly
    as a keyed record's is matched against its keys. The bare
    ``option_key``/``keyed`` shape survives only for the tests that pin the
    back-compat matcher's behaviour directly, with no record to match against.
    """
    if pending_conf is not None:
        return resolve_skill_decision(pending_conf, chosen)
    if option_key in _SKILL_OPTION_KEYS:
        return option_key
    if keyed:
        return None
    return match_skill_decision_text(chosen)


#: The three engagement families whose near-dupe confirmation the candidate can
#: now resolve deterministically (founder ruling V-5). Keyed by the `section`
#: the builders put on the confirmation's context.
_ENGAGEMENT_OPS = {
    "work_experience": "UpsertWork",
    "projects": "UpsertProject",
    "volunteer_activities": "UpsertVolunteer",
}


async def _apply_engagement_confirmation(
    db: AsyncSession,
    profile_record: MasterProfile,
    context: dict,
    chosen: str,
    *,
    session_id: str,
    option_key: str | None = None,
) -> bool:
    """Write the entity the candidate just confirmed (founder ruling V-5).

    Symmetrical with `_apply_interview_confirmation`'s skill arm and built to
    the same precedent: the op batch is reconstructed from the confirmation's
    own `context["incoming"]` — the op the reconciler emitted, parked verbatim —
    and the candidate's answer travels as a CAPABILITY
    (`UserConfirmedEngagement`) rather than as an op field, so no schema the
    model can emit gains the power to waive the #177 near-dupe guard
    (ADR-063 clause 1).

    `"distinct"` creates the entry with the guard skipped; `"merge"` folds it
    into the id the builder recorded in `context["existing_ids"]` — an ID, never
    a rendered label, because #669 made the answer's identity
    language-independent and re-deriving it from prose would undo that.

    `pending_bullets` ride along as an `AddBullets` op against the same local
    ref, so they land on whichever entity results. That is what the carrier was
    written for in the first place; it just never had a reader.

    Returns whether anything was applied. `grounding=None`: the candidate
    answering their own question is a direct act (§7.4). **Flush, not commit** —
    the caller owns the transaction.
    """
    section = context.get("section")
    incoming = context.get("incoming")
    if section not in _ENGAGEMENT_OPS or not isinstance(incoming, dict):
        return False

    from applire.services.profile.commit import CommitProvenance, commit_ops
    from applire.services.profile.reconcile.apply import UserConfirmedEngagement
    from applire.services.profile.reconcile import ops as _ops

    op_cls = getattr(_ops, _ENGAGEMENT_OPS[section])
    payload = {k: v for k, v in incoming.items() if k != "op"}
    ref = str(payload.get("ref") or "confirmed-1")
    payload["ref"] = ref
    try:
        entity_op = op_cls(**payload)
    except Exception:  # noqa: BLE001 — a parked op from an older release
        logger.warning(
            "interview: could not rebuild the parked %s op from a confirmation "
            "context; the answer cannot be applied deterministically", section,
        )
        return False

    decision = "merge" if _engagement_decision(chosen, option_key) == "merge" else "distinct"
    existing_ids = [i for i in (context.get("existing_ids") or []) if i]
    target_id = existing_ids[0] if (decision == "merge" and existing_ids) else None

    batch: list = [entity_op]
    carried = context.get("pending_bullets") or {}
    if isinstance(carried, dict) and any(carried.values()):
        batch.append(
            _ops.AddBullets(
                target=ref,
                responsibilities=list(carried.get("responsibilities") or []),
                achievements=list(carried.get("achievements") or []),
                technologies=list(carried.get("technologies") or []),
            )
        )

    await commit_ops(
        db,
        batch,
        CommitProvenance(
            source="interview",
            intake="interview_confirmation",
            session_id=session_id,
            actor="candidate",
        ),
        record=profile_record,
        grounding=None,
        snapshot=None,
        embedding_provider=None,
        user_confirmed_engagement=UserConfirmedEngagement(
            ref=ref, decision=decision, target_id=target_id
        ),
    )
    return True


def _engagement_decision(chosen: str, option_key: str | None) -> str:
    """`"merge"` or `"distinct"` for an engagement confirmation (#669 + V-5).

    The stable key decides when present — the whole point of #669. The English
    substring fallback is the same back-compat path the skill arm keeps, for
    confirmations persisted before the key existed, and it fails toward
    `"distinct"`: creating a duplicate the candidate can merge later is
    recoverable; folding two real positions into one is not.
    """
    if option_key in ("merge", "distinct"):
        return option_key
    c = (chosen or "").strip().lower()
    if "same" in c or "merge" in c or "zusammen" in c or "dieselbe" in c or "dasselbe" in c:
        return "merge"
    return "distinct"


async def _apply_interview_confirmation(
    db: AsyncSession,
    profile_record: MasterProfile,
    context: dict,
    chosen: str,
    *,
    session_id: str,
    option_key: str | None = None,
    keyed: bool = False,
    pending_conf: dict | None = None,
) -> bool:
    """Apply a resolved interview-turn skill confirmation to the profile (#187).

    Returns whether anything was applied — ``False`` when there is nothing to
    apply deterministically (a non-skill confirmation, or "keep the existing
    skills"). The caller advances the interview either way, which is what closes
    the loop.

    Raises :class:`ConfirmationAnswerUnmatched` — before any write — when the
    answer names none of the confirmation's options (#730, ADR-063 amended
    2026-09-20). ``keyed`` is whether the record carries ``option_keys`` and is
    what distinguishes "this answer is not one of the options" from "this record
    predates #669 and only the substring matcher can read it".

    ``pending_conf`` (adversarial finding, Nougat UAT-fixes batch, 2026-09-20):
    the full parked record, handed through so a KEYLESS record's decision is
    resolved against its own rendered options (ADR-063's identity rule, applied
    without keys) instead of substring-matching the candidate's raw free-text
    answer — the gap that let a refusal quoting a rejected option's own word
    ("do not add it as a **separate** skill") still write the skill.

    ADR-063 (#480 PR 7) — **the family-list correction.** The design listed this
    function among the metadata writers; code contact says otherwise. Its
    metadata half (clearing the parked ask) already routes through
    ``ResolveConfirmation`` since PR 5, and what was left here is not a metadata
    write at all: it is a ``skills[]`` upsert that called ``_apply_upsert_skill``
    directly and assigned ``profile_json`` by hand — no trail, no completeness
    recompute, no persisted-denial floor, no deterministic skill enrichment. It
    is therefore routed as an ordinary ``UpsertSkill`` through the committer.

    **The dedupe bypass is a CALL-PATH capability, not an op field.** Skill
    dedupe is the reported vector: the candidate has ANSWERED the question
    ``_apply_upsert_skill``'s stateless containment guard would ask, so
    re-running it re-asks the identical question forever (the #187 loop). The
    waiver travels as ``UserConfirmedSkill(name, decision)`` on ``commit_ops``,
    keyed to the one skill the candidate answered about. Spelling it as a field
    on ``UpsertSkill`` would put a guard-disabling parameter into the *model's*
    vocabulary, which is precisely what ADR-063 clause 1's governing rule
    forbids — and one hallucinated key would then switch the guard off.

    ``grounding=None``: the candidate answering their own question is a direct
    act (§7.4), which is also what keeps the incoming skill ``confirmed``, as it
    was before the routing. **Flush, not commit** — the caller owns the
    transaction, exactly as it did when this function returned a dict.
    """
    incoming = context.get("incoming_skill")
    if not incoming:
        # Founder ruling V-5 (2026-09-09) — the ENGAGEMENT arm.
        #
        # This branch used to read: *"Not a skill confirmation (entity near-dupe
        # etc.) — advancing is enough to break the loop; entity-merge resolution
        # is out of #187's scope."* Advancing broke the loop and lost the
        # answer: the park closed with a receipt saying the candidate's choice
        # was recorded, and the station, the project or the volunteering — plus
        # any bullets carried onto `context["pending_bullets"]` — were never
        # written. Reproduced in `test_confirmation_carried_bullets_are_lost`.
        applied = await _apply_engagement_confirmation(
            db, profile_record, context, chosen,
            session_id=session_id, option_key=option_key,
        )
        if applied:
            return True
        # Still unresolvable (a shape with no `incoming`, a section outside the
        # three engagement families): advancing remains the honest exit, and the
        # applier's WARNING is what makes it diagnosable.
        return False

    decision = _skill_confirmation_decision(
        chosen, option_key, keyed=keyed, pending_conf=pending_conf
    )
    if decision is None:
        # #730 — the answer named no option. Refuse the write and let the door
        # re-ask. This is the guard the founder UAT of 2026-09-20 needed: the
        # refusal *"Please do not add it as a separate skill and do not merge it
        # into my existing Collaboration skill either"* used to resolve to
        # `distinct` (it quotes the option it rejects), and the denied skill
        # reached the vault at `confirmed` and then the delivered CV.
        logger.warning(
            "interview confirmation: the answer names none of the options for "
            "incoming skill %r — refusing the write and re-asking (#730). "
            "option_key=%r keyed=%s",
            incoming,
            option_key,
            keyed,
        )
        raise ConfirmationAnswerUnmatched(incoming)
    if decision == "keep":
        # "Neither / keep the existing skills" — discard the incoming; the
        # existing skills stand unchanged. Family 3 carries this option since
        # #730 (`skill_containment_confirmation`), family 2 always has.
        logger.info(
            "interview confirmation: candidate declined incoming skill %r "
            "(option key 'keep') — nothing written",
            incoming,
        )
        return False

    from applire.services.profile.commit import CommitProvenance, commit_ops
    from applire.services.profile.reconcile.apply import UserConfirmedSkill
    from applire.services.profile.reconcile.ops import UpsertSkill

    await commit_ops(
        db,
        [
            UpsertSkill(
                name=incoming,
                category=context.get("category"),
                proficiency=context.get("proficiency"),
                evidence=list(context.get("evidence_refs") or []),
            )
        ],
        CommitProvenance(
            source="interview",
            intake="interview_confirmation",
            session_id=session_id,
            actor="candidate",
        ),
        record=profile_record,
        grounding=None,
        # ADR-063 amendment (5) / #339 — an interview turn snapshots nothing.
        snapshot=None,
        user_confirmed_skill=UserConfirmedSkill(name=incoming, decision=decision),
    )
    return True


async def _ask_queued_confirmation(
    record: InterviewSession,
    state: InterviewState,
    db: AsyncSession,
    queue: list[dict],
    current_idx: int,
    lang: str,
) -> SessionMessageResponse:
    """Ask the next confirmation the SAME turn already owed the candidate (#353).

    The head of ``queue`` becomes the new ``pending_interview_confirmation``, so
    the next answer is resolved by the same deterministic handler (#187) — no LLM
    re-run, no reconciler pass that could fail to re-emit it.

    #669 residual (2026-09-17 close-out) — a state-dict-shaped queue entry
    carries the same language-independent form ``_confirmation_state`` stores
    (plain ``question``/``options`` are the ENGLISH back-compat fields, see
    ``reconcile/confirmations.py::_build``); rendering happens HERE, against
    ``lang``, exactly like ``_ask_confirmation``'s ``_to_confirmation_prompts``
    call for the turn's FIRST confirmation. Before this, the promoted head's
    plain English fields were surfaced verbatim and ``option_keys`` was never
    carried at all — the delivery-run defect (`11b-interview-confirm-sap-pp.
    json`): a German answer to the re-emitted (English, key-less) confirmation
    resolves through the back-compat English substring matcher instead.
    """
    head, tail = queue[0], queue[1:]
    state["resolving_confirmation"] = True
    state["pending_interview_confirmation"] = head
    state["pending_interview_confirmation_queue"] = tail
    question, options = render_confirmation(head, lang)
    state["current_question"] = question
    state["current_choices"] = options
    state["messages"].append({"role": "assistant", "content": question})
    state["questions_asked"] = state.get("questions_asked", 0) + 1
    record.questions_asked = state["questions_asked"]
    record.state = state
    record.updated_at = datetime.now(timezone.utc)
    await db.commit()
    gaps_remaining = _count_remaining(
        state["critical_gaps"], current_idx, set(state.get("skipped_gaps", []))
    )
    return SessionMessageResponse(
        complete=False,
        question=question,
        gaps_remaining=gaps_remaining,
        choices=options,
        pending_confirmations=[
            _confirmation_prompt_from_state(c, lang) for c in queue
        ],
        current_gap_id=_current_gap_id(state),
        addressed_gap_ids=list(state.get("addressed_gaps", [])),
    )


def _confirmation_state(confirmation) -> dict:
    """The JSONB-safe session-state shape of one parked confirmation.

    #480 PR 5 — it carries ``confirmation_id`` now. A turn's asks are parked
    durably on ``metadata.pending_confirmations`` since the durable CLEAR
    exists, and the id is what lets the in-session answer address the parked
    entry through ``ResolveConfirmation``. A session that persisted only
    question/options/context could never clear its own park — which is exactly
    why durable parking had to wait for this PR.
    """
    # #669 — the STORED form is language-independent; rendering happens at the
    # projection against the reader's current `ui_language`. `option_keys` is
    # what the ANSWER resolves on (`resolve_option_key`), so it has to survive
    # the round-trip through session state, not only through the durable park.
    # All three keys are absent from a state dict written before this change,
    # and every reader below defaults them.
    return {
        "confirmation_id": getattr(confirmation, "confirmation_id", None),
        "question": confirmation.question,
        "options": list(confirmation.options),
        "context": dict(confirmation.context),
        "question_i18n": (
            dict(confirmation.question_i18n)
            if getattr(confirmation, "question_i18n", None)
            else None
        ),
        "options_i18n": (
            [dict(o) for o in confirmation.options_i18n]
            if getattr(confirmation, "options_i18n", None)
            else None
        ),
        "option_keys": list(getattr(confirmation, "option_keys", []) or []),
    }


def render_confirmation(pending_conf: dict, lang: str) -> tuple[str, list[str]]:
    """One parked confirmation as this reader's language sees it (#669).

    Mirrors ``RequestConfirmation.rendered`` for the dict form that lives in
    session state and in ``metadata.pending_confirmations`` — same fallback
    chain (``[lang] ?? de ?? en ?? the plain field``), so a record persisted
    before this change renders exactly as it always did.
    """
    i18n = pending_conf.get("options_i18n")
    q_i18n = pending_conf.get("question_i18n")
    return render_localized_confirmation(
        question=pending_conf.get("question", "") or "",
        options=list(pending_conf.get("options") or []),
        question_i18n=q_i18n if isinstance(q_i18n, dict) else None,
        options_i18n=i18n if isinstance(i18n, list) else None,
        lang=lang,
    )


def _confirmation_prompt_from_state(pending_conf: dict, lang: str) -> ConfirmationPrompt:
    """One state-dict-shaped parked confirmation as a rendered API DTO (#669
    residual, 2026-09-17 close-out).

    ``_to_confirmation_prompts`` (below) does the same job for the engine's
    ``RequestConfirmation``/``PendingConfirmation`` op objects (``.rendered()``
    + ``.option_keys``); this is its counterpart for the ``_confirmation_state``
    dict shape that lives in ``pending_interview_confirmation_queue`` — the
    ONLY other place a confirmation is turned into the wire DTO, so no site
    can emit one without going through a ``lang``-aware renderer that also
    carries ``option_keys``.
    """
    question, options = render_confirmation(pending_conf, lang)
    return ConfirmationPrompt(
        question=question,
        options=options,
        context=dict(pending_conf.get("context") or {}),
        option_keys=list(pending_conf.get("option_keys") or []),
    )


async def _re_ask_unmatched_confirmation(
    record: InterviewSession,
    state: InterviewState,
    db: AsyncSession,
    current_idx: int,
    entry: dict,
    question: str,
    options: list[str],
    lang: str,
) -> SessionMessageResponse:
    """Re-ask a confirmation whose answer named none of its options (#730,
    ADR-063 amended 2026-09-20). Shared by BOTH resolution doors.

    Nothing is written, nothing is resolved, ``questions_asked`` does not move —
    the ask is not spent, because it was not answered. The empty-answer paths in
    both doors are the precedent (``session.py`` "An empty answer re-asks the
    same question + options (never guess)"); the only thing that is new is that
    an answer which is not one of the options counts as "never guess" too.

    The response carries ``pending_confirmations`` so the AGENT door sees the
    option TEXTS and their stable ``option_keys`` again on the re-ask — the first
    ask already carries them (``_ask_confirmation`` /
    ``_ask_queued_confirmation``) and a re-ask that dropped them would leave an
    agent with rendered strings only, which is the shape #730's answer came out
    of (ADR-063 door parity).
    """
    hint = render_unmatched_answer_hint(lang)
    prompt = f"{hint}\n\n{question}"
    state["current_question"] = prompt
    state["current_choices"] = list(options)
    state["messages"].append({"role": "assistant", "content": prompt})
    record.state = state
    record.updated_at = datetime.now(timezone.utc)
    await db.commit()
    gaps_remaining = _count_remaining(
        state["critical_gaps"], current_idx, set(state.get("skipped_gaps", []))
    )
    return SessionMessageResponse(
        complete=False,
        question=prompt,
        gaps_remaining=gaps_remaining,
        choices=list(options),
        pending_confirmations=[
            ConfirmationPrompt(
                question=question,
                options=list(options),
                context=dict(entry.get("context") or {}),
                option_keys=list(entry.get("option_keys") or []),
            )
        ],
        current_gap_id=_current_gap_id(state),
        addressed_gap_ids=list(state.get("addressed_gaps", [])),
    )


async def _handle_interview_confirmation_answer(
    record: InterviewSession,
    state: InterviewState,
    db: AsyncSession,
    provider: LLMProvider,
    current_idx: int,
    pending_conf: dict,
    message: str,
    lang: str,
) -> SessionMessageResponse:
    """Resolve a reconciler-emitted interview-turn confirmation (#187).

    Deterministic (no LLM re-run): the user's choice is applied via the carried
    context, then the interview advances. An empty answer re-asks the same
    question + options (never guess)."""
    # #669 — render in the reader's language; resolve on the stable key.
    question, options = render_confirmation(pending_conf, lang)
    context = pending_conf.get("context") or {}
    chosen = (message or "").strip()

    if not chosen:
        state["current_question"] = question
        state["current_choices"] = options
        state["messages"].append({"role": "assistant", "content": question})
        record.state = state
        record.updated_at = datetime.now(timezone.utc)
        await db.commit()
        gaps_remaining = _count_remaining(
            state["critical_gaps"], current_idx, set(state.get("skipped_gaps", []))
        )
        return SessionMessageResponse(
            complete=False, question=question, gaps_remaining=gaps_remaining,
            choices=options,
            current_gap_id=_current_gap_id(state),
            addressed_gap_ids=list(state.get("addressed_gaps", [])),
        )

    profile_record = await _load_profile(state["profile_id"], db)
    try:
        await _apply_interview_confirmation(
            db, profile_record, context, chosen, session_id=str(record.id),
            option_key=resolve_option_key(pending_conf, chosen),
            keyed=bool(pending_conf.get("option_keys")),
            pending_conf=pending_conf,
        )
    except ConfirmationAnswerUnmatched:
        # #730 — nothing was written and the ask is NOT spent: the park stays
        # open, `questions_asked` does not advance (a re-ask is not a new
        # question, exactly as on the empty-answer path above) and the same
        # question comes back with its options.
        return await _re_ask_unmatched_confirmation(
            record, state, db, current_idx, pending_conf, question, options, lang
        )

    # #480 PR 5 — the turn's ask is parked DURABLY on
    # `metadata.pending_confirmations` now, so answering it in session state is
    # only half the act: the park has to be cleared too, or a later session
    # rebuilds a confirmation cluster for a question the candidate already
    # answered (`_open_confirmations` → `build_confirmation_clusters`).
    #
    # Through the op and the committer, never by mutating the parked list here.
    # `ResolveConfirmation` is the ONLY act permitted to touch this one metadata
    # list (design §4.5 — which is precisely why it is its own op and not folded
    # into `SetProfileMeta`, whose key enum keeps the REST of `metadata`
    # op-unreachable). So the clear is receipted on the enrichment trail and
    # carries the committer's invariant set, identical to the
    # import/profile-review door's clear. Editing `profile_json` at this call
    # site instead would mint a new direct writer of the very attribute
    # ADR-063 clause 6 is closing down.
    #
    # Unconditional on `applied`: "keep the existing skills" and non-skill
    # confirmations resolve nothing in the vault, but the candidate still
    # ANSWERED, so the ask is spent either way.
    confirmation_id = pending_conf.get("confirmation_id")
    if confirmation_id:
        await _resolve_confirmation_safely(db, confirmation_id, chosen)

    # Consume the pending confirmation AND the one-shot flag — the loop is closed.
    state.pop("pending_interview_confirmation", None)
    state.pop("resolving_confirmation", None)

    # #353 — the SAME turn may have owed more than one confirmation. Promote the
    # next one from the queue and ask it instead of advancing: every question the
    # reconciler raised is answered by the candidate, none is dropped in silence.
    queue = list(state.get("pending_interview_confirmation_queue") or [])
    if queue:
        return await _ask_queued_confirmation(record, state, db, queue, current_idx, lang)
    state.pop("pending_interview_confirmation_queue", None)

    current_gap = state["critical_gaps"][current_idx]
    if current_gap not in state.get("addressed_gaps", []):
        state["addressed_gaps"] = state.get("addressed_gaps", []) + [current_gap]
    state["questions_asked"] = state.get("questions_asked", 0) + 1
    record.questions_asked = state["questions_asked"]

    return await _ask_or_complete_at(record, state, db, provider, current_idx + 1, lang)


async def _resolve_conflict_safely(
    db: AsyncSession, conflict_id: str, resolution: str
) -> None:
    """Apply the user's correction, tolerating an already-resolved conflict."""
    from applire.services.profile import resolve_conflict  # lazy: avoid import cycle

    try:
        await resolve_conflict(conflict_id, resolution, None, db)
    except LookupError:
        # Idempotent on resume: the conflict is already gone from pending, so
        # advancing past it is correct.
        pass


async def _handle_conflict_answer(
    record: InterviewSession,
    state: InterviewState,
    db: AsyncSession,
    provider: LLMProvider,
    current_idx: int,
    conflict_entry: dict,
    message: str,
) -> SessionMessageResponse:
    """Resolve a pending Tier-2 conflict from the user's answer (US165).

    A clear keep/use choice writes through the ADR-013 merge (manual_edit
    EnrichmentRecord) and advances; an ambiguous answer re-asks the same
    question (safe default: never guess a factual correction)."""
    decision = interpret_conflict_answer(
        message,
        conflict_entry["existing_value"],
        conflict_entry["incoming_value"],
        # #218 — the drawer submits the offered choice verbatim; trust the button
        # over the prose inside it (a bullet-valued choice collides with the
        # keep/use word sets and would re-ask forever).
        choices=conflict_entry.get("choices"),
    )

    if decision == "unclear":
        question = conflict_entry["question"]
        choices = conflict_entry["choices"]
        state["current_question"] = question
        state["current_choices"] = choices
        state["messages"].append({"role": "assistant", "content": question})
        record.state = state
        record.updated_at = datetime.now(timezone.utc)
        await db.commit()
        gaps_remaining = _count_remaining(
            state["critical_gaps"], current_idx, set(state.get("skipped_gaps", []))
        )
        return SessionMessageResponse(
            complete=False, question=question, gaps_remaining=gaps_remaining, choices=choices,
            current_gap_id=_current_gap_id(state),
            addressed_gap_ids=list(state.get("addressed_gaps", [])),
        )

    resolution = "existing" if decision == "existing" else "incoming"
    await _resolve_conflict_safely(db, conflict_entry["conflict_id"], resolution)

    current_gap = state["critical_gaps"][current_idx]
    state["addressed_gaps"] = state.get("addressed_gaps", []) + [current_gap]
    state["questions_asked"] = state.get("questions_asked", 0) + 1
    record.questions_asked = state["questions_asked"]

    lang = await get_conversation_language(db, job_id=state.get("job_id"))
    return await _ask_or_complete_at(record, state, db, provider, current_idx + 1, lang)


def _to_confirmation_prompts(confirmations, lang: str = "en") -> list[ConfirmationPrompt]:
    """Map engine RequestConfirmation ops to the API confirmation DTO (US185).

    #669 — rendering happens HERE, at the projection, against the reader's
    language (ADR-063 amended 2026-09-05, clause 2), and the DTO carries
    ``option_keys`` so the agent door (ADR-058 parity) sees the same stable
    identity the UI door does. ``lang`` defaults to ``"en"`` so a caller that
    has not resolved a language yet behaves exactly as it did before.
    """
    prompts: list[ConfirmationPrompt] = []
    for c in confirmations:
        question, options = c.rendered(lang)
        prompts.append(
            ConfirmationPrompt(
                question=question,
                options=options,
                context=dict(c.context),
                option_keys=list(c.option_keys),
            )
        )
    return prompts


async def _ask_confirmation(
    record: InterviewSession,
    state: InterviewState,
    db: AsyncSession,
    turn,
    current_gap: str,
    current_idx: int,
) -> SessionMessageResponse:
    """Surface a reconciler ambiguity as a targeted confirmation question (US185).

    The underlying answer was already applied (the ambiguity is a refinement, not
    the gap going unmet), so the current gap is marked addressed and a one-shot
    ``resolving_confirmation`` flag makes the *next* turn advance rather than
    re-ask. The engine resolves the entity identity from the user's answer — the
    system asks, it never guesses (mirrors the US163/US165 confirm principle).

    #187 — the confirmation op (question, options, context) is persisted in state
    so the NEXT turn resolves it DETERMINISTICALLY (no LLM re-run), mirroring the
    import-time ``_handle_confirmation_answer`` mechanism. Without this the
    stateless reconciler re-emits the identical confirmation every turn and the
    interview loops forever.

    #353 — a turn may owe the candidate MORE THAN ONE confirmation (two skills
    of the same family, e.g. 'SAP PP' and 'SAP MM' against an existing 'SAP').
    Only ``[0]`` used to be persisted, so every further confirmation was shown
    once in the response DTO and then existed nowhere: the next turn found no
    pending confirmation and advanced, losing the candidate's second skill with
    no error (ADR-061 — silent loss becomes visible state). The head is asked
    now; the tail is persisted in
    ``pending_interview_confirmation_queue`` and promoted one at a time by
    ``_handle_interview_confirmation_answer``."""
    # #669 — the parked form is language-independent (`_confirmation_state`);
    # what the candidate SEES is rendered here, against the conversation's
    # language, exactly like every LLM-generated question on this surface.
    lang = await get_conversation_language(db, job_id=state.get("job_id"))
    confirmations = [_confirmation_state(c) for c in turn.pending_confirmations]
    confirmation = turn.pending_confirmations[0]
    question, choices = confirmation.rendered(lang)
    if current_gap not in state.get("addressed_gaps", []):
        state["addressed_gaps"] = state.get("addressed_gaps", []) + [current_gap]
    state["resolving_confirmation"] = True
    state["pending_interview_confirmation"] = confirmations[0]
    state["pending_interview_confirmation_queue"] = confirmations[1:]
    state["current_question"] = question
    state["current_choices"] = list(choices)
    state["messages"].append({"role": "assistant", "content": question})
    record.state = state
    record.updated_at = datetime.now(timezone.utc)
    await db.commit()
    gaps_remaining = _count_remaining(
        state["critical_gaps"], current_idx, set(state.get("skipped_gaps", []))
    )
    return SessionMessageResponse(
        complete=False,
        question=question,
        gaps_remaining=gaps_remaining,
        choices=list(choices),
        pending_confirmations=_to_confirmation_prompts(turn.pending_confirmations, lang),
        pending_conflicts=turn.conflict_summaries or None,
        current_gap_id=_current_gap_id(state),
        addressed_gap_ids=list(state.get("addressed_gaps", [])),
        denial_recorded=turn.denial_recorded,  # #380
        changes_applied=turn.addressed,  # ADR-089 — the agent door's status reads it
    )


async def _get_active_profile_review_session(
    db: AsyncSession,
) -> InterviewSession | None:
    """The active standalone profile-review session, if one is in flight.

    A profile-review session is recorded as ``mode='guided'`` with no
    ``job_analysis_id``. Real guided (JD) sessions always carry a job, and the
    Mode-C enrichment sessions are ``mode='profile_enrich'``, so
    "guided + job IS NULL" identifies a profile review uniquely (resume-safe,
    ADR-004)."""
    result = await db.execute(
        select(InterviewSession)
        .where(
            InterviewSession.job_analysis_id.is_(None),
            InterviewSession.mode == "guided",
            InterviewSession.status == "active",
            InterviewSession.deleted_at.is_(None),
        )
        .order_by(InterviewSession.created_at.desc())
    )
    return result.scalars().first()


async def _open_conflicts(profile_record: MasterProfile) -> list[dict]:
    """Unresolved ADR-013 conflicts on the profile, shaped for the cluster builder."""
    profile_data = MasterProfileData.model_validate(profile_record.profile_json)
    if profile_data.metadata is None:
        return []
    return [
        {
            "conflict_id": c.conflict_id,
            "section": c.section,
            "field": c.field,
            "existing_value": c.existing_value,
            "incoming_value": c.incoming_value,
            # #685 — `Conflict.source` carried the real provenance all along and
            # this dict simply dropped it, so `_CONFLICT_COPY` had nothing to say
            # but "an import suggested" — for an INTERVIEW answer, on the
            # founder's own install (2026-09-06).
            "source": c.source,
        }
        for c in profile_data.metadata.pending_conflicts
        if not c.resolved
    ]


async def _open_confirmations(profile_record: MasterProfile) -> list[dict]:
    """Unresolved import-time confirmations (E037 PQ #4), shaped for the cluster
    builder. Each is an N-option ambiguity the reconciler could not auto-resolve.

    Adversarial finding (2026-09-09, WP-adv-vault) — this used to carry only
    ``{confirmation_id, question, options}``, dropping ``context`` and
    ``option_keys`` exactly the way ``_open_conflicts`` used to drop
    ``Conflict.source`` before #685. Two consequences, both silent:

    1. an engagement near-dupe ambiguity raised by ``submit_testimony`` /
       ``submit_claims`` / a CV import and answered through THIS surface (the
       standalone profile-review interview, US165 — what #686's "Decide now"
       CTA opens) could never reach founder ruling V-5's resolution turn
       (``session._apply_engagement_confirmation``), because that turn is
       rebuilt from ``context["incoming"]`` / ``context["existing_ids"]``,
       which never arrived here. The candidate's answer was recorded as
       "resolved" and the station/project/volunteering was still never
       created or merged — V-5's own defect, reproduced via the door +
       resolution-route combination the adversarial brief named explicitly.
    2. with no ``options_i18n`` either, ``build_confirmation_clusters``
       rendered the door's ALWAYS-ENGLISH plain ``question``/``options``
       fields verbatim regardless of the reader's ``ui_language`` — a German
       session saw an English question (#669's whole point, undone one layer
       up).

    Both are fixed together: this now carries the full language-independent
    shape ``PendingConfirmation`` already persists.
    """
    profile_data = MasterProfileData.model_validate(profile_record.profile_json)
    if profile_data.metadata is None:
        return []
    return [
        {
            "confirmation_id": c.confirmation_id,
            "question": c.question,
            "options": list(c.options),
            "context": dict(c.context),
            "question_i18n": dict(c.question_i18n) if c.question_i18n else None,
            "options_i18n": [dict(o) for o in c.options_i18n] if c.options_i18n else None,
            "option_keys": list(c.option_keys),
        }
        for c in profile_data.metadata.pending_confirmations
        if not c.resolved
    ]


async def create_profile_review_session(
    db: AsyncSession,
    provider: LLMProvider,
    lang: str | None = None,
) -> SessionCreateResponse:
    """Launch the standalone profile-review interview (US165).

    No ``job_id``: the session walks the user's open Tier-2 conflicts and resolves
    each in place through the ADR-013 merge. Resume-safe — a second call returns
    the in-flight session rather than starting a new one.
    """
    profile_result = await db.execute(
        select(MasterProfile)
        .where(MasterProfile.deleted_at.is_(None))
        .order_by(MasterProfile.created_at.desc())
        .limit(1)
    )
    profile_record = profile_result.scalar_one_or_none()
    if profile_record is None:
        raise LookupError("No profile found — upload a CV first")

    if lang is None:
        lang = await get_ui_language(db)

    existing = await _get_active_profile_review_session(db)
    if existing is not None:
        return _resumed_response(existing)

    conflicts = await _open_conflicts(profile_record)
    conflict_ids, conflict_categories, conflict_by_id = build_conflict_clusters(
        conflicts, lang
    )
    # E037 PQ #4 — import-time ambiguities surface alongside conflicts, as their
    # own N-option question + per-option buttons (not a garbled 2-value conflict).
    confirmations = await _open_confirmations(profile_record)
    confirm_ids, confirm_categories, confirm_by_id = build_confirmation_clusters(
        confirmations, lang
    )

    review_ids = conflict_ids + confirm_ids
    review_categories = {**conflict_categories, **confirm_categories}
    review_by_id = {**conflict_by_id, **confirm_by_id}

    # ADR-080 — the budget follows the plan. Every conflict/confirmation cluster
    # here is answered deterministically in ONE turn (no LLM re-ask, no follow-up),
    # so this plan's real cost is well under the derived worst case; the shared
    # derivation is used anyway, because a second formula for one mode plan is the
    # ADR-066 defect this ADR exists to remove.
    hard_ceiling = derive_hard_ceiling(
        len(review_ids), cap=settings.interview_max_questions_guided
    )

    state: InterviewState = _build_state(
        mode="guided",
        job_id=None,
        gap_analysis_id=None,
        profile_id=profile_record.id,
        critical_gaps=review_ids,
        gap_categories=review_categories,
        gap_clusters_by_id=review_by_id,
        current_question="",
        hard_ceiling=hard_ceiling,
    )
    state["entry"] = "profile_review"
    state["conflict_clusters"] = conflict_by_id
    state["confirmation_clusters"] = confirm_by_id

    # Nothing flagged — record a complete session and tell the user they're clear.
    if not review_ids:
        record = _make_session_record(
            job_id=None,
            gap_analysis_id=None,
            profile_id=profile_record.id,
            mode="guided",
            status="complete",
            state=state,
            hard_ceiling=hard_ceiling,
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)
        all_clear = "No open issues to review — your Master Profile is in good shape!"
        return SessionCreateResponse(
            session_id=record.id,
            mode="guided",
            first_question=all_clear,
            question=all_clear,
            estimated_questions=0,
            hard_ceiling=0,
            gaps_total=0,
            gaps_remaining=0,
        )

    first_entry = review_by_id[review_ids[0]]
    first_question = first_entry["question"]
    first_choices = first_entry["choices"]
    state["current_question"] = first_question
    state["current_choices"] = first_choices
    state["messages"].append({"role": "assistant", "content": first_question})
    state["questions_asked"] = 1

    record = _make_session_record(
        job_id=None,
        gap_analysis_id=None,
        profile_id=profile_record.id,
        mode="guided",
        status="active",
        state=state,
        hard_ceiling=hard_ceiling,
        questions_asked=1,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    return SessionCreateResponse(
        session_id=record.id,
        mode="guided",
        first_question=first_question,
        question=first_question,
        estimated_questions=_estimated_questions("guided", hard_ceiling),
        hard_ceiling=hard_ceiling,
        gaps_total=len(review_ids),
        gaps_remaining=len(review_ids),
        choices=first_choices,
        current_gap_id=review_ids[0],
    )


# ---------------------------------------------------------------------------
# POST /api/session
# ---------------------------------------------------------------------------


async def gap_cluster_ids(job_id: uuid.UUID, db: AsyncSession) -> list[str] | None:
    """Return the gap-cluster ids from the job's latest gap analysis.

    Used by the agent channel (`resolve_gap`) to validate a caller-supplied
    ``gap_id`` with exact membership before opening a targeted micro-session —
    the same fail-fast discipline `submit_claims` applies to ledger concepts,
    so an agent gets the valid ids back instead of a silently generic session.
    Returns ``None`` when no analysis exists yet (call ``analyze_gaps`` first);
    ``[]`` when an analysis exists but has no gap clusters (near-complete match).
    """
    result = await db.execute(
        select(GapAnalysis)
        .where(
            GapAnalysis.job_analysis_id == job_id,
            GapAnalysis.deleted_at.is_(None),
        )
        .order_by(GapAnalysis.created_at.desc())
        .limit(1)
    )
    gap_analysis = result.scalar_one_or_none()
    if gap_analysis is None:
        return None
    return [c.get("id") for c in (gap_analysis.gap_clusters or []) if c.get("id")]


def is_micro_session(record: InterviewSession) -> bool:
    """Whether `record` is a Gap-Click micro-session (US265/19.9), never a
    full MODE A/B interview — the one predicate `create_session`'s
    idempotency branch and `resolve_gap`'s guard (mcp/server.py) both use.

    #627 follow-up: neither signal used before this was safe alone.
      - `hard_ceiling == 1` is an operator-overridable `Settings` field
        (`interview_max_questions_targeted`/`_guided`, config.py) — a
        self-hoster who sets either to 1 would have every FULL interview
        misidentified as a micro-session too, under the default (12/20)
        this never surfaces.
      - `mode == "targeted"` cannot tell a micro-session apart from a full
        MODE A targeted interview at all — `_create_micro_session` also
        persists `mode="targeted"`.

    The authoritative signal is the `micro_session` marker
    `_build_state`/`_create_micro_session` stamp into the session's own
    state at creation. `hard_ceiling == 1` is kept ONLY as a fallback for a
    session persisted before this marker existed (a self-hoster's
    pre-upgrade DB row) — every session created by this codebase from here
    on always carries the marker explicitly (default False).
    """
    state = record.state or {}
    if "micro_session" in state:
        return bool(state["micro_session"])
    return record.hard_ceiling == 1


async def active_full_interview_exists(job_id: uuid.UUID, db: AsyncSession) -> bool:
    """Whether the job has an active FULL interview (MODE A or MODE B) —
    i.e. an active session that is NOT a Gap-Click micro-session.

    Lets the agent channel (`resolve_gap`) refuse to stomp an in-progress full
    interview — `_create_micro_session` completes any active session wholesale,
    which would silently discard its remaining question plan. A leftover
    micro-session is safe to reap either way.

    #627 follow-up — replaces `active_session_mode`, whose only caller
    compared the returned mode string to `"targeted"`: that correctly
    protected an in-progress MODE B guided run, but a MODE A targeted run
    and a Gap-Click micro-session BOTH persist `mode="targeted"`, so it
    silently let a half-finished targeted interview get stomped too.
    `is_micro_session` is the predicate that actually tells them apart.
    """
    active = await _get_active_session(job_id, db)
    return active is not None and not is_micro_session(active)


# ---------------------------------------------------------------------------
# ADR-089 — the per-gap record at the interview doors: one budget for every
# door, the refusal of a cluster that cannot be asked, the prior exchanges a
# later question may read, and the outcome every answered turn writes. The
# facts themselves (classification, coverage, budget) are `gap_coverage`'s —
# this module only calls them (ADR-066).
# ---------------------------------------------------------------------------


class GapNotAskableError(Exception):
    """ADR-089 clause 1/7 — a session was requested on a gap cluster that can
    no longer be asked: its per-gap budget is spent, or its coverage is already
    ``covered``/``declined``. The REST door answers HTTP 409 with the
    machine-readable ``error_code`` (``gap_budget_spent`` |
    ``gap_already_covered``); the agent door (``resolve_gap``) answers
    ``invalid_input`` with the same ``message``, which names the cluster."""

    def __init__(self, error_code: str, message: str, cluster_id: str):
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.cluster_id = cluster_id


def _prior_asked(cluster: dict | None) -> int:
    """``outcome.asked`` off a persisted cluster entry — 0 for a legacy row."""
    outcome = (cluster or {}).get("outcome") or {}
    try:
        return max(int(outcome.get("asked") or 0), 0)
    except (TypeError, ValueError):
        return 0


def gap_not_askable(cluster: dict) -> GapNotAskableError | None:
    """The refusal for a cluster ``gap_coverage.is_askable`` rejects, else None.

    ADR-089 clause 1: "a session opened on a cluster with no budget left is
    refused with a message naming it". The error code says which of the two
    facts refused it; a cluster that is both covered and spent reports
    ``gap_already_covered`` — nothing is left to ask either way, and "covered"
    is the fact the candidate can act on.
    """
    per_gap = INTERVIEW_MAX_QUESTIONS_PER_GAP
    if gap_coverage.is_askable(cluster, per_gap):
        return None
    cluster_id = str(cluster.get("id") or "")
    label = str(cluster.get("label") or cluster_id)
    coverage = gap_coverage.stored_or_derived_coverage(cluster)
    if coverage == "declined":
        return GapNotAskableError(
            "gap_already_covered",
            f'"{label}" is already settled — the candidate declined every '
            "requirement in it, so there is nothing left to ask.",
            cluster_id,
        )
    if coverage == "covered" or gap_coverage.remaining_budget(cluster, per_gap) > 0:
        return GapNotAskableError(
            "gap_already_covered",
            f'"{label}" is already covered — every requirement in it is backed '
            "by the profile, so there is nothing left to ask.",
            cluster_id,
        )
    return GapNotAskableError(
        "gap_budget_spent",
        f'"{label}" has already been asked {_prior_asked(cluster)} time(s) — its '
        f"question budget ({per_gap} per gap, across every interview) is spent.",
        cluster_id,
    )


_PRIOR_EXCHANGES_MAX = 6


def _cluster_turn_pairs(state: dict, cluster_id: str) -> list[dict]:
    """The (question, answer) pairs a session's transcript holds for one
    cluster, read through its ``cluster_turns`` index (ADR-089 clause 6).

    Indexes that do not point at an assistant question followed by a user
    answer are skipped, never guessed — a malformed row contributes nothing.
    """
    messages = state.get("messages") or []
    pairs: list[dict] = []
    for turn in state.get("cluster_turns") or []:
        if not isinstance(turn, dict) or turn.get("cluster_id") != cluster_id:
            continue
        q_idx, a_idx = turn.get("q"), turn.get("a")
        if not isinstance(q_idx, int) or not isinstance(a_idx, int):
            continue
        if not (0 <= q_idx < a_idx < len(messages)):
            continue
        q_msg, a_msg = messages[q_idx], messages[a_idx]
        if (q_msg or {}).get("role") != "assistant" or (a_msg or {}).get("role") != "user":
            continue
        question = str(q_msg.get("content") or "").strip()
        answer = str(a_msg.get("content") or "").strip()
        if question and answer:
            pairs.append({"question": question, "answer": answer})
    return pairs


async def _prior_exchanges(
    db: AsyncSession,
    cluster: dict | None,
    *,
    exclude_session_id: str | None = None,
) -> list[dict]:
    """Earlier (question, answer) pairs on ``cluster``, oldest first (ADR-089
    clause 6).

    Read from the sessions its ``outcome.session_ids`` references — NEVER
    copied into the analysis row or into the asking session's state, so an
    answer lives exactly as long as the transcript it belongs to (VVT §4.1): a
    session past its retention period is gone and simply contributes nothing.
    ``exclude_session_id`` is the asking session itself, whose own exchanges
    already reach the prompt as its "Recent conversation".
    """
    if not cluster:
        return []
    cluster_id = str(cluster.get("id") or "")
    session_ids = (cluster.get("outcome") or {}).get("session_ids") or []
    pairs: list[dict] = []
    for sid in session_ids:
        if not sid or str(sid) == str(exclude_session_id or ""):
            continue
        try:
            uid = uuid.UUID(str(sid))
        except ValueError:
            continue
        row = (
            await db.execute(
                select(InterviewSession).where(
                    InterviewSession.id == uid,
                    InterviewSession.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if row is None:
            continue
        pairs.extend(_cluster_turn_pairs(dict(row.state or {}), cluster_id))
    return pairs[-_PRIOR_EXCHANGES_MAX:]


async def _latest_cluster(
    job_id: uuid.UUID, cluster_id: str, db: AsyncSession
) -> dict | None:
    """The cluster entry as the job's latest analysis row carries it, or None."""
    result = await db.execute(
        select(GapAnalysis)
        .where(
            GapAnalysis.job_analysis_id == job_id,
            GapAnalysis.deleted_at.is_(None),
        )
        .order_by(GapAnalysis.created_at.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return gap_coverage.cluster_by_id(row.gap_clusters, cluster_id)


async def last_recorded_answer(
    job_id: uuid.UUID, cluster_id: str, db: AsyncSession
) -> str | None:
    """The answer the cluster's record last recorded, or None (ADR-089 clause 7).

    The agent door refuses a call whose testimony is identical (after
    normalisation) to this one: an agent retrying a call whose turn already
    committed would otherwise be charged a second question for the same words.
    """
    cluster = await _latest_cluster(job_id, cluster_id, db)
    if cluster is None:
        return None
    pairs = await _prior_exchanges(db, cluster)
    return pairs[-1]["answer"] if pairs else None


async def cluster_coverage_for(
    job_id: uuid.UUID, cluster_id: str, db: AsyncSession
) -> ClusterCoverage | None:
    """The cluster's coverage as the job's latest analysis row records it — the
    agent door's fallback when a turn wrote no record of its own."""
    return _coverage_of(await _latest_cluster(job_id, cluster_id, db))


def _is_pending_micro_on(record: InterviewSession, cluster_id: str) -> bool:
    """An active Gap-Click micro-session on exactly ``cluster_id`` that already
    holds an answered turn — i.e. a follow-up (or a confirmation) it asked is
    waiting for its answer. Re-opening the same gap resumes it instead of
    retiring it, so the pending question is the one that gets answered and no
    second question is generated for the same slot (ADR-089 clauses 7 and 8)."""
    if not is_micro_session(record):
        return False
    state = record.state or {}
    return (
        list(state.get("critical_gaps") or []) == [cluster_id]
        and int(state.get("questions_asked") or 0) > 1
    )


def _seed_questions_per_gap(clusters_by_id: dict[str, dict]) -> dict[str, int]:
    """The in-session per-gap counter, seeded from the cross-session record.

    ``questions_per_gap[cid]`` is "questions asked on this gap, the current one
    included" (default 1 via ``.get(cid, 1)``). ADR-089 clause 1 makes the
    budget cross-session, so a cluster already asked ``k`` times elsewhere
    starts at ``k + 1``: the counter and ``outcome.asked`` agree at every
    answer, and every existing ``questions_for_gap < per_gap`` gate (the
    ADR-064 probe, the no-change follow-up) reads the shared budget unchanged.
    """
    seeded: dict[str, int] = {}
    for cid, cluster in clusters_by_id.items():
        asked = _prior_asked(cluster)
        if asked > 0:
            seeded[cid] = asked + 1
    return seeded


def _coverage_of(cluster: dict | None) -> ClusterCoverage | None:
    """The API projection of a persisted cluster entry (facts only)."""
    if not cluster or not cluster.get("id"):
        return None
    coverage = gap_coverage.stored_or_derived_coverage(cluster)
    return ClusterCoverage(
        cluster_id=str(cluster["id"]),
        coverage=coverage,
        open_concepts=[str(m) for m in (cluster.get("gaps") or []) if m],
        budget_remaining=gap_coverage.remaining_budget(
            cluster, INTERVIEW_MAX_QUESTIONS_PER_GAP
        ),
    )


async def _cluster_question(
    state: InterviewState,
    profile: dict,
    provider: LLMProvider,
    db: AsyncSession,
    *,
    session_id: str | None = None,
    **kwargs,
) -> dict:
    """``question_generator_with_profile`` for the session's current gap, with
    the ADR-089 clause 6 input view: a MODE A cluster that earlier sessions
    already asked carries those exchanges (read from the referenced
    transcripts, :func:`_prior_exchanges`). ``prior_exchanges`` is passed only
    when there is one, so a first-time cluster's call is unchanged."""
    if state.get("mode") == "targeted":
        cluster_id = _current_gap_id(state)
        cluster = (state.get("gap_clusters_by_id") or {}).get(cluster_id) if cluster_id else None
        prior = await _prior_exchanges(db, cluster, exclude_session_id=session_id)
        if prior:
            kwargs["prior_exchanges"] = prior
    return await question_generator_with_profile(state, profile, provider, **kwargs)


def _with_coverage(
    response: SessionMessageResponse, coverage: ClusterCoverage | None
) -> SessionMessageResponse:
    if coverage is not None:
        response.cluster_coverage = coverage
    return response


async def _record_cluster_turn(
    record: InterviewSession,
    state: InterviewState,
    db: AsyncSession,
    *,
    current_gap: str,
    updated_profile: dict,
) -> tuple[dict | None, list[str]]:
    """ADR-089 clauses 2 and 3 — after this turn's #188 seam: classify the
    cluster's open members against the session row's ledger and write the
    outcome onto the per-gap record, in the turn's own transaction
    (``record_turn_outcome`` flushes, never commits; ``send_message``'s one
    commit per turn persists it with the vault write and the transcript).

    Returns ``(recorded_cluster, open_members)``: the persisted cluster entry
    as written (``None`` when no analysis row carries the cluster — a legacy or
    MODE B session, which keeps its pre-ADR-089 behaviour), and the members this
    turn left open, in cluster order. Also indexes the turn in
    ``state["cluster_turns"]`` so a later session can read the exchange
    (clause 6) — indexes only, never a copy of the text.
    """
    gap_analysis_id = state.get("gap_analysis_id")
    if not gap_analysis_id or state.get("mode") != "targeted":
        return None, []
    clusters_by_id = dict(state.get("gap_clusters_by_id") or {})
    cluster = clusters_by_id.get(current_gap)
    if not isinstance(cluster, dict) or not cluster:
        return None, []

    row = (
        await db.execute(
            select(GapAnalysis).where(GapAnalysis.id == uuid.UUID(str(gap_analysis_id)))
        )
    ).scalar_one_or_none()
    if row is None:
        return None, []

    # The members to classify: the cluster's open members as the session's own
    # analysis row carries them, plus the session's copy (which
    # `filter_answered_concepts` may have narrowed at session start). Reading
    # the row too means a member the filter dropped because it was already
    # `direct` is classified — and so moved to `covered` — instead of staying
    # listed as open on the record.
    persisted = gap_coverage.cluster_by_id(row.gap_clusters, current_gap)
    members: list[str] = []
    seen: set[str] = set()
    for m in [*((persisted or {}).get("gaps") or []), *(cluster.get("gaps") or [])]:
        key = ats_norm(str(m or ""))
        if m and key and key not in seen:
            seen.add(key)
            members.append(str(m))
    if not members:
        return None, []

    denied_records = [
        d
        for d in ((updated_profile or {}).get("metadata") or {}).get("denied_concepts") or []
        if isinstance(d, dict) and d.get("concept")
    ]
    facts = gap_coverage.classify_members(members, row.keyword_ledger, denied_records)

    # The transcript index of this answered turn: the user message this turn
    # appended is the last one; the question it answers is the last assistant
    # message before it.
    messages = state.get("messages") or []
    a_idx = len(messages) - 1
    q_idx = next(
        (i for i in range(a_idx - 1, -1, -1) if (messages[i] or {}).get("role") == "assistant"),
        None,
    )
    if q_idx is not None and a_idx >= 0:
        state["cluster_turns"] = list(state.get("cluster_turns") or []) + [
            {"cluster_id": current_gap, "q": q_idx, "a": a_idx}
        ]

    recorded = await gap_coverage.record_turn_outcome(
        db,
        job_id=record.job_analysis_id,
        fallback_gap_analysis_id=uuid.UUID(str(gap_analysis_id)),
        cluster_id=current_gap,
        member_facts=facts,
        session_id=str(record.id),
        charge=True,
    )
    # With no ledger on the row there is no evidence to classify against:
    # every member reads `open` by absence, which is no reason to ask again —
    # such a turn is charged and indexed, but never earns a partial-coverage
    # follow-up (the pre-ledger behaviour: advance).
    open_members = (
        [m for m in members if facts.get(m) == "open"] if row.keyword_ledger else []
    )
    if recorded is None:
        return None, open_members

    # Keep the session's own copy in step with the record's `outcome` and
    # `coverage` (the all-members readers, and the "asked before" fact the
    # question generator reads to keep the US265 nudge on the opening question
    # only). Its `gaps` are NOT narrowed here: the ADR-064 probe selection later
    # in this same turn matches the denied concept against the members the
    # question was ABOUT — narrowing first would hide a just-declined member
    # from it. The partial-coverage follow-up narrows them when it asks
    # (`_ask_partial_coverage_follow_up`).
    clusters_by_id[current_gap] = {
        **cluster,
        "outcome": recorded.get("outcome") or gap_coverage.empty_outcome(),
        "coverage": recorded.get("coverage") or "open",
    }
    state["gap_clusters_by_id"] = clusters_by_id
    # The in-session counter agrees with the record (clause 1): after this
    # turn, `outcome.asked` answered turns exist on the cluster.
    qpg = dict(state.get("questions_per_gap") or {})
    qpg[current_gap] = max(int(qpg.get(current_gap, 1)), _prior_asked(recorded))
    state["questions_per_gap"] = qpg
    return recorded, open_members


async def create_session(
    request: SessionCreateRequest,
    db: AsyncSession,
    provider: LLMProvider,
) -> SessionCreateResponse:
    job_id = request.job_id

    # Resolve job analysis
    job_result = await db.execute(
        select(JobAnalysis).where(
            JobAnalysis.id == job_id,
            JobAnalysis.deleted_at.is_(None),
        )
    )
    job = job_result.scalar_one_or_none()
    if job is None:
        raise LookupError(f"Job analysis {job_id} not found")

    # Resolve latest profile (may be None for MODE B)
    profile_result = await db.execute(
        select(MasterProfile)
        .where(MasterProfile.deleted_at.is_(None))
        .order_by(MasterProfile.created_at.desc())
        .limit(1)
    )
    profile_record = profile_result.scalar_one_or_none()

    # --- Mode resolution ---
    if request.mode is not None:
        resolved_mode = request.mode
    else:
        resolved_mode = _auto_detect_mode(profile_record)

    # Resolve conversation language once per request (ADR-038, amended 2026-08-01)
    lang = await get_conversation_language(db, job_id=job.id)

    # --- Micro-session: target_gap scopes to a single gap (Gap-Click mode, 19.9) ---
    # #627 — target_gap alone is authoritative: a caller naming one specific
    # cluster always gets that cluster's micro-session, regardless of how
    # `mode` resolves. _create_micro_session below never consults
    # resolved_mode at all (it hardcodes mode="targeted" on the record it
    # builds), so gating this branch on `resolved_mode == "targeted"` served
    # no purpose but to trap a caller that sends target_gap without ALSO
    # remembering an explicit mode="targeted" — silently falling through to
    # the idempotency branch below instead of the requested gap.
    if request.target_gap:
        return await _create_micro_session(job_id, job, profile_record, request.target_gap, db, provider, lang)

    # --- Idempotency: return existing active session if one exists for this job ---
    existing = await _get_active_session(job_id, db)
    if existing is not None:
        # #627 — a Gap-Click micro-session that the user opened and then
        # closed WITHOUT answering stays `active` forever: send_message is
        # the only thing that ever completes it, and closing the panel/tab
        # never calls it (Cancel/close is local-only UI state). A later
        # GENERIC request (no target_gap — "start the/an interview") must
        # not resume that orphaned single question as if it were the
        # freshly requested interview. Retire it exactly the way
        # _create_micro_session retires a stale active session when IT
        # supersedes one, then fall through to a real create. An unanswered
        # micro-session never reached reconcile_interview_turn, so nothing is
        # lost by retiring it — there is no vault write to preserve.
        # is_micro_session (not hard_ceiling==1 directly) so an operator who
        # overrides interview_max_questions_targeted/_guided down to 1 never
        # has a real full interview misidentified and retired here.
        if is_micro_session(existing):
            existing.status = "complete"
            existing.updated_at = datetime.now(timezone.utc)
            await db.flush()
        else:
            return _resumed_response(existing)

    try:
        # --- MODE A: Targeted Gap-Fill ---
        if resolved_mode == "targeted":
            return await _create_targeted_session(job_id, job, profile_record, db, provider, lang)

        # --- MODE B: Guided Build ---
        return await _create_guided_session(job_id, job, profile_record, db, provider, lang)
    except IntegrityError:
        # Lost a create race: a concurrent request (e.g. React StrictMode
        # double-fire) committed its session after our idempotency check but
        # before our insert.  The unique active-per-job index rejected ours —
        # return the winner instead of surfacing a 500.
        await db.rollback()
        winner = await _get_active_session(job_id, db)
        if winner is None:
            raise
        return _resumed_response(winner)


def _resumed_response(existing: InterviewSession) -> SessionCreateResponse:
    state: InterviewState = dict(existing.state)
    gaps_total = len(state.get("critical_gaps", []))
    gaps_remaining = gaps_total - state.get("current_gap_index", 0)
    estimated = _estimated_questions(existing.mode, existing.hard_ceiling)
    current_q = state.get("current_question", "")
    current_choices = state.get("current_choices")
    # `resumed` must reflect genuine in-progress work, not mere session
    # pre-existence (issue #44).  The onboarding overlay pre-creates the guided
    # session before routing to /interview, so the interview page's own
    # idempotent create call always lands here — but the user has answered
    # nothing yet.  A freshly created session sits at questions_asked == 1 (first
    # question generated, zero answers); only once the user has answered at least
    # one question (questions_asked > 1) is there somewhere to "continue from".
    real_questions_asked = state.get("questions_asked", existing.questions_asked) or 1
    answered = real_questions_asked > 1
    return SessionCreateResponse(
        session_id=existing.id,
        mode=existing.mode,
        first_question=current_q,
        question=current_q,
        estimated_questions=estimated,
        # The record's own persisted ceiling — the single source of truth,
        # rather than re-deriving it from mode (issue #245).
        hard_ceiling=existing.hard_ceiling,
        gaps_total=gaps_total,
        gaps_remaining=gaps_remaining,
        choices=current_choices,
        resumed=answered,
        current_gap_id=_current_gap_id(state),
        addressed_gap_ids=list(state.get("addressed_gaps", [])),
        # #259 run-4 finding 9 — the real server-tracked count, so a page
        # refresh restores "N of up to M" instead of resetting to "1 of…".
        questions_asked=real_questions_asked,
    )


async def _create_targeted_session(
    job_id: uuid.UUID,
    job: JobAnalysis,
    profile_record: MasterProfile | None,
    db: AsyncSession,
    provider: LLMProvider,
    lang: str = "en",
) -> SessionCreateResponse:
    if profile_record is None:
        raise LookupError(
            "No profile found — upload a CV first, or use mode='guided' to build from scratch"
        )

    # Lazy gap analysis
    gap_result = await db.execute(
        select(GapAnalysis)
        .where(
            GapAnalysis.job_analysis_id == job_id,
            GapAnalysis.deleted_at.is_(None),
        )
        .order_by(GapAnalysis.created_at.desc())
        .limit(1)
    )
    gap_analysis = gap_result.scalar_one_or_none()
    if gap_analysis is None:
        gap_response = await analyze_gaps(job_id, db, provider)
        ga_result2 = await db.execute(
            select(GapAnalysis).where(GapAnalysis.id == gap_response.id)
        )
        gap_analysis = ga_result2.scalar_one()

    # #274/#284/#273 (PO reframing 2026-07-26) — before building the question
    # plan, re-check every still-open ledger entry against the CURRENT vault:
    # evidence may have arrived through a door other than THIS turn (CV
    # import, testimony intake, an earlier session, submit_claims) and #188's
    # addressed-gate only ever fires for the turn that wrote it. Deterministic,
    # no LLM call; reassign the whole attribute (plain _JSON column, not a
    # MutableList — mirrors the #188 JSONB-tracking gotcha at line ~1371).
    new_ledger, ledger_changed = reevaluate_gap_ledger_against_vault(
        gap_analysis.keyword_ledger, profile_record.profile_json
    )
    # #318 / ADR-061 — the affirmative invariant on the row actually written.
    # This seam is the widest net in the system (it runs at every session
    # start, over the whole ledger, whatever door wrote it), so it is also
    # where a row corrupted by an EARLIER build is caught: `violations` alone
    # is enough to make the write worth doing.
    new_ledger, ledger_violations = assert_claimable_backed(
        new_ledger, profile_record.profile_json, seam="interview vault re-evaluation"
    )
    if ledger_changed or ledger_violations:
        gap_analysis.keyword_ledger = new_ledger

    # #259 — pass the profile so gap_detector can promote a JD-required
    # keyword-only/unquantified concept ahead of nice-to-have breadth within
    # its C/B bucket (services/interview/sufficiency.cluster_needs_priority).
    cluster_ids, cluster_categories, clusters_by_id = gap_detector(
        gap_analysis, profile=profile_record.profile_json
    )

    # #273/#284 — gap_analysis.gap_clusters is a clustering-LLM snapshot that
    # is never recomputed when the ledger is later upgraded (through the
    # reevaluation just above, THIS session's own #188 write path on a
    # resumed session, or submit_claims). Drop/narrow any cluster whose
    # concepts the ledger now shows as genuinely "direct" — never re-ask a
    # requirement the vault already answers.
    cluster_ids, cluster_categories, clusters_by_id = filter_answered_concepts(
        cluster_ids, cluster_categories, clusters_by_id, gap_analysis.keyword_ledger
    )

    # ADR-089 clause 6 — the full interview honours the per-gap record: a
    # cluster whose coverage is `covered`/`declined`, or whose budget an
    # earlier door (Gap-Click, resolve_gap, an earlier interview) already
    # spent, is not asked again. `is_askable` is the one predicate; the
    # budget the rest get is what the record leaves them (seeded below).
    askable_ids = [
        cid
        for cid in cluster_ids
        if gap_coverage.is_askable(clusters_by_id[cid], INTERVIEW_MAX_QUESTIONS_PER_GAP)
    ]
    if len(askable_ids) != len(cluster_ids):
        logger.info(
            "targeted session plan for job %s: skipped %d cluster(s) the per-gap "
            "record marks covered/declined or budget-spent (ADR-089 clause 6): %s",
            job_id,
            len(cluster_ids) - len(askable_ids),
            [cid for cid in cluster_ids if cid not in askable_ids],
        )
    skipped_by_record = len(cluster_ids) - len(askable_ids)
    cluster_ids = askable_ids
    cluster_categories = {cid: cluster_categories[cid] for cid in cluster_ids}
    clusters_by_id = {cid: clusters_by_id[cid] for cid in cluster_ids}

    # US163: prepend any open deferred Tier-1 gate ahead of the JD gaps —
    # mandatory and job-irrelevant.
    gate_ids, gate_categories, gate_by_id = await _pending_gate_clusters(
        db, lang, _account_name(profile_record)
    )
    critical_gaps = gate_ids + cluster_ids
    gap_categories = {**cluster_categories, **gate_categories}
    gap_clusters_by_id = {**clusters_by_id, **gate_by_id}

    # ADR-080 — the budget is derived from the plan THIS session will walk, so it
    # is computed here: after `filter_answered_concepts` narrowed the clustering
    # snapshot and after the US163 gates were prepended. Deriving it from the raw
    # `gap_analysis.gap_clusters` instead would buy questions for clusters the
    # session has already decided not to ask. This is the call site #646 is about:
    # ADR-029 targets 5-12 clusters and the budget was a flat 12.
    hard_ceiling = derive_hard_ceiling(
        len(critical_gaps), cap=settings.interview_max_questions_targeted
    )

    if not critical_gaps:
        state: InterviewState = _build_state(
            mode="targeted",
            job_id=job_id,
            gap_analysis_id=gap_analysis.id,
            profile_id=profile_record.id,
            critical_gaps=[],
            gap_categories={},
            gap_clusters_by_id={},
            current_question="",
            hard_ceiling=hard_ceiling,
        )
        record = _make_session_record(
            job_id=job_id,
            gap_analysis_id=gap_analysis.id,
            profile_id=profile_record.id,
            mode="targeted",
            status="complete",
            state=state,
            hard_ceiling=hard_ceiling,
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)
        # #166: "strong match" is only honest when there really are NO critical
        # gaps. has_clustering_input() mirrors cluster_gaps()'s OWN augmented
        # input (persisted category_c PLUS keyword-only honest gaps, #166
        # Important-1) — checking raw category_c alone missed the case where
        # category_c is empty but keyword-only honest gaps were non-empty, so
        # clustering had real input yet still produced nothing askable. When
        # that shared predicate says there was something to cluster, clustering
        # silently failed (JSON-object-mode parse loss, or the keyword-only path)
        # — telling the candidate they're a strong match is a dangerous lie.
        # Emit an honest fallback instead.
        if skipped_by_record:
            # ADR-089 — nothing failed: every cluster the analysis holds has
            # already been worked (covered, declined, or its per-gap budget
            # spent in an earlier door). Saying "strong match" or "clustering
            # failed" would both be false.
            no_gaps_msg = (
                "Every gap in this analysis has already been worked through — "
                "you can proceed to CV generation."
            )
        elif has_clustering_input(gap_analysis):
            logger.warning(
                "targeted session %s: clustering had input (category_c=%d) but no "
                "askable clusters/gates — clustering likely failed; emitting honest "
                "fallback",
                record.id,
                len(gap_analysis.category_c or []),
            )
            no_gaps_msg = (
                "The gap interview is currently unavailable — "
                "you can proceed to CV generation."
            )
        else:
            no_gaps_msg = "No critical gaps identified — your profile is a strong match!"
        return SessionCreateResponse(
            session_id=record.id,
            mode="targeted",
            first_question=no_gaps_msg,
            question=no_gaps_msg,
            estimated_questions=0,
            hard_ceiling=0,
            gaps_total=0,
            gaps_remaining=0,
        )

    state = _build_state(
        mode="targeted",
        job_id=job_id,
        gap_analysis_id=gap_analysis.id,
        profile_id=profile_record.id,
        critical_gaps=critical_gaps,
        gap_categories=gap_categories,
        gap_clusters_by_id=gap_clusters_by_id,
        current_question="",
        hard_ceiling=hard_ceiling,
    )
    state["gate_clusters"] = gate_by_id
    # ADR-089 clause 1 — each planned cluster starts from the budget the
    # cross-session record leaves it.
    state["questions_per_gap"] = _seed_questions_per_gap(clusters_by_id)

    first_cluster_id = critical_gaps[0]
    gate_entry = gate_by_id.get(first_cluster_id)
    if gate_entry is not None:
        first_question = gate_entry["question"]
        first_choices = gate_entry["choices"]
    else:
        first_category = gap_categories.get(first_cluster_id)
        # US265 — availability elicitation is folded into the FIRST real MODE A
        # cluster question only: computed once here, never recomputed for any
        # later cluster in this session, so "one availability question" holds
        # by construction rather than a tracked flag.
        include_availability = should_ask_availability(
            job.raw_text, profile_record.profile_json
        )
        q_data = await _cluster_question(
            state, profile_record.profile_json, provider, db,
            gap_category=first_category, lang=lang,
            include_availability=include_availability,
        )
        first_question = q_data["question"]
        first_choices = q_data["choices"]
    state["current_question"] = first_question
    state["current_choices"] = first_choices
    state["messages"].append({"role": "assistant", "content": first_question})
    state["questions_asked"] = 1

    record = _make_session_record(
        job_id=job_id,
        gap_analysis_id=gap_analysis.id,
        profile_id=profile_record.id,
        mode="targeted",
        status="active",
        state=state,
        hard_ceiling=hard_ceiling,
        questions_asked=1,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    return SessionCreateResponse(
        session_id=record.id,
        mode="targeted",
        first_question=first_question,
        question=first_question,
        estimated_questions=_estimated_questions("targeted", hard_ceiling),
        hard_ceiling=hard_ceiling,
        gaps_total=len(critical_gaps),
        gaps_remaining=len(critical_gaps),
        choices=first_choices,
        current_gap_id=first_cluster_id,
    )


async def _create_guided_session(
    job_id: uuid.UUID,
    job: JobAnalysis,
    profile_record: MasterProfile | None,
    db: AsyncSession,
    provider: LLMProvider,
    lang: str = "en",
) -> SessionCreateResponse:
    # MODE B can start without a profile — create an empty stub if needed.
    #
    # #480 PR 8: the row is created by the vault's own write module, inside the
    # ADR-063 clause-6 token, instead of by a `MasterProfile(profile_json={})`
    # here — the third of the three keyword-argument constructors, and the one
    # that would have made PR 9's strict guard break Mode B outright.
    #
    # It is still EMPTY, byte for byte. This session has learned nothing yet:
    # the stub exists so the session has a `profile_id` to point at, and giving
    # it a metadata block, a completeness score and an enrichment record would
    # be the committer claiming a change to a vault where nothing has happened.
    # The first answer is the first real write, and it goes through `commit_ops`
    # like every other interview turn.
    if profile_record is None:
        from applire.services.profile.commit import create_profile_record

        profile_record = await create_profile_record(db)

    sections = gap_detector_mode_b(job)
    job_context = {
        "role_title": job.role_title or "",
        "seniority_level": job.seniority_level or "",
    }

    # US163: an open deferred gate blocks even a from-scratch guided build.
    gate_ids, gate_categories, gate_by_id = await _pending_gate_clusters(
        db, lang, _account_name(profile_record)
    )
    critical_gaps = gate_ids + sections

    # ADR-080 — same derivation as every other mode plan.
    #
    # This comment used to say the MODE B derivation REPRODUCES the historical
    # constant: 7 core sections plus up to 2 JD-signalled ones, so an ungated
    # 9-section plan derives 2*9+2 = 20, exactly INTERVIEW_HARD_CEILING_GUIDED.
    # That coincidence was the evidence in ADR-080 that the formula is the law
    # the system had been following unevenly — and it ENDED on 2026-09-08, when
    # ADR-028's amendment dropped `professional_summary` from
    # `_MODE_B_CORE_SECTIONS` (epic #683). Six core sections plus up to two
    # signalled ones derives 2*8+2 = 18; the constant stays 20 and is now what
    # ADR-080 clause 4 says it is — the operator's cap, applied after the
    # derivation, not the budget. One section fewer costs two questions of
    # budget, which is exactly what the formula is for; the constant is not
    # re-tuned to hide the change.
    hard_ceiling = derive_hard_ceiling(
        len(critical_gaps), cap=settings.interview_max_questions_guided
    )

    state: InterviewState = _build_state(
        mode="guided",
        job_id=job_id,
        gap_analysis_id=None,
        profile_id=profile_record.id,
        critical_gaps=critical_gaps,
        gap_categories=gate_categories,
        gap_clusters_by_id=gate_by_id,
        current_question="",
        hard_ceiling=hard_ceiling,
    )
    state["gate_clusters"] = gate_by_id

    first_cluster_id = critical_gaps[0]
    gate_entry = gate_by_id.get(first_cluster_id)
    if gate_entry is not None:
        first_question = gate_entry["question"]
        first_choices = gate_entry["choices"]
    else:
        # US265 — same one-shot check as the targeted path: computed once,
        # here, for the FIRST section only. Every later section is asked via
        # send_message's advance branch, which never passes this flag, so
        # "one availability question" holds by construction.
        include_availability = should_ask_availability(
            job.raw_text, profile_record.profile_json
        )
        q_data = await question_generator_with_profile(
            state,
            profile_record.profile_json,
            provider,
            gap_category=None,
            job_context=job_context,
            lang=lang,
            include_availability=include_availability,
        )
        first_question = q_data["question"]
        first_choices = None
    state["current_question"] = first_question
    state["current_choices"] = first_choices
    state["messages"].append({"role": "assistant", "content": first_question})
    state["questions_asked"] = 1

    record = _make_session_record(
        job_id=job_id,
        gap_analysis_id=None,
        profile_id=profile_record.id,
        mode="guided",
        status="active",
        state=state,
        hard_ceiling=hard_ceiling,
        questions_asked=1,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    return SessionCreateResponse(
        session_id=record.id,
        mode="guided",
        first_question=first_question,
        question=first_question,
        estimated_questions=_estimated_questions("guided", hard_ceiling),
        hard_ceiling=hard_ceiling,
        gaps_total=len(critical_gaps),
        gaps_remaining=len(critical_gaps),
        choices=first_choices,
        current_gap_id=first_cluster_id,
    )


async def _create_micro_session(
    job_id: uuid.UUID,
    job: JobAnalysis,
    profile_record: MasterProfile | None,
    target_cluster_id: str,
    db: AsyncSession,
    provider: LLMProvider,
    lang: str = "en",
) -> SessionCreateResponse:
    """Create a micro-session scoped to a single cluster (Gap-Click mode).

    ADR-089 clause 1 — no longer one question: the ceiling is
    ``derive_hard_ceiling(1)`` (ADR-080 clause 3's ``n = 1`` case) and the
    cluster's cross-session budget (``INTERVIEW_MAX_QUESTIONS_PER_GAP`` minus
    ``outcome.asked``) decides how many answers it takes; the ``micro_session``
    marker stays the authoritative predicate (#627).

    Refuses (``GapNotAskableError`` → HTTP 409 / ``invalid_input``) a cluster
    whose budget is spent or whose coverage is ``covered``/``declined``.
    Re-opening a cluster whose micro-session is still waiting for the answer to
    a follow-up RESUMES that session (clauses 7/8): the pending question is the
    one answered, and nothing is generated or charged twice.
    """
    if profile_record is None:
        raise LookupError(
            "No profile found — upload a CV first before using Gap-Click mode"
        )

    # Load latest gap analysis to find the cluster
    gap_result = await db.execute(
        select(GapAnalysis)
        .where(
            GapAnalysis.job_analysis_id == job_id,
            GapAnalysis.deleted_at.is_(None),
        )
        .order_by(GapAnalysis.created_at.desc())
        .limit(1)
    )
    gap_analysis = gap_result.scalar_one_or_none()

    cluster: dict = {
        "id": target_cluster_id,
        "label": target_cluster_id,
        "gaps": [],
        "jd_skills": [],
        "jd_context": "",
    }
    gap_category: str | None = None
    found = False
    if gap_analysis is not None:
        persisted = gap_coverage.cluster_by_id(gap_analysis.gap_clusters, target_cluster_id)
        if persisted is not None:
            cluster = persisted
            gap_category = persisted.get("category")
            found = True

    existing_active = await _get_active_session(job_id, db)
    if existing_active is not None and _is_pending_micro_on(existing_active, target_cluster_id):
        return _resumed_response(existing_active)

    if found:
        refusal = gap_not_askable(cluster)
        if refusal is not None:
            raise refusal

    prior_asked = _prior_asked(cluster)
    micro_ceiling = derive_hard_ceiling(1)
    state: InterviewState = _build_state(
        mode="targeted",
        job_id=job_id,
        gap_analysis_id=gap_analysis.id if gap_analysis else None,
        profile_id=profile_record.id,
        critical_gaps=[target_cluster_id],
        gap_categories={target_cluster_id: gap_category or "C"},
        gap_clusters_by_id={target_cluster_id: cluster},
        current_question="",
        hard_ceiling=micro_ceiling,
        micro_session=True,
    )
    state["questions_per_gap"] = _seed_questions_per_gap({target_cluster_id: cluster})
    # US265 — the availability ask rides the cluster's OPENING question only:
    # this session's first question, and only when no earlier door has asked
    # this cluster before (its opening question then already carried it).
    # Follow-ups never pass the flag.
    include_availability = prior_asked == 0 and should_ask_availability(
        job.raw_text, profile_record.profile_json
    )
    q_data = await _cluster_question(
        state, profile_record.profile_json, provider, db,
        gap_category=gap_category, lang=lang,
        include_availability=include_availability,
    )
    first_question = q_data["question"]
    first_choices = q_data["choices"]
    state["current_question"] = first_question
    state["current_choices"] = first_choices
    state["messages"].append({"role": "assistant", "content": first_question})
    state["questions_asked"] = 1

    if existing_active is not None:
        existing_active.status = "complete"
        existing_active.updated_at = datetime.now(timezone.utc)
        await db.flush()

    record = _make_session_record(
        job_id=job_id,
        gap_analysis_id=gap_analysis.id if gap_analysis else None,
        profile_id=profile_record.id,
        mode="targeted",
        status="active",
        state=state,
        hard_ceiling=micro_ceiling,
        questions_asked=1,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    return SessionCreateResponse(
        session_id=record.id,
        mode="targeted",
        first_question=first_question,
        question=first_question,
        # The answers this cluster can still take (its remaining per-gap
        # budget), not the ceiling's headroom.
        estimated_questions=gap_coverage.remaining_budget(
            cluster, INTERVIEW_MAX_QUESTIONS_PER_GAP
        ),
        hard_ceiling=micro_ceiling,
        gaps_total=1,
        gaps_remaining=1,
        choices=first_choices,
        current_gap_id=target_cluster_id,
    )


# ---------------------------------------------------------------------------
# POST /api/session/{session_id}/message
# ---------------------------------------------------------------------------


def _denied_concept_entry(concept: str, denied_concepts: list[dict]) -> dict | None:
    """Lookup of a concept's raw DURABLE entry off a profile's
    ``metadata.denied_concepts`` — the ``DeniedConcept`` record, never the
    ``keyword_ledger`` mirror (ADR-064: ``KeywordLedgerEntry.denial_level`` is
    refreshed on every ledger rebuild, not durable, and a known second write
    path — ``upgrade_ledger_for_concepts`` in services/keyword_ledger.py —
    can leave it ``None`` on a "denied" ledger row even after a real denial).

    Normalised with ``ats_audit._norm`` (M3 finding-fix, 2026-07-29) — the
    SAME normaliser ``record_denials`` uses for its own dedupe, so a concept
    that IS one durable record there (e.g. "RAG-Pipeline" == "RAG pipeline")
    can never look like two different ones here. A plain ``.strip().casefold()``
    doesn't fold the hyphen, so ``probe_asked``/``denial_level`` bookkeeping
    written under one spelling could go invisible to a probe-gate lookup
    under a variant spelling of the SAME concept.
    """
    norm = ats_norm(concept)
    for entry in denied_concepts:
        if not isinstance(entry, dict):
            continue
        if ats_norm(entry.get("concept") or "") == norm:
            return entry
    return None


def _denial_level_for(concept: str, denied_concepts: list[dict]) -> str | None:
    """``denial_level`` half of :func:`_denied_concept_entry`. Returns
    ``None`` when the concept has no denied_concepts entry at all.
    """
    entry = _denied_concept_entry(concept, denied_concepts)
    return entry.get("denial_level") if entry else None


def _probe_already_asked(concept: str, denied_concepts: list[dict]) -> bool:
    """ADR-064 finding-fix — has the ONE permitted transfer probe already
    been ISSUED for this concept (elicitation bookkeeping), regardless of
    ``denial_level``? A concept with no durable entry yet has never been
    probed — defensively ``False``, never "already probed".
    """
    entry = _denied_concept_entry(concept, denied_concepts)
    return bool(entry.get("probe_asked")) if entry else False


async def _mark_probe_asked(
    db: AsyncSession, profile_record: MasterProfile, concept: str, session_id: str
) -> dict:
    """ADR-064 finding-fix — durably set ``DeniedConcept.probe_asked = True``
    for ``concept`` the moment its transfer probe is ISSUED (never when it is
    answered), so an abandoned session cannot lose it. This is bookkeeping
    ("we asked"), never testimony ("they denied") — it must NOT touch
    ``denial_level``, which only a genuine candidate denial may move.

    ADR-063 (#480 PR 7) — the write is a :class:`MarkProbeAsked` act through
    ``commit_ops`` now; the op's applier owns the normaliser, the fail-safe and
    the reach. Deferred here from PR 2 by the design's own ruling, because
    ``metadata.*`` is op-unreachable by design and the whole metadata-writer
    family had to be shaped before one narrow exception could be carved.

    ``grounding=None`` (ruling 4): bookkeeping is never testimony, and
    ``TurnGrounding`` stays candidate-text-only. **Flush, not commit** — the
    caller's own ``db.commit()`` writes this in the SAME transaction as the
    probe question, which is what keeps an abandoned session from losing it.

    M4 finding-fix (2026-07-29): ``_select_denial_probe_concept`` now REQUIRES
    a durable ``DeniedConcept`` record to exist before it will ever select a
    concept to probe (it no longer treats "no record at all" the same as
    "direct/unprobed") — so reaching "no durable entry" is now a genuine
    contract violation by the caller, not a documented edge case. The applier
    keeps failing safe rather than raising mid-turn or minting a fresh
    ``DeniedConcept`` from bookkeeping alone — inventing a denial record, with
    no candidate statement behind it, would be worse than a probe whose "asked"
    bookkeeping is merely unrecorded: it would durably attribute testimony the
    candidate never gave. The safer half of the fix is requiring the record
    upstream.

    Returns the post-write profile JSON, which the caller hands to the question
    generator.
    """
    from applire.services.profile.commit import CommitProvenance, commit_ops
    from applire.services.profile.reconcile.ops import MarkProbeAsked

    await commit_ops(
        db,
        [MarkProbeAsked(concept=concept)],
        CommitProvenance(
            source="interview",
            intake="denial_probe",
            session_id=session_id,
            actor="system",
        ),
        record=profile_record,
        grounding=None,
        snapshot=None,
    )
    return profile_record.profile_json


async def _select_denial_probe_concept(
    state: InterviewState,
    turn,
    updated_profile: dict,
    db: AsyncSession,
    current_gap: str,
) -> str | None:
    """ADR-064 — the ONE deterministic (pure Python, no LLM) trigger for the
    denial transfer probe: the first concept denied on THIS turn
    (``turn.denied_concepts``) that is ALL of

      * JD-critical — ``concept_is_required`` reads ``"required" in
        entry["sources"]`` off the persisted ``GapAnalysis.keyword_ledger``,
        never a hard-coded taxonomy of skills/frameworks/technologies, AND
      * a member of the CURRENT gap cluster (F3 finding-fix, 2026-07-29) —
        matched against ``state["gap_clusters_by_id"][current_gap]["gaps"]``
        with the SAME cross-generator matcher ``concept_is_required`` uses
        for its own ledger lookup (``_concept_matches_ledger_key`` — cluster
        labels and denied-concept text come from independently generated
        LLM output, so byte-identical text can't be assumed). Without this,
        a denial of an UNRELATED requirement (e.g. "FastAPI experience"
        denied while the interview is on the "GCP certification" cluster)
        could fire a probe carrying the wrong gap's label alongside a
        follow-up hint naming a different concept, spend that wrong gap's
        retry budget, and leave the actually-denied gap not advancing, AND

      * backed by a DURABLE ``DeniedConcept`` record (M4 finding-fix,
        2026-07-29) whose ``denial_level`` is still ``"direct"`` and whose
        ``probe_asked`` bookkeeping flag is still ``False``. A concept with
        NO durable record is never selected — ``reconcile_interview_turn``'s
        ``record_denials`` call always writes one for every concept in
        ``turn.denied_concepts`` in the same turn, so "no record" is not a
        normal case to design a probe trigger around; probing without one
        would leave ``_mark_probe_asked``'s bookkeeping with nothing to
        attach to (it fails safe rather than minting a record from
        bookkeeping alone — see its docstring), so the "asked" fact could be
        lost to an abandoned session and a later genuine denial of the same
        concept could re-trigger the probe. Requiring the record is the
        safer half of that agreement.

    ADR-064 finding-fix: gating on ``denial_level`` alone let a probe whose
    answer was unproductive (neither evidence nor a denial — so
    ``denial_level`` never escalates to "partial") fire AGAIN on a later
    genuine denial of the same concept. ``probe_asked`` is written the
    instant the probe is ISSUED (see ``_ask_denial_probe``), independent of
    how the answer is later classified, so it closes that gap without
    writing an unanswered probe up as testimony the candidate never gave.

    Returns ``None`` (never probe) when the session has no ledger at all
    (guided / Mode-B — ``gap_analysis_id`` is unset, mirroring
    ``_upgrade_ledger_for_addressed_gap``'s own guard), the current cluster
    has no constituent concepts (same conservative no-op as
    ``_upgrade_ledger_for_addressed_gap``), or no denied concept qualifies.
    Deciding WHETHER to probe is this function; deciding HOW to phrase it is
    the model's, via the existing follow-up generation path
    (``_ask_denial_probe`` below) — never the reverse.
    """
    gap_analysis_id = state.get("gap_analysis_id")
    if not gap_analysis_id:
        return None  # guided / Mode B sessions have no ledger — never probe
    result = await db.execute(
        select(GapAnalysis).where(GapAnalysis.id == uuid.UUID(str(gap_analysis_id)))
    )
    gap = result.scalar_one_or_none()
    keyword_ledger = gap.keyword_ledger if gap else None
    if not keyword_ledger:
        return None

    # F6 (2026-07-29): a persisted session `state` predating `gap_clusters_by_id`
    # (legacy JSONB — every in-code creation site populates this key; see
    # `_make_active_session`-style state builders and `gap_detector`) falls
    # through `.get(...) or {}` to an empty cluster with no "gaps" below, so
    # `cluster_concepts` is empty and this function returns `None` — never
    # probes. That is the correct, FAIL-SAFE direction for a legacy record:
    # no behaviour change, just documenting why an old session can't reach
    # the probe rather than raising or guessing a cluster.
    cluster = (state.get("gap_clusters_by_id") or {}).get(current_gap) or {}
    cluster_concepts = [c for c in (cluster.get("gaps") or []) if c]
    if not cluster_concepts:
        return None  # no constituent concepts to match against — never guess

    denied_meta = (updated_profile.get("metadata") or {}).get("denied_concepts") or []
    for concept in turn.denied_concepts:
        if not concept or not concept_is_required(concept, keyword_ledger):
            continue
        # F3 finding-fix (2026-07-29): `_concept_matches_ledger_key` folds via
        # its own `.strip().casefold()`, not `ats_norm` — it does NOT fold
        # hyphens, so "GCP-certification" (cluster gap, clustering LLM) vs
        # "GCP certification" (denied concept, classification LLM) compare
        # unequal and this gate silently never fires. `_concept_matches_
        # ledger_key` also drives `_ledger_entry_for`/`concept_is_required`/
        # `cluster_needs_priority` in services/interview/sufficiency.py —
        # changing its normaliser in place would ALSO change matching there
        # (and casefold() vs ats_norm's `.lower()` differ on e.g. German
        # "ß"), so the fold is applied here, at this call site, instead:
        # pre-fold both sides with the SAME `ats_norm` the rest of this
        # probe path already uses, then let the unchanged helper do its
        # substring comparison on the already-folded text.
        if not any(
            _concept_matches_ledger_key(ats_norm(concept), ats_norm(cc))
            for cc in cluster_concepts
        ):
            continue  # off-cluster denial — advance as before, never probe it
        # M4 finding-fix: require a durable record (`level is not None`) —
        # never probe a concept `_mark_probe_asked` could not durably mark.
        level = _denial_level_for(concept, denied_meta)
        if level == "direct" and not _probe_already_asked(concept, denied_meta):
            return concept
    return None


async def _ask_denial_probe(
    record: InterviewSession,
    state: InterviewState,
    db: AsyncSession,
    provider: LLMProvider,
    current_gap: str,
    current_idx: int,
    probe_concept: str,
    updated_profile: dict,
    turn,
    questions_for_gap: int,
    lang: str,
    profile_record: MasterProfile,
) -> SessionMessageResponse:
    """ADR-064 — issue the ONE, terminal transfer-probe follow-up: a direct
    denial of a JD-critical concept earns exactly one more question aimed at
    the broader SKILL AREA the denied concept belongs to, never a repeat of
    the same named form and never a second probe (enforced by the
    ``probing_concept``/``probing_gap`` state markers this sets, consumed
    unconditionally at the top of the NEXT ``send_message`` call, before that
    turn is evaluated for anything else — see the ADR-064 block above the
    hard-ceiling check).

    Wired to the EXISTING follow-up generation path
    (``question_generator_with_profile(..., follow_up_hint=...)``) — same
    entry point as the "more specific example" retry follow-up below, a new
    hint rather than a new generator. The hint names the concept the
    candidate just denied so the model can aim at its skill area; it is
    deliberately NOT elaborate wording — Task 3 owns refining that prompt.
    ``denial_probe=True`` (M8 finding-fix, 2026-07-29) is the ONE difference
    from that other follow-up: it routes through the choices-producing
    schema/prompt (same coverage/truthfulness rules and
    ``filter_ungrounded_choices`` guard as MODE A) instead of the plain
    text-only path, so this ONE question — where partial-versus-denial IS
    the point — gets the same level-tagged denial-choice guarantee every
    other question does. The "more specific example" retry keeps
    ``choices: None`` unchanged.

    Counts against the SAME per-gap retry budget as an ordinary follow-up
    (``questions_per_gap``) — the caller only reaches here when that budget
    is not yet exhausted, so the probe can never push a gap past
    ``INTERVIEW_MAX_QUESTIONS_PER_GAP``.

    ADR-064 finding-fix: ``probing_concept``/``probing_gap`` are one-shot
    ``InterviewState`` markers, not durable — a raised
    ``INTERVIEW_MAX_QUESTIONS_PER_GAP`` lets the SAME gap stay open past
    this turn, and if the probe's answer is unproductive (neither evidence
    nor a denial) ``denial_level`` never escalates to "partial", so those
    markers alone can't keep a later genuine denial of the same concept from
    re-triggering the probe. ``DeniedConcept.probe_asked`` is the durable
    half: it is set to ``True`` HERE, the moment the probe is issued —
    never when it is answered — and written into ``profile_record`` in the
    SAME commit as the question, so an abandoned session cannot lose it.

    #480 PR 7: that write is a `MarkProbeAsked` act through `commit_ops`, which
    flushes; this function's own `db.commit()` below is what makes it durable —
    and is load-bearing exactly as it was when the assignment was inline.
    """
    updated_profile = await _mark_probe_asked(
        db, profile_record, probe_concept, str(record.id)
    )

    qpg = dict(state.get("questions_per_gap", {}))
    qpg[current_gap] = questions_for_gap + 1
    state["questions_per_gap"] = qpg
    state["probing_concept"] = probe_concept
    state["probing_gap"] = current_gap

    follow_up_hint = (
        f'the candidate just denied direct experience with "{probe_concept}" — '
        "ask ONE follow-up about the broader SKILL AREA it belongs to (not the "
        "same named form again), to check for adjacent or transferable experience"
    )
    gap_category = (state.get("gap_categories") or {}).get(current_gap)

    # M8 finding-fix (2026-07-29): `denial_probe=True` routes through the
    # choices-producing schema/prompt (same coverage/truthfulness rules and
    # `filter_ungrounded_choices` guard as MODE A) instead of the plain
    # text-only follow-up path — the branch's own coverage rule must reach
    # the ONE question where partial-versus-denial is the entire point.
    probe_data = await question_generator_with_profile(
        state, updated_profile, provider,
        gap_category=gap_category, follow_up_hint=follow_up_hint, lang=lang,
        denial_probe=True,
    )
    probe_question = probe_data["question"]
    probe_choices = probe_data.get("choices")
    state["current_question"] = probe_question
    state["current_choices"] = probe_choices
    state["messages"].append({"role": "assistant", "content": probe_question})
    record.state = state
    record.updated_at = datetime.now(timezone.utc)
    await db.commit()

    gaps_remaining = _count_remaining(
        state["critical_gaps"], current_idx, set(state.get("skipped_gaps", [])),
    )
    return SessionMessageResponse(
        complete=False,
        question=probe_question,
        gaps_remaining=gaps_remaining,
        pending_conflicts=turn.conflict_summaries if turn.conflict_summaries else None,
        choices=probe_choices,
        current_gap_id=_current_gap_id(state),
        addressed_gap_ids=list(state.get("addressed_gaps", [])),
        # #380: the probe is issued ON the denial turn — the caller must see
        # that the denial landed even though the session keeps asking.
        denial_recorded=turn.denial_recorded,
        changes_applied=turn.addressed,  # ADR-089 — the agent door's status reads it
    )


async def _upgrade_ledger_for_addressed_gap(
    state: InterviewState,
    current_gap: str,
    answer: str,
    db: AsyncSession,
    *,
    upgrade: bool = True,
) -> None:
    """When an interview turn ADDRESSED a gap, upgrade the matching keyword_ledger
    entries on the persisted GapAnalysis row IN PLACE (#188) — no LLM re-run.

    The ledger is built ONCE during gap analysis and persisted on
    ``GapAnalysis.keyword_ledger``; BOTH the CV and cover-letter generators read
    that same row. A strength the candidate has now CONFIRMED in the interview
    otherwise stays classed as an honest gap, so the cover letter hedges it as a
    growth area — contradicting the CV. Flipping the matched entry to claimable
    (with the answer text as evidence) fixes both documents from the one row.

    This is the single backend seam that covers ALL interview channels — the full
    ``/interview`` flow, the inline micro-session, and the MCP ``run_interview`` —
    since every one of them funnels through ``send_message``.

    Conservative (truthfulness): only the CURRENT cluster's concepts are eligible,
    and only honest-gap entries that normalize-match them are touched. NO-OP (never
    fabricate) when the session has no ledger (guided / Mode B — ``gap_analysis_id``
    is None), the cluster has no concepts, the row has no ledger, or nothing matches.

    ``upgrade=False`` (#352) runs the ADR-059 polarity floor and NOTHING else:
    the caller's turn applied no ops, so it confirmed nothing and may not
    upgrade — but a denial-only turn is still a real answer that must be able to
    REVERSE an upgrade an earlier turn committed. Before #352 the whole seam was
    called only ``if addressed:`` (``bool(applied.changes)``), so the one turn
    shape that is a retraction was the one shape the floor never saw.
    """
    gap_analysis_id = state.get("gap_analysis_id")
    if not gap_analysis_id:
        return  # guided / Mode B sessions have no ledger — never fabricate one

    cluster = (state.get("gap_clusters_by_id") or {}).get(current_gap)
    if not cluster:
        return
    concepts = [c for c in (cluster.get("gaps") or []) if c]
    if not concepts:
        return

    result = await db.execute(
        select(GapAnalysis).where(GapAnalysis.id == uuid.UUID(str(gap_analysis_id)))
    )
    gap = result.scalar_one_or_none()
    if gap is None or not gap.keyword_ledger:
        return

    # --- Polarity + per-concept evidence (ADR-059 amended 2026-07-27) ---------
    # `addressed` is bool(applied.changes) — it says the turn CHANGED the vault,
    # not that it confirmed any particular concept of the cluster. Charter run #7
    # showed both ways that fails on a MIXED turn (a denial plus real positive
    # content, which is the normal shape of an honest answer):
    #   * the answer denied PSD2/BaFin while supplying genuine security content,
    #     so `addressed` was True and every concept of the "Security and
    #     Compliance" cluster flipped to direct+claimable — with the denial
    #     sentence itself stored as the backing evidence;
    #   * the payments answer closed its cluster and dragged
    #     "Crypto/blockchain settlement rails" — a concept it never mentions and
    #     the interview never asked about — to claimable along with it.
    # So: denials are passed down (a denial is a real answer and must move the
    # requirement's status), and a concept is only eligible to be UPGRADED when
    # the answer literally evidences it, judged by `surface_present` — THE shared
    # presence predicate (#122), never a second matcher that could disagree with
    # the ATS panel. Conservative by construction: an answer that confirms a
    # concept without naming it simply does not upgrade here, and the ordinary
    # vault re-evaluation picks it up once the testimony lands.
    profile_record = await _load_profile(state["profile_id"], db)
    profile_json = (profile_record.profile_json if profile_record else None) or {}
    # ADR-064/#486 — the RECORDS, not just the concept strings: the in-place
    # seam asserts the durable denial's own `denial_level`, exactly as a
    # rebuild through `_enforce_denial_stance` does. The bare concept list is
    # what the local eligibility check needs.
    denied_records = [
        d
        for d in (profile_json.get("metadata") or {}).get("denied_concepts") or []
        if isinstance(d, dict) and d.get("concept")
    ]
    denied_concepts = [d["concept"] for d in denied_records]
    # #351 — the vault's own attestations, so the containment branch of the
    # denial predicate can be judged against real evidence instead of
    # fail-closing. Same instrument and same input `_enforce_denial_stance` and
    # `reevaluate_gap_ledger_against_vault` already use; this door was the third
    # of ADR-064's "all three places" and the only one that passed nothing.
    # #480 §7.5(a) / ADR-059 amended 2026-08-09 — site 4 of the five that feed
    # the release predicate. The corpus narrows to ATTESTED ENTITY LABELS: an
    # `unconfirmed` entry backs nothing (ADR-061 clause 3, step 1), and neither
    # does a sentence the candidate typed into a document (step 2).
    vault_corpus = denial_release_corpus(profile_json)

    answer_norm = ats_norm(answer or "")
    by_concept = {
        str(e.get("concept", "")): (e.get("surface_forms") or [e.get("concept", "")])
        for e in gap.keyword_ledger
        if e.get("concept")
    }

    def _evidenced(concept: str) -> bool:
        forms = by_concept.get(concept) or [concept]
        return any(surface_present(f, answer_norm) for f in forms if f)

    eligible = [c for c in concepts if _evidenced(c)]
    # A denied concept stays in the list even when unevidenced: the point is to
    # RECORD the denial on the requirement, not to upgrade it.
    if denied_concepts:
        eligible += [
            c
            for c in concepts
            if c not in eligible and is_denied_concept(c, denied_concepts)
        ]
    if not eligible:
        return

    new_ledger, changed = upgrade_ledger_for_concepts(
        gap.keyword_ledger,
        eligible,
        answer,
        denied_concepts=denied_records,
        upgrade=upgrade,
        vault_corpus=vault_corpus,
    )
    # #318 / ADR-061 — the affirmative invariant, at the seam that produced the
    # measured divergence. `_evidenced` above only proves the ANSWER names the
    # concept; charter run #7 case 2 upgraded `MES`/`OEE` on exactly that
    # evidence while the same turn's skill ops were dropped by the stance
    # guard, so the vault ended the turn with neither. `profile_json` is read
    # AFTER this turn's ops were applied, so a turn whose testimony genuinely
    # landed still upgrades — the two seams either both accept the turn or
    # neither does, which is the reconciliation #318 exists for.
    new_ledger, violations = assert_claimable_backed(
        new_ledger, profile_json, seam="interview #188 upgrade"
    )
    if changed or violations:
        # JSONB tracking gotcha: keyword_ledger is a plain _JSON column, NOT a
        # MutableList — mutating a dict inside the list in place would not be
        # flagged dirty. Reassign the WHOLE attribute (a freshly built list) so
        # SQLAlchemy persists it. Entry values are JSON-native (str/bool/float/
        # list), so no model_dump(mode="json") coercion is needed here.
        gap.keyword_ledger = new_ledger


async def send_message(
    session_id: uuid.UUID,
    message: str,
    db: AsyncSession,
    provider: LLMProvider,
) -> SessionMessageResponse:
    # Load session
    session_result = await db.execute(
        select(InterviewSession).where(
            InterviewSession.id == session_id,
            InterviewSession.deleted_at.is_(None),
        )
    )
    record = session_result.scalar_one_or_none()
    if record is None:
        raise LookupError(f"Session {session_id} not found")
    if record.status == "complete":
        raise ValueError("Session is already complete")

    # Resolve conversation language once for this turn (ADR-038, amended 2026-08-01)
    lang = await get_conversation_language(db, job_id=record.job_analysis_id)

    state: InterviewState = dict(record.state)
    state["messages"].append({"role": "user", "content": message})

    # --- Done-signal check (pre-LLM, deterministic) ---
    if is_termination_signal(message):
        return await _complete_session(record, state, db, "user_ended", provider)

    current_idx = state["current_gap_index"]
    current_gap = state["critical_gaps"][current_idx]
    current_question = state["current_question"]

    # --- US163: a deferred Tier-1 gate is resolved deterministically (no LLM),
    # never run through the gap response parser / profile updater. ---
    gate_entry = _gate_entry(state, current_gap)
    if gate_entry is not None:
        return await _handle_gate_answer(
            record, state, db, provider, current_idx, gate_entry, message
        )

    # --- US165: a pending Tier-2 conflict is resolved deterministically (no LLM),
    # through the ADR-013 merge — never the gap response parser / profile updater. ---
    conflict_entry = _conflict_entry(state, current_gap)
    if conflict_entry is not None:
        return await _handle_conflict_answer(
            record, state, db, provider, current_idx, conflict_entry, message
        )

    # --- E037 PQ #4: a pending import-time confirmation (N-option ambiguity) is
    # resolved deterministically (no LLM) — the user picks one of the engine's
    # options; never run through the gap response parser / profile updater. ---
    confirmation_entry = _confirmation_entry(state, current_gap)
    if confirmation_entry is not None:
        return await _handle_confirmation_answer(
            record, state, db, provider, current_idx, confirmation_entry, message
        )

    # --- #187: a reconciler-emitted interview-turn confirmation (skill dedupe,
    # entity near-dupe) surfaced on a PREVIOUS turn is resolved deterministically
    # here — NEVER re-run through the reconciler, whose stateless guard would
    # re-emit the identical confirmation and loop forever. Mirrors the
    # deterministic import-time confirmation path above. ---
    pending_conf = state.get("pending_interview_confirmation")
    if pending_conf is not None:
        return await _handle_interview_confirmation_answer(
            record, state, db, provider, current_idx, pending_conf, message, lang
        )

    skipped_set = set(state.get("skipped_gaps", []))
    addressed_set = set(state.get("addressed_gaps", []))
    clusters_by_id = state.get("gap_clusters_by_id") or {}
    current_cluster = clusters_by_id.get(current_gap, {"label": current_gap})
    cluster_label = current_cluster.get("label", current_gap)

    # --- Reconcile this answer into the profile (US182a / ADR-046) ---
    profile_record = await _load_profile(state["profile_id"], db)
    # ADR-063 (#480 PR 2) — the bridge is the interview's intake adapter and
    # writes through `commit_ops` itself, including the trail, the completeness
    # recompute and both clocks this function used to set by hand. It FLUSHES
    # and never commits, so the vault write and the `session.state` writes below
    # stay ONE unit — and this function's own `db.commit()` is load-bearing:
    # dropping it is a silent no-write.
    turn = await reconcile_interview_turn(
        db,
        profile_record=profile_record,
        gap=cluster_label,
        question=current_question,
        answer=message,
        provider=provider,
        session_id=str(record.id),
        lang=lang,
    )
    conflict_summaries = turn.conflict_summaries
    # The reconciled profile feeds the next/follow-up question generator below.
    updated_profile = turn.profile_dict

    # --- ADR-064: resolve a pending denial transfer-probe BEFORE any
    # early-return branch below (hard ceiling, US185 confirmation, ...) so the
    # terminal "second denial -> partial" bump always lands, even when THIS
    # turn's answer is also the one that trips the session's hard ceiling.
    # `probing_concept`/`probing_gap` are popped UNCONDITIONALLY here — a
    # transfer probe is asked at MOST ONCE per concept, ever, regardless of
    # how its answer is later classified (evidence / another denial /
    # unproductive). `probing_concept` (not None) also marks, for the
    # advance-decision below, that THIS turn is itself a probe's answer — so
    # it is never re-evaluated as a fresh probe trigger. ---
    probing_concept: str | None = None
    if state.get("probing_gap") == current_gap:
        probing_concept = state.pop("probing_concept", None)
        state.pop("probing_gap", None)
    # F1 finding-fix (2026-07-29): escalate ONLY when the concept the probe
    # was ABOUT is itself among the concepts denied on THIS turn — not merely
    # "some denial happened" (`turn.denial_recorded` is turn-wide and says
    # nothing about WHICH concept). Matched with the SAME normaliser
    # `record_denials` uses for its own dedupe (`ats_audit._norm`, M3) so a
    # spelling/hyphenation variant can't slip the match either way.
    probe_concept_reaffirmed = probing_concept is not None and any(
        ats_norm(c) == ats_norm(probing_concept) for c in turn.denied_concepts
    )
    if probe_concept_reaffirmed:
        # A second, genuine denial of the PROBED concept: escalate its
        # DURABLE denial_level to "partial" (elicitation exhausted). This is
        # the ONLY thing that changes — the original denial (`denied`, still
        # recorded) is never touched (Global Constraint 3 / ADR-059 /
        # ADR-040): no `DeniedConcept` is deleted, no status flips off
        # `denied`. The op's applier routes to `record_denials(level_only=
        # True)`, whose F1 guarantee is that the escalation can NEVER rewrite
        # `statement` — the candidate's verbatim words from the original denial
        # stay immutable; the level is bookkeeping, not testimony content.
        #
        # ADR-063 (#480 PR 7): a typed `EscalateDenialLevel` act through
        # `commit_ops`. The hand-appended `EnrichmentRecord` the F5 finding-fix
        # added here is DELETED — invariant 3 makes the committer the trail's
        # only author, and two authors is how a write ends up with two records
        # or none. The receipt lands on the committer's receipt-only `denials`
        # list, never on `changes`: an escalation is the candidate ruling MORE
        # out and must not read as "gap addressed" to the gates that read
        # `bool(changes)` (#231/#352).
        #
        # This is a SECOND, ungrounded `commit_ops` call for the turn, beside
        # the grounded one `reconcile_interview_turn` already made (ruling 4:
        # bookkeeping is never testimony, so `TurnGrounding` stays
        # candidate-text-only). Both only flush; this function's own
        # `db.commit()` further down persists them together, so the turn is
        # still one transaction.
        from applire.services.profile.commit import CommitProvenance, commit_ops
        from applire.services.profile.reconcile.ops import EscalateDenialLevel

        await commit_ops(
            db,
            [EscalateDenialLevel(concept=probing_concept)],
            CommitProvenance(
                source="interview",
                intake="denial_probe_escalation",
                session_id=str(record.id),
                actor="candidate",
            ),
            record=profile_record,
            grounding=None,
            snapshot=None,
        )
        updated_profile = profile_record.profile_json

    # Increment questions_asked
    questions_asked = state.get("questions_asked", 1) + 1
    state["questions_asked"] = questions_asked
    record.questions_asked = questions_asked

    # Deterministic gap-progress (US182a): a profile mutation means the answer
    # addressed the gap. "declined" is already handled upstream by
    # is_termination_signal, so an answer that changes nothing -> follow up once.
    addressed = turn.addressed
    # #259 sufficiency criterion (b): an explicit denial (#231) is a TERMINAL
    # answer — never re-asked. F8 (interview_bridge.py) deliberately keeps
    # `addressed` False on a denial-only turn (a denial must never read as a
    # CONFIRMED strength / trigger the ledger UPGRADE below), but that turn
    # still resolves the gap for advance purposes — it just falls into a
    # separate OR branch instead of widening `addressed` itself, so the F8
    # ledger-upgrade guard is untouched by this. #352 keeps that separation
    # exactly: the seam below runs on `addressed or denial_recorded`, and
    # `addressed` alone is what it passes as `upgrade=`.
    denial_recorded = turn.denial_recorded

    # --- #188 / #352: the ledger polarity seam. A turn that ADDRESSED the
    # current gap deterministically upgrades the matching keyword_ledger entry
    # on the persisted GapAnalysis row IN PLACE, so a confirmed strength stops
    # reading as an honest gap in the CV and cover letter (both read that one
    # row). `addressed` is exactly `bool(applied.changes)` (interview_bridge)
    # and still gates the UPGRADE — passed as `upgrade=`, so a turn that
    # touched no field still cannot promote anything.
    #
    # #352 widened WHEN the seam runs, not what it may write. Calling it only
    # `if addressed:` also gated the ADR-059 denial floor, and a retraction
    # ("correction — I never actually owned that") is precisely the turn that
    # produces no ops. So the floor could stop an upgrade in flight and never
    # reverse one, and a stale `claimable` row outlived the candidate taking
    # the claim back. ADR-059 clause 3: polarity at EVERY ledger write seam.
    #
    # ADR-089 clause 2: the seam runs BEFORE the hard-ceiling check now. Before
    # it, the ceiling returned first — so a Gap-Click turn (hard_ceiling=1)
    # never reached the seam at all, and neither did the turn that tripped a
    # full interview's budget. The per-gap record below classifies the
    # cluster's members against the ledger as this seam leaves it, so every
    # answered turn must pass through it first.
    #
    # Placed BEFORE the ADR-064 transfer probe, which `return`s: a denial that
    # triggers a probe must still reverse on its own turn, not a turn later.
    # A no-op for guided/Mode-B (no ledger) and for clusters whose concepts
    # don't normalize-match any ledger entry. The same turn's single commit
    # persists it. ---
    if addressed or denial_recorded:
        await _upgrade_ledger_for_addressed_gap(
            state, current_gap, message, db, upgrade=addressed
        )

    # --- ADR-089 clauses 2/3: the per-gap record. Classify the cluster's open
    # members as facts against the ledger the seam just wrote, and charge the
    # answered turn to the cluster's cross-session budget — flushed, persisted
    # by this turn's one commit. `None` for a turn that answered no recorded
    # cluster (MODE B, a legacy session): those keep the pre-ADR-089 path. ---
    recorded_cluster, open_members = await _record_cluster_turn(
        record, state, db, current_gap=current_gap, updated_profile=updated_profile
    )
    cluster_coverage = _coverage_of(recorded_cluster)

    # --- Hard ceiling check ---
    if questions_asked >= state["hard_ceiling"]:
        state["addressed_gaps"] = state.get("addressed_gaps", []) + [current_gap]
        # A session that hits its ceiling completes here, BEFORE the US185
        # confirmation-surfacing branch below — so carry any reconciler ambiguity
        # into the completion response instead of silently dropping it.
        #
        # #669 residual (2026-09-17 close-out) — `lang` (resolved once above,
        # for this whole turn) was omitted here, so `_to_confirmation_prompts`
        # silently fell back to its `"en"` default regardless of the session's
        # actual language. Pass it through, same as `_ask_confirmation`'s call.
        return await _complete_session(
            record, state, db, "max_questions_reached", provider, profile_record,
            pending_confirmations=_to_confirmation_prompts(turn.pending_confirmations, lang)
            if turn.pending_confirmations
            else None,
            conflict_summaries=conflict_summaries or None,
            changes_applied=turn.addressed,
            denial_recorded=turn.denial_recorded,
            cluster_coverage=cluster_coverage,
        )

    # #187 — consume the one-shot resolving flag BEFORE the re-ask check below.
    # The primary resolution path is the deterministic handler above; this flag is
    # the ordering backstop that guarantees a re-emitted identical confirmation can
    # never re-loop (the flag was previously popped AFTER the re-ask, so the
    # advance logic was unreachable whenever the reconciler re-emitted a
    # confirmation — the loop).
    resolving_confirmation = state.pop("resolving_confirmation", False)

    # --- US185: an unresolved ambiguity becomes a targeted confirmation question.
    # The reconciler never guesses entity identity (synonym role, project-vs-
    # position, DE<->EN employer); it asks. Surface that before advancing —
    # unless this turn is itself resolving a prior confirmation (#187). ---
    if turn.pending_confirmations and not resolving_confirmation:
        return _with_coverage(
            await _ask_confirmation(record, state, db, turn, current_gap, current_idx),
            cluster_coverage,
        )

    # --- Advance decision ---
    # The per-gap counter, read AFTER the record: `_record_cluster_turn` keeps
    # it equal to the cluster's cross-session `outcome.asked` (ADR-089 clause
    # 1), so every `questions_for_gap < per_gap` gate below spends the shared
    # budget, not a per-session one.
    questions_for_gap = state.get("questions_per_gap", {}).get(current_gap, 1)

    # --- ADR-064: the denial transfer probe. A DIRECT-level denial of a
    # JD-critical concept gets exactly ONE follow-up aimed at the broader
    # skill area instead of advancing immediately — but only on a genuine
    # denial-only turn (`not addressed`; a turn that also produced real
    # profile content elsewhere keeps advancing exactly as today), never on
    # the probe's OWN answer turn (`probing_concept is None` — that turn was
    # already resolved above, before the hard-ceiling check), never past the
    # per-gap retry budget, and never when a US185 confirmation is already
    # owed. `_select_denial_probe_concept` is the ONE deterministic (pure
    # Python) trigger; only the follow-up's WORDING is the model's (Task 3).
    # ADR-089 clause 2 precedence: probe > partial coverage > no change. ---
    if (
        not addressed
        and denial_recorded
        and probing_concept is None
        and not resolving_confirmation
        and questions_for_gap < INTERVIEW_MAX_QUESTIONS_PER_GAP
    ):
        probe_concept = await _select_denial_probe_concept(
            state, turn, updated_profile, db, current_gap
        )
        if probe_concept is not None:
            return _with_coverage(
                await _ask_denial_probe(
                    record, state, db, provider, current_gap, current_idx,
                    probe_concept, updated_profile, turn, questions_for_gap, lang,
                    profile_record,
                ),
                cluster_coverage,
            )

    # --- ADR-089 clause 2 (an exception under ADR-058 clause 4, bounded): the
    # partial-coverage follow-up. The answer changed the vault (`addressed`),
    # yet members of the cluster are still OPEN on the record — a `partial`
    # row, a #260 liability without its story, a requirement the answer never
    # named — and the cluster has budget left: ask ONE follow-up aimed at
    # exactly those members. Whether to ask is these facts; how to phrase it
    # is the model's. A turn with no record (MODE B, legacy) never gets here. ---
    if (
        recorded_cluster is not None
        and addressed
        and open_members
        and not resolving_confirmation
        and gap_coverage.remaining_budget(recorded_cluster, INTERVIEW_MAX_QUESTIONS_PER_GAP) > 0
    ):
        return await _ask_partial_coverage_follow_up(
            record, state, db, provider, current_gap, current_idx, open_members,
            updated_profile, turn, questions_for_gap, lang, cluster_coverage,
        )

    if (
        addressed
        or denial_recorded
        or resolving_confirmation
        or questions_for_gap >= INTERVIEW_MAX_QUESTIONS_PER_GAP
    ):
        # Advance to next gap
        state["addressed_gaps"] = state.get("addressed_gaps", []) + [current_gap]
        skipped_set_updated = set(state.get("skipped_gaps", []))
        next_index = _next_valid_index(
            state["critical_gaps"], current_idx + 1, skipped_set_updated
        )
        state["current_gap_index"] = next_index
        gaps_remaining = _count_remaining(
            state["critical_gaps"], next_index, skipped_set_updated
        )

        # Gap exhaustion check — #259: named sufficiency predicate (every gap
        # from here on is addressed, denied, or triaged as a true gap/skipped).
        # Same arithmetic _count_remaining already computed above; naming it
        # makes "termination = sufficiency OR budget OR user-done" an explicit,
        # independently testable seam rather than an implicit `<= 0` check.
        if is_interview_sufficient(state["critical_gaps"], next_index, skipped_set_updated):
            # #380: the terminal turn's denial must reach the caller ON this
            # response (ADR-059 — the flag is the honest status, and the
            # captured 2026-08-15 instance completed exactly here). The
            # hard-ceiling twin above already threads it.
            #
            # ADR-089 — a micro-session now completes HERE (its ceiling no
            # longer ends it on the first answer), so the single-turn caller's
            # `changes_applied` (resolve_gap's addressed/no_change split) and
            # the reconciler's pending conflicts travel on this completion too.
            return await _complete_session(
                record, state, db, "gaps_resolved", provider, profile_record,
                conflict_summaries=conflict_summaries or None,
                changes_applied=turn.addressed,
                denial_recorded=turn.denial_recorded,
                cluster_coverage=cluster_coverage,
            )

        # Generate next question
        next_gap = state["critical_gaps"][next_index]
        next_category = (state.get("gap_categories") or {}).get(next_gap)
        job_context: dict | None = None
        if state.get("mode") == "guided":
            job_context = await _load_job_context(state["job_id"], db)

        next_q_data = await _cluster_question(
            state,
            updated_profile,
            provider,
            db,
            session_id=str(record.id),
            gap_category=next_category,
            job_context=job_context,
            lang=lang,
        )
        next_question = next_q_data["question"]
        next_choices = next_q_data["choices"]
        state["current_question"] = next_question
        state["current_choices"] = next_choices
        state["messages"].append({"role": "assistant", "content": next_question})
        record.state = state
        record.updated_at = datetime.now(timezone.utc)
        # ONE commit per turn (#179): the reconciler's profile write (~line 1164)
        # and this transcript write share the turn's transaction. Never commit
        # between them — an early commit would persist the profile while a failed
        # question call rolls the transcript back (a real partial commit).
        await db.commit()

        return SessionMessageResponse(
            complete=False,
            question=next_question,
            gaps_remaining=gaps_remaining,
            pending_conflicts=conflict_summaries if conflict_summaries else None,
            choices=next_choices,
            current_gap_id=_current_gap_id(state),
            addressed_gap_ids=list(state.get("addressed_gaps", [])),
            # #380: every response built from a reconciled turn carries the
            # turn's fact — False is "no denial this turn", None is reserved
            # for responses with no reconciled turn behind them.
            denial_recorded=turn.denial_recorded,
            # ADR-089 — the same holds for `changes_applied` now that a turn
            # may be followed by a follow-up rather than a completion: the
            # agent door's addressed/partly_covered/no_change split reads it.
            changes_applied=turn.addressed,
            cluster_coverage=cluster_coverage,
        )

    else:
        # Follow-up: stay on current gap
        qpg = dict(state.get("questions_per_gap", {}))
        qpg[current_gap] = questions_for_gap + 1
        state["questions_per_gap"] = qpg

        # #301: the hint is prompt text the follow-up prompt tells the model to
        # LEAD with, so it must carry the gap's label, never its internal id
        # (`cluster-data-modeling` was read as ML clustering — see
        # interview_graph.gap_display_label for the run-7 evidence).
        follow_up_hint = (
            "ask for a more specific or concrete example related to "
            f"{gap_display_label(state, current_gap)}"
        )
        gap_category = (state.get("gap_categories") or {}).get(current_gap)

        follow_up_data = await question_generator_with_profile(
            state,
            updated_profile,
            provider,
            gap_category=gap_category,
            follow_up_hint=follow_up_hint,
            lang=lang,
        )
        follow_up_question = follow_up_data["question"]
        state["current_question"] = follow_up_question
        state["current_choices"] = None
        state["messages"].append({"role": "assistant", "content": follow_up_question})
        record.state = state
        record.updated_at = datetime.now(timezone.utc)
        await db.commit()

        gaps_remaining = _count_remaining(
            state["critical_gaps"],
            current_idx,
            set(state.get("skipped_gaps", [])),
        )

        return SessionMessageResponse(
            complete=False,
            question=follow_up_question,
            gaps_remaining=gaps_remaining,
            pending_conflicts=conflict_summaries if conflict_summaries else None,
            choices=None,
            current_gap_id=_current_gap_id(state),
            addressed_gap_ids=list(state.get("addressed_gaps", [])),
            denial_recorded=turn.denial_recorded,  # #380
            changes_applied=turn.addressed,  # ADR-089
            cluster_coverage=cluster_coverage,
        )


async def _ask_partial_coverage_follow_up(
    record: InterviewSession,
    state: InterviewState,
    db: AsyncSession,
    provider: LLMProvider,
    current_gap: str,
    current_idx: int,
    open_members: list[str],
    updated_profile: dict,
    turn,
    questions_for_gap: int,
    lang: str,
    cluster_coverage: ClusterCoverage | None,
) -> SessionMessageResponse:
    """ADR-089 clause 2 — the ONE follow-up a partially covering answer earns.

    Spends the cluster's next budget slot exactly like the no-change retry
    (``questions_per_gap`` + 1; the answer to it is charged on the record by
    its own turn). The question is drafted by the MODE A generator with the
    cluster narrowed to its open members and a follow-up focus naming them
    (``follow_up_focus``) — so the choices keep the #110/ADR-062 grounding
    guard and the ADR-064 coverage rules, and the US265 nudge stays off (the
    cluster's opening question already had it).
    """
    qpg = dict(state.get("questions_per_gap", {}))
    qpg[current_gap] = questions_for_gap + 1
    state["questions_per_gap"] = qpg
    gap_category = (state.get("gap_categories") or {}).get(current_gap)
    # The follow-up is ABOUT the open members: the session's copy of the
    # cluster narrows to them, so the question's "Constituent gaps", the #188
    # seam and the probe selection of the follow-up's own answer all read the
    # same set the record lists as open.
    clusters_by_id = dict(state.get("gap_clusters_by_id") or {})
    if isinstance(clusters_by_id.get(current_gap), dict):
        clusters_by_id[current_gap] = {**clusters_by_id[current_gap], "gaps": list(open_members)}
        state["gap_clusters_by_id"] = clusters_by_id

    q_data = await _cluster_question(
        state,
        updated_profile,
        provider,
        db,
        session_id=str(record.id),
        gap_category=gap_category,
        lang=lang,
        follow_up_focus=list(open_members),
    )
    question = q_data["question"]
    choices = q_data.get("choices")
    state["current_question"] = question
    state["current_choices"] = choices
    state["messages"].append({"role": "assistant", "content": question})
    record.state = state
    record.updated_at = datetime.now(timezone.utc)
    await db.commit()  # ONE commit per turn (#179)

    gaps_remaining = _count_remaining(
        state["critical_gaps"], current_idx, set(state.get("skipped_gaps", []))
    )
    return SessionMessageResponse(
        complete=False,
        question=question,
        gaps_remaining=gaps_remaining,
        pending_conflicts=turn.conflict_summaries if turn.conflict_summaries else None,
        choices=choices,
        current_gap_id=_current_gap_id(state),
        addressed_gap_ids=list(state.get("addressed_gaps", [])),
        denial_recorded=turn.denial_recorded,
        changes_applied=turn.addressed,
        cluster_coverage=cluster_coverage,
    )


# ---------------------------------------------------------------------------
# GET /api/session/{session_id}
# ---------------------------------------------------------------------------


async def get_session_state(
    session_id: uuid.UUID,
    db: AsyncSession,
) -> SessionStateResponse:
    session_result = await db.execute(
        select(InterviewSession).where(
            InterviewSession.id == session_id,
            InterviewSession.deleted_at.is_(None),
        )
    )
    record = session_result.scalar_one_or_none()
    if record is None:
        raise LookupError(f"Session {session_id} not found")

    state: InterviewState = dict(record.state)
    profile_record = await _load_profile(state["profile_id"], db)
    profile_data = MasterProfileData.model_validate(profile_record.profile_json)
    completeness = profile_data.calculate_completeness()

    current_question: str | None = None
    gaps_remaining = 0
    status_str: str = record.status

    if record.status == "active":
        current_question = state.get("current_question") or None
        idx = state.get("current_gap_index", 0)
        skipped = set(state.get("skipped_gaps", []))
        gaps_remaining = _count_remaining(
            state.get("critical_gaps", []),
            idx,
            skipped,
        )

    # Treat expired sessions (past expires_at) as "expired" status. Coerce a
    # naive timestamp (some backends/drivers drop tzinfo) to UTC before comparing.
    expires_at = record.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if (
        expires_at is not None
        and datetime.now(timezone.utc) > expires_at
        and record.status != "complete"
    ):
        status_str = "expired"

    return SessionStateResponse(
        session_id=record.id,
        job_id=record.job_analysis_id,
        mode=record.mode,
        status=status_str,
        questions_asked=record.questions_asked,
        hard_ceiling=record.hard_ceiling,
        current_question=current_question,
        gaps_remaining=gaps_remaining,
        completeness_score=completeness,
        created_at=record.created_at,
        updated_at=record.updated_at,
        expires_at=record.expires_at,
    )


# ---------------------------------------------------------------------------
# Completion helper
# ---------------------------------------------------------------------------


async def _complete_session(
    record: InterviewSession,
    state: InterviewState,
    db: AsyncSession,
    reason: str,
    provider: LLMProvider,
    profile_record: MasterProfile | None = None,
    pending_confirmations: list | None = None,
    conflict_summaries: list | None = None,
    changes_applied: bool | None = None,
    denial_recorded: bool | None = None,
    cluster_coverage: ClusterCoverage | None = None,
) -> SessionMessageResponse:
    record.state = state
    record.status = "complete"
    record.updated_at = datetime.now(timezone.utc)
    await db.commit()

    # Capture scalars off `record` BEFORE any further commit/rollback below —
    # a rollback (e.g. the IntegrityError branch inside analyze_gaps' idempotency
    # race handling) unconditionally expires ORM state regardless of
    # expire_on_commit, and a later lazy-load on an expired attribute inside an
    # async session raises (sync IO in an async context). Mirrors the
    # capture-ids-before-commit lesson from #122/#207.
    session_id = record.id
    job_analysis_id = record.job_analysis_id
    fallback_questions_asked = record.questions_asked

    # Also read off profile_record's completeness NOW, before analyze_gaps runs
    # below — same expiry hazard as `record` above.
    # issue #245 — this must never propagate: `record.status` is ALREADY
    # committed 'complete' above, so a scoring failure here must not turn an
    # already-successful completion into a 500 that strands the frontend on
    # the last question with no way to learn the session actually finished
    # (the DB says complete; the response the caller sees must agree). Same
    # best-effort contract as advance_flow_on_interview_complete/analyze_gaps
    # immediately below — mirrors their try/except, not new behaviour.
    completeness = 0.0
    if profile_record is not None:
        try:
            profile_data = MasterProfileData.model_validate(profile_record.profile_json)
            completeness = profile_data.calculate_completeness()
        except Exception:
            logger.warning(
                "Completeness scoring failed on interview completion for session %s; "
                "reporting 0.0 (recoverable — profile health view recomputes it)",
                record.id,
                exc_info=True,
            )

    # Issue #68: completing the interview must move the flow off the 'interview'
    # step, else resuming from the dashboard re-opens it with a fresh session.
    # Lazy import to avoid a session<->flow import cycle (mirrors the lazy-import
    # pattern in flow.orchestrator.advance_flow). Best-effort: the interview is
    # already committed complete above; advancing the flow is a recoverable
    # convenience (the Generate-CV button re-advances idempotently), so a failure
    # here must not break the completion response.
    from applire.services.flow.orchestrator import advance_flow_on_interview_complete
    try:
        await advance_flow_on_interview_complete(session_id, db)
    except Exception:
        logger.warning(
            "Flow advance after interview completion failed for session %s; "
            "flow left on 'interview' step (recoverable via Generate CV)",
            session_id,
            exc_info=True,
        )

    # #240: an interview that closed gap clusters must refresh the match score
    # that reaches the gaps page / CV workspace — without this, FlowSession.
    # gap_analysis_id stays pointed at the pre-interview row forever (analyze_gaps
    # only repoints the flow on ITS OWN recompute paths — /gaps/refresh,
    # gap-click — never on interview completion, which advanced the flow's step
    # but never touched the gap-analysis FK). Runs for every completion reason
    # (gaps_resolved, user_ended, max_questions_reached — the targeted
    # micro-session resolve_gap rides) so every way an interview ends refreshes
    # the score.
    #
    # ADR-089 clause 5 — the recompute is ANSWER-DRIVEN: `answer_scope` names
    # what this session touched (the clusters it worked, and every answer it
    # received — a self-correction outside the answered cluster must still be
    # able to lower that requirement). Outside the touched set a downward move
    # is replaced by the previous row unless it is a denial; the denial floor
    # and the vault floor then run on the merged ledger, so a new denial always
    # lowers the score (ruling B-1's purpose, kept). Replaces the whole-slice
    # `clamp_to_previous` (retired with `published_score_slice`). Idempotent
    # per (job, profile-fingerprint) — if the profile didn't change this turn,
    # analyze_gaps cheaply reuses the existing row instead of re-running the LLM.
    # Best-effort: the interview is already committed complete above; a failure
    # here must not break the completion response — the next /gaps/refresh or
    # gap-click recomputes it.
    answer_scope = _answer_scope(state)
    if job_analysis_id is not None:
        try:
            await analyze_gaps(job_analysis_id, db, provider, answer_scope=answer_scope)
        except Exception:
            logger.warning(
                "Post-interview gap recompute failed for session %s (job %s); "
                "match score left on the pre-interview analysis (recoverable "
                "via gaps/refresh)",
                session_id,
                job_analysis_id,
                exc_info=True,
            )

    # ADR-089 — the coverage this turn wrote, re-read after the recompute: the
    # carried cluster (same id, clause 4) is re-split against the NEW ledger,
    # and that row is what the gaps page and `analyze_gaps` show next. Kept as
    # the turn wrote it when the recompute did not carry the cluster.
    if cluster_coverage is not None and job_analysis_id is not None:
        try:
            refreshed = _coverage_of(
                await _latest_cluster(job_analysis_id, cluster_coverage.cluster_id, db)
            )
            if refreshed is not None:
                cluster_coverage = refreshed
        except Exception:
            logger.warning(
                "Reading the recomputed cluster record failed for session %s; "
                "reporting the turn's own record",
                session_id,
                exc_info=True,
            )

    addressed = state.get("addressed_gaps", [])
    all_gaps = state.get("critical_gaps", [])
    idx = state.get("current_gap_index", 0)
    skipped = set(state.get("skipped_gaps", []))
    addressed_set = set(addressed)
    unresolved = (
        [g for g in all_gaps[idx:] if g not in skipped and g not in addressed_set]
        if reason != "gaps_resolved"
        else []
    )

    return SessionMessageResponse(
        complete=True,
        reason=reason,
        questions_asked=state.get("questions_asked", fallback_questions_asked),
        gaps_resolved=len(addressed),
        gaps_unresolved=unresolved,
        completeness_score=completeness,
        # A turn that reconciled just before completing (the ceiling-hit path,
        # e.g. a targeted micro-session) may carry an ambiguity the reconciler
        # refused to guess — surface it on completion rather than dropping it,
        # since the confirmation-surfacing branch is skipped by an early return.
        pending_confirmations=pending_confirmations or None,
        pending_conflicts=conflict_summaries or None,
        changes_applied=changes_applied,
        denial_recorded=denial_recorded,
        cluster_coverage=cluster_coverage,
    )


def _answer_scope(state: InterviewState) -> AnswerScope:
    """ADR-089 clause 5 — what this session may treat as touched.

    ``cluster_ids`` — the clusters the session WORKED (an answered turn is on
    the record: ``cluster_turns``), in order. ``answers`` — every candidate
    answer of the session; a control word that ended it ("done") is not
    testimony and is left out.
    """
    worked: list[str] = []
    for turn in state.get("cluster_turns") or []:
        cid = (turn or {}).get("cluster_id") if isinstance(turn, dict) else None
        if cid and cid not in worked:
            worked.append(str(cid))
    answers = [
        str(m.get("content") or "").strip()
        for m in state.get("messages") or []
        if isinstance(m, dict) and m.get("role") == "user"
    ]
    answers = [a for a in answers if a and not is_termination_signal(a)]
    return AnswerScope(cluster_ids=tuple(worked), answers=tuple(answers))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auto_detect_mode(profile_record: MasterProfile | None) -> str:
    if profile_record is None:
        return "guided"
    profile_data = MasterProfileData.model_validate(profile_record.profile_json)
    score = profile_data.calculate_completeness()
    return "targeted" if score >= MODE_B_COMPLETENESS_THRESHOLD else "guided"


def _estimated_questions(mode: str, hard_ceiling: int | None = None) -> int:
    """The soft "about this many questions" midpoint shown before a session has
    a real budget to report.

    ADR-080 (#646): the upper end of the range is this SESSION's derived budget
    when the caller has one, not the operator cap. Since ADR-080 the cap
    defaults to 30 and no longer describes any particular interview, so
    midpointing against it would tell a 3-cluster candidate to expect ~16
    questions for an interview that will ask at most 6. Callers with a budget
    in hand pass it; `_resumed_response` and any caller without one fall back to
    the cap, which is the pre-ADR-080 behaviour and only ever a fallback (the
    frontend prefers `hard_ceiling` over this value — issue #245).
    """
    if mode == "guided":
        upper = hard_ceiling if hard_ceiling else settings.interview_max_questions_guided
        return (INTERVIEW_TARGET_MIN_GUIDED + upper) // 2
    upper = hard_ceiling if hard_ceiling else settings.interview_max_questions_targeted
    return (INTERVIEW_TARGET_MIN_TARGETED + upper) // 2


def _build_state(
    *,
    mode: str,
    job_id: uuid.UUID | None,
    gap_analysis_id: uuid.UUID | None,
    profile_id: uuid.UUID,
    critical_gaps: list[str],
    gap_categories: dict,
    gap_clusters_by_id: dict,
    current_question: str,
    hard_ceiling: int,
    micro_session: bool = False,
) -> InterviewState:
    return {
        "mode": mode,
        "job_id": str(job_id) if job_id else None,
        "gap_analysis_id": str(gap_analysis_id) if gap_analysis_id else None,
        "profile_id": str(profile_id),
        "critical_gaps": critical_gaps,
        "gap_categories": gap_categories,
        "gap_clusters_by_id": gap_clusters_by_id,
        "addressed_gaps": [],
        "current_gap_index": 0,
        "current_question": current_question,
        "current_choices": None,
        "messages": [],
        "questions_asked": 0,
        "hard_ceiling": hard_ceiling,
        "questions_per_gap": {},
        "skipped_gaps": [],
        "full_gaps": [],
        "na_gaps": [],
        # #627 — the authoritative "is this a Gap-Click micro-session" marker
        # every NEW session now stamps explicitly (defaults False for every
        # MODE A/B full session; _create_micro_session passes True). See
        # is_micro_session() — hard_ceiling alone is operator-overridable,
        # and mode alone can't distinguish a micro-session from a full MODE A
        # targeted interview (both persist mode="targeted").
        "micro_session": micro_session,
    }


def _make_session_record(
    *,
    job_id: uuid.UUID,
    gap_analysis_id: uuid.UUID | None,
    profile_id: uuid.UUID,
    mode: str,
    status: str,
    state: InterviewState,
    hard_ceiling: int,
    questions_asked: int = 0,
) -> InterviewSession:
    now = datetime.now(timezone.utc)
    return InterviewSession(
        job_analysis_id=job_id,
        gap_analysis_id=gap_analysis_id,
        profile_id=profile_id,
        mode=mode,
        status=status,
        state=state,
        hard_ceiling=hard_ceiling,
        questions_asked=questions_asked,
        expires_at=now + timedelta(days=_SESSION_TTL_DAYS),
    )


async def _get_active_session(
    job_id: uuid.UUID, db: AsyncSession
) -> InterviewSession | None:
    # Newest-first + first(): pre-migration databases may still hold
    # duplicate active sessions; never raise MultipleResultsFound here.
    result = await db.execute(
        select(InterviewSession)
        .where(
            InterviewSession.job_analysis_id == job_id,
            InterviewSession.status == "active",
            InterviewSession.deleted_at.is_(None),
        )
        .order_by(InterviewSession.created_at.desc())
    )
    return result.scalars().first()


async def _load_profile(profile_id: str, db: AsyncSession) -> MasterProfile:
    result = await db.execute(
        select(MasterProfile).where(MasterProfile.id == uuid.UUID(profile_id))
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise LookupError(f"Profile {profile_id} not found")
    return record


async def _load_job_context(job_id: str | None, db: AsyncSession) -> dict:
    if not job_id:
        return {}
    result = await db.execute(
        select(JobAnalysis).where(JobAnalysis.id == uuid.UUID(job_id))
    )
    job = result.scalar_one_or_none()
    if job is None:
        return {}
    return {
        "role_title": job.role_title or "",
        "seniority_level": job.seniority_level or "",
    }


def _next_valid_index(
    critical_gaps: list[str],
    from_index: int,
    skipped_gaps: set[str],
) -> int:
    """Return the first index >= from_index whose gap is not in skipped_gaps.

    Returns len(critical_gaps) if all remaining gaps are skipped (signals exhaustion).
    """
    idx = from_index
    while idx < len(critical_gaps) and critical_gaps[idx] in skipped_gaps:
        idx += 1
    return idx


def _count_remaining(
    critical_gaps: list[str],
    from_index: int,
    skipped_gaps: set[str],
) -> int:
    """Count non-skipped gaps from from_index onwards (inclusive)."""
    return sum(
        1 for g in critical_gaps[from_index:]
        if g not in skipped_gaps
    )


def _current_gap_id(state: InterviewState) -> str | None:
    """The critical_gaps entry the session is currently asking about, if any.

    issue #241 item 1 — the honest anchor for the frontend split-screen cluster
    tracker. In MODE A this entry IS the gap-cluster id (gap_detector() builds
    critical_gaps straight from gap_analysis.gap_clusters[].id), so the value
    returned here can be matched 1:1 against the ids in GET /api/job/{id}/gaps.
    Returns None when the index is out of range (defensive — should not happen
    on a non-complete turn).
    """
    gaps = state.get("critical_gaps") or []
    idx = state.get("current_gap_index", 0)
    if 0 <= idx < len(gaps):
        return gaps[idx]
    return None

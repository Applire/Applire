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

# backend/applire/services/cv_assist.py
"""Kaile micro-session assist service (Sprint 10, ADR-004 micro-session concept).

Two-step LLM interaction:
  POST  assist → generate one focused question, store micro-session
  PATCH assist → submit answer, generate suggested section text

Sessions are kept in a module-level dict (_sessions) — per-process, lightweight.

**M5.7.1 / ADR-040 amendment 2026-09-13 (ruling W-2) — this surface is now inside the
truthfulness contract.** It is the oldest LLM *writing* surface in the product and was
outside every control: ADR-021 never reviewed it, ADR-040 never named it, and its three
prompts were inline f-strings outside ``prompts/``. What it produces is prose the
candidate pastes into a CV section, which is exactly the "externally-harmful-if-wrong
content" ADR-040 clause 1 governs. Both content-producing calls now run the Oracle triage
over their own output before it is shown; see :func:`_ground_suggestion` for the evidence
set, the withhold polarity and why the question call is not triaged.
"""
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from applire.models.cv import GeneratedCV
from applire.models.flow import FlowSession
from applire.models.gap import GapAnalysis
from applire.models.job import JobAnalysis
from applire.prompts.cv_assist import (
    ASSIST_QUESTION_SYSTEM_PROMPT,
    ASSIST_REWRITE_SYSTEM_PROMPT,
    ASSIST_SUGGESTION_SYSTEM_PROMPT,
    build_assist_question_prompt,
    build_assist_rewrite_prompt,
    build_assist_suggestion_prompt,
)
from applire.providers.llm.base import LLMProvider
from applire.schemas.cv_sections import (
    AssistAnswerResponse,
    AssistStartResponse,
    ContentSnapshot,
    RewriteResponse,
)

logger = logging.getLogger(__name__)

#: Verdicts that WITHHOLD a sentence. Deliberately the three ADVERSE ones only:
#: ``unverifiable`` means the Oracle could not decide, and ADR-068's fail-safe polarity
#: is permissive — withholding on it would make the feature refuse most true content a
#: deterministic matcher simply cannot see. ``not_applicable`` is an explicit exemption.
_WITHHOLD_VERDICTS = frozenset({"inflated", "misattributed", "unbacked"})

# ---------------------------------------------------------------------------
# Module-level session store (per-process, no DB)
# ---------------------------------------------------------------------------

_sessions: dict[str, dict] = {}

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def start_assist_session(
    cv_id: uuid.UUID,
    section_id: str,
    gap_id: str,
    provider: LLMProvider,
    db: AsyncSession,
) -> AssistStartResponse:
    """Generate one focused question for a gap in a CV section.

    Raises:
        LookupError: CV not found.
        ValueError: gap_id not found in gap_analysis, or section_id unknown.
    """
    section_label, section_content = await _load_cv_and_section(cv_id, section_id, db)

    if not await _gap_exists(cv_id, gap_id, db):
        raise ValueError(f"gap_id {gap_id!r} not found in gap_analysis for CV {cv_id}")

    # NOT triaged (ADR-040 amendment clause 3): this call produces a QUESTION for the
    # candidate, not CV content. Nothing in it can leave the system as a truth claim.
    question = await provider.acomplete(
        build_assist_question_prompt(section_label, section_content, gap_id),
        system=ASSIST_QUESTION_SYSTEM_PROMPT,
        temperature=0.4,
        max_tokens=512,  # chrome ceiling, raised for thinking-model headroom (F-B)
        disable_thinking=True,
    )
    question = question.strip()

    session_id = str(uuid.uuid4())
    _sessions[session_id] = {
        "cv_id": str(cv_id),
        "section_id": section_id,
        "gap_id": gap_id,
        "section_label": section_label,
        "section_content": section_content,
        "question": question,
    }

    return AssistStartResponse(session_id=session_id, question=question)


async def submit_assist_answer(
    cv_id: uuid.UUID,
    section_id: str,
    session_id: str,
    answer: str,
    provider: LLMProvider,
    db: AsyncSession,
) -> AssistAnswerResponse:
    """Generate suggested section text from user's answer.

    Raises:
        ValueError: session_id not found or cv_id/section_id mismatch.
    """
    session = _sessions.get(session_id)
    if not session or session["cv_id"] != str(cv_id) or session["section_id"] != section_id:
        raise ValueError(f"Invalid session_id: {session_id!r}")

    suggestion = await provider.acomplete(
        build_assist_suggestion_prompt(
            session["section_label"],
            session["section_content"],
            session["gap_id"],
            answer,
        ),
        system=ASSIST_SUGGESTION_SYSTEM_PROMPT,
        temperature=0.5,
        max_tokens=600,
        disable_thinking=True,  # chrome generation (F-B)
    )

    # The candidate's own ANSWER is part of the evidence set — it is testimony they gave
    # seconds ago, and grounding this suggestion against the vault alone would withhold
    # almost everything the feature exists to produce (ADR-040 amendment clause 1).
    kept, withheld = await _ground_suggestion(
        suggestion,
        db,
        session_evidence=[("session.answer", answer)],
        prior_text=session.get("section_content"),
        gap_ids=[session["gap_id"]],
    )
    return AssistAnswerResponse(suggestion=kept, withheld_count=withheld)


async def rewrite_section(
    cv_id: uuid.UUID,
    section_id: str,
    directions: str,
    gap_ids: list[str],
    provider: LLMProvider,
    db: AsyncSession,
) -> RewriteResponse:
    """Single-turn directed rewrite for a CV section.

    The user provides free-text directions and optional gap IDs.
    Kaile rewrites the section accordingly.

    Raises:
        LookupError: CV not found or has no content snapshot.
        ValueError: section_id is unknown.
    """
    section_label, section_content = await _load_cv_and_section(cv_id, section_id, db)

    # Load job role title for context (best-effort — omitted if no flow found)
    role_title = await _get_role_title(cv_id, db)

    suggestion = await provider.acomplete(
        build_assist_rewrite_prompt(
            section_label, section_content, directions, gap_ids, role_title
        ),
        system=ASSIST_REWRITE_SYSTEM_PROMPT,
        temperature=0.5,
        max_tokens=600,
        disable_thinking=True,  # chrome generation (F-B)
    )

    kept, withheld = await _ground_suggestion(
        suggestion,
        db,
        session_evidence=[("session.directions", directions)],
        prior_text=section_content,
        gap_ids=gap_ids,
    )
    return RewriteResponse(suggestion=kept, withheld_count=withheld)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


async def _load_cv_and_section(
    cv_id: uuid.UUID,
    section_id: str,
    db: AsyncSession,
) -> tuple[str, str]:
    """Return (section_label, section_content) for the given section.

    Raises LookupError if CV not found or section_id unknown.
    """
    result = await db.execute(
        select(GeneratedCV).where(
            GeneratedCV.id == cv_id,
            GeneratedCV.deleted_at.is_(None),
        )
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise LookupError(f"Generated CV {cv_id} not found")

    if not record.content_snapshot:
        raise LookupError(f"CV {cv_id} has no content snapshot — regenerate CV first")

    snapshot = ContentSnapshot.model_validate(record.content_snapshot)
    overrides: dict = record.section_overrides or {}

    if section_id == "introduction":
        content = overrides.get("introduction", snapshot.introduction)
        return "Introduction", content

    if section_id == "skills":
        content = overrides.get("skills", "\n".join(snapshot.skills))
        return "Skills", content

    if section_id.startswith("position::"):
        pos_uuid = section_id[len("position::"):]
        for pos in snapshot.positions:
            if pos.id == pos_uuid:
                sid_key = f"position::{pos.id}"
                content = overrides.get(sid_key, "\n".join(pos.bullets))
                label = f"{pos.title} — {pos.company}"
                return label, content

    raise ValueError(f"Unknown section_id: {section_id!r}")


async def _gap_exists(cv_id: uuid.UUID, gap_id: str, db: AsyncSession) -> bool:
    """Return True if gap_id appears in the gap_analysis linked to this CV."""
    flow_result = await db.execute(
        select(FlowSession).where(
            FlowSession.generated_cv_id == cv_id,
            FlowSession.deleted_at.is_(None),
        ).limit(1)
    )
    flow = flow_result.scalar_one_or_none()
    if not flow or not flow.gap_analysis_id:
        return False

    gap_analysis = await db.get(GapAnalysis, flow.gap_analysis_id)
    if not gap_analysis:
        return False

    all_gaps = list(gap_analysis.category_b) + list(gap_analysis.category_c)
    if gap_id in all_gaps:
        return True
    # #117: hints are ledger-derived — a direct-status entry (claimable, not yet in
    # the document) is a valid assist target even though it is not in category_b/c.
    return any(
        (entry.get("concept") or "") == gap_id
        for entry in (gap_analysis.keyword_ledger or [])
    )


async def _get_role_title(cv_id: uuid.UUID, db: AsyncSession) -> str | None:
    """Return the job role title linked to this CV, or None if not found."""
    flow_result = await db.execute(
        select(FlowSession).where(
            FlowSession.generated_cv_id == cv_id,
            FlowSession.deleted_at.is_(None),
        ).limit(1)
    )
    flow = flow_result.scalar_one_or_none()
    if not flow or not flow.job_id:
        return None

    job = await db.get(JobAnalysis, flow.job_id)
    return job.role_title if job else None


async def _ground_suggestion(
    suggestion: str,
    db: AsyncSession,
    *,
    session_evidence: list[tuple[str, str]],
    prior_text: str | None = None,
    gap_ids: list[str] | None = None,
) -> tuple[str, int]:
    """Withhold the sentences of ``suggestion`` the candidate's own data does not support.

    Returns ``(kept_text, withheld_count)``.

    **The evidence set is named, and it is not the vault alone** (ADR-040 amendment
    2026-09-13 clause 1, ruling W-2). It is

      * the vault (``MasterProfile.profile_json``), plus
      * ``session_evidence`` — the candidate's own fresh input in THIS micro-session (the
        answer they just submitted, or the directions they just gave), plus
      * ``prior_text`` — the CV section as it already stands, because a rewrite that
        preserves a sentence already in the document is not introducing a new claim.

    A vault-only check would withhold essentially every suggestion this feature exists to
    produce: the whole point of the assist is that the candidate is telling us something
    the vault does not yet hold. Telling them a true fact they typed ten seconds ago is
    unsupported is the failure mode `SF-ASSIST.2` records.

    **Withhold polarity.** Only the three ADVERSE verdicts remove a sentence —
    ``inflated``, ``misattributed``, ``unbacked``. ``unverifiable`` means the Oracle could
    not decide and ADR-068's fail-safe polarity is permissive; ``not_applicable`` is an
    explicit exemption. So this can under-withhold and never over-withhold, which is the
    right direction for a control whose false positive costs the candidate a true
    sentence and whose false negative is still caught by the delivery-time audit.

    **Deterministic.** No provider is threaded, so no LLM call is added to an interactive
    chrome-tier surface: the Oracle's deterministic matchers (grounding, numbers, stance,
    attribution) decide, and the bounded judgement seams stay off — the same scoping
    `oracle/selfaudit.py` applies at generation time (ADR-068 clause 7). A judgement seam
    can only ever move a sentence from ``unverifiable`` toward a decision, so leaving it
    off cannot cause a false withhold.

    **Fail-open, always.** Any failure — no profile yet, a malformed profile, an
    exception anywhere in the audit — returns the suggestion unchanged with a withheld
    count of 0 and a logged error. This is a prevention tier, not a gate (ADR-040 clause
    4): a broken check must not take the feature down, and the delivery-time truthfulness
    audit over the persisted ``tailored_data`` is the control that still looks at what
    actually ships.

    **The ADR-059 denial floor, applied before grounding** (ruling D-1, 2026-09-14).
    ``gap_ids`` — the concept(s) this micro-session targets (``submit_assist_answer``'s
    own ``gap_id``, or ``rewrite_section``'s ``gap_ids``) — are checked against the
    vault's ``metadata.denied_concepts`` with :func:`is_denied_concept`, at its EXISTING
    calling convention (a concept label vs. ``denied_concepts[*].concept`` — the same
    check ``keyword_ledger``'s ledger floor already runs, never a new classifier). W-2's
    "fresh input is evidence" stands for a genuinely new fact; it must not let a fresh
    answer to a targeted question silently reverse a denial the SAME interview recorded
    minutes earlier. A hit withholds the WHOLE suggestion (every extracted claim), logged
    with the denied concept and a distinct reason.

    **Measured, honest limit.** This catches a DIRECT/literal concept overlap only. The
    2026-09-13 delivery-run instance it was built to close (gap concept
    "Investitionsverantwortung", denial concept "Investitionsentscheidungen selbst
    treffen") is a semantic paraphrase, not a lexical one — German compound-noun
    morphology means neither concept is a bounded substring of the other, in either
    direction, so :func:`is_denied_concept` does not fire on it (pinned by
    ``test_the_exact_delivery_run_pair_is_the_measured_honest_limit``). Checking the
    denial's free-text ``statement`` instead was measured and rejected: the SAME
    statement's other sentence explicitly AFFIRMS "Budgetverantwortung", which also
    matches via ``is_denied_concept``'s compound-containment branch with no release
    (``corpus=None``) — trading a miss for a new false positive against an affirmed
    fact. Closing the semantic gap needs a bounded judgement seam or a clause-scoped
    corpus, out of this pass's scope (collector line).
    """
    text = (suggestion or "").strip()
    if not text:
        return "", 0
    try:
        from applire.models.profile import MasterProfile
        from applire.services.oracle.audit import verify_claim
        from applire.services.oracle.extract import extract_claims_from_text
        from applire.services.oracle.matchers import build_vault_index, extend_vault_index
        from applire.services.profile.reconcile.stance import is_denied_concept

        result = await db.execute(
            select(MasterProfile)
            .where(MasterProfile.deleted_at.is_(None))
            .order_by(MasterProfile.created_at.desc())
            .limit(1)
        )
        record = result.scalar_one_or_none()
        if record is None:
            # No vault to check against. Everything the candidate says is, trivially,
            # all the evidence there is — withholding here would block the very first
            # thing a new user does.
            return text, 0

        claims = await extract_claims_from_text(text, provider=None)
        if not claims:
            return text, 0

        denied_concepts = (record.profile_json or {}).get("metadata", {}).get(
            "denied_concepts"
        ) or []
        denial_labels = [
            dc.get("concept")
            for dc in denied_concepts
            if isinstance(dc, dict) and dc.get("concept")
        ]
        denied_hit = next(
            (
                gid
                for gid in (gap_ids or [])
                if gid and denial_labels and is_denied_concept(gid, denial_labels)
            ),
            None,
        )
        if denied_hit is not None:
            withheld = [c.text for c in claims]
            kept = ""
            logger.info(
                "CV_ASSIST_WITHHELD_DENIAL concept=%r reason=contradicts_recorded_denial "
                "count=%d",
                denied_hit,
                len(withheld),
            )
            return kept, len(withheld)

        index = build_vault_index(record.profile_json or {})
        evidence = list(session_evidence)
        if prior_text:
            evidence.append(("section.current", prior_text))
        index = extend_vault_index(index, evidence)

        withheld = []
        for claim in claims:
            verdict = await verify_claim(claim, record.profile_json or {}, index=index)
            if getattr(verdict, "verdict", None) in _WITHHOLD_VERDICTS:
                withheld.append(claim.text)
        if not withheld:
            return text, 0

        kept = text
        for sentence in withheld:
            kept = kept.replace(sentence, "")
        kept = " ".join(kept.split())
        logger.info(
            "CV_ASSIST_WITHHELD count=%d verdicts_checked=%d",
            len(withheld),
            len(claims),
        )
        return kept, len(withheld)
    except Exception:  # noqa: BLE001 — prevention tier, never a gate
        logger.error(
            "cv_assist grounding failed; showing the suggestion unchecked "
            "(the delivery-time truthfulness audit remains the gate)",
            exc_info=True,
        )
        return text, 0

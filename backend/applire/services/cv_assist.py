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
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from applire.models.cv import GeneratedCV
from applire.models.flow import FlowSession
from applire.models.gap import GapAnalysis
from applire.models.job import JobAnalysis
from applire.providers.llm.base import LLMProvider
from applire.schemas.cv_sections import (
    AssistAnswerResponse,
    AssistStartResponse,
    ContentSnapshot,
    RewriteResponse,
)

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

    question = await provider.acomplete(
        _question_prompt(section_label, section_content, gap_id),
        system=(
            "Du bist Kaile, ein KI-Karriereassistent. "
            "Deine Aufgabe ist es, dem Nutzer mit einer einzigen präzisen Frage zu helfen, "
            "eine Lücke in seinem Lebenslauf zu schließen."
        ),
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
        _suggestion_prompt(
            session["section_label"],
            session["section_content"],
            session["gap_id"],
            answer,
        ),
        system=(
            "Du bist Kaile, ein KI-Karriereassistent. "
            "Generiere verbesserten Lebenslauf-Text, der natürlich klingt und die "
            "identifizierte Lücke schließt."
        ),
        temperature=0.5,
        max_tokens=600,
        disable_thinking=True,  # chrome generation (F-B)
    )

    return AssistAnswerResponse(suggestion=suggestion.strip())


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
        _rewrite_prompt(section_label, section_content, directions, gap_ids, role_title),
        system=(
            "Du bist Kaile, ein KI-Karriereassistent. "
            "Rewrite the given CV section exactly as directed by the user. "
            "Output only the improved section text — no commentary, no introduction."
        ),
        temperature=0.5,
        max_tokens=600,
        disable_thinking=True,  # chrome generation (F-B)
    )

    return RewriteResponse(suggestion=suggestion.strip())


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


def _question_prompt(section_label: str, section_content: str, gap_id: str) -> str:
    return (
        f"Abschnitt: {section_label}\n"
        f"Aktueller Inhalt:\n{section_content}\n\n"
        f"Identifizierte Lücke: {gap_id}\n\n"
        "Stelle eine einzige, kurze, konkrete Frage auf Deutsch, die dem Nutzer hilft, "
        "Informationen zu liefern, mit denen diese Lücke im Lebenslauf geschlossen werden kann. "
        "Nur die Frage, keine Erklärung."
    )


def _suggestion_prompt(
    section_label: str,
    section_content: str,
    gap_id: str,
    answer: str,
) -> str:
    return (
        f"Abschnitt: {section_label}\n"
        f"Aktueller Inhalt:\n{section_content}\n\n"
        f"Lücke: {gap_id}\n"
        f"Antwort des Nutzers: {answer}\n\n"
        "Generiere einen verbesserten Text für diesen Abschnitt, der die Lücke schließt "
        "und natürlich klingt. Gib nur den verbesserten Text aus, ohne Kommentar oder Einleitung."
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


def _rewrite_prompt(
    section_label: str,
    section_content: str,
    directions: str,
    gap_ids: list[str],
    role_title: str | None,
) -> str:
    lines = [f"Abschnitt: {section_label}"]
    if role_title:
        lines.append(f"Zielrolle: {role_title}")
    lines.append(f"Aktueller Inhalt:\n{section_content}")
    if gap_ids:
        lines.append(f"Zu schließende Lücken: {', '.join(gap_ids)}")
    if directions:
        lines.append(f"Anweisungen des Nutzers: {directions}")
    lines.append(
        "\nSchreibe den Abschnitt neu und berücksichtige dabei die Anweisungen und Lücken. "
        "Gib nur den verbesserten Text aus."
    )
    return "\n".join(lines)

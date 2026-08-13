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

"""Cover letter generation service — Sprint 25

Mirrors services/cv.py:
  generate_cover_letter:
    Create GeneratedCoverLetter record with status='pending'.
    Update FlowSession.generated_cover_letter_id.
    Enqueue _render_cover_letter_background via BackgroundTasks.
    Return immediately — caller polls GET /api/cover-letter/{id}/status.

  _render_cover_letter_background:
    LLM + Jinja2 + Playwright — runs outside request lifecycle.
    Updates status: pending → generating → ready | failed.
    Creates its own DB session.
"""

import copy
import json
import logging
import re
import uuid
from datetime import date, timezone
from pathlib import Path

from fastapi import BackgroundTasks
from applire.templates.filters import build_template_env
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from applire.db.session import AsyncSessionLocal
from applire.models.cover_letter import CoverLetterStatus, GeneratedCoverLetter
from applire.models.cv import CVGenerationStatus, GeneratedCV
from applire.models.flow import FlowSession
from applire.models.job import JobAnalysis
from applire.models.profile import MasterProfile
from applire.constants import CV_GENERATION_MAX_TOKENS, LLM_REVIEW_MAX_RETRIES
from applire.prompts.cover_letter import (
    SYSTEM_PROMPT,
    build_condense_prompt,
    build_cover_letter_prompt,
)
from applire.prompts.review_cover_letter import (
    COVER_LETTER_REFINEMENT_PROMPT,
    REVIEW_SYSTEM_PROMPT,
    build_retry_prompt,
    build_review_prompt,
)
from applire.providers import get_provider
from applire.providers.llm.base import LLMProvider
from applire.providers.llm.debug_log import log_letter_over_budget
from applire.services.letter_figure_guard import (
    figure_ownership_reviewer_prompt_fn,
    guard_letter_figures,
)
from applire.services.letter_outcome_guard import guard_letter_outcome_preference
from applire.services.load_bearing import load_bearing_fn_from_ledger
from applire.services.reviewer import review_and_refine
from applire.schemas.cover_letter import (
    CoverLetterGenerateRequest,
    CoverLetterGenerateResponse,
    CoverLetterStatusResponse,
)
from applire.utils.language_detection import resolve_jd_language
from applire.utils.letter_date import format_letter_date
from applire.utils.recipient_extraction import extract_recipient_from_jd

logger = logging.getLogger(__name__)

_TEMPLATE_FILES: dict[str, str] = {
    "classic_german": "lebenslauf_letter.html.j2",
    "modern_swiss": "modern_swiss_letter.html.j2",
    "executive": "executive_letter.html.j2",
    "tech_developer": "tech_developer_letter.html.j2",
    "creative_sidebar": "creative_sidebar_letter.html.j2",
    "academic": "academic_letter.html.j2",
    "compact_pro": "compact_pro_letter.html.j2",
}

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_jinja_env = build_template_env(_TEMPLATES_DIR)


async def generate_cover_letter(
    request: CoverLetterGenerateRequest,
    db: AsyncSession,
    provider: LLMProvider,
    background_tasks: BackgroundTasks | None = None,
    base_url: str = "http://localhost:8001",
) -> CoverLetterGenerateResponse:
    """Create a GeneratedCoverLetter record and render it.

    REST passes a ``BackgroundTasks`` so rendering runs after the response is
    sent. The MCP/agent channel has no request lifecycle, so it omits it
    (``background_tasks=None``) and we render inline before returning — the
    agent polls ``get_cover_letter_status`` and sees a terminal status on the
    first read (mirrors services/cv.py:generate_cv)."""
    # Resolve flow session for this job
    flow_result = await db.execute(
        select(FlowSession).where(
            FlowSession.job_id == request.job_id,
            FlowSession.deleted_at.is_(None),
        )
    )
    flow = flow_result.scalar_one_or_none()
    if flow is None:
        raise LookupError(f"No flow session found for job {request.job_id}")

    # Resolve the active CV (for template + color_profile_id)
    cv: GeneratedCV | None = None
    template = "classic_german"
    color_profile_id: uuid.UUID | None = None
    if flow.generated_cv_id is not None:
        cv_result = await db.execute(
            select(GeneratedCV).where(GeneratedCV.id == flow.generated_cv_id)
        )
        cv = cv_result.scalar_one_or_none()
        if cv is not None:
            template = cv.template
            color_profile_id = cv.color_profile_id

    # Resolve profile
    profile_result = await db.execute(
        select(MasterProfile)
        .where(MasterProfile.deleted_at.is_(None))
        .order_by(MasterProfile.created_at.desc())
        .limit(1)
    )
    profile = profile_result.scalar_one_or_none()
    if profile is None:
        raise LookupError("No profile found — complete the interview step first")

    # Build pre_gen_inputs for storage
    pre_gen_inputs = {
        "recipient_name": request.recipient_name,
        "recipient_company": request.recipient_company,
        "salary": request.salary,
        "availability": request.availability,
        "motivation": request.motivation,
        "tone": request.tone,
    }

    # Create the record — flush immediately so cl.id is populated before we
    # assign it to flow.generated_cover_letter_id (SQLAlchemy default=uuid.uuid4
    # is applied at flush time, not at object construction time).
    cl = GeneratedCoverLetter(
        job_analysis_id=request.job_id,
        profile_id=profile.id,
        template=template,
        letter_data={},
        pre_gen_inputs=pre_gen_inputs,
        color_profile_id=color_profile_id,
        status=CoverLetterStatus.pending.value,
    )
    db.add(cl)
    await db.flush()  # assigns cl.id

    # Update FlowSession pointer
    flow.generated_cover_letter_id = cl.id

    await db.commit()
    await db.refresh(cl)

    if background_tasks is None:
        # Agent channel: no request lifecycle to defer to — render inline.
        await _render_cover_letter_background(
            cl_id=cl.id, cv_id=flow.generated_cv_id, job_id=request.job_id
        )
        await db.refresh(cl)
    else:
        # REST: enqueue heavy work — runs after the response is sent.
        background_tasks.add_task(
            _render_cover_letter_background,
            cl_id=cl.id,
            cv_id=flow.generated_cv_id,
            job_id=request.job_id,
        )

    return CoverLetterGenerateResponse(
        cover_letter_id=cl.id,
        status=CoverLetterStatus(cl.status),
        html_url=f"{base_url}/api/cover-letter/{cl.id}/html",
        pdf_url=f"{base_url}/api/cover-letter/{cl.id}/pdf",
        expires_at=cl.expires_at,
    )


async def get_cover_letter_status(
    cl_id: uuid.UUID,
    db: AsyncSession,
    base_url: str,
) -> CoverLetterStatusResponse:
    result = await db.execute(
        select(GeneratedCoverLetter).where(
            GeneratedCoverLetter.id == cl_id,
            GeneratedCoverLetter.deleted_at.is_(None),
        )
    )
    cl = result.scalar_one_or_none()
    if cl is None:
        raise LookupError(f"Cover letter {cl_id} not found")

    html_url = None
    pdf_url = None
    letter_data = None
    if cl.status == CoverLetterStatus.ready.value:
        html_url = f"{base_url}/api/cover-letter/{cl_id}/html"
        pdf_url = f"{base_url}/api/cover-letter/{cl_id}/pdf"
        letter_data = cl.letter_data

    return CoverLetterStatusResponse(
        cover_letter_id=cl.id,
        status=cl.status,
        html_url=html_url,
        pdf_url=pdf_url,
        error_message=cl.error_message,
        expires_at=cl.expires_at,
        letter_data=letter_data,
        origin=cl.origin,
        critic_report=cl.critic_report,
    )


async def get_cover_letter_pdf_filename(cl_id: uuid.UUID, db: AsyncSession) -> str:
    """Build the Content-Disposition filename for a cover-letter PDF (E039/US219).

    Format: <name>_<company>_<role>_<suffix>.pdf — same contract as the CV
    filename; the suffix keeps the pair from colliding in a Downloads folder.

    issue #241 item 3 — the suffix must follow the letter's actual output
    language, not be hardcoded to German. The letter itself carries no
    language field of its own; its content language is the JD's language,
    resolved via ``resolve_jd_language`` (ADR-038 — the same resolution
    `generate_cover_letter`/`_inject_letter_date`/etc. already use to write the
    letter, so the filename can never disagree with the document it names).
    """
    from applire.services.cv import compose_document_filename

    cl = await db.get(GeneratedCoverLetter, cl_id)
    if cl is None or cl.deleted_at is not None:
        raise LookupError(f"Cover letter {cl_id} not found")

    profile = await db.get(MasterProfile, cl.profile_id)
    name = ((profile.profile_json or {}).get("personal_info") or {}).get("name") if profile else None
    job = await db.get(JobAnalysis, cl.job_analysis_id)
    language = resolve_jd_language(job) if job is not None else "de"
    suffix = "Cover-Letter" if language == "en" else "Anschreiben"
    fallback_stem = "cover-letter" if language == "en" else "anschreiben"
    return compose_document_filename(
        name,
        job.company_name if job else None,
        job.role_title if job else None,
        suffix=suffix,
        fallback=f"{fallback_stem}-{str(cl_id)[:8]}",
    )


async def get_cover_letter_html(
    cl_id: uuid.UUID,
    db: AsyncSession,
    require_ready: bool = True,
) -> str:
    """Render the cover letter HTML via Jinja2. Only works when status='ready'.

    ``require_ready`` (E037 PQ #2): the generation path renders the smoke PDF before the
    status flips to 'ready' (so the ATS audit lands BEFORE 'ready' is observable). The
    public download path keeps the default ready-only guard.
    """
    result = await db.execute(
        select(GeneratedCoverLetter).where(
            GeneratedCoverLetter.id == cl_id,
            GeneratedCoverLetter.deleted_at.is_(None),
        )
    )
    cl = result.scalar_one_or_none()
    if cl is None:
        raise LookupError(f"Cover letter {cl_id} not found")
    if require_ready and cl.status != CoverLetterStatus.ready.value:
        raise ValueError(f"Cover letter not ready (status={cl.status})")

    color_ctx = _default_color_context()
    if cl.color_profile_id is not None:
        from applire.models.color_profile import ColorProfile
        cp_result = await db.execute(
            select(ColorProfile).where(ColorProfile.id == cl.color_profile_id)
        )
        cp = cp_result.scalar_one_or_none()
        if cp is not None:
            color_ctx = {
                "primary": cp.primary,
                "primary_tint": cp.primary_tint,
                "surface": cp.surface,
                "surface_text": cp.surface_text,
            }

    letter_data = _apply_section_overrides(cl.letter_data, cl.section_overrides or {})
    template_file = _TEMPLATE_FILES.get(cl.template, "lebenslauf_letter.html.j2")
    tmpl = _jinja_env.get_template(template_file)
    # #4 (ADR-038): the subject prefix + html lang follow the document's output language
    # (resolved from the target job, like the CV), not a hardcoded German "Bewerbung".
    from applire.models.job import JobAnalysis
    from applire.templates.labels import cover_letter_labels
    job = await db.get(JobAnalysis, cl.job_analysis_id)
    lang = resolve_jd_language(job) if job else "de"
    # F3 (blind PQ blocker) AC #3: the subject must reference the target role, not
    # just the bare word "Application"/"Bewerbung". Computed at render time (never
    # stored on letter_data) so it always reflects the job's current role_title —
    # role_title lives on JobAnalysis, not in the LLM's letter_data schema.
    labels = cover_letter_labels(lang)
    role_title = job.role_title if job is not None else None
    if role_title:
        subject = f"{labels['subject_prefix']}: {role_title}"
    else:
        subject = labels["subject_prefix"]
    return tmpl.render(
        letter=letter_data,
        color=color_ctx,
        lang=lang,
        labels=labels,
        subject=subject,
    )


async def patch_cover_letter_section(
    cl_id: uuid.UUID,
    section: str,
    content: str,
    db: AsyncSession,
    background_tasks: BackgroundTasks | None = None,
) -> None:
    result = await db.execute(
        select(GeneratedCoverLetter).where(
            GeneratedCoverLetter.id == cl_id,
            GeneratedCoverLetter.deleted_at.is_(None),
        )
    )
    cl = result.scalar_one_or_none()
    if cl is None:
        raise LookupError(f"Cover letter {cl_id} not found")

    overrides = dict(cl.section_overrides or {})
    overrides[section] = content
    cl.section_overrides = overrides
    await db.commit()

    if background_tasks is not None:
        background_tasks.add_task(_update_ats_report_letter_by_id, cl_id)


async def get_cover_letter_by_job(
    job_id: uuid.UUID,
    db: AsyncSession,
    base_url: str,
) -> CoverLetterStatusResponse:
    # Find active cover letter via flow session
    flow_result = await db.execute(
        select(FlowSession).where(
            FlowSession.job_id == job_id,
            FlowSession.deleted_at.is_(None),
        )
    )
    flow = flow_result.scalar_one_or_none()
    if flow is None or flow.generated_cover_letter_id is None:
        raise LookupError(f"No cover letter found for job {job_id}")
    return await get_cover_letter_status(flow.generated_cover_letter_id, db, base_url)


def _default_color_context() -> dict:
    return {
        "primary": "#1a1a2e",
        "primary_tint": "#e8e8f0",
        "surface": "#1a1a2e",
        "surface_text": "#ffffff",
    }


def _inject_letter_date(letter_data: dict, language: str, today: date | None = None) -> dict:
    """Set recipient.date from the system clock, overwriting any LLM value —
    the model cannot know today's date and hallucinates one if asked."""
    letter_data.setdefault("recipient", {})["date"] = format_letter_date(language, today)
    return letter_data


def _normalize_signature_closing(letter_data: dict, language: str) -> dict:
    """Overwrite ``signature.closing`` with the language-routed chrome label (#189).

    The sign-off is CHROME, not LLM content, so per ADR-038 it must follow the
    document's output language deterministically — the same discipline every other
    cover-letter label already uses (subject_prefix/subject_at/email/…). The LLM was
    primed to German by the schema example ("Mit freundlichen Grüßen") and the mock
    hardcodes it, so an English letter closed with the German sign-off. This is a twin
    of :func:`_inject_letter_date`: it overlays a deterministic value after generation
    so the chrome is correct regardless of what the model emitted.
    """
    from applire.templates.labels import cover_letter_labels

    closing = cover_letter_labels(language)["closing"]
    signature = letter_data.get("signature")
    if isinstance(signature, dict):
        signature["closing"] = closing
    elif signature is None:
        letter_data["signature"] = {"closing": closing}
    # A non-dict, non-None signature is a legacy/unexpected shape — leave it untouched
    # rather than clobber it (fail-open; production always uses the nested dict schema).
    return letter_data


# Salutation openers we recognise as "already present" (matched after lowercasing
# and folding punctuation to spaces, so "Sg." → "sg " and "Werter Herr," →
# "werter herr"). German + English formal/semi-formal forms; startswith so an
# inline salutation ("Sehr geehrter Herr Müller, mit …") also counts.
_SALUTATION_OPENERS: tuple[str, ...] = (
    "sehr geehrte",  # geehrte/geehrter/geehrtes Damen und Herren / Herr / Frau
    "werte",  # werte/werter (Werter Herr Schmidt, …)
    "sg ",  # Sg. — the Austrian/Swiss abbreviation of "Sehr geehrte"
    "liebe",  # liebe/lieber
    "hallo",
    "guten tag",
    "dear ",
    "hello",
    "hi ",
    "to whom it may concern",
)


def _salutation_norm(text: str) -> str:
    s = re.sub(r"[^\w\s]", " ", (text or "").strip().lower(), flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip()


def _has_salutation(letter_data: dict) -> bool:
    """True if any of the first few non-empty paragraphs opens with a salutation.

    Scans more than paragraphs[0] because an author may put a Betreff/subject
    line first, and tolerates abbreviations/punctuation ("Sg.", "Werte/r …") —
    otherwise the floor injection would DOUBLE a real, author-written salutation.
    """
    body = letter_data.get("body")
    paragraphs = body.get("paragraphs") if isinstance(body, dict) else None
    if not paragraphs:
        return False
    non_empty = [p for p in paragraphs if (p or "").strip()][:2]
    return any(_salutation_norm(p).startswith(_SALUTATION_OPENERS) for p in non_empty)


def _inject_salutation(letter_data: dict, language: str) -> dict:
    """Prepend a norm-conformant Anrede when the author omitted one (#224).

    A twin of :func:`_inject_letter_date` / :func:`_normalize_signature_closing`:
    a missing salutation is a formal defect in both DE and EN business letters
    (flagged by an HR screener in the 2026-07-21 edge hiring-panel run). We only
    inject the generic, always-correct floor ("Sehr geehrte Damen und Herren," /
    "Dear Sir or Madam,") — a capable agent is guided to author the named form
    itself, and injecting a guessed named/gendered Anrede risks getting it wrong.
    """
    from applire.templates.labels import cover_letter_labels

    if _has_salutation(letter_data):
        return letter_data
    salutation = cover_letter_labels(language)["salutation"]
    body = letter_data.setdefault("body", {})
    if not isinstance(body, dict):
        return letter_data
    paragraphs = body.get("paragraphs")
    if not isinstance(paragraphs, list):
        paragraphs = []
    body["paragraphs"] = [salutation, *paragraphs]
    return letter_data


# A salutation is short in BOTH senses, and the word cap is the load-bearing one.
# "Sehr geehrte Frau Prof. Dr. Müller-Lüdenscheidt," is 6 words / 48 chars; a
# collective address like "Liebe Kolleginnen und Kollegen aus der Fertigung, der
# Instandhaltung und ..." clears the character cap at its FIRST comma while being a
# single continuing salutation — splitting there strands "der Instandhaltung ..." as
# a body paragraph. Counting words catches that; counting characters does not.
_MAX_SALUTATION_WORDS = 6
_MAX_SALUTATION_CHARS = 60


def _split_inline_salutation(letter_data: dict) -> dict:
    """Give the Anrede its own paragraph when the model ran it into the opener (#307).

    Charter run #7 delivered a German Anschreiben whose first body paragraph was
    ``"Sehr geehrte Damen und Herren, mit großem Interesse habe ich Ihre
    Stellenausschreibung ... gelesen. Als erfahrener Produktionsleiter ..."`` — one
    justified block. In a German business letter (and an English one) the salutation
    stands on its own line with a blank line after it; running it into the first
    sentence is the most visible formal defect a reader meets on the page.

    The deterministic twin of :func:`_inject_salutation`: that one supplies a missing
    Anrede, this one un-glues a present-but-inline Anrede. Both doors need it — the
    agent door because an agent may write the same shape, the pipeline because the
    writer LLM demonstrably does.

    Note the German casing works out: "Sehr geehrte Damen und Herren," is correctly
    followed by a lowercase continuation, so the split yields conventional prose
    without touching a single word.
    """
    body = letter_data.get("body")
    if not isinstance(body, dict):
        return letter_data
    paragraphs = body.get("paragraphs")
    if not isinstance(paragraphs, list) or not paragraphs:
        return letter_data

    for index, para in enumerate(paragraphs[:2]):
        text = (para or "").strip()
        if not text or not _salutation_norm(text).startswith(_SALUTATION_OPENERS):
            continue
        head, sep, tail = text.partition(",")
        # No comma, nothing after it, or a "salutation" too long to be one: leave
        # the paragraph exactly as the author wrote it. Failing closed here costs a
        # cosmetic defect; failing open would cut a real sentence in half.
        if not sep or not tail.strip():
            continue
        if len(head.split()) > _MAX_SALUTATION_WORDS or len(head) > _MAX_SALUTATION_CHARS:
            continue
        paragraphs[index : index + 1] = [f"{head}{sep}", tail.strip()]
        break

    body["paragraphs"] = paragraphs
    return letter_data


def _backfill_sender_name(letter_data: dict, cv_data: dict, profile) -> dict:
    """Fill an empty ``signature.name`` / ``header.name`` from the candidate's real
    name (#189).

    ``signature.name`` (and ``header.name``) are LLM-owned with no deterministic
    guarantee, so when the fallback cv_data path fed the prompt a blank name (the
    profile_json schema uses ``personal_info``, not ``contact``) the letter shipped
    with no sender name after the sign-off. Source the name robustly from BOTH schemas
    and backfill only when the field is missing (an LLM-provided name still wins).
    """
    profile_json = (getattr(profile, "profile_json", None) or {}) if profile is not None else {}
    name = (
        (cv_data.get("contact") or {}).get("name")
        or (profile_json.get("personal_info") or {}).get("name")
        or (profile_json.get("contact") or {}).get("name")
    )
    if name:
        for key in ("signature", "header"):
            section = letter_data.get(key)
            if isinstance(section, dict):
                if not section.get("name"):
                    section["name"] = name
            elif section is None:
                letter_data[key] = {"name": name}
            # A non-dict, non-None value is a legacy/unexpected shape — leave it
            # untouched (fail-open; production always uses the nested dict schema).
    return letter_data


def _apply_recipient_overrides(letter_data: dict, pre_gen_inputs: dict) -> dict:
    """Overlay the user's own dialog input onto letter_data.recipient — F3 (blind PQ
    blocker): the LLM's JSON solely owns letter_data, so a user who typed a recipient
    name/company in the generate dialog could see it silently dropped (LLM returned
    null, or a different value) with no deterministic guarantee it survives. User input
    always wins over the LLM's value when the user provided one (AC #2); when the user
    left a field blank, the LLM's own extracted/guessed value (e.g. from the JD) is kept.
    """
    data = copy.deepcopy(letter_data)
    recipient = data.setdefault("recipient", {})
    for field, key in (("name", "recipient_name"), ("company", "recipient_company")):
        user_value = pre_gen_inputs.get(key)
        if user_value:
            recipient[field] = user_value
    return data


def _apply_section_overrides(letter_data: dict, overrides: dict) -> dict:
    """Return a copy of letter_data with manual section overrides applied."""
    data = copy.deepcopy(letter_data)
    for section, content in overrides.items():
        if section == "body" and isinstance(content, str):
            data.setdefault("body", {})["paragraphs"] = [content]
        elif section in data:
            if isinstance(data[section], dict) and isinstance(content, str):
                data[section]["_override"] = content
            else:
                data[section] = content
    return data


async def _render_cover_letter_background(
    cl_id: uuid.UUID,
    cv_id: uuid.UUID | None,
    job_id: uuid.UUID,
) -> None:
    """Background task: LLM → Jinja2 → PDF. Updates status on completion."""
    async with AsyncSessionLocal() as db:
        try:
            # Load cover letter record
            cl_result = await db.execute(
                select(GeneratedCoverLetter).where(GeneratedCoverLetter.id == cl_id)
            )
            cl = cl_result.scalar_one_or_none()
            if cl is None:
                return

            cl.status = CoverLetterStatus.generating.value
            await db.commit()

            # Load job
            job_result = await db.execute(
                select(JobAnalysis).where(JobAnalysis.id == job_id)
            )
            job = job_result.scalar_one_or_none()
            if job is None:
                raise LookupError("Job not found")

            # Load CV tailored_data
            cv_data: dict = {}
            if cv_id is not None:
                cv_result = await db.execute(
                    select(GeneratedCV).where(GeneratedCV.id == cv_id)
                )
                cv = cv_result.scalar_one_or_none()
                if cv is not None:
                    cv_data = cv.tailored_data or {}

            # Load profile
            profile_result = await db.execute(
                select(MasterProfile)
                .where(MasterProfile.deleted_at.is_(None))
                .order_by(MasterProfile.created_at.desc())
                .limit(1)
            )
            profile = profile_result.scalar_one_or_none()
            if profile is not None and not cv_data:
                # ADR-061 clause 3: no CV exists yet, so this raw profile stands in
                # as the writer's evidence — an unconfirmed skill/language/
                # certification must not reach the letter as though established.
                from applire.services.profile.reconcile.stance import exclude_unconfirmed
                cv_data = exclude_unconfirmed(profile.profile_json or {})

            # Auto-extract recipient if not provided
            pre_gen = dict(cl.pre_gen_inputs or {})
            if not pre_gen.get("recipient_name"):
                extracted = extract_recipient_from_jd(job.raw_text)
                if extracted["name"]:
                    pre_gen["recipient_name"] = extracted["name"]
            if not pre_gen.get("recipient_company") and hasattr(job, "company_name") and job.company_name:
                pre_gen["recipient_company"] = job.company_name

            # ADR-038: the letter follows the language the JD is written in —
            # not language_requirement, which is the candidate requirement
            # (e.g. "Bilingual DE/EN") and misroutes.
            detected_language = resolve_jd_language(job)

            # ADR-048 / US201: load the latest Keyword Ledger for this job (read-only,
            # mirrors cv.py) so the prompt surfaces claimable terms with their profile
            # evidence and forbids honest-gap terms. Legacy pre-E037 gap rows have none.
            from applire.models.gap import GapAnalysis
            gap_result = await db.execute(
                select(GapAnalysis)
                .where(
                    GapAnalysis.job_analysis_id == job_id,
                    GapAnalysis.deleted_at.is_(None),
                )
                .order_by(GapAnalysis.created_at.desc())
                .limit(1)
            )
            gap = gap_result.scalar_one_or_none()
            keyword_ledger: list[dict] = (gap.keyword_ledger or []) if gap else []

            # E048/US264 (ADR-057 amended 2026-07-24 / ADR-058 exception (a)): deterministic,
            # no-LLM positioning inputs — a blind hiring panel rejected an otherwise-honest
            # letter for (1) never engaging the employer's product/domain, (2) never arguing
            # the candidate's own transfer story for the one true gap even though it sat in
            # the vault as interview testimony, (3) never addressing an obvious
            # concurrent-roles/availability question. All three are found here from data
            # ALREADY loaded above (job, gap, profile) — no new query, no new LLM chain — and
            # threaded into the prompt ONLY when genuinely present (silence over invention).
            from applire.services.cover_letter_positioning import (
                detect_concurrent_roles,
                find_availability_testimony,
                find_gap_testimony,
            )
            from applire.services.gap import askable_gap_inputs
            from applire.services.cross_document import exclude_claimable_concepts

            profile_json = profile.profile_json if profile is not None else {}
            signature_stories = profile_json.get("signature_stories") or []
            # #270 Fix A (the run-5 regression): askable_gap_inputs() deliberately
            # folds #260 keyword LIABILITIES (claimable, required, no narrative
            # depth) into the clusterable gap list so they stay reachable via
            # resolve_gap — correct for clustering, but a CLAIMABLE concept must
            # never be positioned as THIS letter's honest gap (the ledger already
            # says the vault positively supports it). Filter at this call site
            # only; askable_gap_inputs itself is untouched (other callers depend
            # on the #260 fold).
            gap_testimony = (
                find_gap_testimony(
                    exclude_claimable_concepts(askable_gap_inputs(gap), keyword_ledger),
                    signature_stories,
                )
                if gap is not None
                else None
            )

            work_experience = profile_json.get("work_experience") or []
            enrichment_history = (
                (profile_json.get("metadata") or {}).get("enrichment_history") or []
            )
            # #272 Task 1: hoisted above the availability block (was computed further
            # below, after this call site) so find_availability_testimony can search
            # denied_concepts[].statement too — the run-5 ground truth showed the
            # candidate's real availability testimony living as the TAIL of a denial
            # statement, a source this call never searched before.
            denied_concepts = (profile_json.get("metadata") or {}).get("denied_concepts") or []
            availability_testimony: str | None = None
            if detect_concurrent_roles(work_experience):
                availability_testimony = find_availability_testimony(
                    signature_stories, enrichment_history, denied_concepts
                )
                if availability_testimony is None:
                    logger.info(
                        "Letter positioning: concurrent-roles condition detected for CL %s "
                        "but no vault availability testimony found — no availability claim "
                        "made.",
                        cl_id,
                    )
                else:
                    logger.info(
                        "Letter positioning: concurrent-roles condition detected for CL %s — "
                        "threading vault availability testimony into the prompt.",
                        cl_id,
                    )

            # STATED LIMITS: the candidate's persisted denial statements, verbatim, so the
            # writer never contradicts one. Facts only — which claimable concept a given
            # limit bears on is a question about meaning and is left to the model. The
            # deterministic pairing this replaces (`find_scoped_boundaries`) got that
            # question backwards on real data and made the writer invent limits; see
            # services/cross_document.collect_stated_limits for the run-8 ground truth.
            from applire.services.cross_document import (
                collect_stated_limits,
                find_unaddressed_hard_requirements,
                render_stated_limits_block,
                render_unaddressed_hard_requirements_block,
            )
            # denied_concepts already resolved above (#272 Task 1 hoist).
            stated_limits_block = render_stated_limits_block(
                collect_stated_limits(denied_concepts)
            )

            # #270(c): unmet JD hard requirements (claimable: false, required) that need
            # an explicit positioning decision. Computed with letter_data=None — no draft
            # exists yet, so every required honest gap is trivially "unaddressed" and the
            # FIRST draft gets a chance to address it (never relies solely on the
            # gap-transfer-argument slot above, which only fires when a signature story
            # happens to token-overlap the gap label — this is the deterministic
            # backstop for when it does not).
            #
            # This is the WRITER's pre-draft use, and the ONLY one that may read a
            # `letter_data=None` computation (ADR-021 amended 2026-08-13, #526). The
            # reviewer's copy is recomputed per round by
            # `unaddressed_requirements_reviewer_prompt_fn` below; it must never be
            # snapshotted into `grounding_source`, which the loop hands unchanged to
            # every round of both the reviewer and the corrector.
            unaddressed_requirements = find_unaddressed_hard_requirements(keyword_ledger, None)
            unaddressed_requirements_block = render_unaddressed_hard_requirements_block(
                unaddressed_requirements
            )
            # ADR-074 clause 5 (#526): the letter is deliberately written as
            # though these requirements had not been named — every other move is
            # a truthfulness defect. A requirement that leaves no trace in the
            # document is exactly the shape of a control that never fires, so the
            # silence is logged ONCE per generation, always-on and PII-free (the
            # #264 discipline), and told to the candidate on
            # GapAnalysisResponse.unasked_requirements. Logged here rather than in
            # the selector, which the reviewer wrapper re-runs every round.
            from applire.services.keyword_ledger import unasked_hard_requirements

            _unasked = unasked_hard_requirements(keyword_ledger)
            if _unasked:
                logger.warning(
                    "LETTER_UNASKED_REQUIREMENTS cl=%s count=%d concepts=%r — JD hard "
                    "requirement(s) with no evidence, no adjacent capability and no "
                    "stated limit. Excluded from generation (ADR-074): asserting is "
                    "ungrounded, denying invents a limit the candidate never stated, "
                    "and silence is the only honest option left. Surfaced to the "
                    "candidate instead.",
                    cl_id,
                    len(_unasked),
                    [e.get("concept", "") for e in _unasked],
                )

            # ADR-070 clause 2: the candidate's own scale evidence for partial scope
            # entries (bar.attested + typed values) — the ONLY channel scope material
            # takes into the letter (scope entries are excluded from the ledger block
            # AND from the unaddressed-hard-requirements block by is_scope_entry).
            from applire.services.scope_requirements import render_scope_positioning_block

            scope_positioning_block = render_scope_positioning_block(
                keyword_ledger, detected_language
            ) or None

            # #255 (ADR-057 amended 2026-07-24): the run-4 ground truth showed the writer
            # received all three POSITIONING blocks (and engaged the domain) but the
            # ADR-021 reviewer/corrector loop never did — so it could not tell a legitimate,
            # requested domain reference / honest gap-transfer argument apart from a
            # forbidden candidate-competence claim, and stripped it. Build the SAME
            # positioning content once here (all three inputs are already resolved above)
            # and thread it into grounding_source below, so every review_and_refine call in
            # this render (including the condense pass) carries it too.
            positioning_requested: dict = {}
            # #272 Task 2: UNCONDITIONAL — unlike the other positioning entries, every
            # letter needs a genuine closing paragraph, so this is never gated on a
            # deterministic condition. Plugs into the SAME machinery: reviewer check 4
            # already flags missing required positioning content, and the corrector
            # prompt already has a PRESERVE REQUIRED POSITIONING CONTENT rule.
            positioning_requested["closing"] = {
                "required": True,
                "instruction": (
                    "REQUIRED content: the letter must end with a genuine closing "
                    "paragraph expressing interest and a call to action (e.g. inviting "
                    "further discussion or an interview). Availability/notice-period "
                    "content, when present, is folded INTO this closing paragraph — "
                    "never left as a bare, standalone terminal line. Deleting or "
                    "shrinking this closing paragraph to fix an unrelated issue is "
                    "itself a review issue."
                ),
            }
            if stated_limits_block:
                positioning_requested["stated_limits"] = {
                    "limits": collect_stated_limits(denied_concepts),
                    "instruction": (
                        "These are the candidate's own words about what they cannot "
                        "claim, and the ONLY limits the vault holds. A draft must not "
                        "contradict one — and must not invent one either. A concept "
                        "named inside a statement as something the candidate DOES have "
                        "is a strength, not a limit: an honest denial names the "
                        "adjacent strengths that transfer. Everything the Keyword "
                        "Ledger marks claimable stays fully claimable unless a "
                        "statement here denies it."
                    ),
                }
            # NOTE (ADR-021 amended 2026-08-13, #526): there is deliberately no
            # `positioning_requested["unaddressed_hard_requirements"]` entry. Every
            # other key here is a STANDING obligation, true of the letter regardless
            # of what any draft says, and each carries its own named rule in the
            # corrector prompt and in reviewer check 4. "Which hard requirements are
            # still unaddressed" is a statement about the CURRENT DRAFT, and
            # `grounding_source` is built once and reused for every round — so the
            # entry was computed against a draft that did not exist yet and then
            # asserted for ten rounds, overruling the per-round wrapper that had
            # correctly gone silent (gate charter run 1, rounds 80 and 88 of
            # backend/logs/llm/2026-08-11.jsonl). Nothing replaces it on the
            # corrector side either: measured across the same ten rounds, the frozen
            # entry never produced content the reviewer's feedback had not already
            # demanded, and in the one round that destroyed a correct honest-gap
            # sentence the recomputed list was empty — a per-round corrector block
            # would have been silent there too.
            if job.company_name:
                positioning_requested["company_domain_engagement"] = {
                    "target_company": job.company_name,
                    "required": True,
                    "instruction": (
                        "REQUIRED content: the letter must concretely engage this "
                        "employer's product/domain/market in the opening or motivation "
                        "paragraph, grounded ONLY in the job_description text above. Its "
                        "absence from the letter body is a review issue."
                    ),
                }
            if gap_testimony:
                gt_story = gap_testimony.get("story") or {}
                gt_testimony_text = " ".join(
                    p for p in (
                        gt_story.get("challenge") or "",
                        gt_story.get("mechanism") or "",
                        gt_story.get("outcome") or "",
                        gt_story.get("benchmark") or "",
                    ) if p
                )
                positioning_requested["gap_transfer_argument"] = {
                    "gap": gap_testimony.get("gap", ""),
                    "testimony": gt_testimony_text,
                    "required": True,
                    "instruction": (
                        "REQUIRED content: exactly one honest paragraph naming this gap "
                        "and delivering the candidate's OWN transfer argument, grounded "
                        "verbatim in 'testimony' above. Its absence is a review issue. "
                        "Naming the gap itself is honesty, not a forbidden claim."
                    ),
                }
            if availability_testimony:
                positioning_requested["availability"] = {
                    "testimony": availability_testimony,
                    "required": True,
                    "instruction": (
                        "REQUIRED content: address availability/commitment using ONLY "
                        "this testimony, grounded verbatim. Its absence is a review issue."
                    ),
                }
            if scope_positioning_block:
                # ADR-070 clause 3: the reviewer requires it (check 4), the corrector
                # preserves it, and the "testimony" field grounds its figures for
                # check 1 (a positioning testimony is a valid FIGURES source there).
                positioning_requested["scope_positioning"] = {
                    "testimony": scope_positioning_block,
                    "required": True,
                    "instruction": (
                        "REQUIRED content: state the candidate's real, attested scope "
                        "honestly — grounded ONLY in the typed values and attested "
                        "quote in 'testimony' above, as exactly what their stated "
                        "unit/semantics say. NEVER state or imply the posting's own "
                        "figure as something the candidate has done, led, or held. "
                        "Its absence from the letter body is a review issue."
                    ),
                }

            # Call LLM
            # #177 / ADR-051 §6 amended: feedforward body-word budget from the region
            # norm registry — the CV's guarantee shape, extended to letters. NO
            # component may hard-code a word/page number (ADR-051 §1); this is the
            # sole read of REGION_NORMS for the generation + condense path below.
            from applire.norms import DEFAULT_REGION, REGION_NORMS
            norm = REGION_NORMS[DEFAULT_REGION]
            provider = get_provider()
            # #271 Task 1: the SAME de-chromed JD excerpt the writer AND the
            # reviewer (grounding_source below) both see — never re-sliced
            # independently, so the two can never disagree about what the
            # JD says (applire.services.jd_excerpt module docstring).
            from applire.services.jd_excerpt import build_jd_excerpt
            jd_excerpt = build_jd_excerpt(job.raw_text)
            # #271 Tasks 2/3: the vault's strongest JD-relevant evidence,
            # selected independently of what cv_data's tailoring
            # condensation kept — so a fact present in the vault but
            # dropped by tailoring can still reach the letter.
            from applire.services.vault_evidence import (
                render_vault_evidence_block,
                select_vault_evidence,
            )
            from applire.services.profile.reconcile.stance import exclude_unconfirmed

            vault_evidence_block = render_vault_evidence_block(
                select_vault_evidence(
                    keyword_ledger,
                    jd_excerpt,
                    # ADR-061 clause 3: unconfirmed vault content is not evidence.
                    exclude_unconfirmed(profile.profile_json) if profile else {},
                    # #271: the posting's own leadership-vs-hands-on weighting,
                    # extracted at analyse time. Drives rule 3's trigger, its
                    # sub-cap and the quote the writer positions against; None
                    # on a pre-migration-0056 row falls back to the legacy JD
                    # marker check inside the selector.
                    leadership_emphasis=getattr(job, "leadership_emphasis", None),
                )
            )
            user_prompt = build_cover_letter_prompt(
                cv_data=cv_data,
                jd_text=job.raw_text,
                pre_gen_inputs=pre_gen,
                detected_language=detected_language,
                keyword_ledger=keyword_ledger,
                role_title=job.role_title,
                word_budget=norm.letter_body_word_budget,
                letter_pages=norm.letter_pages,
                company_name=job.company_name,
                gap_testimony=gap_testimony,
                availability_testimony=availability_testimony,
                stated_limits_block=stated_limits_block,
                unaddressed_requirements_block=unaddressed_requirements_block,
                vault_evidence_block=vault_evidence_block,
                scope_positioning_block=scope_positioning_block,
            )
            # Explicit budget to match CV generation (cv.py): a signed letter must
            # never close its JSON early under budget pressure (F-B, ADR-009 amendment).
            letter_data = await provider.aparse_json(
                user_prompt, system=SYSTEM_PROMPT, max_tokens=CV_GENERATION_MAX_TOKENS
            )

            # ADR-040 §1 / US170 (JF-M-8.1): the letter is signed and sent, so it carries
            # the same two-tier truthfulness contract as the CV. Prevention tier — a grounding
            # reviewer audits the body for invented dates/employers/achievements before the
            # letter is shown. Source of truth = the grounded CV data + profile + the
            # candidate's OWN inputs (so user-stated facts are not false-flagged).
            from applire.services.profile.reconcile.stance import exclude_unconfirmed

            grounding_source = json.dumps(
                {
                    "cv_data": cv_data,
                    # ADR-061 clause 3: an unconfirmed vault entry must not count as
                    # grounding for the reviewer — it cannot back a letter sentence.
                    "profile": exclude_unconfirmed(profile.profile_json) if profile is not None else {},
                    "candidate_inputs": {
                        k: pre_gen.get(k)
                        for k in ("motivation", "salary", "availability")
                        if pre_gen.get(k)
                    },
                    # E048/US264 (ADR-057 amended 2026-07-24): the letter now engages the
                    # employer's own product/domain concretely (POSITIONING: COMPANY & DOMAIN
                    # ENGAGEMENT above), so the reviewer needs the SAME JD text the generator
                    # saw to judge whether a company/domain claim is grounded — an invented
                    # company fact must still fail review 4 (Oracle discipline unchanged).
                    # #271 Task 1: literally the SAME excerpt string the writer prompt above
                    # was built from (jd_excerpt) — never a second, independently-sliced copy.
                    "job_description": jd_excerpt,
                    # #255 (ADR-057 amended 2026-07-24): the SAME positioning inputs the
                    # writer received — see build above. Without this the reviewer/
                    # corrector cannot distinguish a REQUESTED, grounded domain reference /
                    # honest transfer argument from a forbidden candidate-competence claim,
                    # and cannot flag a requested block's absence either.
                    "positioning_requested": positioning_requested,
                },
                ensure_ascii=False,
                indent=2,
            )
            # ADR-048 / US202+US213 (#122): ledger to the reviewer for the forbidden-claim
            # check; the reviewer prompt is wrapped so each iteration carries the
            # DETERMINISTIC verified-coverage state of the current draft (LLM detection
            # retired — the reviewer only arbitrates grounding waivers).
            from applire.services.keyword_ledger import (
                coverage_corrector_prompt_fn,
                coverage_reviewer_prompt_fn,
                render_ledger_reviewer_block,
            )
            ledger_block = render_ledger_reviewer_block(keyword_ledger)
            if ledger_block:
                grounding_source = f"{grounding_source}\n\n{ledger_block}"
            # #321 (ADR-062 clause 2): the vault's own recorded job titles, as
            # FACTS — "this position is recorded as 'Produktionsleiter'". Run #8
            # delivered a letter naming the candidate "Bereichsverantwortlicher"
            # at that position, a noun lifted from the same position's ISO-9001
            # achievement line, so every coverage-based check passed it. Deciding
            # whether a sentence STATES a title is a judgement about prose and
            # stays with the model (no title-marker list exists anywhere).
            # This goes into the grounding SOURCE rather than a reviewer-only
            # wrapper (as FIGURE OWNERSHIP does) for two reasons: it does not
            # depend on the draft, and review_and_refine hands `source` to the
            # CORRECTOR as well — which is the call that has to restate the
            # title. Both letter loops below share this string, so the condense
            # pass carries it too. No new LLM call, no new pass (ADR-058 freeze
            # amended 2026-07-24: threading existing vault data into an existing
            # prompt is bugfix-grade).
            from applire.services.cover_letter_positioning import (
                render_role_titles_block,
                vault_role_titles,
            )
            role_titles_block = render_role_titles_block(
                vault_role_titles(profile.profile_json if profile else {})
            )
            if role_titles_block:
                grounding_source = f"{grounding_source}\n\n{role_titles_block}"
            # #270(c): compose (never replace) coverage_reviewer_prompt_fn with a
            # SECOND deterministic wrapper — each reviewer iteration also carries the JD
            # hard requirements the CURRENT draft has not addressed, recomputed fresh
            # every pass exactly like the verified-coverage check above. No new LLM pass,
            # no new loop. This wrapper used to also append cross-document conflict
            # findings derived from a negation-proximity matcher; ADR-062 deleted them
            # (the matcher read contrastive transfer arguments — "X nicht, doch Y" — as
            # denials, and the reviewer, told the flag was ground truth, could not
            # approve any draft). The cross-document rule is now stated once in the
            # reviewer prompt, which already holds both documents and the ledger.
            from applire.services.cross_document import (
                unaddressed_requirements_reviewer_prompt_fn,
            )
            reviewer_prompt_fn = unaddressed_requirements_reviewer_prompt_fn(
                coverage_reviewer_prompt_fn(build_review_prompt, keyword_ledger),
                keyword_ledger=keyword_ledger,
            )
            # #272 Task 6: a THIRD deterministic wrapper — each reviewer iteration also
            # carries a WORD FLOOR check against the CURRENT draft's body (ADR-051 norm
            # registry; a thin letter previously passed silently since only an upper
            # bound existed). Composes on top of the two wrappers above exactly like
            # they compose with each other — no new LLM call, no new loop.
            from applire.services.cover_letter_positioning import (
                body_word_count,
                has_closing_paragraph,
                within_word_budget,
                word_floor_reviewer_prompt_fn,
            )
            reviewer_prompt_fn = word_floor_reviewer_prompt_fn(
                reviewer_prompt_fn, word_floor=norm.letter_body_word_floor
            )
            # #299 (ADR-062 clause 2): a FOURTH deterministic wrapper — each
            # reviewer iteration also carries the vault OWNERSHIP of every
            # grounded figure in the CURRENT draft ("figure N appears in the
            # vault only under X"). That is a data-structure lookup, a FACT;
            # deciding which employer the sentence carrying it is ABOUT is a
            # judgement about prose, and it now belongs to the reviewer — which
            # can re-anchor or rewrite the claim, where the post-loop guard
            # (below, still the floor) can only delete the sentence. Same
            # composition as the three wrappers above: no new LLM call, no new
            # pass, no new loop (ADR-058 freeze amended 2026-07-24 — threading
            # existing vault data into an existing prompt is bugfix-grade).
            reviewer_prompt_fn = figure_ownership_reviewer_prompt_fn(
                reviewer_prompt_fn, profile.profile_json if profile else {}
            )
            # Wave-6 follow-up (charter run #6, Task 2): prefer_if is a SECONDARY,
            # structural-only tie-break over drafts retain_if already accepts.
            # #420 (ADR-021 amended 2026-08-02): it is wired into the CONDENSE
            # loop ONLY. On the primary content loop the writer writes to the
            # feedforward budget and correctors ADD demanded content, so the
            # only draft satisfying the budget preference is structurally the
            # pre-review draft — run 14's settle substituted it, silently
            # discarding the attested scope fact and every reviewer-demanded
            # delivery of five rounds. Budget ownership: the feedforward word
            # budget in the writer prompt, the page-gated condense pass below,
            # and the LETTER_OVER_BUDGET line for an over-budget primary settle.
            def _within_budget(draft: dict) -> bool:
                return within_word_budget(draft, norm.letter_body_word_budget)

            # #306 (b): the retain_if/prefer_if substitution must not be
            # evidence-blind — charter run #7 case 2 substituted an earlier
            # round's draft that satisfied BOTH structural predicates but had
            # silently dropped the case's OEE arc (61 % -> 73 %). load_bearing_fn
            # is the SAME keyword_ledger already routed to the reviewer prompt
            # above (coverage_reviewer_prompt_fn) — see services/load_bearing.py
            # for the shared "load-bearing claim" definition.
            load_bearing_fn = load_bearing_fn_from_ledger(keyword_ledger)

            # #306: the CORRECTOR half of the coverage scan. Every round the
            # loop already computes verified_missing_claimable and hands it to
            # the REVIEWER (coverage_reviewer_prompt_fn above); the writer that
            # can act on it was never told the other half — which terms the
            # draft it is patching already holds. So a corrector rewrite of one
            # flagged sentence silently deleted grounded, coverage-bearing
            # content in it, and the loop spent the next rounds re-demanding
            # what an earlier draft already had (2026-08-06 chain=cover_letter:
            # round 1 {Shopfloor-Management, Deutsch, SAP MM, Englisch} →
            # round 2 {Deutsch, Englisch} → round 3 {SMED, KVP}, neither ever
            # demanded before and both present in drafts 0 AND 1 — exhausted
            # 5/5). Same instrument, same ledger, no new LLM call and no new
            # pass (ADR-058 freeze); literal presence is a FACT (ADR-062
            # clause 1) and the block states the SAME grounding-outranks-
            # coverage precedence the reviewer block does (ADR-062 clause 4).
            corrector_prompt_fn = coverage_corrector_prompt_fn(
                build_retry_prompt, keyword_ledger
            )

            letter_data = await review_and_refine(
                source=grounding_source,
                draft=letter_data,
                generator_prompt_fn=corrector_prompt_fn,
                generator_system=COVER_LETTER_REFINEMENT_PROMPT,
                reviewer_prompt_fn=reviewer_prompt_fn,
                reviewer_system=REVIEW_SYSTEM_PROMPT,
                provider=provider,
                max_retries=LLM_REVIEW_MAX_RETRIES,
                chain_id="cover_letter",
                # #272 Task 3: the ADR-021 loop has no no-regression invariant — a
                # reviewer mistake (RC-C/RC-E) can erode content a prior round had
                # right (RC-D: the real closing paragraph, eroded to a bare stub).
                # retain_if is opt-in and structural-only (never a quality score);
                # when the settled draft fails it, an earlier round's draft that
                # passed is substituted instead — no new LLM call.
                retain_if=has_closing_paragraph,
                load_bearing_fn=load_bearing_fn,
            )

            # #420 (ADR-021 amended 2026-08-02): with prefer_if gone from this
            # loop, an over-budget settle that never trips the page gate below
            # ships wordy — recorded, never resolved by reverting content.
            _settled_word_count = body_word_count(letter_data)
            if _settled_word_count > norm.letter_body_word_budget:
                log_letter_over_budget(
                    "cover_letter", _settled_word_count, norm.letter_body_word_budget
                )

            # #254 — deterministic figure-attribution guard, run on the FINAL
            # settled output of the review/corrector loop (never mid-loop): the
            # ADR-021 corrector sees the whole profile and can mint a figure
            # borrowed from an unrelated role/story (e.g. "mentoring teams of
            # 5+" borrowed from a different position's "team of five"). Belt
            # and suspenders with the Oracle's post-hoc detection (services/
            # oracle/audit.py) — this is the generation-path prevention half.
            letter_data = guard_letter_figures(letter_data, profile.profile_json if profile else {})

            # #261 (run-4 blind hiring-panel finding): the letter-side twin of the
            # CV's outcome-preference guard — surface a measured result over a bare
            # target/projection for the same, unambiguously-named initiative. Same
            # settled-output contract as the figure guard above.
            letter_data = guard_letter_outcome_preference(
                letter_data, profile.profile_json if profile else {}, detected_language
            )

            # F3 (blind PQ blocker): the recipient the user typed in the generate dialog
            # is overlaid deterministically AFTER the LLM/review step — the LLM's JSON
            # solely owns letter_data, so a typed recipient could otherwise be silently
            # dropped (null) or altered. User input always wins (AC #2).
            letter_data = _apply_recipient_overrides(letter_data, pre_gen)

            # The letter date is system-injected AFTER review — the model never sets it
            # (the prior date-hallucination fix, 2026-06-10). recipient.date stays null
            # through generation + review, then is stamped here from the system clock.
            letter_data = _inject_letter_date(letter_data, detected_language)

            # #189: the sign-off is chrome — overwrite it with the language-routed label
            # so an EN letter never closes with the German "Mit freundlichen Grüßen"
            # (ADR-038). And backfill the sender name from the candidate's real name when
            # the LLM left signature.name/header.name empty (the fallback profile_json
            # path fed a blank name), so the letter is never unsigned.
            letter_data = _normalize_signature_closing(letter_data, detected_language)
            letter_data = _backfill_sender_name(letter_data, cv_data, profile)
            # #307: the Anrede gets its own paragraph — the writer runs it into the
            # opening sentence, which is the DACH letter's most visible formal defect.
            letter_data = _split_inline_salutation(letter_data)

            # Store the letter body, but keep status 'generating' for now. E037 PQ #2
            # (ATS "not available" race): the ATS audit must be persisted BEFORE status
            # flips to 'ready', because the frontend fetches the report once with no retry
            # — a 'ready' row that has no report yet read NULL and showed "unavailable"
            # permanently. So: persist letter_data → render smoke PDF → audit (commits the
            # report while still 'generating') → ONLY THEN flip to 'ready'. The ready flip
            # is the last write, so 'ready' is never observable before the report exists.
            cl.letter_data = letter_data
            await db.commit()

            # Generate PDF via Playwright. allow_unready=True lets the renderer work while
            # the letter is still 'generating' (the audit needs the PDF before the flip).
            pdf_bytes: bytes | None = None
            try:
                from applire.services.cover_letter_pdf import render_pdf
                pdf_bytes = await render_pdf(cl_id, allow_unready=True)
            except Exception as pdf_err:
                logger.warning("PDF render failed for CL %s: %s", cl_id, pdf_err)
                # HTML preview still works; PDF download will fail gracefully

            # #177 / ADR-051 §6 (amended 2026-07-16): the letter gets the CV's guarantee
            # shape — measure the real render, ONE bounded condense-regenerate, audit as
            # the honest backstop. Letters have no bullet model, so the condense is an
            # LLM rewrite routed back through the grounding review (ADR-040) — a
            # deliberate, scoped deviation from the CV's deterministic cuts (ADR-approved
            # amendment). Skipped when the user has section overrides (their edits win;
            # the audit still reports). The page-count measurement is deliberately
            # fail-open — the same philosophy as the audit itself (services/cv.py
            # ``_update_ats_report``): a measurement error must never fail generation or
            # keep status away from 'ready'.
            if pdf_bytes is not None and not (cl.section_overrides or {}):
                page_count: int | None = None
                try:
                    from applire.services.ats_audit import extract_text_and_pages
                    _, page_count = extract_text_and_pages(pdf_bytes)
                except Exception as measure_err:
                    logger.warning(
                        "Letter page-count measurement failed for CL %s: %s", cl_id, measure_err
                    )
                if page_count is not None and page_count > norm.letter_pages:
                    # Review Finding 2: the condense generation+review is a bounded,
                    # best-effort optimization pass over an ALREADY-VALID rendered
                    # letter. A transient LLM error here must fail OPEN — keep the
                    # original letter_data/pdf_bytes untouched and let the ATS audit
                    # stay the honest backstop — rather than propagate to the outer
                    # handler and mark the whole letter 'failed', discarding a good
                    # letter over a failed optimization.
                    original_letter_data = letter_data
                    try:
                        condensed = await provider.aparse_json(
                            build_condense_prompt(
                                letter_data, norm.letter_body_word_budget, page_count,
                                letter_pages=norm.letter_pages,
                            ),
                            system=SYSTEM_PROMPT,
                            max_tokens=CV_GENERATION_MAX_TOKENS,
                        )
                        # #270 Fix D: same composed reviewer wrapper as the primary loop
                        # above — the condense pass is a fresh rewrite and must be
                        # re-checked for cross-document conflicts against cv_data too.
                        condensed = await review_and_refine(
                            source=grounding_source,
                            draft=condensed,
                            # #306: same corrector-side coverage retention as
                            # the primary loop — the condense pass is a fresh
                            # rewrite under length pressure, and on 2026-08-06
                            # the cover_letter_condense chain lost SMED at
                            # round 2 exactly as the primary chain did.
                            generator_prompt_fn=corrector_prompt_fn,
                            generator_system=COVER_LETTER_REFINEMENT_PROMPT,
                            reviewer_prompt_fn=reviewer_prompt_fn,
                            reviewer_system=REVIEW_SYSTEM_PROMPT,
                            provider=provider,
                            max_retries=LLM_REVIEW_MAX_RETRIES,
                            chain_id="cover_letter_condense",
                            # #272 Task 3: same structural retention guard as the
                            # primary loop — the condense pass is a fresh rewrite
                            # under length pressure and must not lose the closing
                            # paragraph either.
                            retain_if=has_closing_paragraph,
                            # Wave-6 follow-up (charter run #6, Task 2): same
                            # secondary tie-break as the primary loop — among
                            # closing-satisfying drafts THIS condense loop produces,
                            # prefer one that also fits the budget. retain_if still
                            # decides eligibility, so a condense round that dropped
                            # the closing again is never selected just because it
                            # is shorter.
                            prefer_if=_within_budget,
                            # #306 (b): same evidence-blind-substitution guard as
                            # the primary loop above — the condense pass is a
                            # fresh rewrite under length pressure and must not
                            # trade load-bearing figures away for a shorter shape.
                            load_bearing_fn=load_bearing_fn,
                        )
                        # #254 — same generation-path guard as the primary loop
                        # above: the condense pass is itself a fresh corrector-
                        # style rewrite routed back through review_and_refine
                        # and must be checked again before it ships.
                        condensed = guard_letter_figures(
                            condensed, profile.profile_json if profile else {}
                        )
                        # #261 — same generation-path guard as the primary loop above,
                        # re-applied to the condense pass's fresh rewrite.
                        condensed = guard_letter_outcome_preference(
                            condensed, profile.profile_json if profile else {}, detected_language
                        )
                        condensed = _apply_recipient_overrides(condensed, pre_gen)
                        condensed = _inject_letter_date(condensed, detected_language)
                        # #189: the condense pass is a fresh LLM rewrite, so re-apply the
                        # deterministic chrome sign-off + sender-name backfill to it too.
                        condensed = _normalize_signature_closing(condensed, detected_language)
                        condensed = _backfill_sender_name(condensed, cv_data, profile)
                        # #307: the condense pass is a fresh rewrite — re-split too.
                        condensed = _split_inline_salutation(condensed)
                        letter_data = condensed
                        # Wave-6 follow-up (charter run #6, Task 3): the condense
                        # pass is bounded to exactly one round (ADR-051 §6) and
                        # retain_if never sacrifices the closing to hit the budget
                        # — so the letter CAN still ship over budget (with a
                        # genuine closing) when no round satisfied both. That must
                        # stay countable after the fact, not silent.
                        _condensed_word_count = body_word_count(letter_data)
                        if _condensed_word_count > norm.letter_body_word_budget:
                            log_letter_over_budget(
                                "cover_letter_condense",
                                _condensed_word_count,
                                norm.letter_body_word_budget,
                            )
                        cl.letter_data = letter_data
                        await db.commit()
                        try:
                            pdf_bytes = await render_pdf(cl_id, allow_unready=True)
                        except Exception as pdf_err:
                            logger.warning("Condense re-render failed for CL %s: %s", cl_id, pdf_err)
                            # Finding 1 / ADR-039: the re-render failed, so pdf_bytes
                            # must NOT keep the STALE pre-condense PDF — that PDF
                            # describes content that no longer matches letter_data
                            # (already overwritten above). A NULL report (or the
                            # fresh internal re-render _update_ats_report_letter
                            # falls back to when pdf=None) beats an audit persisted
                            # against stale content.
                            pdf_bytes = None
                    except Exception as condense_err:
                        logger.warning(
                            "Condense pass failed for CL %s — keeping the original letter: %s",
                            cl_id, condense_err,
                        )
                        # #181 (review item 4): if the failure was the condense
                        # db.commit() itself, cl.letter_data is already the condensed
                        # value in-memory and the session needs a rollback before it
                        # can be reused — otherwise the function's final commit would
                        # persist the half-condensed state (or raise on a dirty
                        # session). Roll back to discard the failed transaction, then
                        # refresh cl so it holds the last-committed (original) value as
                        # clean, LOADED state — a bare rollback leaves cl expired, and
                        # the expired-attribute reload during the final flush trips a
                        # MissingGreenlet (rollback expires the ORM).
                        try:
                            await db.rollback()
                            await db.refresh(cl)
                        except Exception as restore_err:  # pragma: no cover - defensive
                            logger.warning(
                                "Condense rollback/restore failed for CL %s: %s",
                                cl_id, restore_err,
                            )
                        letter_data = original_letter_data

            # ADR-039 — persist the ATS audit (commits while status is still 'generating').
            # An audit failure is non-fatal: it leaves ats_report NULL and we still flip ready.
            await _update_ats_report_letter(cl, db, pdf=pdf_bytes)

            # Now flip to 'ready' — the report is already committed, so the frontend's
            # single fetch always sees it.
            cl.status = CoverLetterStatus.ready.value
            await db.commit()

        except Exception as exc:
            logger.exception("Cover letter generation failed for %s: %s", cl_id, exc)
            async with AsyncSessionLocal() as err_db:
                err_result = await err_db.execute(
                    select(GeneratedCoverLetter).where(GeneratedCoverLetter.id == cl_id)
                )
                err_cl = err_result.scalar_one_or_none()
                if err_cl is not None:
                    err_cl.status = CoverLetterStatus.failed.value
                    err_cl.error_message = str(exc)[:500]
                    await err_db.commit()


# ---------------------------------------------------------------------------
# ADR-039: ATS audit persistence helpers (letter twin of services/cv.py)
# ---------------------------------------------------------------------------


async def _latest_keyword_ledger(db: AsyncSession, job_id: uuid.UUID) -> list[dict] | None:
    """Return the latest non-deleted GapAnalysis Keyword Ledger for *job_id* (ADR-048/US203).

    Mirrors the generation-path gap query; ``None`` for legacy pre-E037 rows (then all
    missing keywords default to honest-gap in the ATS report).
    """
    from applire.models.gap import GapAnalysis

    result = await db.execute(
        select(GapAnalysis)
        .where(
            GapAnalysis.job_analysis_id == job_id,
            GapAnalysis.deleted_at.is_(None),
        )
        .order_by(GapAnalysis.created_at.desc())
        .limit(1)
    )
    gap = result.scalar_one_or_none()
    return (gap.keyword_ledger or []) if gap else None


async def _update_ats_report_letter(
    cl: GeneratedCoverLetter,
    db: AsyncSession,
    pdf: bytes | None = None,
) -> None:
    """ADR-039 — letter twin of services/cv.py:_update_ats_report.

    Engine errors leave ats_report NULL, never raise — an audit failure must
    NEVER fail or alter generation status.

    Deliberately wipes any previous report on error: ADR-039 forbids a persisted
    report describing a document state it was not computed from (no stale reports).
    A NULL report is always preferable to a report computed from old content.

    Args:
        cl:  The cover letter ORM object (already in *db*'s session).
        db:  The active async session.
        pdf: Pre-rendered PDF bytes from the generation path's smoke render.
             When provided, the render is reused and no second Playwright
             launch is needed.  The BackgroundTasks patch path leaves this
             None, triggering a fresh render inside the try block.
    """
    try:
        from applire.services.ats_audit import audit_cover_letter
        from applire.services.cover_letter_pdf import render_pdf

        pdf = pdf if pdf is not None else await render_pdf(cl.id)
        job = await db.get(JobAnalysis, cl.job_analysis_id)
        letter_data = _apply_section_overrides(cl.letter_data, cl.section_overrides or {})
        # ADR-048 / US203: the latest Keyword Ledger buckets each MISSING keyword as
        # missing-claimable vs missing-honest-gap (legacy rows have none → all honest-gap).
        ledger = await _latest_keyword_ledger(db, cl.job_analysis_id)
        # #249 run-4: same shared-predicate guard as the CV path — a keyword with a
        # literal vault tie never lands in present_unsupported (one vocabulary).
        from applire.services.keyword_ledger import profile_literal_corpus

        profile_row = await db.get(MasterProfile, cl.profile_id)
        vault_text_norm = (
            profile_literal_corpus(profile_row.profile_json if profile_row else None)
            or None
        )
        cl.ats_report = audit_cover_letter(
            pdf,
            letter_data,
            list(job.keywords or []) if job else [],
            ledger,
            vault_text_norm=vault_text_norm,
        ).model_dump()
    except Exception:
        logger.exception("ATS audit failed for cover letter %s — ats_report left NULL", cl.id)
        cl.ats_report = None
    # E043/US246 (ADR-052 §4): truthfulness self-audit rides the same commit as
    # the artifact + ATS report (letter twin of services/cv.py). Deterministic-
    # only, non-fatal, never gates delivery.
    try:
        from applire.services.oracle.selfaudit import build_self_audit_report

        profile_row = await db.get(MasterProfile, cl.profile_id)
        audited = _apply_section_overrides(cl.letter_data, cl.section_overrides or {})
        # ADR-068 clause 7: the JD-resolved OUTPUT language (ADR-038,
        # ``resolve_jd_language`` — the same value the writer generated this
        # letter in, see e.g. line ~662's ``detected_language =
        # resolve_jd_language(job)``) is this document's own language for the
        # cross-language judgement seam. A job-less audit (job row missing/
        # deleted) leaves it ``None`` — the seam then stays off, fail-open,
        # exactly like the pre-ADR-068 report.
        judgement_job = await db.get(JobAnalysis, cl.job_analysis_id)
        cl.truthfulness_report = await build_self_audit_report(
            profile_row.profile_json if profile_row else {},
            letter_data=audited,
            provider=get_provider(),
            document_language=(
                resolve_jd_language(judgement_job) if judgement_job else None
            ),
        )
    except Exception:
        logger.exception(
            "Truthfulness self-audit failed for cover letter %s — report left NULL", cl.id
        )
        cl.truthfulness_report = None
    # ADR-060 Pass B (amended 2026-07-30, #322): the outcome critic's
    # cross-document coherence advisory rides the same commit as the
    # artifact + the two reports above. A SEPARATE try block, deliberately
    # independent of the ats_report block's local variables (like the
    # truthfulness block above it) — a render_pdf/audit failure earlier in
    # this function must never take the critic down with it, or vice versa.
    # Non-fatal, never gates delivery; see run_pass_b's own docstring for the
    # full set of short-circuits (SF-CRITIC.1/.8).
    try:
        from applire.services.outcome_critic import run_pass_b

        job_row = await db.get(JobAnalysis, cl.job_analysis_id)
        ledger = await _latest_keyword_ledger(db, cl.job_analysis_id)
        audited_letter = _apply_section_overrides(cl.letter_data, cl.section_overrides or {})

        # Pass B needs BOTH documents (ADR-060 amended 2026-07-30). Resolution
        # corrected 2026-07-31 (third amendment / SF-CRITIC.10): resolving the
        # CV EXCLUSIVELY via flow.generated_cv_id starved this pass on every
        # direct-generation path — that field is populated only by an explicit
        # flow-advance-to-complete with the CV as artifact_id, which a direct
        # generate_cv call (charter runs; agent-door runs that never advance
        # the flow) never performs. Run-11 ground truth: `ran:false,
        # reason:missing_cv` persisted while a ready CV for the same job sat
        # two minutes away. Fallback is deterministic: the latest ready
        # GeneratedCV for the SAME job_analysis_id. Both paths log which one
        # resolved, so a starved run is diagnosable from the log alone.
        cv_record = None
        flow_result = await db.execute(
            select(FlowSession).where(
                FlowSession.job_id == cl.job_analysis_id,
                FlowSession.deleted_at.is_(None),
            )
        )
        flow = flow_result.scalar_one_or_none()
        if flow is not None and flow.generated_cv_id is not None:
            cv_record = await db.get(GeneratedCV, flow.generated_cv_id)
        if cv_record is None:
            fallback_result = await db.execute(
                select(GeneratedCV)
                .where(
                    GeneratedCV.job_analysis_id == cl.job_analysis_id,
                    GeneratedCV.status == CVGenerationStatus.ready.value,
                )
                .order_by(GeneratedCV.created_at.desc())
                .limit(1)
            )
            cv_record = fallback_result.scalar_one_or_none()
            if cv_record is not None:
                logger.info(
                    "outcome critic (letter mount): CV resolved via latest-ready "
                    "fallback for job %s (flow link absent — SF-CRITIC.10)",
                    cl.job_analysis_id,
                )
        else:
            logger.info(
                "outcome critic (letter mount): CV resolved via flow.generated_cv_id"
            )

        # The critic must judge the CV the user actually has — overrides
        # applied, exactly as the render path does (the letter side already
        # applied its own overrides above; asymmetry found 2026-07-31).
        cv_tailored: dict | None = None
        if cv_record is not None:
            cv_tailored = cv_record.tailored_data
            if cv_record.section_overrides:
                from applire.schemas.cv import TailoredCVData
                from applire.services.cv_section_editor import (
                    apply_overrides_to_tailored,
                )

                try:
                    cv_tailored = apply_overrides_to_tailored(
                        TailoredCVData.model_validate(cv_record.tailored_data),
                        cv_record.content_snapshot,
                        cv_record.section_overrides,
                    ).model_dump(mode="json")
                except Exception:
                    # Advisory pass: a row that fails strict validation (legacy
                    # shape) is judged un-overridden rather than not at all.
                    logger.warning(
                        "outcome critic (letter mount): could not apply CV "
                        "section overrides for %s — judging the raw "
                        "tailored_data instead",
                        cv_record.id,
                    )

        from applire.services.jd_excerpt import build_jd_excerpt

        critic_provider = get_provider()
        critic_report = await run_pass_b(
            cv_tailored=cv_tailored,
            letter_data=audited_letter,
            keyword_ledger=ledger,
            job_role_title=job_row.role_title if job_row else None,
            jd_excerpt=build_jd_excerpt(job_row.raw_text) if job_row else None,
            provider=critic_provider,
        )
        cl.critic_report = critic_report.model_dump(mode="json")
    except Exception:
        logger.exception(
            "Outcome critic Pass B failed for cover letter %s — critic_report left NULL",
            cl.id,
        )
        cl.critic_report = None
    await db.commit()


async def _update_ats_report_letter_by_id(cl_id: uuid.UUID) -> None:
    """BackgroundTasks entrypoint — own session (request session gone by run time)."""
    async with AsyncSessionLocal() as db:
        cl = await db.get(GeneratedCoverLetter, cl_id)
        if cl is not None:
            await _update_ats_report_letter(cl, db)


async def get_cover_letter_ats_report(cl_id: uuid.UUID, db: AsyncSession) -> "ATSReportResponse":
    """Return the persisted ATS report for a cover letter (ADR-039).

    Raises LookupError if the cover letter is not found (→ 404 in the router).
    """
    from applire.schemas.ats import ATSReport, ATSReportResponse

    result = await db.execute(
        select(GeneratedCoverLetter).where(
            GeneratedCoverLetter.id == cl_id,
            GeneratedCoverLetter.deleted_at.is_(None),
        )
    )
    cl = result.scalar_one_or_none()
    if cl is None:
        raise LookupError(f"Cover letter {cl_id} not found")
    # E037 PQ #2 hardening: a non-conforming stored report must degrade to report:null,
    # never raise (which would surface as an HTTP 500 the frontend can't recover from).
    report = None
    if cl.ats_report:
        try:
            report = ATSReport.model_validate(cl.ats_report)
        except Exception:
            logger.warning(
                "Stored ATS report for cover letter %s is malformed — returning report=null",
                cl.id,
            )
            report = None
    return ATSReportResponse(document_id=cl.id, status=cl.status, report=report)


async def get_cover_letter_truthfulness_report(
    cl_id: uuid.UUID, db: AsyncSession
) -> "TruthfulnessReportResponse":
    """Return the persisted truthfulness report for a cover letter (ADR-052/US246).

    Raises LookupError if the cover letter is not found (→ 404 in the router).
    A malformed stored report degrades to report=null, never a 500.
    """
    from applire.schemas.oracle import TruthfulnessReport, TruthfulnessReportResponse

    result = await db.execute(
        select(GeneratedCoverLetter).where(
            GeneratedCoverLetter.id == cl_id,
            GeneratedCoverLetter.deleted_at.is_(None),
        )
    )
    cl = result.scalar_one_or_none()
    if cl is None:
        raise LookupError(f"Cover letter {cl_id} not found")
    report = None
    if cl.truthfulness_report:
        try:
            report = TruthfulnessReport.model_validate(cl.truthfulness_report)
        except Exception:
            logger.warning(
                "Stored truthfulness report for cover letter %s is malformed — "
                "returning report=null",
                cl.id,
            )
            report = None
    return TruthfulnessReportResponse(document_id=cl.id, status=cl.status, report=report)


async def get_cover_letter_critic_report(
    cl_id: uuid.UUID, db: AsyncSession
) -> "OutcomeCriticReportResponse":
    """Return the persisted ADR-060 Pass B advisory for a cover letter (#322).

    Mirror of :func:`get_cover_letter_truthfulness_report` — same envelope
    shape, same malformed-row degrade-to-null contract. Raises LookupError if
    the cover letter is not found (→ 404 in the router). This is the ONE
    read path both doors use (REST router + MCP tool both call this
    function directly, ADR-066 clause 2) — there is no second implementation
    to drift out of parity.
    """
    from applire.schemas.outcome_critic import OutcomeCriticReport, OutcomeCriticReportResponse

    result = await db.execute(
        select(GeneratedCoverLetter).where(
            GeneratedCoverLetter.id == cl_id,
            GeneratedCoverLetter.deleted_at.is_(None),
        )
    )
    cl = result.scalar_one_or_none()
    if cl is None:
        raise LookupError(f"Cover letter {cl_id} not found")
    report = None
    if cl.critic_report:
        try:
            report = OutcomeCriticReport.model_validate(cl.critic_report)
        except Exception:
            logger.warning(
                "Stored outcome-critic report for cover letter %s is malformed — "
                "returning report=null",
                cl.id,
            )
            report = None
    return OutcomeCriticReportResponse(document_id=cl.id, status=cl.status, report=report)


# ---------------------------------------------------------------------------
# Agent door: render_document (E044/US250, ADR-054)
# ---------------------------------------------------------------------------


async def render_agent_letter(
    content: dict,
    job_id: uuid.UUID,
    db: AsyncSession,
    template: str = "classic_german",
) -> GeneratedCoverLetter:
    """Render agent-authored cover-letter content (ADR-054) — letter twin of
    ``services.cv.render_agent_cv``.

    The caller is the author: content is persisted VERBATIM except for
    (a) the photo strip (``header.photo_url`` is never honored — no letter
    template renders it and ``storage.read`` has no traversal guard), and
    (b) the chrome rule (US249): caller-supplied ``recipient.date`` /
    ``signature.closing`` are kept verbatim; only when absent does Applire
    inject the norm-conformant defaults — a deliberate deviation from the
    pipeline, which OVERWRITES both (ADR-054 §4: never rewrite agent content).

    Sequencing mirrors the generation path (E037 PQ #2): the row is committed
    while still 'generating' (``render_pdf`` opens its OWN session — an
    in-memory-only row is invisible there), pre-rendered with
    ``allow_unready=True``, audited (reports commit while 'generating'), and
    only then flipped 'ready' — so 'ready' is never observable without reports.
    """
    from applire.schemas.cover_letter import LetterData

    job = await db.get(JobAnalysis, job_id)
    if job is None:
        raise LookupError(f"Job analysis {job_id} not found")

    profile_result = await db.execute(
        select(MasterProfile)
        .where(MasterProfile.deleted_at.is_(None))
        .order_by(MasterProfile.created_at.desc())
        .limit(1)
    )
    profile = profile_result.scalar_one_or_none()
    if profile is None:
        raise LookupError("No profile found — import a CV first")

    # extra="forbid" throughout LetterData: unknown fields raise ValidationError
    # with field paths for the MCP layer to surface (US251).
    letter = LetterData.model_validate(content)
    letter_data = letter.model_dump(mode="json")

    # Photo strip (security) — see render_agent_cv.
    letter_data["header"]["photo_url"] = None

    # Chrome rule: inject only when the caller left it empty.
    language = resolve_jd_language(job)
    if not letter_data["recipient"].get("date"):
        letter_data = _inject_letter_date(letter_data, language)
    if not letter_data["signature"].get("closing"):
        letter_data = _normalize_signature_closing(letter_data, language)
    # #224: a missing Anrede is a formal defect — inject the generic floor when
    # the author didn't open with a salutation. Scoped to the agent door; the
    # generation pipeline's LLM reliably writes its own salutation.
    letter_data = _inject_salutation(letter_data, language)
    # #307: an agent may write the Anrede inline just as the pipeline writer does.
    letter_data = _split_inline_salutation(letter_data)

    cl = GeneratedCoverLetter(
        job_analysis_id=job_id,
        profile_id=profile.id,
        letter_data=letter_data,
        pre_gen_inputs={},
        template=template,
        status=CoverLetterStatus.generating.value,
        origin="agent",
    )
    db.add(cl)
    await db.commit()
    await db.refresh(cl)

    pdf_bytes: bytes | None = None
    try:
        from applire.services.cover_letter_pdf import render_pdf

        pdf_bytes = await render_pdf(cl.id, allow_unready=True)
    except Exception as pdf_err:
        # Fail-open like the pipeline: HTML preview still works; the audit
        # below degrades to a NULL ATS report (truthfulness needs no PDF).
        logger.warning("PDF render failed for agent letter %s: %s", cl.id, pdf_err)

    await _update_ats_report_letter(cl, db, pdf=pdf_bytes)

    cl.status = CoverLetterStatus.ready.value
    await db.commit()
    await db.refresh(cl)
    return cl

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
import hashlib
import json
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from applire.constants import JD_ANALYSIS_MAX_TOKENS, LLM_REVIEW_MAX_RETRIES
from applire.models.job import JobAnalysis
from applire.prompts.job_analysis import SYSTEM_PROMPT, build_user_prompt
from applire.prompts.review_job_analysis import (
    JOB_ANALYSIS_REFINEMENT_PROMPT,
    JOB_ANALYSIS_REVIEW_SYSTEM_PROMPT,
    build_job_analysis_retry_prompt,
    build_job_analysis_review_prompt,
)
from applire.providers.embedding.base import EmbeddingProvider
from applire.providers.embedding.noop import NoopEmbeddingProvider
from applire.providers.llm.base import LLMProvider
from applire.schemas.job import JobAnalysisResponse
from applire.services.jd_level_guard import apply_jd_level_guard
from applire.services.jd_shape_guard import apply_jd_shape_guard
from applire.services.reviewer import review_and_refine
from applire.utils.language_detection import detect_language

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# KldB 2020 validation helpers
# Source: Bundesagentur für Arbeit — Klassifikation der Berufe 2020 (BA-Klassifikation)
# ---------------------------------------------------------------------------
_KLDB_PATH = Path(__file__).parent.parent / "data" / "kldb2020.json"


def _load_kldb_codes() -> set[str]:
    """Load valid KldB 2020 codes from the bundled lookup table (excluding _meta)."""
    try:
        raw: dict = json.loads(_KLDB_PATH.read_text(encoding="utf-8"))
        return {k for k in raw if k != "_meta"}
    except Exception:
        logger.warning("Could not load kldb2020.json; berufsbild_code validation disabled.", exc_info=True)
        return set()


_VALID_KLDB_CODES: set[str] = _load_kldb_codes()


def _validate_berufsbild(code: str | None, label: str | None) -> tuple[str | None, str | None]:
    """Validate and normalise berufsbild fields from LLM output.

    Returns (code, label) if the code is present in the KldB 2020 lookup,
    otherwise (None, None) with a warning log (not fatal).
    """
    if not code:
        return None, None
    code = code.strip()
    if _VALID_KLDB_CODES and code not in _VALID_KLDB_CODES:
        logger.warning(
            "berufsbild_code %r not found in KldB 2020 lookup; storing as null.", code
        )
        return None, None
    return code, (label.strip() if label else None)


_DEFAULT_EMBEDDING_PROVIDER = NoopEmbeddingProvider()


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# --- LLM payload type coercion (charter run #8) --------------------------------
#
# `analyze_jd` fed the LLM payload straight into the ORM with only `or ""` guards.
# Those guard EMPTINESS, never TYPE — and a dict is truthy, so it sails through and
# reaches a `Text` column. Charter run #8 crashed the endpoint with a 500 exactly
# there: after the `job_analysis` review loop exhausted all five retries (it has never
# converged on this JD — runs 6, 7 and 8 all ran to exhaustion), the fifth corrector
# round returned
#     "language_requirement": {"Deutsch": "sehr gut", "Englisch": "gut"}
# where every earlier round had returned a string. Run 7 shipped an equally unreviewed
# fifth-round draft and merely got a string that time.
#
# The lesson is the general one, so the guard is general: an unconverged review loop is
# free to drift the payload's SHAPE, not just its content, and the boundary between
# "whatever the model returned" and "our schema" has to be a real boundary. Coerce every
# field to the type its column declares, and log it — a coercion is evidence the loop
# drifted, so it must not be silent.
_JD_TEXT_FIELDS = (
    "company_name",
    "role_title",
    "seniority_level",
    "language_requirement",
    # Founder ruling B-2 / migration 0068: ONE string, never a list of fields of
    # study — an unconverged loop that returns ["Master's degree", "Computer
    # science"] here is exactly the shape this field exists to stop, so it is
    # flattened and logged like every other text field rather than crashing.
    "education_requirement",
    "berufsbild_code",
    "berufsbild_label",
)
_JD_LIST_FIELDS = (
    "required_skills",
    "nice_to_have_skills",
    "keywords",
    "company_culture_signals",
)


def _as_text(value: object) -> object:
    """Flatten a model-returned value into a string, preserving its information.

    A dict becomes ``"key: value; key: value"`` rather than being dropped — the
    run-#8 payload genuinely carried both language requirements, and discarding
    them would trade a crash for silent data loss.
    """
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "; ".join(f"{k}: {v}" for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return str(value)


def _as_str_list(value: object) -> object:
    """Normalise a list-typed field to ``list[str]``.

    These are JSONB columns, so a wrong shape does not crash the write — it breaks
    `build_keyword_ledger` and everything downstream of it instead, which is worse
    because it fails later and further away.
    """
    if value is None or isinstance(value, list) and all(isinstance(v, str) for v in value):
        return value
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        # `{"5S": "required", ...}` — the KEYS are the concepts; the values are the
        # model editorialising about them.
        return [str(k) for k in value]
    if isinstance(value, (list, tuple)):
        return [v if isinstance(v, str) else str(v) for v in value]
    return [str(value)]


def _coerce_jd_payload(data: dict) -> dict:
    """Coerce an LLM job-analysis payload to the types the ORM columns declare."""
    coerced = dict(data)
    drifted: list[str] = []
    for field in _JD_TEXT_FIELDS:
        if field in coerced:
            fixed = _as_text(coerced[field])
            if fixed is not coerced[field]:
                drifted.append(f"{field}({type(coerced[field]).__name__}→str)")
                coerced[field] = fixed
    for field in _JD_LIST_FIELDS:
        if field in coerced:
            fixed = _as_str_list(coerced[field])
            if fixed is not coerced[field]:
                drifted.append(f"{field}({type(coerced[field]).__name__}→list[str])")
                coerced[field] = fixed
    if drifted:
        logger.warning(
            "analyze_jd: LLM payload shape drift coerced before persistence — %s. "
            "This is normal-looking output from an UNCONVERGED review loop; check "
            "REVIEW_EXHAUSTED for chain=job_analysis.",
            ", ".join(drifted),
        )
    return coerced


def _clean(value: str | None) -> str | None:
    """A caller-supplied label, stripped; blank → None."""
    if value is None:
        return None
    value = value.strip()
    return value or None


async def get_job_for_user(
    db: AsyncSession, job_id: uuid.UUID, user_id: uuid.UUID
) -> JobAnalysis:
    """The shared posting ``job_id`` if ``user_id`` holds a link to it (ADR-092 cl. 5c).

    The posting cache has no owner (S-17); a user reaches a posting only through
    their own ``applications`` row for it (RD-2). A soft-deleted application still
    counts — removing the card keeps access to the posting. A missing posting, a
    soft-deleted posting and a posting the user never analysed are the same
    ``OwnedNotFound("job")`` (404 ``{"detail": "job not found"}``, S-10).
    """
    from applire.models.application import Application
    from applire.ownership import OwnedNotFound

    job = (
        await db.execute(
            select(JobAnalysis)
            .join(Application, Application.job_analysis_id == JobAnalysis.id)
            .where(
                JobAnalysis.id == job_id,
                JobAnalysis.deleted_at.is_(None),
                Application.user_id == user_id,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if job is None:
        raise OwnedNotFound("job")
    return job


async def caller_link(db: AsyncSession, job_id: uuid.UUID, user_id: uuid.UUID):
    """The caller's ``applications`` row for ``job_id`` — soft-deleted included
    (a hidden repost link, 4a-1, is still the caller's link) — or ``None``."""
    from applire.models.application import Application

    return (
        await db.execute(
            select(Application).where(
                Application.user_id == user_id,
                Application.job_analysis_id == job_id,
            )
        )
    ).scalar_one_or_none()


async def ensure_application_link(
    db: AsyncSession,
    job: JobAnalysis,
    user_id: uuid.UUID,
    *,
    role_title_override: str | None = None,
    company_name_override: str | None = None,
    source_url: str | None = None,
    hidden: bool = False,
):
    """Get-or-create the caller's link to a posting — their ``applications`` row (RD-2).

    Analyze (REST + MCP) calls this so the analysing user can reach the posting
    (``get_job_for_user``); an agent-only analysis becomes a visible tracking card
    (ADR-058 door parity). A new row is ``user_status='tracking'`` with the
    posting's labels denormalised, overrides winning; an existing row — also a
    soft-deleted one, which keeps its deleted state — receives only the non-blank
    overrides (#222: the authoritative title a later call carries is never
    dropped, and never written to the shared posting, ADR-092 cl. 5a).
    ``source_url`` is the CALLER's own URL — never the shared row's, which may
    be another user's (MD-10). ``hidden`` creates a NEW row soft-deleted — the
    link exists (access works, cl. 5c) but no dashboard card appears (used for a
    recognised repost, Branch F / 4a-1). Flushes; the caller commits.
    """
    from sqlalchemy.exc import IntegrityError

    from applire.models.application import Application

    role = _clean(role_title_override)
    company = _clean(company_name_override)

    async def _existing():
        return (
            await db.execute(
                select(Application).where(
                    Application.user_id == user_id,
                    Application.job_analysis_id == job.id,
                )
            )
        ).scalar_one_or_none()

    app = await _existing()
    if app is None:
        candidate = Application(
            user_id=user_id,
            job_analysis_id=job.id,
            role_title=role or job.role_title,
            company_name=company or job.company_name,
            source_url=source_url,
        )
        if hidden:
            from datetime import datetime, timezone

            candidate.deleted_at = datetime.now(timezone.utc)
        try:
            async with db.begin_nested():
                db.add(candidate)
                await db.flush()
            return candidate
        except IntegrityError:
            # A concurrent analyze of the same posting by the same user won
            # uq_application_user_job — adopt the winner (savepoint rolled back).
            app = await _existing()
            if app is None:
                raise
    if role is not None:
        app.role_title = role
    if company is not None:
        app.company_name = company
    if source_url and not (app.source_url or "").strip():
        # MD-31: the caller's own URL fills their own empty slot, so the response
        # (`posting_response`, which reads only this row) still carries the URL
        # the caller just analysed from.
        app.source_url = source_url
    await db.flush()
    return app


_SCOPE_KINDS = ("team_size", "budget")
_SCOPE_COMPARATORS = ("approx", "min", "exact", "range")
_SCOPE_LEVELS = ("required", "nice_to_have")


def _coerce_scope_requirements(raw: object, jd_text: str) -> list[dict]:
    """ADR-069 clause 1's deterministic floor on the extracted scope bars.

    Facts only (ADR-062): kind/comparator/level in their closed sets, value
    numeric, quote a non-empty string that actually occurs in the posting
    (whitespace-folded — the reviewer judges wording, this checks presence).
    An entry failing any check is dropped and logged — never repaired, never
    invented. The no-invention rule lives in the prompt; this floor only
    guarantees nothing structurally invalid reaches the ORM or the ledger.
    """
    if not isinstance(raw, list):
        return []
    folded_jd = " ".join(jd_text.split()).casefold()
    kept: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        kind = entry.get("kind")
        value = entry.get("value")
        value_max = entry.get("value_max")
        comparator = entry.get("comparator") or "approx"
        quote = entry.get("quote")
        level = entry.get("level") or "required"
        ok = (
            kind in _SCOPE_KINDS
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and (value_max is None or (isinstance(value_max, (int, float)) and not isinstance(value_max, bool)))
            and comparator in _SCOPE_COMPARATORS
            and level in _SCOPE_LEVELS
            and isinstance(quote, str)
            and quote.strip()
            and " ".join(quote.split()).casefold() in folded_jd
        )
        if not ok:
            logger.warning(
                "analyze_jd: dropping structurally invalid scope_requirements "
                "entry %r (ADR-069 floor — closed kinds, numeric value, quote "
                "present in the posting).",
                entry,
            )
            continue
        kept.append(
            {
                "kind": kind,
                "value": float(value),
                "value_max": float(value_max) if value_max is not None else None,
                "comparator": comparator,
                "quote": quote.strip(),
                "level": level,
            }
        )
    return kept


_LEADERSHIP_EMPHASIS_VALUES = ("leadership_led", "balanced", "hands_on_led")


def _coerce_leadership_emphasis(raw: object, jd_text: str) -> dict | None:
    """#271's deterministic floor on the extracted leadership weighting.

    Facts only (ADR-062 clause 1 — the JUDGEMENT "does this posting weight
    leadership" is the model's and stays the model's; this checks structure,
    nothing else). Mirrors :func:`_coerce_scope_requirements`: ``emphasis`` in
    its closed set, ``quote`` a non-empty string that actually occurs in the
    posting (whitespace-folded — the reviewer judges wording, this checks
    presence). Anything else is dropped whole and logged, never repaired and
    never invented, so a fabricated weighting cannot reach the ORM.

    Returns exactly the two consumed fields; keys the model volunteers are not
    persisted. ``None`` means "no weighting stored" — which the selection seam
    resolves at use time, because it is also what every pre-migration row holds.
    """
    if not isinstance(raw, dict):
        return None
    emphasis = raw.get("emphasis")
    quote = raw.get("quote")
    ok = (
        emphasis in _LEADERSHIP_EMPHASIS_VALUES
        and isinstance(quote, str)
        and quote.strip()
        and " ".join(quote.split()).casefold() in " ".join(jd_text.split()).casefold()
    )
    if not ok:
        logger.warning(
            "analyze_jd: dropping structurally invalid leadership_emphasis %r "
            "(#271 floor — closed emphasis set, quote present in the posting).",
            raw,
        )
        return None
    return {"emphasis": emphasis, "quote": quote.strip()}


async def analyze_jd(
    text: str,
    db: AsyncSession,
    provider: LLMProvider,
    source_url: str | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    role_title_override: str | None = None,
    company_name_override: str | None = None,
    *,
    user_id: uuid.UUID | None = None,
    raw_text_origin: str | None = None,
) -> JobAnalysisResponse:
    """See :func:`_analyze_jd_once`; retried ONCE on an ``IntegrityError``.

    ADR-092 cl. 11 / SF-OWN.4: a concurrent erasure can delete the shared
    posting between this call's cache hit and its link insert (the link's FK
    then fails). The retry runs the whole dedup again in a fresh transaction —
    the posting is re-found or re-analysed, never linked to a deleted row.
    """
    from sqlalchemy.exc import IntegrityError

    kwargs = dict(
        source_url=source_url,
        embedding_provider=embedding_provider,
        role_title_override=role_title_override,
        company_name_override=company_name_override,
        user_id=user_id,
        raw_text_origin=raw_text_origin,
    )
    try:
        return await _analyze_jd_once(text, db, provider, **kwargs)
    except IntegrityError:
        # The link/posting inserts run in savepoints, so the outer transaction is
        # normally still usable (and the caller's loaded objects stay loaded); roll
        # back only a transaction the error left inactive.
        tx = db.sync_session.get_transaction()
        if tx is not None and not tx.is_active:
            await db.rollback()
        logger.warning("analyze_jd: integrity error (concurrent posting delete?) — retrying the dedup once.")
        return await _analyze_jd_once(text, db, provider, **kwargs)


async def _analyze_jd_once(
    text: str,
    db: AsyncSession,
    provider: LLMProvider,
    source_url: str | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    role_title_override: str | None = None,
    company_name_override: str | None = None,
    *,
    user_id: uuid.UUID | None = None,
    raw_text_origin: str | None = None,
) -> JobAnalysisResponse:
    """Analyse a posting into the shared cache and link it to the caller (ADR-092 cl. 5).

    * The ``job_analyses`` row is one shared, immutable analysis per posting
      (S-17): a cache hit by URL or text hash returns the existing row, and no
      caller value is ever written onto it — the title/company overrides (#222)
      land on the caller's own ``applications`` row (RD-2).
    * URL dedup only matches rows whose text Applire scraped itself
      (``raw_text_origin='scraped'``, MD-10): pasted text may carry a person's
      notes and must never reach another user by URL. ``raw_text_origin``
      defaults to ``scraped`` when ``source_url`` is given (both doors pass a
      URL only when they fetched the text from it), else ``supplied``.
    * The caller's link is get-or-created (``ensure_application_link``) and the
      response carries the caller's effective labels (``posting_labels``) and
      the Branch-F repost hint (E039/US220), computed BEFORE the link so the
      fresh link never flags itself.
    * Named residual (cl. 5e): an instant cache hit reveals that someone
      analysed this posting before.
    """
    from applire.services.owner_resolution import resolve_user_id

    uid = resolve_user_id(user_id, "job.analyze_jd")
    origin = raw_text_origin or ("scraped" if source_url else "supplied")
    if origin not in ("scraped", "supplied"):
        raise ValueError(f"raw_text_origin must be 'scraped' or 'supplied', got {origin!r}")

    existing: JobAnalysis | None = None
    if source_url and origin == "scraped":
        existing = (
            await db.execute(
                select(JobAnalysis)
                .where(
                    JobAnalysis.source_url == source_url,
                    JobAnalysis.raw_text_origin == "scraped",
                )
                .order_by(JobAnalysis.created_at)
                .limit(1)
            )
        ).scalar_one_or_none()

    raw_hash = _hash_text(text)
    if existing is None:
        existing = (
            await db.execute(select(JobAnalysis).where(JobAnalysis.raw_text_hash == raw_hash))
        ).scalar_one_or_none()
    if existing is not None:
        return await _link_and_respond(
            db, existing, uid, text, source_url, role_title_override, company_name_override
        )

    # Stage label (#538/#539 pattern, applied here for #617). The review loop
    # labels its own calls — `reviewer.py:715` sets the chain id — but THIS call
    # fires before the loop starts, so it inherits whatever the contextvar
    # happens to hold: nothing on a fresh task, or the PREVIOUS chain's label
    # when a JD analysis follows another chain in the same task. Measured on the
    # captured corpus: 1,528 of 1,531 extractor records carry no usable stage,
    # which makes every log-based per-chain count silently wrong about the one
    # call that produces the draft the whole loop then argues about.
    from applire.providers.llm.debug_log import set_stage as _set_llm_log_stage

    _set_llm_log_stage("job_analysis")
    data: dict = await provider.aparse_json(
        build_user_prompt(text),
        system=SYSTEM_PROMPT,
        temperature=0.1,
        max_tokens=JD_ANALYSIS_MAX_TOKENS,
    )

    # #264 (ADR-021 review-loop coverage): every downstream truthfulness surface
    # (keyword ledger, gap analysis, interview, tailoring) treats required/nice-to-have
    # skills as ground truth about what the posting asked for — a fabricated requirement
    # here poisons all of them. No deterministic grounding guard exists for this output
    # today (only the KldB code lookup and the "something JD-like is present" garbage
    # check below), so it gets the standard author/reviewer loop.
    data = await review_and_refine(
        source=text,
        draft=data,
        generator_prompt_fn=build_job_analysis_retry_prompt,
        generator_system=JOB_ANALYSIS_REFINEMENT_PROMPT,
        reviewer_prompt_fn=build_job_analysis_review_prompt,
        reviewer_system=JOB_ANALYSIS_REVIEW_SYSTEM_PROMPT,
        provider=provider,
        max_retries=LLM_REVIEW_MAX_RETRIES,
        generator_max_tokens=JD_ANALYSIS_MAX_TOKENS,
        chain_id="job_analysis",
        # Wave-6 Task 2: company_name/role_title were observed being dropped entirely
        # by a false-positive reviewer round and never recovered (#264 follow-up) —
        # once either field is populated in any round, it must never ship absent.
        required_fields=("company_name", "role_title"),
        # ADR-069 clause 4: a required↔nice-to-have move the corrector performed
        # but did not declare in `level_changes` is reverted (run 12 silently
        # demoted a required skill across correction rounds, 2026-07-31 18:05).
        settle_guard=apply_jd_level_guard,
        # #537 measurement-only: this chain's draft is schema JSON (classification
        # fields + keyword lists), so the two-sided ungrounded-value compliance
        # shape is safe here — a keyword is in the list or it is not; there is no
        # aspiration-reframe escape the prose chains have.
        structured_output=True,
    )

    # Wave-6 Task 3 (belt and braces): the review loop's prompt-level shape
    # contract (concept terms, never sentences) is necessary but not
    # sufficient — apply the deterministic guard to the settled output before
    # it feeds build_keyword_ledger(). Conservative by design: only drops a
    # sentence-shaped entry when a concept-shaped equivalent is already
    # present; anything ambiguous is left alone and logged, never invented.
    data = apply_jd_shape_guard(data)

    # Charter run #8: the boundary between "whatever the model returned" and our
    # schema. Runs before every read of `data` below, so no field reaches the ORM
    # (or the garbage check) with a shape the column cannot hold.
    data = _coerce_jd_payload(data)

    emb_provider = embedding_provider or _DEFAULT_EMBEDDING_PROVIDER
    try:
        embedding = await emb_provider.embed(text)
    except Exception:
        logger.warning("Embedding generation failed for JD; storing NULL.", exc_info=True)
        embedding = None

    # Don't persist zero-vectors (noop provider) — NULL signals "not computed".
    if embedding is not None and all(v == 0.0 for v in embedding):
        embedding = None

    inferred_role_title = (data.get("role_title") or "").strip()
    required = data.get("required_skills") or []
    nice = data.get("nice_to_have_skills") or []
    # US159 / FMEA JF-M-4.5: validity must not hinge solely on the title. A real
    # JD that merely lacks an explicit title line is still valid when requirements
    # were extracted — the UI asks for the title inline (see the JD echo, US158).
    # Reject only true garbage (no title AND nothing JD-like), so this — our only
    # garbage detector — keeps surfacing a 422 instead of a 500. Run the check on
    # the INFERRED title, not the override: an authoritative title supplies a
    # missing title line, it must not rescue non-JD text as a valid JobAnalysis.
    if not inferred_role_title and not required and not nice:
        raise ValueError(
            "The provided text does not appear to be a job description "
            "(no role title or requirements could be detected)."
        )

    # ADR-092 cl. 5a: the shared row keeps what the POSTING says; a caller's
    # authoritative title/company (#222) goes onto their application below.
    # The creation-time override is gone too (cl. 5a): an empty inferred title
    # stays empty here and the caller's override shows through its application.
    role_title = inferred_role_title
    company_name = (data.get("company_name") or None)

    berufsbild_code, berufsbild_label = _validate_berufsbild(
        data.get("berufsbild_code"),
        data.get("berufsbild_label"),
    )

    record = JobAnalysis(
        raw_text_hash=raw_hash,
        raw_text=text,
        source_url=source_url,
        raw_text_origin=origin,
        company_name=company_name,
        role_title=role_title,
        required_skills=data.get("required_skills", []),
        nice_to_have_skills=data.get("nice_to_have_skills", []),
        keywords=data.get("keywords", []),
        scope_requirements=_coerce_scope_requirements(
            data.get("scope_requirements"), text
        ),
        leadership_emphasis=_coerce_leadership_emphasis(
            data.get("leadership_emphasis"), text
        ),
        # #675 line 39 / migration 0067: an honest null (the posting grounds
        # no tier) is stored as NULL, never laundered into "" — the two used
        # to be indistinguishable, which silently withheld
        # gap_inference's "N years meets seniority bar" signal.
        seniority_level=data.get("seniority_level") or None,
        company_culture_signals=data.get("company_culture_signals", []),
        language_requirement=data.get("language_requirement") or "",
        # Founder ruling B-2 / migration 0068 (#675 line 78): an honest "the
        # posting states no education bar" is stored as NULL, never laundered
        # into "" — the same distinction migration 0067 restored for
        # seniority_level. A whitespace-only value is the model saying nothing.
        education_requirement=(data.get("education_requirement") or "").strip() or None,
        jd_language=detect_language(text),
        berufsbild_code=berufsbild_code,
        berufsbild_label=berufsbild_label,
        embedding=embedding,
    )
    from sqlalchemy.exc import IntegrityError

    try:
        async with db.begin_nested():
            db.add(record)
            await db.flush()
    except IntegrityError:
        # Another analysis of the same text committed first (raw_text_hash is
        # instance-wide unique) — adopt the winner; the shared row is the same
        # posting by construction.
        record = (
            await db.execute(select(JobAnalysis).where(JobAnalysis.raw_text_hash == raw_hash))
        ).scalar_one_or_none()
        if record is None:
            raise
    return await _link_and_respond(
        db, record, uid, text, source_url, role_title_override, company_name_override
    )


async def _link_and_respond(
    db: AsyncSession,
    job: JobAnalysis,
    user_id: uuid.UUID,
    text: str,
    source_url: str | None,
    role_title_override: str | None,
    company_name_override: str | None,
) -> JobAnalysisResponse:
    """Repost hint (before the link), link, commit, answer with the caller's labels."""
    from applire.services.application import find_duplicate_application

    duplicate_of = None
    try:
        duplicate_of = await find_duplicate_application(
            user_id,
            job_analysis_id=job.id,
            source_url=source_url,
            raw_text=text,
            db=db,
        )
    except Exception:
        # Best-effort read-model enrichment (E039/US220) — never fails the analysis.
        logger.warning("duplicate-JD check failed; returning analysis without hint.", exc_info=True)
    app = await ensure_application_link(
        db,
        job,
        user_id,
        role_title_override=role_title_override,
        company_name_override=company_name_override,
        source_url=source_url,
        # 4a-1 (recommendation B, founder question open): a recognised repost
        # (Branch F) gets a hidden link — no phantom card beside the one the
        # user already has; "continue anyway" (create_application) reactivates it.
        hidden=duplicate_of is not None,
    )
    await db.commit()
    await db.refresh(job)
    await db.refresh(app)
    from applire.services.posting_labels import posting_response

    response = posting_response(job, app)
    response.duplicate_of = duplicate_of
    return response

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


async def _apply_title_overrides(
    record: JobAnalysis,
    role_title_override: str | None,
    company_name_override: str | None,
    db: AsyncSession,
) -> JobAnalysis:
    """Apply caller-supplied title/company overrides to an existing record (#222).

    Used on the dedup/cache-hit paths so a later call carrying the authoritative
    title the first pass lacked isn't silently dropped. Commits only when a value
    actually changes.
    """
    changed = False
    if role_title_override and role_title_override.strip():
        new = role_title_override.strip()
        if record.role_title != new:
            record.role_title = new
            changed = True
    if company_name_override and company_name_override.strip():
        new = company_name_override.strip()
        if record.company_name != new:
            record.company_name = new
            changed = True
    if changed:
        await db.commit()
        await db.refresh(record)
    return record


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
) -> JobAnalysisResponse:
    # #222: LinkedIn (and most boards) separate the title/company from the body,
    # so the caller can pass authoritative values — otherwise the LLM infers a
    # title from the body and a heading leaks into the letter subject.
    # URL-based deduplication: return existing record for the same URL.
    if source_url:
        result = await db.execute(
            select(JobAnalysis).where(JobAnalysis.source_url == source_url)
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing = await _apply_title_overrides(
                existing, role_title_override, company_name_override, db
            )
            return JobAnalysisResponse.model_validate(existing)

    raw_hash = _hash_text(text)

    result = await db.execute(
        select(JobAnalysis).where(JobAnalysis.raw_text_hash == raw_hash)
    )
    existing = result.scalar_one_or_none()
    if existing:
        # #222: a later call may carry the authoritative title the first pass
        # lacked — apply it to the cached record rather than silently dropping it.
        existing = await _apply_title_overrides(
            existing, role_title_override, company_name_override, db
        )
        return JobAnalysisResponse.model_validate(existing)

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

    role_title = inferred_role_title
    if role_title_override and role_title_override.strip():
        role_title = role_title_override.strip()
    company_name = (data.get("company_name") or None)
    if company_name_override and company_name_override.strip():
        company_name = company_name_override.strip()

    berufsbild_code, berufsbild_label = _validate_berufsbild(
        data.get("berufsbild_code"),
        data.get("berufsbild_label"),
    )

    record = JobAnalysis(
        raw_text_hash=raw_hash,
        raw_text=text,
        source_url=source_url,
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
        seniority_level=data.get("seniority_level") or "",
        company_culture_signals=data.get("company_culture_signals", []),
        language_requirement=data.get("language_requirement") or "",
        jd_language=detect_language(text),
        berufsbild_code=berufsbild_code,
        berufsbild_label=berufsbild_label,
        embedding=embedding,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return JobAnalysisResponse.model_validate(record)

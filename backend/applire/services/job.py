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

from applire.models.job import JobAnalysis
from applire.prompts.job_analysis import SYSTEM_PROMPT, build_user_prompt
from applire.providers.embedding.base import EmbeddingProvider
from applire.providers.embedding.noop import NoopEmbeddingProvider
from applire.providers.llm.base import LLMProvider
from applire.schemas.job import JobAnalysisResponse
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


async def analyze_jd(
    text: str,
    db: AsyncSession,
    provider: LLMProvider,
    source_url: str | None = None,
    embedding_provider: EmbeddingProvider | None = None,
) -> JobAnalysisResponse:
    # URL-based deduplication: return existing record for the same URL.
    if source_url:
        result = await db.execute(
            select(JobAnalysis).where(JobAnalysis.source_url == source_url)
        )
        existing = result.scalar_one_or_none()
        if existing:
            return JobAnalysisResponse.model_validate(existing)

    raw_hash = _hash_text(text)

    result = await db.execute(
        select(JobAnalysis).where(JobAnalysis.raw_text_hash == raw_hash)
    )
    existing = result.scalar_one_or_none()
    if existing:
        return JobAnalysisResponse.model_validate(existing)

    data: dict = await provider.aparse_json(
        build_user_prompt(text),
        system=SYSTEM_PROMPT,
        temperature=0.1,
    )

    emb_provider = embedding_provider or _DEFAULT_EMBEDDING_PROVIDER
    try:
        embedding = await emb_provider.embed(text)
    except Exception:
        logger.warning("Embedding generation failed for JD; storing NULL.", exc_info=True)
        embedding = None

    # Don't persist zero-vectors (noop provider) — NULL signals "not computed".
    if embedding is not None and all(v == 0.0 for v in embedding):
        embedding = None

    role_title = (data.get("role_title") or "").strip()
    required = data.get("required_skills") or []
    nice = data.get("nice_to_have_skills") or []
    # US159 / FMEA JF-M-4.5: validity must not hinge solely on the title. A real
    # JD that merely lacks an explicit title line is still valid when requirements
    # were extracted — the UI asks for the title inline (see the JD echo, US158).
    # Reject only true garbage (no title AND nothing JD-like), so this — our only
    # garbage detector — keeps surfacing a 422 instead of a 500.
    if not role_title and not required and not nice:
        raise ValueError(
            "The provided text does not appear to be a job description "
            "(no role title or requirements could be detected)."
        )

    berufsbild_code, berufsbild_label = _validate_berufsbild(
        data.get("berufsbild_code"),
        data.get("berufsbild_label"),
    )

    record = JobAnalysis(
        raw_text_hash=raw_hash,
        raw_text=text,
        source_url=source_url,
        company_name=data.get("company_name") or None,
        role_title=role_title,
        required_skills=data.get("required_skills", []),
        nice_to_have_skills=data.get("nice_to_have_skills", []),
        keywords=data.get("keywords", []),
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

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

"""ADR-038 document-language routing + cover-letter date injection (unit tests).

Bug 1: the cover-letter prompt asked the LLM for "today's date" — hallucinated.
       The date must be injected in code from datetime.now().
Bug 2: document language was routed via job.language_requirement (a candidate
       requirement like "Bilingual DE/EN"), not the JD's own language, and CV
       tailoring had no deterministic language input at all.

No Docker, no LLM. SQLite in-memory + stub provider.
"""

import sys
import uuid
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


GERMAN_JD = """\
Wir suchen einen Creative Director (m/w/d) für unsere Agentur in München.
Sie verantworten die kreative Leitung unserer Markenprojekte und führen ein
Team von zwölf Designerinnen und Designern. Sie entwickeln Kampagnen für
nationale und internationale Kunden und präsentieren die Ergebnisse auf
Geschäftsführungsebene. Erfahrung mit Pitches und Awards ist von Vorteil.
"""

ENGLISH_JD = """\
We are looking for a Creative Director to lead the brand design team at our
Munich studio. You will own the creative direction of all client projects,
mentor a team of twelve designers, and present campaign work to executive
stakeholders. Experience with pitches and award submissions is a plus.
"""


# ---------------------------------------------------------------------------
# detect_language — deterministic DE/EN detection
# ---------------------------------------------------------------------------


class TestDetectLanguage:
    def test_german_jd_detected_as_de(self):
        from applire.utils.language_detection import detect_language
        assert detect_language(GERMAN_JD) == "de"

    def test_english_jd_detected_as_en(self):
        from applire.utils.language_detection import detect_language
        assert detect_language(ENGLISH_JD) == "en"

    def test_german_without_umlauts_detected_as_de(self):
        from applire.utils.language_detection import detect_language
        text = (
            "Wir suchen eine erfahrene Person, die unser Team leitet und "
            "die Verantwortung der kreativen Arbeit mit den Kunden tragen kann."
        )
        assert detect_language(text) == "de"

    def test_empty_text_defaults_to_de(self):
        from applire.utils.language_detection import detect_language
        assert detect_language("") == "de"


# ---------------------------------------------------------------------------
# resolve_jd_language — stored column wins, raw_text fallback for old rows
# ---------------------------------------------------------------------------


def _job(**kwargs):
    from applire.models.job import JobAnalysis
    defaults = dict(
        id=uuid.uuid4(),
        raw_text_hash=uuid.uuid4().hex,
        raw_text=GERMAN_JD,
        role_title="Creative Director",
        required_skills=[],
        nice_to_have_skills=[],
        keywords=[],
        seniority_level="Lead",
        company_culture_signals=[],
        language_requirement="Bilingual DE/EN",
    )
    defaults.update(kwargs)
    return JobAnalysis(**defaults)


class TestResolveJdLanguage:
    def test_stored_jd_language_wins(self):
        from applire.utils.language_detection import resolve_jd_language
        job = _job(jd_language="en")
        assert resolve_jd_language(job) == "en"

    def test_null_jd_language_falls_back_to_raw_text(self):
        from applire.utils.language_detection import resolve_jd_language
        job = _job(jd_language=None, raw_text=GERMAN_JD)
        assert resolve_jd_language(job) == "de"

    def test_bilingual_language_requirement_does_not_misroute_german_jd(self):
        """Regression: 'Bilingual DE/EN' used to resolve to 'en' via
        language_requirement.startswith('de') — the German JD got an English letter."""
        from applire.utils.language_detection import resolve_jd_language
        job = _job(
            jd_language=None,
            raw_text=GERMAN_JD,
            language_requirement="Bilingual DE/EN",
        )
        assert resolve_jd_language(job) == "de"


# ---------------------------------------------------------------------------
# JobAnalysis.jd_language column + analyze_jd persistence
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db():
    """In-memory SQLite session with all models registered."""
    from applire.db.session import Base  # noqa: F401
    import applire.models.user          # noqa: F401
    import applire.models.job           # noqa: F401
    import applire.models.profile       # noqa: F401
    import applire.models.gap           # noqa: F401
    import applire.models.cv            # noqa: F401
    import applire.models.session       # noqa: F401
    import applire.models.flow          # noqa: F401
    import applire.models.uploads       # noqa: F401
    import applire.models.application   # noqa: F401
    import applire.models.color_profile # noqa: F401
    import applire.models.company       # noqa: F401
    import applire.models.user_settings # noqa: F401
    import applire.models.cover_letter  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


_VALID_JD_RESPONSE = {
    "company_name": "Südlicht GmbH",
    "role_title": "Creative Director",
    "required_skills": ["Markenführung"],
    "nice_to_have_skills": [],
    "keywords": ["Kampagnen"],
    "seniority_level": "Lead",
    "company_culture_signals": [],
    "language_requirement": "Bilingual DE/EN",
    "berufsbild_code": None,
    "berufsbild_label": None,
}


def test_job_analysis_model_has_jd_language_column():
    import applire.models.job  # noqa: F401
    from applire.db.session import Base
    cols = [c.name for c in Base.metadata.tables["job_analyses"].columns]
    assert "jd_language" in cols


@pytest.mark.asyncio
async def test_analyze_jd_stores_detected_jd_language_german(db):
    from applire.services.job import analyze_jd
    provider = AsyncMock()
    provider.aparse_json = AsyncMock(return_value=_VALID_JD_RESPONSE)
    result = await analyze_jd(GERMAN_JD, db, provider)

    from sqlalchemy import select
    from applire.models.job import JobAnalysis
    record = (await db.execute(select(JobAnalysis))).scalar_one()
    assert record.jd_language == "de"


@pytest.mark.asyncio
async def test_analyze_jd_stores_detected_jd_language_english(db):
    from applire.services.job import analyze_jd
    provider = AsyncMock()
    provider.aparse_json = AsyncMock(return_value=_VALID_JD_RESPONSE)
    await analyze_jd(ENGLISH_JD, db, provider)

    from sqlalchemy import select
    from applire.models.job import JobAnalysis
    record = (await db.execute(select(JobAnalysis))).scalar_one()
    assert record.jd_language == "en"


# ---------------------------------------------------------------------------
# Cover-letter date — injected in code, never asked from the LLM
# ---------------------------------------------------------------------------


class TestLetterDate:
    def test_format_letter_date_german(self):
        from applire.utils.letter_date import format_letter_date
        assert format_letter_date("de", today=date(2026, 6, 10)) == "10. Juni 2026"

    def test_format_letter_date_english(self):
        from applire.utils.letter_date import format_letter_date
        assert format_letter_date("en", today=date(2026, 6, 10)) == "10 June 2026"

    def test_format_letter_date_defaults_to_today(self):
        from applire.utils.letter_date import format_letter_date
        assert str(date.today().year) in format_letter_date("de")

    def test_inject_letter_date_overwrites_llm_value(self):
        """Regression: both Milan letters were dated '10. Oktober 2023' (LLM guess)."""
        from applire.services.cover_letter import _inject_letter_date
        letter_data = {"recipient": {"company": "Südlicht", "date": "10. Oktober 2023"}}
        result = _inject_letter_date(letter_data, "de", today=date(2026, 6, 10))
        assert result["recipient"]["date"] == "10. Juni 2026"

    def test_inject_letter_date_creates_recipient_if_missing(self):
        from applire.services.cover_letter import _inject_letter_date
        result = _inject_letter_date({}, "en", today=date(2026, 6, 10))
        assert result["recipient"]["date"] == "10 June 2026"

    def test_cover_letter_prompt_does_not_ask_llm_for_todays_date(self):
        from applire.prompts.cover_letter import SYSTEM_PROMPT
        assert "today's date" not in SYSTEM_PROMPT.lower()


class TestSalutationChrome:
    """#224 — a norm-conformant Anrede floor for agent-authored letters."""

    def test_injects_generic_german_salutation_when_absent(self):
        from applire.services.cover_letter import _inject_salutation
        letter_data = {"body": {"paragraphs": ["mit großem Interesse habe ich Ihre Anzeige gelesen."]}}
        result = _inject_salutation(letter_data, "de")
        assert result["body"]["paragraphs"][0] == "Sehr geehrte Damen und Herren,"
        # original first paragraph is preserved, now at index 1
        assert result["body"]["paragraphs"][1].startswith("mit großem Interesse")

    def test_injects_generic_english_salutation_when_absent(self):
        from applire.services.cover_letter import _inject_salutation
        letter_data = {"body": {"paragraphs": ["I read your posting with great interest."]}}
        result = _inject_salutation(letter_data, "en")
        assert result["body"]["paragraphs"][0] == "Dear Sir or Madam,"

    def test_does_not_double_up_when_author_wrote_a_salutation(self):
        from applire.services.cover_letter import _inject_salutation
        # Salutation inline at the start of the first paragraph (the pipeline shape).
        letter_data = {"body": {"paragraphs": [
            "Sehr geehrter Herr Dr. Müller, mit großem Interesse habe ich …"
        ]}}
        result = _inject_salutation(letter_data, "de")
        assert len(result["body"]["paragraphs"]) == 1
        assert result["body"]["paragraphs"][0].startswith("Sehr geehrter Herr Dr. Müller")

    def test_recognises_a_standalone_salutation_paragraph(self):
        from applire.services.cover_letter import _inject_salutation
        letter_data = {"body": {"paragraphs": ["Dear Hiring Team,", "I am writing to apply…"]}}
        result = _inject_salutation(letter_data, "en")
        assert len(result["body"]["paragraphs"]) == 2

    def test_empty_body_gets_a_salutation(self):
        from applire.services.cover_letter import _inject_salutation
        result = _inject_salutation({"body": {"paragraphs": []}}, "de")
        assert result["body"]["paragraphs"] == ["Sehr geehrte Damen und Herren,"]


# ---------------------------------------------------------------------------
# CV tailoring — explicit deterministic output-language directive
# ---------------------------------------------------------------------------


class TestCvTailoringLanguageDirective:
    def test_build_user_prompt_includes_german_directive(self):
        from applire.prompts.cv_tailoring import build_user_prompt
        prompt = build_user_prompt({}, {}, [], [], output_language="de")
        assert "OUTPUT LANGUAGE: GERMAN" in prompt

    def test_build_user_prompt_includes_english_directive(self):
        from applire.prompts.cv_tailoring import build_user_prompt
        prompt = build_user_prompt({}, {}, [], [], output_language="en")
        assert "OUTPUT LANGUAGE: ENGLISH" in prompt

    def test_system_prompt_no_longer_delegates_language_detection(self):
        """Rule 7 used to say 'match the job description language' — pure LLM
        inference. The system prompt must defer to the explicit directive."""
        from applire.prompts.cv_tailoring import SYSTEM_PROMPT
        assert "OUTPUT LANGUAGE" in SYSTEM_PROMPT

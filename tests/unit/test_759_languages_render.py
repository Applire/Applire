# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
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

"""#759, founder ruling E5-2 (2026-10-08): the LANGUAGES list renders in the
document's language. The build-2 delivery run's German CV (1680cd28) read
"German · Native / English · C1 / Czech · B2".

Pinned: the closed vocabulary (whole-string, CEFR codes and unknown phrases
verbatim); one render-context step at each of the three places a CV reaches a
renderer — ``get_cv_html``, the section-editor preview, the ``.docx`` prep —
one named seam test per call site; and that ``tailored_data`` itself is never
rewritten (the Oracle and the audits keep reading the vault's transcription).
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from tests.support.profile_factory import make_master_profile  # noqa: E402

LANGS = [
    {"language": "German", "level": "Native"},
    {"language": "English", "level": "C1"},
    {"language": "Czech", "level": "B2"},
    {"language": "Klingonisch", "level": "conversational, rusty"},
]


def test_names_and_level_words_follow_the_document_language():
    from applire.services.profile.language_names import (
        localized_language_level as lvl,
        localized_language_name as nm,
    )

    assert nm("German", "de") == "Deutsch"
    assert nm("Czech", "de") == "Tschechisch"
    assert nm("Deutsch", "en") == "German"
    assert nm("Deutsch", "de") == "Deutsch"
    assert lvl("Native", "de") == "Muttersprache"
    assert lvl("mother tongue", "de") == "Muttersprache"
    assert lvl("Verhandlungssicher", "en") == "Business fluent"
    assert lvl("Fluent", "de") == "Fließend"


def test_cefr_codes_unknown_names_and_partial_forms_stay_verbatim():
    from applire.services.profile.language_names import (
        localized_language_level as lvl,
        localized_language_name as nm,
    )

    assert lvl("C1", "de") == "C1"
    assert lvl("conversational, rusty", "de") == "conversational, rusty"
    assert nm("Klingonisch", "en") == "Klingonisch"
    assert nm("Chinese (Cantonese)", "de") == "Chinese (Cantonese)"
    assert lvl("Native (C2)", "de") == "Native (C2)"


def test_localize_languages_is_pure_and_returns_a_copy():
    from applire.schemas.cv import TailoredCVData
    from applire.services.cv import localize_languages

    t = TailoredCVData.model_validate({"contact": {"name": "M"}, "languages": LANGS})
    out = localize_languages(t, "de")
    assert [(x.language, x.level) for x in out.languages] == [
        ("Deutsch", "Muttersprache"), ("Englisch", "C1"), ("Tschechisch", "B2"),
        ("Klingonisch", "conversational, rusty"),
    ]
    assert t.languages[0].language == "German", "input not mutated"
    assert localize_languages(t, "en").languages[0].language == "German"


# --- one seam test per render call site -------------------------------------

@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base
    import applire.models.user  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.uploads  # noqa: F401
    import applire.models.color_profile  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _tailored() -> dict:
    return {
        "contact": {"name": "Milan Novak", "email": "milan@example.com"},
        "summary": "Kreativleitung mit fünfzehn Jahren Erfahrung.",
        "work_history": [{
            "id": "w1", "company": "Nivalo GmbH", "role": "Brand Design Lead",
            "start_date": "2022-05", "end_date": None,
            "bullets": ["Führte ein vierköpfiges internes Designteam."],
        }],
        "skills": ["Figma"], "education": [], "languages": LANGS,
    }


async def _seed_ready_cv(db, *, template="creative_sidebar"):
    from applire.models.cv import CVGenerationStatus, GeneratedCV
    from applire.models.job import JobAnalysis
    from applire.models.user import User

    db.add(User(id=uuid.uuid4(), email=f"lang-{uuid.uuid4().hex[:8]}@test.com"))
    job = JobAnalysis(
        id=uuid.uuid4(), raw_text_hash=uuid.uuid4().hex, raw_text="Stelle",
        role_title="Creative Director", company_name="Südlicht", required_skills=[],
        nice_to_have_skills=[], keywords=[], seniority_level="senior",
        company_culture_signals=[], language_requirement="de", jd_language="de",
    )
    db.add(job)
    profile = make_master_profile(profile_json={"personal_info": {"name": "Milan Novak"}})
    db.add(profile)
    await db.flush()
    cv = GeneratedCV(
        job_analysis_id=job.id, profile_id=profile.id, template=template,
        tailored_data=_tailored(), status=CVGenerationStatus.ready.value,
        target_pages=2, document_language="de",
    )
    db.add(cv)
    await db.commit()
    await db.refresh(cv)
    return cv


@pytest.mark.asyncio
async def test_seam_get_cv_html_renders_the_languages_in_german(db):
    from applire.services.cv import get_cv_html

    cv = await _seed_ready_cv(db)
    html = await get_cv_html(cv.id, db)
    assert "Deutsch · Muttersprache" in html
    assert "Tschechisch · B2" in html
    assert "German · Native" not in html
    assert cv.tailored_data["languages"][0]["language"] == "German", \
        "the persisted row keeps the vault's transcription"


@pytest.mark.asyncio
async def test_seam_section_editor_preview_renders_the_languages_in_german(db):
    from applire.schemas.cv import TailoredCVData
    from applire.services.cv_section_editor import build_content_snapshot, patch_cv_section

    cv = await _seed_ready_cv(db)
    cv.content_snapshot = build_content_snapshot(TailoredCVData.model_validate(_tailored()))
    await db.commit()
    resp = await patch_cv_section(
        cv_id=cv.id, section_id="introduction",
        content="Kreativleitung mit Agenturerfahrung.", save_to_profile=False,
        db=db, background_tasks=None,
    )
    assert "Deutsch · Muttersprache" in resp.html
    assert "German · Native" not in resp.html


@pytest.mark.asyncio
async def test_seam_docx_prep_hands_the_writer_german_languages(db):
    from applire.services.cv import _prepare_cv_docx_render

    cv = await _seed_ready_cv(db)
    tailored, lang, *_ = await _prepare_cv_docx_render(cv, db)
    assert lang == "de"
    assert [(x.language, x.level) for x in tailored.languages][:2] == [
        ("Deutsch", "Muttersprache"), ("Englisch", "C1"),
    ]

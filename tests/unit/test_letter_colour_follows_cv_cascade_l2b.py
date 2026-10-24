# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""L-2b (Hazelnut build 4, collector 784:97) — the letter resolves its colour through the
CV's own cascade (``color_detection.resolve_color_context``, ADR-066: one implementation):
the letter row's profile (copied from its CV), else the company's detected colour, else the
owner's default colour, else the system default.

Before: the letter read ``cp.primary`` off a ``ColorProfile`` (the model has no such
attribute — only ``derived["--cv-accent"]``), so a letter whose CV carried a CV-specific
profile raised ``AttributeError`` at render, and the company / user-default colours were
never consulted. One seam test per call site: the HTML render and the .docx preparation.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.support.owners import HARNESS_USER_ID
from tests.support.profile_factory import make_master_profile


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.uploads  # noqa: F401
    import applire.models.user  # noqa: F401
    import applire.models.user_settings  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


def _cp(accent: str):
    from applire.models.color_profile import ColorProfile
    from applire.services.color_detection import derive_tint

    return ColorProfile(
        id=uuid.uuid4(), seed_primary=accent, source="user",
        derived={"--cv-accent": accent, "--cv-accent-tint": derive_tint(accent)},
    )


async def _letter(db, *, letter_profile=None, company_profile=None, user_default=None):
    """A ready letter; the three optional colour sources are the cascade's steps 1-3."""
    from applire.models.company import Company
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.models.job import JobAnalysis
    from applire.models.user import User
    from applire.models.user_settings import UserSettings

    profile = make_master_profile(id=uuid.uuid4(), profile_json={})
    job = JobAnalysis(
        id=uuid.uuid4(), raw_text_hash=str(uuid.uuid4()), raw_text="job", role_title="Engineer",
        required_skills=[], nice_to_have_skills=[], keywords=[], seniority_level="mid",
        company_culture_signals=[], language_requirement="de",
    )
    db.add_all([User(id=HARNESS_USER_ID, email="harness@example.de"), profile, job])
    for cp in (letter_profile, company_profile, user_default):
        if cp is not None:
            db.add(cp)
    await db.commit()
    if company_profile is not None:
        company = Company(name="Acme", domain="acme.example", color_profile_id=company_profile.id)
        db.add(company)
        await db.commit()
        job.company_id = company.id
    if user_default is not None:
        db.add(UserSettings(user_id=HARNESS_USER_ID, default_color_profile_id=user_default.id))
    cl = GeneratedCoverLetter(
        id=uuid.uuid4(), job_analysis_id=job.id, profile_id=profile.id, user_id=HARNESS_USER_ID,
        template="classic_german", status="ready", document_language="de",
        color_profile_id=letter_profile.id if letter_profile is not None else None,
        letter_data={
            "header": {"name": "Anna Bauer"}, "recipient": {"name": "Team"},
            "body": {"paragraphs": ["Text."]},
            "signature": {"closing": "Mit freundlichen Grüßen", "name": "Anna Bauer"},
        },
    )
    db.add(cl)
    await db.commit()
    return cl


CASES = {
    "cv_specific": ({"letter_profile": "#ff0000"}, "#ff0000"),
    "company": ({"company_profile": "#00cc00"}, "#00cc00"),
    "user_default": ({"user_default": "#0000ff"}, "#0000ff"),
    "cv_specific_beats_company_and_user": (
        {"letter_profile": "#ff0000", "company_profile": "#00cc00", "user_default": "#0000ff"}, "#ff0000",
    ),
    "company_beats_user": ({"company_profile": "#00cc00", "user_default": "#0000ff"}, "#00cc00"),
    "system_default": ({}, None),
}


async def _build(db, spec):
    return await _letter(db, **{k: _cp(v) for k, v in spec.items()})


@pytest.mark.asyncio
@pytest.mark.parametrize("name", list(CASES))
async def test_html_render_uses_the_cv_cascade(db, name):
    from applire.services.color_detection import DEFAULT_ACCENT
    from applire.services.cover_letter import get_cover_letter_html

    spec, expected = CASES[name]
    cl = await _build(db, spec)
    html = await get_cover_letter_html(cl.id, db, user_id=HARNESS_USER_ID)
    assert f"--primary: {expected or DEFAULT_ACCENT};" in html


@pytest.mark.asyncio
@pytest.mark.parametrize("name", list(CASES))
async def test_docx_render_uses_the_cv_cascade(db, name):
    from applire.services.color_detection import DEFAULT_ACCENT
    from applire.services.cover_letter import _prepare_cover_letter_docx_render

    spec, expected = CASES[name]
    cl = await _build(db, spec)
    prepared = await _prepare_cover_letter_docx_render(cl, db)
    assert prepared[2] == (expected or DEFAULT_ACCENT)

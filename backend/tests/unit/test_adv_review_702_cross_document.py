# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Strawberry build 2 adversarial review — #702 cross-document card (backend).

Every test here is EXPECTED TO FAIL on the reviewed tree (`a96007bb`); each one
pins one finding of `Documents/Runs/Strawberry/build-2/adv-review/findings.md`.
Same in-memory harness as `test_review_signals_kept.py` (real review router,
real `review_actions` / `review_signals`, the rewriter replaced by a double that
removes every form it is handed — which is Contract 1's stated behaviour,
"remove every matched form of one finding from one section").
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.auth import get_auth_provider
from applire.auth.no_auth import NoAuthProvider
from applire.db.session import get_db
from tests.support.profile_factory import make_master_profile

import applire.routers.review as review_router

# One letter sentence holding a letter-only fact (Kosmetik-Verpackungen) AND a
# letter-richer one (MES-Einführung — the CV names it, the letter says more).
SENTENCE = (
    "Die MES-Einführung bei Weberit und meine Arbeit für Kosmetik-Verpackungen "
    "sind übertragbare Grundlagen."
)
# A different paragraph: the CV-backed achievement the CV itself carries.
ACHIEVEMENT = (
    "Bei Weberit leitete ich die MES-Einführung an 14 Spritzgussmaschinen; "
    "dadurch stieg die OEE von 61 % auf 73 %."
)
REPORT = {
    "ran": True, "mount": "letter", "dropped_citations": 0,
    "advisories": [
        {"kind": "letter_only", "concept": "Kosmetik-Verpackungen",
         "cv_state": "not mentioned in the CV", "letter_state": SENTENCE},
        {"kind": "letter_richer", "concept": "MES-Einführung",
         "cv_state": "MES-Einführung an 14 Spritzgussmaschinen", "letter_state": SENTENCE},
    ],
}


def _key() -> str:
    from applire.schemas.outcome_critic import OutcomeCriticReport

    (item,) = OutcomeCriticReport.model_validate(REPORT).cross_document
    return item.key


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base  # noqa: F401
    import applire.models.user  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.uploads  # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.cover_letter  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _client(db) -> TestClient:
    async def _override_get_db():
        yield db

    app = FastAPI()
    app.dependency_overrides[get_auth_provider] = lambda: NoAuthProvider()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[review_router._get_provider] = lambda: object()
    app.include_router(review_router.router)
    return TestClient(app, raise_server_exceptions=True)


async def _seed_letter(db, paragraphs: list[str]) -> uuid.UUID:
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.models.job import JobAnalysis

    job_id, profile_id, cl_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db.add(JobAnalysis(
        id=job_id, raw_text_hash=str(job_id), raw_text="JD text", role_title="Leiter Operations",
        required_skills=[], nice_to_have_skills=[], keywords=[], seniority_level="lead",
        company_culture_signals=[], language_requirement="de",
    ))
    db.add(make_master_profile(id=profile_id, profile_json={}))
    db.add(GeneratedCoverLetter(
        id=cl_id, job_analysis_id=job_id, profile_id=profile_id, template="classic_german",
        letter_data={"body": {"paragraphs": paragraphs}}, status="ready", document_language="de",
        critic_report=REPORT, review_state=None,
    ))
    await db.commit()
    return cl_id


class _RemoveEveryForm:
    """Contract 1 double: removes every handed form from the handed section."""

    def __init__(self):
        self.calls = []

    async def __call__(self, kind, record, section_id, section_text, forms, provider, *, language,
                       figures_only=False):
        self.calls.append({"section_id": section_id, "forms": list(forms), "text": section_text})
        after = section_text
        for f in forms:
            after = after.replace(f, "").replace("  ", " ")
        return SimpleNamespace(section_id=section_id, before=section_text, after=after,
                               changed=after != section_text, llm_calls=1)


async def _noop_reaudit(kind, record, db, **kw):
    await db.commit()
    await db.refresh(record)


# ── 6. take-out removes a CV-backed (letter_richer) concept ───────────────────
def test_adv_review_6_take_out_target_is_only_the_letter_only_facts():
    """The card marks and counts ONLY the letter-only facts ("Applire schreibt den
    Satz ohne diese Angaben neu" next to the letter-only chips). `cross_document_target`
    sorts letter-only first but keeps every literal concept, so the CV-backed
    "MES-Einführung" is handed to the removal rewrite as well."""
    from applire.services.review_signals import cross_document_target

    _label, wording = cross_document_target(SimpleNamespace(critic_report=REPORT), _key())
    assert wording == ["Kosmetik-Verpackungen"], wording


# ── 6b. end to end: the CV-backed achievement paragraph loses its subject ─────
@pytest.mark.asyncio
async def test_adv_review_6b_take_out_keeps_the_cv_backed_achievement(db):
    """Finding 6 at the delivery point: through the real router, *Aus dem Anschreiben
    nehmen* on the card hands "MES-Einführung" to the removal rewrite, and the CV-backed
    achievement two paragraphs away ("Bei Weberit leitete ich die MES-Einführung an 14
    Spritzgussmaschinen …") comes back without its subject."""
    import applire.services.review_actions as ra

    cl_id = await _seed_letter(db, ["Sehr geehrte Damen und Herren,", ACHIEVEMENT, SENTENCE])
    rewrite = _RemoveEveryForm()
    with patch.object(ra, "_rewriter", return_value=rewrite), patch.object(ra, "reaudit", _noop_reaudit):
        resp = _client(db).post(f"/api/cover-letter/{cl_id}/review/take-out", json={"finding_key": _key()})
    assert resp.status_code == 200, resp.text
    assert ACHIEVEMENT in await _paragraphs(db, cl_id)


# ── 7. take-out is not scoped to the item's sentence ──────────────────────────
@pytest.mark.asyncio
async def test_adv_review_7_take_out_rewrites_only_the_items_sentence(db):
    """The letter body is ONE patchable section, so the removal rewrite gets the whole
    body and removes the forms everywhere. A second, different sentence that also
    names the letter-only fact — one the user was not shown on this card — is
    rewritten too. The ruling: "the take-out rewrite on the letter paragraph holding
    the sentence"; the card: "Applire schreibt den Satz … neu"."""
    import applire.services.review_actions as ra

    other = "Für Kosmetik-Verpackungen habe ich 2022 die Prüfpläne überarbeitet."
    cl_id = await _seed_letter(db, ["Sehr geehrte Damen und Herren,", other, SENTENCE])
    rewrite = _RemoveEveryForm()
    with patch.object(ra, "_rewriter", return_value=rewrite), patch.object(ra, "reaudit", _noop_reaudit):
        resp = _client(db).post(f"/api/cover-letter/{cl_id}/review/take-out", json={"finding_key": _key()})
    assert resp.status_code == 200, resp.text
    assert other in await _paragraphs(db, cl_id)


async def _paragraphs(db, cl_id) -> list[str]:
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.services.cover_letter import _apply_section_overrides

    row = await db.get(GeneratedCoverLetter, cl_id)
    await db.refresh(row)
    return _apply_section_overrides(row.letter_data, row.section_overrides or {})["body"]["paragraphs"]


# ── 8. weight ignores the CV span it holds ────────────────────────────────────
def test_adv_review_8_a_cv_backed_figure_does_not_make_the_item_high():
    """Weight = "a letter-only fact stated with a figure the CV does not carry". The
    rule reads the letter-only advisory's `cv_state`, which for letter_only is the
    literal "not mentioned in the CV" — so EVERY digit in the sentence counts, even
    one the same item's letter-richer advisory shows in the CV ("38"). Feinplanung
    carries no number; the card says "besonders mit Jahreszahlen und Dauern"."""
    from applire.schemas.outcome_critic import CriticAdvisory
    from applire.services.outcome_critic import group_cross_document

    sentence = "Bei Weberit führte ich 38 Mitarbeitende und verantwortete die Feinplanung."
    (item,) = group_cross_document([
        CriticAdvisory(kind="letter_only", concept="Feinplanung",
                       cv_state="not mentioned in the CV", letter_state=sentence),
        CriticAdvisory(kind="letter_richer", concept="Mitarbeiterführung",
                       cv_state="Führung von 38 Mitarbeitenden im Dreischichtbetrieb",
                       letter_state=sentence),
    ])
    assert item.weight == "normal", (item.weight, item.figures)

# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-090 — seam tests for the five review endpoints, per document kind.

Drives the REAL router (``applire.routers.review``) + the REAL
``services/review_actions.py`` over an in-memory SQLite DB, faking only the
three seams named in the run brief:

* ``review_actions.reaudit`` — the real one renders a PDF and runs the ATS/
  Oracle engines; faked here to set ``record.ats_report`` /
  ``record.truthfulness_report`` directly, commit and refresh.
* ``review_actions._rewriter`` — WP-B's ``rewrite_for_removal`` does not exist
  on this branch yet; faked to return an async callable with the documented
  ``section_id/before/after/changed/llm_calls`` shape.
* ``applire.services.profile.reconcile.testimony_bridge.submit_testimony`` —
  the real reconcile engine; faked to return a canned ``TestimonyResult``.

Everything else — ``load_document``, ``patchable_sections``, ``write_section``
(the real ``patch_cv_section`` / ``patch_cover_letter_section``),
``findings_of``, ``review_state`` load/derive/lock — runs for real against
real rows, so a persisted-row assertion actually proves the write happened.

Every test is ``async def`` + ``@pytest.mark.asyncio``, and the FastAPI
``TestClient`` is built (and used) from WITHIN that same coroutine — the
established pattern for a real-DB router seam test in this codebase (see
``tests/unit/test_ruling_d1_assist_denial_floor.py``): the in-memory
``aiosqlite`` session is bound to the test's own event loop, and a sync
``TestClient()`` fixture shared across tests would drive it from a different
one.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.auth import get_auth_provider
from applire.db.session import get_db
from applire.schemas.testimony import TestimonyResult
from tests.support.profile_factory import make_master_profile

import applire.routers.review as review_router
import applire.services.review_actions as ra
import applire.services.review_state as rs

# ── DB fixture (mirrors tests/unit/test_iter23_section_editor.py's `db`) ─────


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
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.user_settings  # noqa: F401
    import applire.models.cover_letter  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


def _client(db) -> TestClient:
    """The real review router + the real cv/cover-letter routers (for the
    ATS-report GET tests), wired to the given session."""
    import applire.routers.cv as cv_router
    import applire.routers.cover_letter as cl_router

    async def _override_get_db():
        yield db

    app = FastAPI()
    app.dependency_overrides[get_auth_provider] = lambda: None
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[review_router._get_provider] = lambda: object()
    app.include_router(review_router.router)
    app.include_router(cv_router.router)
    app.include_router(cl_router.router)
    return TestClient(app, raise_server_exceptions=True)


# ── report builders ───────────────────────────────────────────────────────────


def _ats_report(kind: str, present_unsupported: list[str], matches: dict | None = None,
                 claimable: list[str] | None = None) -> dict:
    document = "cv" if kind == "cv" else "cover_letter"
    return {
        "version": 1,
        "document": document,
        "checks": [],
        "keywords": {
            "present": [],
            "missing": [],
            "missing_claimable": [],
            "missing_honest_gap": [],
            "present_unsupported": list(present_unsupported),
            "present_unsupported_matches": matches or {},
            "claimable_concepts": claimable or [],
        },
        "passed": 0,
        "failed": 0,
    }


_KUBERNETES = "Kubernetes"
_KEY = rs.finding_key("ats", _KUBERNETES)  # "ats:kubernetes"
_MATCHES = {_KUBERNETES: [{"form": _KUBERNETES, "stem": False}]}


# ── seed helpers ──────────────────────────────────────────────────────────────


async def _seed_job_and_profile(db):
    from applire.models.job import JobAnalysis

    job_id = uuid.uuid4()
    profile_id = uuid.uuid4()
    db.add(JobAnalysis(
        id=job_id, raw_text_hash=str(job_id), raw_text="JD text",
        role_title="Engineer", required_skills=[], nice_to_have_skills=[],
        keywords=[], seniority_level="mid", company_culture_signals=[],
        language_requirement="de",
    ))
    db.add(make_master_profile(id=profile_id, profile_json={}))
    await db.commit()
    return job_id, profile_id


async def seed_cv(
    db, *,
    introduction: str = "Erfahrener Entwickler.",
    skills: list[str] | None = None,
    position_bullets: list[str] | None = None,
    ats_report: dict | None = None,
    truthfulness_report: dict | None = None,
    review_state: dict | None = None,
) -> uuid.UUID:
    from applire.models.cv import GeneratedCV

    job_id, profile_id = await _seed_job_and_profile(db)
    skills = skills if skills is not None else ["Python", "FastAPI"]
    position_bullets = position_bullets if position_bullets is not None else ["Baute APIs auf."]
    position_uuid = str(uuid.uuid4())

    content_snapshot = {
        "introduction": introduction,
        "skills": skills,
        "positions": [{
            "id": position_uuid, "index": 0, "title": "Engineer", "company": "Acme",
            "period": "2020-01", "bullets": position_bullets,
        }],
    }
    tailored_data = {
        "contact": {"name": "Max"},
        "summary": introduction,
        "work_history": [{
            "company": "Acme", "role": "Engineer", "start_date": "2020-01",
            "bullets": position_bullets,
        }],
        "skills": skills,
    }
    cv_id = uuid.uuid4()
    db.add(GeneratedCV(
        id=cv_id, job_analysis_id=job_id, profile_id=profile_id,
        tailored_data=tailored_data, template="classic_german", status="ready",
        content_snapshot=content_snapshot, document_language="de",
        ats_report=ats_report, truthfulness_report=truthfulness_report,
        review_state=review_state,
    ))
    await db.commit()
    return cv_id


async def seed_letter(
    db, *,
    paragraphs: list[str] | None = None,
    ats_report: dict | None = None,
    truthfulness_report: dict | None = None,
    review_state: dict | None = None,
) -> uuid.UUID:
    from applire.models.cover_letter import GeneratedCoverLetter

    job_id, profile_id = await _seed_job_and_profile(db)
    paragraphs = paragraphs if paragraphs is not None else ["Erster Absatz."]
    letter_data = {
        "header": {"name": "Anna Bauer"},
        "recipient": {"name": "Recruiting Team"},
        "body": {"paragraphs": paragraphs},
        "signature": {"closing": "Mit freundlichen Grüßen", "name": "Anna Bauer"},
    }
    cl_id = uuid.uuid4()
    db.add(GeneratedCoverLetter(
        id=cl_id, job_analysis_id=job_id, profile_id=profile_id,
        template="classic_german", letter_data=letter_data, status="ready",
        document_language="de",
        ats_report=ats_report, truthfulness_report=truthfulness_report,
        review_state=review_state,
    ))
    await db.commit()
    return cl_id


async def _set_review_state(db, kind, doc_id, state):
    record = await ra.load_document(kind, doc_id, db)
    record.review_state = state
    await db.commit()


# ── fakes for the three seams ────────────────────────────────────────────────


class FakeReaudit:
    """Records calls; applies the next (ats, truth) pair from a queue, or
    holds the record's current reports steady if the queue is exhausted."""

    def __init__(self, sequence: list[tuple[dict | None, dict | None]] | None = None):
        self.calls = 0
        self._sequence = list(sequence or [])

    async def __call__(self, kind, record, db):
        self.calls += 1
        if self._sequence:
            ats, truth = self._sequence.pop(0)
            record.ats_report = ats
            record.truthfulness_report = truth
        await db.commit()
        await db.refresh(record)


class FakeRewrite:
    """WP-B's ``rewrite_for_removal`` contract: one (changed, after) outcome
    per section_id; unlisted sections are reported unchanged."""

    def __init__(self, outcomes: dict[str, tuple[bool, str]]):
        self.calls: list[str] = []
        self._outcomes = outcomes

    async def __call__(self, kind, record, section_id, section_text, forms, provider, *, language):
        self.calls.append(section_id)
        changed, after = self._outcomes.get(section_id, (False, section_text))
        return SimpleNamespace(
            section_id=section_id, before=section_text, after=after,
            changed=changed, llm_calls=1,
        )


def _canned_testimony(status: str) -> TestimonyResult:
    return TestimonyResult(
        submission_id=str(uuid.uuid4()), status=status,
        changes=[], confirmations=[], conflicts=[], not_applied=[], matched=[],
    )


# ── add-evidence ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cv_add_evidence_applied_records_added_and_awaits_reaudit(db):
    cv_id = await seed_cv(
        db, introduction="Kenntnisse in Kubernetes.",
        ats_report=_ats_report("cv", [_KUBERNETES], _MATCHES),
    )
    client = _client(db)
    fake_reaudit = FakeReaudit([(_ats_report("cv", []), None)])  # post-reaudit: cleared

    with patch.object(ra, "reaudit", new=fake_reaudit), \
         patch(
             "applire.services.profile.reconcile.testimony_bridge.submit_testimony",
         ) as mock_submit:
        mock_submit.return_value = _canned_testimony("applied")
        response = client.post(
            f"/api/cv/{cv_id}/review/add-evidence",
            json={"finding_key": _KEY, "text": "Ich habe drei Jahre Kubernetes betrieben."},
        )

    assert response.status_code == 200, response.text
    data = response.json()
    assert fake_reaudit.calls == 1, "the re-audit must be awaited before the response"
    # The response's report is the POST-reaudit one — the term cleared.
    assert data["report"]["report"]["keywords"]["present_unsupported"] == []
    assert data["testimony"]["status"] == "applied"
    decisions = data["review_state"]["decisions"]
    assert len(decisions) == 1
    assert decisions[0]["finding_key"] == _KEY
    assert decisions[0]["action"] == "added"


@pytest.mark.asyncio
async def test_cover_letter_add_evidence_applied_records_added_and_awaits_reaudit(db):
    cl_id = await seed_letter(
        db, paragraphs=["Ich bringe Kubernetes-Erfahrung mit."],
        ats_report=_ats_report("cover_letter", [_KUBERNETES], _MATCHES),
    )
    client = _client(db)
    fake_reaudit = FakeReaudit([(_ats_report("cover_letter", []), None)])

    with patch.object(ra, "reaudit", new=fake_reaudit), \
         patch(
             "applire.services.profile.reconcile.testimony_bridge.submit_testimony",
         ) as mock_submit:
        mock_submit.return_value = _canned_testimony("applied")
        response = client.post(
            f"/api/cover-letter/{cl_id}/review/add-evidence",
            json={"finding_key": _KEY, "text": "Ich habe drei Jahre Kubernetes betrieben."},
        )

    assert response.status_code == 200, response.text
    data = response.json()
    assert fake_reaudit.calls == 1
    assert data["report"]["report"]["keywords"]["present_unsupported"] == []
    decisions = data["review_state"]["decisions"]
    assert len(decisions) == 1 and decisions[0]["action"] == "added"


@pytest.mark.asyncio
async def test_cv_add_evidence_no_change_records_no_decision(db):
    cv_id = await seed_cv(
        db, introduction="Kenntnisse in Kubernetes.",
        ats_report=_ats_report("cv", [_KUBERNETES], _MATCHES),
    )
    client = _client(db)
    # Re-audit is still awaited even though nothing gets decided.
    fake_reaudit = FakeReaudit([(_ats_report("cv", [_KUBERNETES], _MATCHES), None)])

    with patch.object(ra, "reaudit", new=fake_reaudit), \
         patch(
             "applire.services.profile.reconcile.testimony_bridge.submit_testimony",
         ) as mock_submit:
        mock_submit.return_value = _canned_testimony("no_change")
        response = client.post(
            f"/api/cv/{cv_id}/review/add-evidence",
            json={"finding_key": _KEY, "text": "Nur ein Gruß."},
        )

    assert response.status_code == 200, response.text
    data = response.json()
    assert fake_reaudit.calls == 1
    assert data["review_state"]["decisions"] == []


# ── take-out ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cv_take_out_only_rewrites_sections_holding_the_wording_and_records_taken_out(db):
    """Introduction holds "Kubernetes"; skills and the position bullet do
    not — only introduction must be passed to the rewriter."""
    intro = "Erfahrener Entwickler mit Kubernetes Erfahrung."
    cv_id = await seed_cv(
        db, introduction=intro, skills=["Python", "FastAPI"],
        position_bullets=["Baute APIs auf."],
        ats_report=_ats_report("cv", [_KUBERNETES], _MATCHES),
    )
    client = _client(db)
    new_intro = "Erfahrener Entwickler."
    fake_rewrite = FakeRewrite({"introduction": (True, new_intro)})
    fake_reaudit = FakeReaudit([(_ats_report("cv", []), None)])  # cleared

    with patch.object(ra, "_rewriter", lambda: fake_rewrite), \
         patch.object(ra, "reaudit", new=fake_reaudit):
        response = client.post(
            f"/api/cv/{cv_id}/review/take-out",
            json={"finding_key": _KEY},
        )

    assert response.status_code == 200, response.text
    data = response.json()
    assert fake_rewrite.calls == ["introduction"], (
        "only the section whose text holds the finding's wording may reach the rewriter"
    )
    assert data["changes"] == [{"section_id": "introduction", "before": intro, "after": new_intro}]
    assert data["still_listed"] is False
    assert fake_reaudit.calls == 1

    from applire.models.cv import GeneratedCV
    record = await db.get(GeneratedCV, cv_id)
    assert record.section_overrides["introduction"] == new_intro
    decisions = record.review_state["decisions"]
    assert len(decisions) == 1
    assert decisions[0]["action"] == "taken_out"
    assert decisions[0]["undo"]["sections"] == [{"section_id": "introduction", "before": intro}]


@pytest.mark.asyncio
async def test_cover_letter_take_out_only_section_is_body_joined_by_blank_line(db):
    paragraphs = ["Erster Absatz.", "Zweiter Absatz mit Kubernetes Erfahrung.", "Dritter Absatz."]
    before_text = "\n\n".join(paragraphs)
    cl_id = await seed_letter(
        db, paragraphs=paragraphs,
        ats_report=_ats_report("cover_letter", [_KUBERNETES], _MATCHES),
    )
    client = _client(db)
    after_text = "\n\n".join(["Erster Absatz.", "Zweiter Absatz.", "Dritter Absatz."])
    fake_rewrite = FakeRewrite({"body": (True, after_text)})
    fake_reaudit = FakeReaudit([(_ats_report("cover_letter", []), None)])

    with patch.object(ra, "_rewriter", lambda: fake_rewrite), \
         patch.object(ra, "reaudit", new=fake_reaudit):
        response = client.post(
            f"/api/cover-letter/{cl_id}/review/take-out",
            json={"finding_key": _KEY},
        )

    assert response.status_code == 200, response.text
    data = response.json()
    assert fake_rewrite.calls == ["body"]
    assert data["changes"] == [{"section_id": "body", "before": before_text, "after": after_text}]
    assert data["still_listed"] is False


@pytest.mark.asyncio
async def test_cv_take_out_unchanged_rewrite_saves_nothing_and_records_no_decision(db):
    intro = "Erfahrener Entwickler mit Kubernetes Erfahrung."
    cv_id = await seed_cv(
        db, introduction=intro,
        ats_report=_ats_report("cv", [_KUBERNETES], _MATCHES),
    )
    client = _client(db)
    fake_rewrite = FakeRewrite({"introduction": (False, intro)})  # changed=False
    fake_reaudit = FakeReaudit()

    with patch.object(ra, "_rewriter", lambda: fake_rewrite), \
         patch.object(ra, "reaudit", new=fake_reaudit):
        response = client.post(
            f"/api/cv/{cv_id}/review/take-out",
            json={"finding_key": _KEY},
        )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["changes"] == []
    assert fake_reaudit.calls == 0, "no reaudit when nothing was rewritten"

    from applire.models.cv import GeneratedCV
    record = await db.get(GeneratedCV, cv_id)
    assert record.section_overrides is None
    assert (record.review_state or {}).get("decisions", []) == []


@pytest.mark.asyncio
async def test_cv_take_out_still_listed_true_when_reaudit_keeps_listing_the_term(db):
    intro = "Erfahrener Entwickler mit Kubernetes Erfahrung."
    cv_id = await seed_cv(
        db, introduction=intro,
        ats_report=_ats_report("cv", [_KUBERNETES], _MATCHES),
    )
    client = _client(db)
    fake_rewrite = FakeRewrite({"introduction": (True, "Erfahrener Entwickler.")})
    # Post-reaudit report still lists the term (e.g. it also appears elsewhere).
    fake_reaudit = FakeReaudit([(_ats_report("cv", [_KUBERNETES], _MATCHES), None)])

    with patch.object(ra, "_rewriter", lambda: fake_rewrite), \
         patch.object(ra, "reaudit", new=fake_reaudit):
        response = client.post(
            f"/api/cv/{cv_id}/review/take-out",
            json={"finding_key": _KEY},
        )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["still_listed"] is True
    # A decision is still recorded — take_out records "taken_out" whenever a
    # section was actually rewritten, independent of whether it stuck.
    assert data["review_state"]["decisions"][0]["action"] == "taken_out"


@pytest.mark.asyncio
async def test_take_out_rewriter_unavailable_returns_503(db):
    """WP-B's ``rewrite_for_removal`` is not installed on this branch — the
    real (unpatched) ``_rewriter()`` must surface as 503."""
    cv_id = await seed_cv(
        db, introduction="Kenntnisse in Kubernetes.",
        ats_report=_ats_report("cv", [_KUBERNETES], _MATCHES),
    )
    client = _client(db)
    response = client.post(
        f"/api/cv/{cv_id}/review/take-out",
        json={"finding_key": _KEY},
    )
    assert response.status_code == 503
    assert "removal rewrite" in response.json()["detail"]


# ── undo ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cv_undo_restores_before_text_and_removes_decision(db):
    intro_before = "Erfahrener Entwickler mit Kubernetes Erfahrung."
    intro_after = "Erfahrener Entwickler."
    state = rs.with_decision(
        rs.load_state(None), _KEY, _KUBERNETES, "taken_out",
        undo_sections=[{"section_id": "introduction", "before": intro_before}],
    )
    cv_id = await seed_cv(
        db, introduction=intro_after,  # current (post-take-out) content
        ats_report=_ats_report("cv", []),  # cleared, per the take-out that ran
        review_state=state,
    )
    client = _client(db)
    fake_reaudit = FakeReaudit([(_ats_report("cv", [_KUBERNETES], _MATCHES), None)])  # restored ⇒ listed again

    with patch.object(ra, "reaudit", new=fake_reaudit):
        response = client.post(
            f"/api/cv/{cv_id}/review/undo",
            json={"finding_key": _KEY},
        )

    assert response.status_code == 200, response.text
    data = response.json()
    assert fake_reaudit.calls == 1
    assert data["review_state"]["decisions"] == []

    from applire.models.cv import GeneratedCV
    record = await db.get(GeneratedCV, cv_id)
    assert record.section_overrides["introduction"] == intro_before


@pytest.mark.asyncio
async def test_cover_letter_undo_restores_before_text_and_removes_decision(db):
    before_text = "Erster Absatz.\n\nZweiter Absatz mit Kubernetes Erfahrung."
    after_text = "Erster Absatz.\n\nZweiter Absatz."
    state = rs.with_decision(
        rs.load_state(None), _KEY, _KUBERNETES, "taken_out",
        undo_sections=[{"section_id": "body", "before": before_text}],
    )
    cl_id = await seed_letter(
        db, paragraphs=[after_text],
        ats_report=_ats_report("cover_letter", []),
        review_state=state,
    )
    client = _client(db)
    fake_reaudit = FakeReaudit([(_ats_report("cover_letter", [_KUBERNETES], _MATCHES), None)])

    with patch.object(ra, "reaudit", new=fake_reaudit):
        response = client.post(
            f"/api/cover-letter/{cl_id}/review/undo",
            json={"finding_key": _KEY},
        )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["review_state"]["decisions"] == []

    from applire.models.cover_letter import GeneratedCoverLetter
    record = await db.get(GeneratedCoverLetter, cl_id)
    assert record.section_overrides["body"] == before_text


@pytest.mark.asyncio
async def test_cv_undo_on_added_decision_returns_409_and_writes_nothing(db):
    state = rs.with_decision(rs.load_state(None), _KEY, _KUBERNETES, "added")
    cv_id = await seed_cv(db, ats_report=_ats_report("cv", []))
    await _set_review_state(db, "cv", cv_id, state)
    client = _client(db)

    fake_reaudit = FakeReaudit()
    with patch.object(ra, "reaudit", new=fake_reaudit):
        response = client.post(
            f"/api/cv/{cv_id}/review/undo",
            json={"finding_key": _KEY},
        )

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "undo_unavailable_for_added"
    assert fake_reaudit.calls == 0

    from applire.models.cv import GeneratedCV
    record = await db.get(GeneratedCV, cv_id)
    assert record.section_overrides is None
    assert record.review_state["decisions"][0]["action"] == "added", "the decision must survive the refusal"


@pytest.mark.asyncio
async def test_cv_undo_with_no_decision_returns_409(db):
    cv_id = await seed_cv(db, ats_report=_ats_report("cv", []))
    client = _client(db)
    response = client.post(
        f"/api/cv/{cv_id}/review/undo",
        json={"finding_key": _KEY},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "no_decision"


# ── edited ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cv_edited_records_edited_only_when_the_finding_cleared(db):
    cv_id = await seed_cv(
        db, introduction="Kenntnisse in Kubernetes.",
        ats_report=_ats_report("cv", [_KUBERNETES], _MATCHES),
    )
    client = _client(db)
    fake_reaudit = FakeReaudit([(_ats_report("cv", []), None)])

    with patch.object(ra, "reaudit", new=fake_reaudit):
        response = client.post(
            f"/api/cv/{cv_id}/review/edited",
            json={"finding_key": _KEY},
        )

    assert response.status_code == 200, response.text
    data = response.json()
    assert fake_reaudit.calls == 1
    decisions = data["review_state"]["decisions"]
    assert len(decisions) == 1
    assert decisions[0]["action"] == "edited"
    assert decisions[0]["label"] == _KUBERNETES


@pytest.mark.asyncio
async def test_cover_letter_edited_records_edited_only_when_the_finding_cleared(db):
    cl_id = await seed_letter(
        db, paragraphs=["Kenntnisse in Kubernetes."],
        ats_report=_ats_report("cover_letter", [_KUBERNETES], _MATCHES),
    )
    client = _client(db)
    fake_reaudit = FakeReaudit([(_ats_report("cover_letter", []), None)])

    with patch.object(ra, "reaudit", new=fake_reaudit):
        response = client.post(
            f"/api/cover-letter/{cl_id}/review/edited",
            json={"finding_key": _KEY},
        )

    assert response.status_code == 200, response.text
    decisions = response.json()["review_state"]["decisions"]
    assert len(decisions) == 1 and decisions[0]["action"] == "edited"


@pytest.mark.asyncio
async def test_cv_edited_not_recorded_when_the_finding_is_still_listed(db):
    cv_id = await seed_cv(
        db, introduction="Kenntnisse in Kubernetes.",
        ats_report=_ats_report("cv", [_KUBERNETES], _MATCHES),
    )
    client = _client(db)
    # Re-audit still lists the term — the edit did not remove it.
    fake_reaudit = FakeReaudit([(_ats_report("cv", [_KUBERNETES], _MATCHES), None)])

    with patch.object(ra, "reaudit", new=fake_reaudit):
        response = client.post(
            f"/api/cv/{cv_id}/review/edited",
            json={"finding_key": _KEY},
        )

    assert response.status_code == 200, response.text
    assert response.json()["review_state"]["decisions"] == []


# ── walked ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cv_walked_sets_walked_at(db):
    cv_id = await seed_cv(db)
    client = _client(db)
    response = client.post(f"/api/cv/{cv_id}/review/walked")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["review_state"]["walked_at"] is not None

    from applire.models.cv import GeneratedCV
    record = await db.get(GeneratedCV, cv_id)
    assert record.review_state["walked_at"] is not None


@pytest.mark.asyncio
async def test_cover_letter_walked_sets_walked_at(db):
    cl_id = await seed_letter(db)
    client = _client(db)
    response = client.post(f"/api/cover-letter/{cl_id}/review/walked")
    assert response.status_code == 200, response.text
    assert response.json()["review_state"]["walked_at"] is not None

    from applire.models.cover_letter import GeneratedCoverLetter
    record = await db.get(GeneratedCoverLetter, cl_id)
    assert record.review_state["walked_at"] is not None


# ── errors shared across kinds ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_document_id_returns_404(db):
    client = _client(db)
    response = client.post(f"/api/cv/{uuid.uuid4()}/review/walked")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_malformed_finding_key_returns_422(db):
    cv_id = await seed_cv(db, ats_report=_ats_report("cv", []))
    client = _client(db)
    response = client.post(
        f"/api/cv/{cv_id}/review/edited",
        json={"finding_key": "nope"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_finding_not_listed_returns_409(db):
    cv_id = await seed_cv(db, ats_report=_ats_report("cv", []))  # nothing listed
    client = _client(db)
    response = client.post(
        f"/api/cv/{cv_id}/review/add-evidence",
        json={"finding_key": _KEY, "text": "irrelevant"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "finding_not_listed"


# ── the ATS-report GET carries review_state ──────────────────────────────────


@pytest.mark.asyncio
async def test_cv_ats_report_get_returns_empty_review_state_for_null_column(db):
    cv_id = await seed_cv(db)  # ats_report=None, review_state=None
    client = _client(db)
    response = client.get(f"/api/cv/{cv_id}/ats-report")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["report"] is None
    assert data["review_state"] == {"walked_at": None, "decisions": []}


@pytest.mark.asyncio
async def test_cover_letter_ats_report_get_returns_empty_review_state_for_null_column(db):
    cl_id = await seed_letter(db)
    client = _client(db)
    response = client.get(f"/api/cover-letter/{cl_id}/ats-report")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["report"] is None
    assert data["review_state"] == {"walked_at": None, "decisions": []}


# ── concurrency: take_out serialises on document_lock ─────────────────────────


@pytest.mark.asyncio
async def test_take_out_serialises_with_a_concurrent_background_reaudit_on_document_lock(db):
    """The same ``document_lock("cv", id)`` guards ``review_actions`` and the
    section editor's background re-audit (``_update_ats_report_by_id``) — a
    holder of the lock (standing in for that background task) must block
    ``take_out`` before it ever reaches the re-audit."""
    intro = "Erfahrener Entwickler mit Kubernetes Erfahrung."
    cv_id = await seed_cv(
        db, introduction=intro,
        ats_report=_ats_report("cv", [_KUBERNETES], _MATCHES),
    )
    fake_rewrite = FakeRewrite({"introduction": (True, "Erfahrener Entwickler.")})
    fake_reaudit = FakeReaudit([(_ats_report("cv", []), None)])

    lock = rs.document_lock("cv", cv_id)
    await lock.acquire()
    try:
        with patch.object(ra, "_rewriter", lambda: fake_rewrite), \
             patch.object(ra, "reaudit", new=fake_reaudit):
            task = asyncio.create_task(
                ra.take_out("cv", cv_id, _KEY, db, provider=object())
            )
            # Give the task every chance to run up to the lock.
            for _ in range(5):
                await asyncio.sleep(0)
            assert not task.done()
            assert fake_reaudit.calls == 0, (
                "take_out must be blocked by the held lock before it ever reaches reaudit"
            )

            lock.release()
            outcome = await asyncio.wait_for(task, timeout=5)
    finally:
        if lock.locked():
            lock.release()

    assert fake_reaudit.calls == 1
    assert outcome.changes


# ---------------------------------------------------------------------------
# RULING B-1 — take-out refuses a finding whose EVERY matched form is stem-only
# ---------------------------------------------------------------------------

_MENTORING = "Mentoring"
_MENTORING_KEY = "ats:mentoring"
_STEM_ONLY = {_MENTORING: [{"form": "Mentoring", "stem": True}]}


@pytest.mark.asyncio
async def test_cv_take_out_stem_only_finding_returns_409_and_changes_nothing(db):
    intro = "Mentored four junior engineers."
    cv_id = await seed_cv(
        db, introduction=intro, ats_report=_ats_report("cv", [_MENTORING], _STEM_ONLY),
    )
    client = _client(db)
    fake_rewrite = FakeRewrite({"introduction": (True, "Worked with four junior engineers.")})
    fake_reaudit = FakeReaudit()
    with patch.object(ra, "_rewriter", lambda: fake_rewrite), \
         patch.object(ra, "reaudit", new=fake_reaudit):
        response = client.post(f"/api/cv/{cv_id}/review/take-out", json={"finding_key": _MENTORING_KEY})

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["error"] == "take_out_unavailable_stem_only"
    assert fake_rewrite.calls == [] and fake_reaudit.calls == 0
    from applire.models.cv import GeneratedCV
    record = await db.get(GeneratedCV, cv_id)
    assert record.section_overrides is None
    assert (record.review_state or {}).get("decisions", []) == []


@pytest.mark.asyncio
async def test_cover_letter_take_out_stem_only_finding_returns_409_and_changes_nothing(db):
    paragraphs = ["Ich habe vier Nachwuchskräfte mentored.", "Zweiter Absatz."]
    cl_id = await seed_letter(
        db, paragraphs=paragraphs,
        ats_report=_ats_report("cover_letter", [_MENTORING], _STEM_ONLY),
    )
    client = _client(db)
    fake_rewrite = FakeRewrite({"body": (True, "Zweiter Absatz.")})
    fake_reaudit = FakeReaudit()
    with patch.object(ra, "_rewriter", lambda: fake_rewrite), \
         patch.object(ra, "reaudit", new=fake_reaudit):
        response = client.post(
            f"/api/cover-letter/{cl_id}/review/take-out", json={"finding_key": _MENTORING_KEY},
        )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["error"] == "take_out_unavailable_stem_only"
    assert fake_rewrite.calls == [] and fake_reaudit.calls == 0
    from applire.models.cover_letter import GeneratedCoverLetter
    record = await db.get(GeneratedCoverLetter, cl_id)
    assert record.section_overrides is None
    assert (record.review_state or {}).get("decisions", []) == []


@pytest.mark.asyncio
async def test_cv_take_out_mixed_stem_and_literal_matches_still_rewrites(db):
    """Only an ALL-stem finding is refused: one literal form keeps take-out automatic."""
    intro = "Mentoring and mentored juniors."
    cv_id = await seed_cv(
        db, introduction=intro,
        ats_report=_ats_report("cv", [_MENTORING], {_MENTORING: [
            {"form": "Mentoring", "stem": False}, {"form": "mentored", "stem": True}]}),
    )
    client = _client(db)
    fake_rewrite = FakeRewrite({"introduction": (True, "Worked with juniors.")})
    fake_reaudit = FakeReaudit([(_ats_report("cv", []), None)])
    with patch.object(ra, "_rewriter", lambda: fake_rewrite), \
         patch.object(ra, "reaudit", new=fake_reaudit):
        response = client.post(f"/api/cv/{cv_id}/review/take-out", json={"finding_key": _MENTORING_KEY})
    assert response.status_code == 200, response.text
    assert fake_rewrite.calls == ["introduction"]

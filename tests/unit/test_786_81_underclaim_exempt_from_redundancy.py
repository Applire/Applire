# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""786:81 (E-12 walk, founder 2026-10-22) — a bullet added for an under-claim demand is
exempt from the redundancy finding for the rest of the loop.

The incident (2026-09-17 in-process chain run, operations_marcus_de, luna, recs 18 -> 20):
round 1's ADR-076 cl. 5 under-claim signal asked for a Lean-Produktion bullet; round 2's
ADR-082/083 redundancy signal named that new bullet "the same achievement" as the
Shopfloor/KVP bullet; the corrector deleted it; the CV shipped
`narrative-evidence: fail — Lean-Produktion`.

Layers: the signal function (demanded groups exempt their earliest carrier, other pairs
keep their slot) and the seam — the CV terminal chain's `_terminal_signal_issues` hands
the under-claim signal's recorded demand to the redundancy signal, across rounds.
"""
import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.support.profile_factory import make_master_profile

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.bullet_redundancy_signal import (  # noqa: E402
    redundancy_signal_issues,
    redundancy_signal_issues_fn,
)

# The real 2026-09-11 Weberit bullets (verbatim from test_659): B1 and B4 are the pair
# `bullets_prose_dupe` flags; B4 is the Lean-Produktion bullet.
_B0 = (
    "Führung von zwei Fertigungsbereichen mit 38 Mitarbeitenden über drei Schichtleiter "
    "im Dreischichtbetrieb; als Eskalationsinstanz für Kundenreklamationen klare "
    "Entscheidungen nach Einbeziehung der Schichtleiter getroffen."
)
_B1 = (
    "Shopfloor-Management und KVP-Routinen eingeführt und dadurch die Ausschussquote "
    "von 4,1 % auf 2,3 % gesenkt; überarbeitete Feinplanung und Rüstworkshops nach SMED "
    "verbesserten die Termintreue von 87 % auf 96 %."
)
_B4 = "Lean-Produktion mit Shopfloor-Management, KVP und SMED in der Fertigung umgesetzt."
_LEAN = (("Lean-Produktion", "Lean Production"),)


def _document(bullets):
    return {
        "contact": {"name": "Stefan Brandt"}, "summary": "Produktionsleiter.",
        "work_history": [{
            "company": "Weberit Kunststofftechnik GmbH", "role": "Produktionsleiter",
            "start_date": "2017-04", "bullets": list(bullets), "projects": [],
        }],
        "projects": [], "skills": [],
    }


def test_without_a_demand_the_pair_is_still_raised():
    assert len(redundancy_signal_issues(_document([_B0, _B1, _B4]))) == 1


def test_the_bullet_a_demand_produced_is_exempt():
    assert redundancy_signal_issues(_document([_B0, _B1, _B4]), demanded_groups=_LEAN) == []


def test_a_demand_for_another_concept_exempts_nothing():
    issues = redundancy_signal_issues(_document([_B0, _B1, _B4]), demanded_groups=(("ISO 9001",),))
    assert len(issues) == 1


def test_the_exemption_frees_the_slot_for_another_pair():
    """The exempt pair is removed BEFORE the per-round bound, so a second genuine pair
    still reaches the corrector."""
    b1_twin = _B1.replace("Shopfloor-Management und", "Shopfloor-Management sowie")
    issues = redundancy_signal_issues(
        _document([_B1, _B4, b1_twin]), demanded_groups=_LEAN, limit=1
    )
    assert len(issues) == 1
    assert "Lean-Produktion" not in issues[0].text


def test_the_bound_function_reads_the_demand_at_call_time():
    demand = {"groups": ()}
    fn = redundancy_signal_issues_fn(
        structured_document_fn=lambda _d: _document([_B0, _B1, _B4]),
        demanded_groups_fn=lambda: demand["groups"],
    )
    assert len(fn({})) == 1
    demand["groups"] = _LEAN  # the under-claim signal recorded its demand
    assert fn({}) == []


# --- seam: the CV terminal chain (v1 and v2) ------------------------------------

_WORK_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_CERT_NAME = "AWS Solutions Architect Professional"


@pytest.fixture(autouse=True)
def _v1_pipelines(monkeypatch):
    """These tests pin the v1 pipelines (0.43 behaviour). v2 is the default since
    ruling W3-7 (2026-10-18); v1 stays supported until its tails are deleted, so
    the tests set it explicitly."""
    monkeypatch.setenv("CV_PIPELINE", "v1")
    monkeypatch.setenv("LETTER_PIPELINE", "v1")


def _profile_json() -> dict:
    return {
        "contact": {"first_name": "Anna", "last_name": "Bauer",
                    "email": "anna@example.com", "phone": None, "location": "Berlin",
                    "linkedin": None, "xing": None, "portfolio": None},
        "professional_summary": {"de": "Erfahrene Entwicklerin", "en": ""},
        "work_experience": [
            {
                "id": _WORK_ID,
                "company": "Weberit Kunststofftechnik GmbH",
                "role": "Produktionsleiter",
                "start_date": "2020-01",
                "end_date": None,
                "team_size": 7,
                "responsibilities": [f"Aufgabe {i}" for i in range(3)],
            }
        ],
        "education": [], "skills": [], "languages": [],
        "certifications": [
            {"name": _CERT_NAME, "issuing_organization": "AWS", "status": "confirmed"}
        ],
    }


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


async def _seed(db, *, profile_json=None):
    from applire.models.job import JobAnalysis
    from applire.models.cv import GeneratedCV

    job_id, profile_id, cv_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db.add_all([
        JobAnalysis(
            id=job_id, raw_text_hash=str(job_id), raw_text="job",
            role_title="Engineer", required_skills=[], nice_to_have_skills=[],
            keywords=["Python"], seniority_level="mid", company_culture_signals=[],
            language_requirement="de",
        ),
        make_master_profile(
            id=profile_id, profile_json=profile_json or _profile_json(),
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        GeneratedCV(
            id=cv_id, job_analysis_id=job_id, profile_id=profile_id,
            tailored_data={}, template="classic_german", status="pending",
            target_pages=2,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
        ),
    ])
    await db.commit()
    return job_id, profile_id, cv_id


async def _run_pipeline(db, ids, *, script=None, captured=None, extra_patches=(),
                        review_retries=2, payload=None, review_fake=None):
    from applire.services.cv import _render_cv_background

    job_id, profile_id, cv_id = ids
    captured = captured if captured is not None else []
    provider = AsyncMock()
    # EVERY provider JSON call returns the same payload — the language pass may
    # legitimately re-emit the prose through the provider, and a diverging
    # default would silently overwrite a test's tailored draft.
    provider.aparse_json.return_value = payload if payload is not None else _writer_payload()
    extract = MagicMock(side_effect=lambda pdf: ("text", 2))

    patches = [
        patch("applire.services.cv.get_provider", return_value=provider),
        patch("applire.services.cv.review_and_refine",
              side_effect=review_fake or _fake_review(script or [], captured)),
        patch("applire.services.cv.LLM_REVIEW_MAX_RETRIES", review_retries),
        patch("applire.services.cv.get_cv_html", new=AsyncMock(return_value="<html></html>")),
        patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"pdf")),
        patch("applire.services.ats_audit.extract_text_and_pages", new=extract),
    ]
    with patch("applire.services.cv.AsyncSessionLocal") as sl:
        sl.return_value.__aenter__.return_value = db
        from contextlib import ExitStack
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            for p in extra_patches:
                stack.enter_context(p)
            await _render_cv_background(cv_id, job_id, profile_id, "classic_german")
    return captured




def _writer_payload(bullets):
    return {"summary": "Produktionsleiter.", "work": [{"id": _WORK_ID, "bullets": list(bullets)}],
            "skills": ["SMED"]}


def _capture_signal(captured):
    async def fake(**kwargs):
        if kwargs.get("chain_id") != "cv_terminal_review":
            return kwargs["draft"]
        fn = kwargs["signal_issues_fn"]
        captured.append([i.text for i in fn(kwargs["draft"])])
        return kwargs["draft"]
    return fake


def _demanding_underclaim(demand_rounds):
    """Stand-in for `cv_gap_hints.underclaim_signal_issues_fn`: reports the scripted
    demand of each round through `on_demand`, as the real signal does."""
    def factory(keyword_ledger, *, on_demand=None, structured_document_fn=None, **kw):
        state = {"round": 0}

        def fn(draft):
            groups = demand_rounds[min(state["round"], len(demand_rounds) - 1)]
            state["round"] += 1
            if on_demand is not None:
                on_demand([SimpleNamespace(concept=g[0], surface_forms=g) for g in groups])
            return []
        return fn
    return factory


@pytest.mark.asyncio
@pytest.mark.parametrize("pipeline", ["v1", "v2"])
@pytest.mark.parametrize("demand,expect_pair", [((("Lean-Produktion",),), False), ((), True)])
async def test_the_cv_chain_hands_the_recorded_demand_to_the_redundancy_signal(
    db, monkeypatch, pipeline, demand, expect_pair
):
    monkeypatch.setenv("CV_PIPELINE", pipeline)
    ids = await _seed(db)
    captured: list = []
    await _run_pipeline(
        db, ids,
        payload=_writer_payload([_B0, _B1, _B4]),
        review_fake=_capture_signal(captured),
        extra_patches=[patch(
            "applire.services.cv_gap_hints.underclaim_signal_issues_fn",
            new=_demanding_underclaim([demand]),
        )],
    )
    assert captured, "the terminal chain ran"
    raised = [t for t in captured[0] if "state the same achievement" in t]
    assert bool(raised) is expect_pair, captured

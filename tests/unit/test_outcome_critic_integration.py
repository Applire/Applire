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

"""ADR-060 Pass B (#322) wired into the real generation path.

Drives ``_render_cover_letter_background`` end to end (the actual service
entrypoint, not just the pure fact function pinned in
``test_outcome_critic_facts.py``) against a real SQLite DB via
``async_sessionmaker`` — mirrors ``test_cover_letter_figure_guard_integration.py``
and ``test_cv_narrative_coverage_integration.py``. Only ``review_and_refine``'s
return value is mocked (the settled letter draft); every assertion reads the
PERSISTED ``critic_report`` after ``await db.refresh(cl)``, and the agent
door's response shape via the SAME service function REST calls
(``get_cover_letter_critic_report`` — ADR-066 clause 2: one implementation).
"""
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

# #322's own founding shape: ISO 9001 present in both documents; only the
# letter's mention carries the depth qualifier ("zehn Jahren").
PROFILE_JSON = {
    "personal_info": {"name": "Anna Bauer", "email": "anna@example.com"},
    "professional_summary": {"de": "Erfahrene Qualitätsmanagerin."},
    "work_experience": [
        {
            "id": "w1",
            "company": "Musterwerk GmbH",
            "role": "Qualitätsmanagerin",
            "achievements": ["Verantwortlich für ISO 9001 Zertifizierungsaudits."],
        }
    ],
}

CV_TAILORED_DATA = {
    "skills": ["ISO 9001", "Qualitätsmanagement"],
    "work_experience": [
        {
            "company": "Musterwerk GmbH",
            "achievements": ["Verantwortlich für ISO 9001 Zertifizierungsaudits."],
        }
    ],
}

_LETTER_DRAFT = {
    "header": {"name": "Anna Bauer"},
    "recipient": {"name": None, "company": "Zielfirma GmbH", "date": None},
    "body": {
        "paragraphs": [
            "Sehr geehrte Damen und Herren,",
            # The employer is named in the SAME sentence as the figure —
            # letter_figure_guard (#254/#296) otherwise strips a digit it
            # cannot attribute to a named employer in context. This is a
            # real, independent guard this fixture must survive, not a
            # detail of the critic under test.
            "Bei Musterwerk GmbH bringe ich mit zehn Jahren ISO-9001-Auditpraxis "
            "genau die Qualitätssicherungs-Expertise mit, die Sie suchen.",
            "Mit freundlichen Grüßen",
        ]
    },
    "signature": {"closing": None, "name": None},
}

_LEDGER = [
    {
        "concept": "ISO 9001",
        "surface_forms": ["ISO 9001", "ISO-9001"],
        "claimable": True,
        "status": "direct",
    }
]


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

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def seeded(db):
    """Full shape Pass B needs: a job, a profile, a GENERATED CV linked via a
    FlowSession (exactly as the real generation path resolves it), a
    GapAnalysis carrying the Keyword Ledger, and a pending cover letter."""
    from applire.models.cover_letter import CoverLetterStatus, GeneratedCoverLetter
    from applire.models.cv import GeneratedCV
    from applire.models.flow import FlowSession
    from applire.models.gap import GapAnalysis
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile
    from applire.models.user import User

    user = User(id=uuid.uuid4(), email="critic-it@test.com")
    db.add(user)
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash="criticit123",
        raw_text="Qualitätsmanager (m/w/d) bei Zielfirma GmbH — ISO 9001 gefordert.",
        role_title="Qualitätsmanager",
        company_name="Zielfirma GmbH",
        required_skills=["ISO 9001"],
        nice_to_have_skills=[],
        keywords=["ISO 9001"],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement="de",
    )
    db.add(job)
    profile = MasterProfile(profile_json=PROFILE_JSON)
    db.add(profile)
    await db.flush()

    cv = GeneratedCV(
        job_analysis_id=job.id,
        profile_id=profile.id,
        tailored_data=CV_TAILORED_DATA,
        template="classic_german",
        status="ready",
    )
    db.add(cv)
    gap = GapAnalysis(
        job_analysis_id=job.id,
        profile_id=profile.id,
        keyword_ledger=_LEDGER,
    )
    db.add(gap)
    await db.flush()

    flow = FlowSession(
        user_id=user.id,
        job_id=job.id,
        generated_cv_id=cv.id,
    )
    db.add(flow)

    cl = GeneratedCoverLetter(
        job_analysis_id=job.id,
        profile_id=profile.id,
        template="classic_german",
        letter_data={},
        pre_gen_inputs={},
        status=CoverLetterStatus.pending.value,
    )
    db.add(cl)
    await db.commit()
    await db.refresh(cl)
    return db, job, profile, cv, cl


def _mock_provider(judgement_side_effect):
    """A provider whose FIRST aparse_json call is the writer's initial draft
    (irrelevant — review_and_refine is mocked to hand back the real settled
    draft regardless) and whose SECOND+ call(s) are the critic's OWN
    judgement call(s).

    The Oracle's pre-grading ``sentence_triage`` seam (ADR-068 amended
    2026-08-08) also runs inside the generation self-audit, so it is answered
    here OUT OF BAND — recognised by its own system prompt and never
    consuming a scripted response — which keeps the scripted sequence
    meaning exactly what its name says.
    """
    from applire.prompts.oracle_triage import ORACLE_TRIAGE_ITEM_RE

    scripted = [{"body": {"paragraphs": []}}, *judgement_side_effect]

    async def _answer(prompt, *, system=None, **kwargs):
        if "sentence triage" in (system or "").lower():
            # Permissive-safe: classify nothing out of the audit.
            return {
                "items": [
                    {
                        "index": int(i),
                        "classification": "candidate-claim",
                        "sentence_quote": text,
                    }
                    for i, text in ORACLE_TRIAGE_ITEM_RE.findall(prompt)
                ]
            }
        nxt = scripted.pop(0)
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt

    provider = MagicMock()
    provider.aparse_json = AsyncMock(side_effect=_answer)
    provider.judgement_calls = lambda: len(
        [
            c
            for c in provider.aparse_json.await_args_list
            if "sentence triage" not in (c.kwargs.get("system") or "").lower()
        ]
    )
    return provider


async def _run_generation(db, job, cl, judgement_side_effect, **run_pass_b_overrides):
    from applire.services.cover_letter import _render_cover_letter_background

    provider = _mock_provider(judgement_side_effect)
    with (
        patch("applire.services.cover_letter.AsyncSessionLocal") as mock_session_local,
        patch("applire.services.cover_letter.get_provider", return_value=provider),
        patch(
            "applire.services.cover_letter.review_and_refine",
            AsyncMock(return_value=_LETTER_DRAFT),
        ),
        patch(
            "applire.services.cover_letter_pdf.render_pdf",
            AsyncMock(side_effect=RuntimeError("no browser in unit test")),
        ),
    ):
        mock_session_local.return_value.__aenter__.return_value = db
        await _render_cover_letter_background(cl_id=cl.id, cv_id=None, job_id=job.id)
    return provider


@pytest.mark.asyncio
async def test_advisory_reaches_the_persisted_report(seeded):
    """#322's founding shape end to end: the persisted critic_report carries
    exactly one advisory, DE+EN populated, changed=False, and the SAME
    service function the agent door calls returns the identical shape."""
    db, job, profile, cv, cl = seeded

    # Third-amendment judgement shape: the model quotes the span its finding
    # rests on; the service verifies the citation against the letter before
    # building the advisory (SF-CRITIC.11).
    judgement = {
        "findings": [
            {
                "kind": "letter_only",
                "concept": "ISO 9001",
                "cv_quote": None,
                "cv_detail_quote": None,
                "letter_quote": (
                    "Bei Musterwerk GmbH bringe ich mit zehn Jahren "
                    "ISO-9001-Auditpraxis genau die Qualitätssicherungs-"
                    "Expertise mit, die Sie suchen."
                ),
                "worth_surfacing": True,
            }
        ]
    }
    await _run_generation(db, job, cl, [judgement])

    await db.refresh(cl)
    from applire.models.cover_letter import CoverLetterStatus

    assert cl.status == CoverLetterStatus.ready.value
    assert cl.critic_report is not None
    assert cl.critic_report["ran"] is True
    assert cl.critic_report["reason"] is None
    advisories = cl.critic_report["advisories"]
    assert len(advisories) == 1
    advisory = advisories[0]
    assert advisory["concept"] == "ISO 9001"
    assert advisory["changed"] is False
    assert "zehn Jahren" in advisory["letter_state"]
    assert set(advisory["message"].keys()) == {"de", "en"}
    assert advisory["message"]["de"].strip()
    assert advisory["message"]["en"].strip()

    # SF-CRITIC.3: the agent door's response shape. `get_cover_letter_critic_report`
    # is the ONE function both the REST router and the MCP tool call — there is
    # no second implementation to drift out of parity (ADR-066 clause 2).
    from applire.services.cover_letter import get_cover_letter_critic_report

    door_response = await get_cover_letter_critic_report(cl.id, db)
    assert door_response.report is not None
    assert door_response.report.advisories[0].concept == "ISO 9001"
    assert door_response.report.advisories[0].changed is False


@pytest.mark.asyncio
async def test_a_judgement_of_not_worth_surfacing_yields_zero_advisories(seeded):
    """The model may see the SAME candidate and rule it not worth surfacing —
    the report must say RAN with zero advisories, not "did not run"."""
    db, job, profile, cv, cl = seeded

    judgement = {"findings": [{"concept": "ISO 9001", "worth_surfacing": False}]}
    await _run_generation(db, job, cl, [judgement])

    await db.refresh(cl)
    assert cl.critic_report["ran"] is True
    assert cl.critic_report["advisories"] == []


@pytest.mark.asyncio
async def test_delivery_is_never_gated_when_the_judgement_call_errors(seeded):
    """The critic's LLM call fails outright — the letter must STILL ship."""
    db, job, profile, cv, cl = seeded

    await _run_generation(db, job, cl, [RuntimeError("provider unavailable")])

    await db.refresh(cl)
    from applire.models.cover_letter import CoverLetterStatus

    assert cl.status == CoverLetterStatus.ready.value  # never gated
    assert cl.critic_report["ran"] is True  # facts DID run
    assert cl.critic_report["reason"] == "judgement_error"
    assert cl.critic_report["advisories"] == []


@pytest.mark.asyncio
async def test_delivery_is_never_gated_when_critic_disabled(seeded, monkeypatch):
    """CRITIC_ENABLED=false: the letter still ships, and the report says so
    distinctly from a clean run that found nothing (SF-CRITIC.1)."""
    db, job, profile, cv, cl = seeded
    monkeypatch.setattr("applire.services.outcome_critic.CRITIC_ENABLED", False)

    await _run_generation(db, job, cl, [])

    await db.refresh(cl)
    from applire.models.cover_letter import CoverLetterStatus

    assert cl.status == CoverLetterStatus.ready.value
    assert cl.critic_report["ran"] is False
    assert cl.critic_report["reason"] == "disabled"
    assert cl.critic_report["advisories"] == []


@pytest.mark.asyncio
async def test_one_round_cap_holds_by_default(seeded):
    """CRITIC_MAX_ROUNDS defaults to 1: a judgement call that ALWAYS returns
    malformed JSON must be attempted exactly once, never retried."""
    db, job, profile, cv, cl = seeded

    malformed = {"not_findings_at_all": True}
    provider = await _run_generation(db, job, cl, [malformed])

    await db.refresh(cl)
    assert cl.critic_report["ran"] is True
    assert cl.critic_report["reason"] == "judgement_error"
    # ONE writer call + ONE judgement attempt — never a silent retry loop.
    # (The Oracle's sentence-triage self-audit call is answered out of band
    # and excluded here; it is not a critic retry.)
    assert provider.judgement_calls() == 2


@pytest.mark.asyncio
async def test_missing_cv_short_circuits_loudly_not_silently(db):
    """No GeneratedCV behind this flow (e.g. an agent-authored letter with no
    linked CV) — Pass B needs both documents by construction (ADR-060 amended
    2026-07-30) and must say so, never silently degrade into a judgement."""
    from applire.models.cover_letter import CoverLetterStatus, GeneratedCoverLetter
    from applire.models.flow import FlowSession
    from applire.models.gap import GapAnalysis
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile
    from applire.models.user import User

    user = User(id=uuid.uuid4(), email="critic-it-2@test.com")
    db.add(user)
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash="criticit456",
        raw_text="Qualitätsmanager (m/w/d)",
        role_title="Qualitätsmanager",
        required_skills=["ISO 9001"],
        nice_to_have_skills=[],
        keywords=["ISO 9001"],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement="de",
    )
    db.add(job)
    profile = MasterProfile(profile_json=PROFILE_JSON)
    db.add(profile)
    await db.flush()
    gap = GapAnalysis(job_analysis_id=job.id, profile_id=profile.id, keyword_ledger=_LEDGER)
    db.add(gap)
    # FlowSession with NO generated_cv_id — the precondition this test pins.
    flow = FlowSession(user_id=user.id, job_id=job.id, generated_cv_id=None)
    db.add(flow)
    cl = GeneratedCoverLetter(
        job_analysis_id=job.id,
        profile_id=profile.id,
        template="classic_german",
        letter_data={},
        pre_gen_inputs={},
        status=CoverLetterStatus.pending.value,
    )
    db.add(cl)
    await db.commit()
    await db.refresh(cl)

    await _run_generation(db, job, cl, [])

    await db.refresh(cl)
    assert cl.status == CoverLetterStatus.ready.value
    assert cl.critic_report["ran"] is False
    assert cl.critic_report["reason"] == "missing_cv"


@pytest.mark.asyncio
async def test_cv_resolved_via_latest_ready_fallback_when_flow_link_absent(db):
    """SF-CRITIC.10 (run-11 ground truth): a direct generate_cv never populates
    flow.generated_cv_id, and resolving the CV EXCLUSIVELY through it starved
    Pass B on both charter cases (`ran:false, reason:missing_cv` persisted
    while a ready CV for the same job existed). The deterministic fallback —
    latest ready GeneratedCV for the same job_analysis_id — must feed the
    pass instead."""
    from applire.models.cover_letter import CoverLetterStatus, GeneratedCoverLetter
    from applire.models.cv import GeneratedCV
    from applire.models.flow import FlowSession
    from applire.models.gap import GapAnalysis
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile
    from applire.models.user import User

    user = User(id=uuid.uuid4(), email="critic-it-3@test.com")
    db.add(user)
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash="criticit789",
        raw_text="Qualitätsmanager (m/w/d) — ISO 9001 gefordert.",
        role_title="Qualitätsmanager",
        required_skills=["ISO 9001"],
        nice_to_have_skills=[],
        keywords=["ISO 9001"],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement="de",
    )
    db.add(job)
    profile = MasterProfile(profile_json=PROFILE_JSON)
    db.add(profile)
    await db.flush()
    # A ready CV for the job — NOT linked to any flow (the charter-run shape).
    cv = GeneratedCV(
        job_analysis_id=job.id,
        profile_id=profile.id,
        tailored_data=CV_TAILORED_DATA,
        template="classic_german",
        status="ready",
    )
    db.add(cv)
    gap = GapAnalysis(job_analysis_id=job.id, profile_id=profile.id, keyword_ledger=_LEDGER)
    db.add(gap)
    flow = FlowSession(user_id=user.id, job_id=job.id, generated_cv_id=None)
    db.add(flow)
    cl = GeneratedCoverLetter(
        job_analysis_id=job.id,
        profile_id=profile.id,
        template="classic_german",
        letter_data={},
        pre_gen_inputs={},
        status=CoverLetterStatus.pending.value,
    )
    db.add(cl)
    await db.commit()
    await db.refresh(cl)

    await _run_generation(db, job, cl, [{"findings": []}])

    await db.refresh(cl)
    assert cl.status == CoverLetterStatus.ready.value
    assert cl.critic_report["ran"] is True, (
        "the latest-ready fallback did not feed the pass — the run-11 "
        "starvation shape (SF-CRITIC.10) is back"
    )
    assert cl.critic_report["reason"] is None
    assert cl.critic_report["mount"] == "letter"
    assert cl.critic_report["advisories"] == []

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

"""SF-GAP.12 — seam test at the ONE production call site
(``services/gap.py::_run_analysis``, via ``analyze_gaps``) for the fifth
"Produktion" recompute path found in the Nougat build-2 delivery run
(``Documents/Runs/Nougat/build-2/delivery-run/2026-09-11-operations-marcus-de.md``,
outcome 3).

Drives the REAL ``analyze_gaps`` path twice against one in-memory DB, exactly
as the delivery run's own two calls did: the first build, then a second
build forced by a genuine profile change (E037 PQ #3's idempotency check
only reuses the row when the fingerprint is unchanged). A scripted provider
returns a DIFFERENT ``surface_forms`` list for 'Produktion' on each call —
the SAME (verbatim) shapes the two real ``gap_analyses`` rows persisted in
the delivery run's own artefacts — so this test fails for the SAME reason
the self-hoster's Gaps screen did before the fix: ``GET /api/job/{id}/gaps``
reads whichever ``GapAnalysis`` row is latest, and that was the broken one.
"""

import copy
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.models.flow import FlowSession
from applire.models.job import JobAnalysis
from applire.models.user import User
from applire.providers.llm.mock import MockLLMProvider
from applire.services.gap import analyze_gaps

from tests.support.profile_factory import make_master_profile, set_profile_json

_STUB_USER_ID = uuid.UUID("00000000-0000-0000-0000-0000000fa120")

_DENIAL_STATEMENT = (
    "Nein, mit IFS oder BRC habe ich keine Erfahrung – das ist eine ehrliche "
    "Lücke, und ich habe nie direkt für Lebensmittelkunden produziert."
)

_DENIED_CONCEPTS = [
    {
        "concept": "direkte Produktion für Lebensmittelkunden",
        "statement": _DENIAL_STATEMENT,
        "source": "interview",
        "date": "2026-09-11",
        "denial_level": "direct",
    },
]

# The two real classification shapes, verbatim from the delivery run's own
# 20-gaps-refresh.json (build 1) and 20b-gaps-final.json (build 2, pre-floor
# — the classifier's own output before _enforce_denial_stance ran on it).
_GOOD_FORMS = ["Produktion", "Produktionsleiter", "Fertigung"]
_BAD_FORMS = ["Produktion", "Fertigungsbereiche", "Fertigung"]


def _gap_response(surface_forms: list[str]) -> dict:
    # Real classifier schema (services/gap.py::ledger_input_from_classification):
    # "requirement" is the concept name, "reason" is the grounding evidence.
    return {
        "classifications": [
            {
                "requirement": "Produktion",
                "status": "direct",
                "surface_forms": surface_forms,
                "reason": "14 years of experience in discrete manufacturing, "
                "including the current role as Produktionsleiter.",
            }
        ],
        "strengths": ["Produktion"],
        "keyword_gaps": [],
    }


class _ScriptedGapProvider(MockLLMProvider):
    """Returns a scripted gap-classification response per call, in order;
    delegates every OTHER call (clustering included) to the real mock."""

    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)
        self._call = 0

    async def aparse_json(self, prompt, **kwargs):  # type: ignore[override]
        system_lower = (kwargs.get("system") or "").lower()
        if "three-category gap analysis" in system_lower:
            idx = min(self._call, len(self._responses) - 1)
            self._call += 1
            return copy.deepcopy(self._responses[idx])
        return await super().aparse_json(prompt, **kwargs)


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base  # noqa: F401
    import applire.models.user           # noqa: F401
    import applire.models.job            # noqa: F401
    import applire.models.profile        # noqa: F401
    import applire.models.gap            # noqa: F401
    import applire.models.cv             # noqa: F401
    import applire.models.cover_letter   # noqa: F401
    import applire.models.session        # noqa: F401
    import applire.models.flow           # noqa: F401
    import applire.models.application    # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company        # noqa: F401
    import applire.models.user_settings  # noqa: F401
    import applire.models.uploads        # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _profile_json() -> dict:
    return {
        "personal_info": {
            "first_name": "Stefan",
            "last_name": "Brandt",
            "email": "stefan@test.de",
        },
        "professional_summary": {"de": "Produktionsleiter", "en": "Production lead"},
        "work_experience": [
            {
                "company": "Weberit Kunststofftechnik GmbH",
                "role": "Produktionsleiter",
                "start_date": "2017-01",
                "technologies": ["SAP PP", "MES"],
            }
        ],
        "education": [],
        "skills": [{"name": "Lean Management", "category": "technical"}],
        "languages": [],
        "certifications": [],
        "publications": [],
        "volunteer_activities": [],
        "metadata": {"denied_concepts": _DENIED_CONCEPTS},
    }


@pytest_asyncio.fixture
async def seeded(db):
    user = User(
        id=_STUB_USER_ID,
        email="local@applire.community",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash="hash-produktion-seam",
        raw_text="Leiter Operations",
        role_title="Leiter Operations",
        required_skills=["Produktion"],
        nice_to_have_skills=[],
        keywords=[],
        seniority_level="lead",
        company_culture_signals=[],
        language_requirement="DE",
    )
    profile = make_master_profile(id=uuid.uuid4(), profile_json=_profile_json())
    db.add_all([user, job, profile])
    await db.commit()

    flow = FlowSession(
        user_id=user.id,
        job_id=job.id,
        current_step="gap_analysis",
        user_type="new",
        available_actions={"next": "interview", "skip": "cv_generation"},
    )
    db.add(flow)
    await db.commit()
    await db.refresh(flow)
    return job, profile, flow


def _by_concept(ledger):
    return {e.concept: e for e in ledger}


@pytest.mark.asyncio
async def test_a_later_recompute_does_not_re_flip_produktion_to_a_critical_gap(
    db, seeded
):
    job, profile, flow = seeded
    provider = _ScriptedGapProvider([_gap_response(_GOOD_FORMS), _gap_response(_BAD_FORMS)])

    r1 = await analyze_gaps(job.id, db, provider)
    entry1 = _by_concept(r1.keyword_ledger)["Produktion"]
    assert entry1.claimable is True, "sanity: the first build releases correctly"
    assert "Produktion" in r1.strengths

    # Force a genuine recompute (E037 PQ #3's idempotency check only reuses
    # the row when nothing changed) — mirrors the delivery run's own second
    # call, triggered by the flow's interview-completion re-derivation.
    new_json = _profile_json()
    new_json["skills"].append({"name": "MES", "category": "technical"})
    set_profile_json(profile, new_json)
    await db.commit()

    r2 = await analyze_gaps(job.id, db, provider)
    assert r2.id != r1.id, "sanity: the recompute must produce a NEW row"

    entry2 = _by_concept(r2.keyword_ledger)["Produktion"]
    assert entry2.claimable is True, (
        "the second build's own classification omits the releasing surface "
        "form ('Produktionsleiter') — exactly the 2026-09-11 delivery-run "
        "shape — but the prior build's already-established release must "
        "carry forward, matching GET /api/job/{id}/gaps (whichever analysis "
        "is latest)"
    )
    assert entry2.status == "direct"
    assert "Produktion" not in (r2.critical_gaps or [])
    assert "Produktion" in (r2.strengths or [])

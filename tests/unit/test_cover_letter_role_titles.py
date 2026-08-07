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

"""#321 — a letter may not mint a job title out of a responsibility bullet.

Charter run #8 delivered *"leite ich seit 2017 als **Bereichsverantwortlicher**
zwei Fertigungsbereiche"* for a position the vault records as
``Produktionsleiter``. The noun was lifted from the same position's own
achievement (``ISO-9001-Bereichsverantwortung``), so every checker that asks
"does this text trace to the vault" passed it: the employer, the headcount and
the area count were all real.

Prompt-first triage (category B — the rule was never written): no prompt asked
for the candidate's OWN title to be used verbatim. The CV writer's prompt
already carries that doctrine (*"job titles … a name is copied exactly"*,
``prompts/cv_tailoring.py``); the letter writer's never did. These tests pin
the two halves of the fix:

* the writer rule, and
* the ADR-062 clause-2 reviewer input — the vault's recorded titles as
  deterministic FACTS plus one narrow rule, mirroring ``FIGURE OWNERSHIP``
  (#296/#299). Whether the model OBEYS either is a prompt effect and needs a
  real-provider charter run (ADR-062 clause 7); what CI pins is that the rule
  is stated and the facts reach the loop.
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

# The run-#8 vault, verbatim from the issue.
PROFILE_JSON = {
    "personal_info": {"name": "Marcus Weber", "email": "marcus@example.com"},
    "professional_summary": {"de": "Produktionsleiter mit Serienfertigungserfahrung."},
    "work_experience": [
        {
            "id": "w-weberit",
            "company": "Weberit Kunststofftechnik GmbH",
            "role": "Produktionsleiter",
            "start_date": "2017-04",
            "end_date": None,
            "is_current": True,
            "achievements": [
                "ISO-9001-Bereichsverantwortung für zwei Fertigungsbereiche mit "
                "38 Mitarbeitenden im Dreischichtbetrieb.",
            ],
        },
        {
            "id": "w-rasselstein-meister",
            "company": "Rasselstein Umformtechnik GmbH",
            "role": "Fertigungsmeister / Schichtleiter",
            "start_date": "2011-08",
            "end_date": "2017-03",
        },
        {
            "id": "w-rasselstein-mech",
            "company": "Rasselstein Umformtechnik GmbH",
            "role": "Industriemechaniker",
            "start_date": "2004-07",
            "end_date": "2011-07",
        },
    ],
}


def _invented_title_letter():
    """The delivered run-#8 paragraph — a real position, a title the vault
    does not carry."""
    return {
        "header": {"name": "Marcus Weber"},
        "recipient": {"name": None, "company": "Rheinwerk GmbH", "date": None},
        "body": {
            "paragraphs": [
                "Sehr geehrte Damen und Herren,",
                "Bei der Weberit Kunststofftechnik GmbH leite ich seit 2017 als "
                "Bereichsverantwortlicher zwei Fertigungsbereiche mit 38 "
                "Mitarbeitenden im Dreischichtbetrieb.",
                "Mit freundlichen Grüßen",
            ]
        },
        "signature": {"closing": None, "name": None},
    }


# ---------------------------------------------------------------------------
# The facts (ADR-062 clause 1: a data-structure lookup, no prose read)
# ---------------------------------------------------------------------------


def test_every_recorded_position_title_is_carried_verbatim():
    from applire.services.cover_letter_positioning import vault_role_titles

    titles = vault_role_titles(PROFILE_JSON)

    assert [t.title for t in titles] == [
        "Produktionsleiter",
        "Fertigungsmeister / Schichtleiter",
        "Industriemechaniker",
    ]
    assert titles[0].org == "Weberit Kunststofftechnik GmbH"
    assert titles[0].span == "2017-04 – present"
    assert titles[1].span == "2011-08 – 2017-03"


def test_a_role_alias_is_carried_as_a_permitted_title_for_that_position():
    """``WorkEntry.role_aliases`` holds every title this position has legitimately
    been called. Omitting them would make the reviewer flag an honest letter."""
    from applire.services.cover_letter_positioning import vault_role_titles

    profile = {
        "work_experience": [
            {
                "id": "w1",
                "company": "Weberit Kunststofftechnik GmbH",
                "role": "Produktionsleiter",
                "role_aliases": ["Head of Production", "Produktionsleiter (Serie)"],
            }
        ]
    }
    titles = vault_role_titles(profile)

    assert titles[0].aliases == ("Head of Production", "Produktionsleiter (Serie)")


def test_volunteer_and_project_roles_are_carried_with_their_own_org_label():
    from applire.services.cover_letter_positioning import vault_role_titles

    profile = {
        "work_experience": [{"id": "w1", "company": "Weberit", "role": "Produktionsleiter"}],
        "volunteer_activities": [
            {"id": "v1", "organization": "THW Ortsverband Neuwied", "role": "Zugführer"}
        ],
        "projects": [{"id": "p1", "name": "MES-Rollout", "role": "Projektleiter"}],
    }
    titles = vault_role_titles(profile)

    assert [(t.title, t.org) for t in titles] == [
        ("Produktionsleiter", "Weberit"),
        ("Zugführer", "THW Ortsverband Neuwied"),
        ("Projektleiter", "MES-Rollout"),
    ]


def test_a_position_known_to_have_ended_is_never_rendered_as_ongoing():
    """``is_current`` is tri-state (#155): False means *known ended*. An entry
    that lost its end date must not be handed to the reviewer as 'present'."""
    from applire.services.cover_letter_positioning import vault_role_titles

    profile = {
        "work_experience": [
            {
                "id": "w1",
                "company": "Rasselstein Umformtechnik GmbH",
                "role": "Industriemechaniker",
                "start_date": "2004-07",
                "end_date": None,
                "is_current": False,
            }
        ]
    }

    assert vault_role_titles(profile)[0].span == "2004-07"


def test_a_position_with_no_recorded_title_is_omitted():
    from applire.services.cover_letter_positioning import vault_role_titles

    profile = {
        "work_experience": [
            {"id": "w1", "company": "Weberit", "role": ""},
            {"id": "w2", "company": "Rasselstein", "role": "Industriemechaniker"},
        ]
    }

    assert [t.title for t in vault_role_titles(profile)] == ["Industriemechaniker"]


def test_a_profile_without_work_history_yields_no_titles():
    from applire.services.cover_letter_positioning import vault_role_titles

    assert vault_role_titles({}) == []
    assert vault_role_titles(None) == []


# ---------------------------------------------------------------------------
# The block (ADR-062 clause 2: the facts, verbatim, plus one narrow rule)
# ---------------------------------------------------------------------------


def test_no_recorded_title_renders_no_block():
    from applire.services.cover_letter_positioning import render_role_titles_block

    assert render_role_titles_block([]) == ""


def test_the_block_names_every_recorded_title_with_its_position():
    from applire.services.cover_letter_positioning import (
        render_role_titles_block,
        vault_role_titles,
    )

    block = render_role_titles_block(vault_role_titles(PROFILE_JSON))

    assert "RECORDED JOB TITLES" in block
    assert '"Produktionsleiter"' in block
    assert "Weberit Kunststofftechnik GmbH" in block
    assert '"Fertigungsmeister / Schichtleiter"' in block
    assert "2004-07 – 2011-07" in block


def test_the_block_carries_titles_only_never_responsibility_prose():
    """The #321 mechanism is a responsibility noun promoted to a title, so the
    block must not hand the model the responsibility text as though it were a
    title candidate."""
    from applire.services.cover_letter_positioning import (
        render_role_titles_block,
        vault_role_titles,
    )

    block = render_role_titles_block(vault_role_titles(PROFILE_JSON))

    assert "Bereichsverantwortung" not in block
    assert "Bereichsverantwortlicher" not in block


def test_the_block_states_that_a_responsibility_is_not_a_title():
    from applire.services.cover_letter_positioning import (
        render_role_titles_block,
        vault_role_titles,
    )

    block = render_role_titles_block(vault_role_titles(PROFILE_JSON))

    # the facts are ground truth; the judgement (does this sentence state a
    # title at all?) stays with the reviewer — ADR-062 clause 2
    assert "ground truth" in block
    assert "responsibility" in block.lower()
    # and the direction that made #321 invisible to the blind panel
    assert "understat" in block.lower()


def test_an_alias_is_offered_to_the_reviewer_as_an_accepted_title():
    from applire.services.cover_letter_positioning import (
        render_role_titles_block,
        vault_role_titles,
    )

    profile = {
        "work_experience": [
            {
                "id": "w1",
                "company": "Weberit",
                "role": "Produktionsleiter",
                "role_aliases": ["Head of Production"],
            }
        ]
    }
    block = render_role_titles_block(vault_role_titles(profile))

    assert "Head of Production" in block


# ---------------------------------------------------------------------------
# The writer rule (category B — the sentence that was never written)
# ---------------------------------------------------------------------------


def test_the_writer_prompt_requires_the_candidates_own_title_verbatim():
    from applire.prompts.cover_letter import SYSTEM_PROMPT

    assert "THE CANDIDATE'S OWN JOB TITLES" in SYSTEM_PROMPT
    assert "never turn it into a title" in SYSTEM_PROMPT


def test_the_reviewer_prompt_names_the_recorded_titles_block_as_ground_truth():
    from applire.prompts.review_cover_letter import REVIEW_SYSTEM_PROMPT

    assert "RECORDED JOB TITLES" in REVIEW_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# The wiring (the facts reach the review loop — and the corrector, which is
# what has to restate the title)
# ---------------------------------------------------------------------------


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
    from applire.models.cover_letter import CoverLetterStatus, GeneratedCoverLetter
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile
    from applire.models.user import User

    user = User(id=uuid.uuid4(), email="roletitles@test.com")
    db.add(user)
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash="roletitles123",
        raw_text="Produktionsleiter (m/w/d) bei der Rheinwerk GmbH",
        role_title="Produktionsleiter",
        company_name="Rheinwerk GmbH",
        required_skills=[],
        nice_to_have_skills=[],
        keywords=[],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement="de",
    )
    db.add(job)
    profile = MasterProfile(profile_json=PROFILE_JSON)
    db.add(profile)
    await db.flush()

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
    return db, job, profile, cl


@pytest.mark.asyncio
async def test_the_review_loop_source_carries_the_recorded_titles(seeded):
    """The reviewer AND the corrector read ``source`` (ADR-021 amended
    2026-06-29), and the corrector is what has to restate the title — so the
    block goes into the grounding source, not into a reviewer-only wrapper."""
    db, job, profile, cl = seeded

    from applire.services.cover_letter import _render_cover_letter_background

    mock_provider = MagicMock()
    mock_provider.aparse_json = AsyncMock(return_value=_invented_title_letter())
    captured: dict = {}

    async def _capture(**kwargs):
        captured.update(kwargs)
        return _invented_title_letter()

    with (
        patch("applire.services.cover_letter.AsyncSessionLocal") as mock_session_local,
        patch("applire.services.cover_letter.get_provider", return_value=mock_provider),
        patch("applire.services.cover_letter.review_and_refine", AsyncMock(side_effect=_capture)),
        patch(
            "applire.services.cover_letter_pdf.render_pdf",
            AsyncMock(side_effect=RuntimeError("no browser in unit test")),
        ),
    ):
        mock_session_local.return_value.__aenter__.return_value = db
        await _render_cover_letter_background(cl_id=cl.id, cv_id=None, job_id=job.id)

    source = captured["source"]
    assert "RECORDED JOB TITLES" in source
    assert '"Produktionsleiter"' in source
    assert '"Industriemechaniker"' in source

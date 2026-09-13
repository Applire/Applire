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

"""Adversarial finding (Nougat build 3, `wt-adv-letter`, letter-chain probe
B(ii)) on `LETTER_FINAL_FLOOR`'s `recondensed_repair` path (ADR-076 clause 3
amended 2026-09-13, ruling L-2/L-2b).

The re-condense's own justification (ruling L-2, the ADR delta, and the log
line's own wording — "re-condensed the corrector's repair ... instead of
discarding it") asserts an OUTCOME: the corrector's added coverage terms
survive AND the page norm holds. But the re-condense is itself a scoped LLM
rewrite (`prompts/cover_letter.py::build_condense_prompt`), instructed only
with the FIXED `LETTER_REQUIRED_CONTENT` list and any PINNED quotes — never
told which sentence is the reason THIS call exists. Nothing verified the
coverage terms actually survived the second rewrite: a fake provider whose
re-condense response was in-norm but had silently dropped the corrector's
added sentence still shipped labelled `selection=recondensed_repair`, the
exact success claim the name asserts.

Fix: a FACT check (ADR-062 clause 1) reusing the ledger's own coverage
instrument (`keyword_ledger.claimable_present_entries`, ADR-066 — one
implementation, the same scan `verified_missing_claimable`'s complement
already performs) — comparing what was covered in the corrector's own
composition against what survives the re-condense. A regression does not
revert the delivery (fail-open, exactly the ADR-077 clause 3 pin pattern:
"by instruction here, by measurement afterwards" — a lost pin does not
revert either) but changes the selection value to
`recondensed_repair_lost_coverage` and names the lost concepts in the log,
so the claim the selection makes is never false.
"""

import logging
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.support.profile_factory import make_master_profile

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

_MINIMAL_PROFILE_JSON = {"work_experience": [], "skills": []}

_LEDGER = [
    {"concept": "Arbeitsvorbereitung", "surface_forms": ["Arbeitsvorbereitung"],
     "claimable": True, "status": "direct", "sources": ["keyword"],
     "fit_weight": 0.25, "evidence": "Fertigungssteuerung bei Weberit"},
    {"concept": "5S", "surface_forms": ["5S"],
     "claimable": True, "status": "direct", "sources": ["keyword"],
     "fit_weight": 0.0, "evidence": "5S-Workshops moderiert"},
    {"concept": "Supply Chain", "surface_forms": ["Supply Chain"],
     "claimable": True, "status": "partial", "sources": ["nice_to_have"],
     "fit_weight": 0.5, "evidence": "Materialdisposition gesteuert"},
]

_REPAIR = (
    "Arbeitsvorbereitung, 5S und Supply Chain habe ich bei Weberit verantwortet."
)


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
    import applire.models.user_settings  # noqa: F401
    import applire.models.cover_letter  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed(db):
    from applire.models.cover_letter import CoverLetterStatus, GeneratedCoverLetter
    from applire.models.job import JobAnalysis
    from applire.models.user import User

    unique = uuid.uuid4().hex[:12]
    user = User(id=uuid.uuid4(), email=f"floor-recond-{unique}@test.com")
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash=f"floorrecond{unique}",
        raw_text="Leiter Operations bei Rheinwerk",
        role_title="Leiter Operations",
        company_name="Rheinwerk",
        required_skills=[],
        nice_to_have_skills=[],
        keywords=[],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement="de",
        jd_language="de",
    )
    profile = make_master_profile(profile_json=_MINIMAL_PROFILE_JSON)
    db.add_all([user, job, profile])
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
    return job, profile, cl


def _letter(marker: str) -> dict:
    return {
        "header": {"name": "Max Prober"},
        "recipient": {"name": None, "company": None, "date": None},
        "body": {"paragraphs": ["Sehr geehrte Damen und Herren,", marker]},
        "signature": {"closing": None, "name": "Max Prober"},
    }


def _measure(pages, words):
    from applire.services.cover_letter import MeasuredLetter

    return MeasuredLetter(
        page_count=pages, letter_pages=1, word_count=words, word_budget=300
    )


def _identity(draft: dict) -> dict:
    return draft


def _repair_regrow(draft: dict) -> dict:
    body = dict(draft["body"])
    body["paragraphs"] = list(body["paragraphs"]) + [_REPAIR]
    return {**draft, "body": body}


async def _invoke(db, cl, *, measures, condense_payloads, script, keyword_ledger):
    from applire.norms import REGION_NORMS
    from applire.services.cover_letter import _terminal_review_letter

    payloads = list(condense_payloads)
    queue = list(measures[1:])
    prompts: list[str] = []

    async def _aparse(prompt, system=None, **kw):
        prompts.append(prompt)
        return payloads.pop(0)

    async def _fake_review(**kwargs):
        action = script.pop(0) if script else None
        return action(kwargs["draft"]) if action else kwargs["draft"]

    async def _persist(cl_, db_, composed, norm_):
        cl_.letter_data = composed
        await db_.commit()
        return b"%PDF-fake", queue.pop(0)

    provider = AsyncMock()
    provider.aparse_json = AsyncMock(side_effect=_aparse)

    with (
        patch("applire.services.cover_letter.review_and_refine",
              side_effect=_fake_review),
        patch("applire.services.cover_letter._persist_and_measure", new=_persist),
    ):
        result = await _terminal_review_letter(
            cl, db,
            draft=_letter("SEED"),
            grounding_source="SOURCE MATERIAL",
            provider=provider,
            corrector_prompt_fn=lambda prev, fb, src: "corrector prompt stub",
            wrap_reviewer=lambda base_fn: base_fn,
            norm=REGION_NORMS["DACH"],
            profile=None,
            cv_data={"contact": {}},
            pre_gen={},
            language="de",
            load_bearing_fn=lambda d: frozenset(),
            within_budget_fn=lambda d: True,
            retain_if_fn=lambda d: True,
            pdf_bytes=b"%PDF-fake",
            measured=measures[0],
            reviews_enabled=True,
            condense_spent=True,
            pins=[],
            final_floor=True,
            keyword_ledger=keyword_ledger,
            denied_concepts=[],
        )
    return result, prompts


async def _delivered(db, cl):
    from applire.models.cover_letter import GeneratedCoverLetter

    row = await db.get(GeneratedCoverLetter, cl.id)
    return " ".join(row.letter_data["body"]["paragraphs"])


@pytest.mark.asyncio
async def test_a_recondense_that_drops_the_added_coverage_is_labelled_honestly(
    db, caplog
):
    """The adversarial case: the re-condense is in-norm but has silently
    dropped the corrector's added coverage sentence entirely. The selection
    must not claim `recondensed_repair` — that name asserts the repair
    survived, and it did not."""
    caplog.set_level(logging.WARNING, logger="applire.services.cover_letter")
    _job, _profile, cl = await _seed(db)

    recondensed_but_hollow = _letter("RECONDENSED-BUT-COVERAGE-GONE")
    result, _prompts = await _invoke(
        db, cl,
        measures=[
            _measure(2, 320),   # seed: over norm
            _measure(1, 240),   # floor condense: in norm
            _measure(2, 285),   # floor round's repair: re-grown, carries _REPAIR
            _measure(1, 230),   # the re-condense: in norm, but the repair is gone
        ],
        condense_payloads=[_letter("FLOOR-CONDENSED"), recondensed_but_hollow],
        script=[_identity, _repair_regrow],
        keyword_ledger=_LEDGER,
    )

    assert result.final_floor_selection == "recondensed_repair_lost_coverage", (
        "a re-condense that lost the coverage repair must not be labelled "
        "recondensed_repair — that name is the claim the repair survived"
    )
    delivered = await _delivered(db, cl)
    assert "RECONDENSED-BUT-COVERAGE-GONE" in delivered
    assert "Arbeitsvorbereitung" not in delivered

    lines = [r.getMessage() for r in caplog.records if "dropped claimable term" in r.getMessage()]
    assert lines, caplog.text
    for concept in ("Arbeitsvorbereitung", "5S", "Supply Chain"):
        assert concept in lines[0], lines[0]


@pytest.mark.asyncio
async def test_a_recondense_that_keeps_the_coverage_still_reports_recondensed_repair(
    db,
):
    """The happy path (WP-L's own captured-run shape) must be unaffected: when
    the re-condense DOES keep the corrector's added terms, the selection stays
    exactly `recondensed_repair` — this is a report, not a new gate."""
    _job, _profile, cl = await _seed(db)

    recondensed_keeps_repair = _letter(f"RECONDENSED. {_REPAIR}")
    result, _prompts = await _invoke(
        db, cl,
        measures=[
            _measure(2, 320), _measure(1, 240), _measure(2, 285), _measure(1, 250),
        ],
        condense_payloads=[_letter("FLOOR-CONDENSED"), recondensed_keeps_repair],
        script=[_identity, _repair_regrow],
        keyword_ledger=_LEDGER,
    )

    assert result.final_floor_selection == "recondensed_repair"
    delivered = await _delivered(db, cl)
    assert "Arbeitsvorbereitung" in delivered


@pytest.mark.asyncio
async def test_a_partial_coverage_loss_is_named_precisely(db, caplog):
    """Only the terms that actually regressed are named — a re-condense that
    keeps two of three demanded terms is not reported as losing all three."""
    caplog.set_level(logging.WARNING, logger="applire.services.cover_letter")
    _job, _profile, cl = await _seed(db)

    partial = _letter(
        "RECONDENSED. Arbeitsvorbereitung und 5S habe ich bei Weberit verantwortet."
    )
    result, _prompts = await _invoke(
        db, cl,
        measures=[
            _measure(2, 320), _measure(1, 240), _measure(2, 285), _measure(1, 240),
        ],
        condense_payloads=[_letter("FLOOR-CONDENSED"), partial],
        script=[_identity, _repair_regrow],
        keyword_ledger=_LEDGER,
    )

    assert result.final_floor_selection == "recondensed_repair_lost_coverage"
    lines = [r.getMessage() for r in caplog.records if "dropped claimable term" in r.getMessage()]
    assert lines
    assert "Supply Chain" in lines[0]
    assert "Arbeitsvorbereitung" not in lines[0]
    assert "5S" not in lines[0]


@pytest.mark.asyncio
async def test_with_no_ledger_the_check_is_inert_today_behaviour(db):
    """`keyword_ledger=None` (a legacy call site, or a test that doesn't wire
    one) must reproduce today's behaviour exactly — no ledger means no fact to
    check, never a spurious regression report."""
    _job, _profile, cl = await _seed(db)

    result, _prompts = await _invoke(
        db, cl,
        measures=[
            _measure(2, 320), _measure(1, 240), _measure(2, 285), _measure(1, 230),
        ],
        condense_payloads=[_letter("FLOOR-CONDENSED"), _letter("RECONDENSED-HOLLOW")],
        script=[_identity, _repair_regrow],
        keyword_ledger=None,
    )
    assert result.final_floor_selection == "recondensed_repair"

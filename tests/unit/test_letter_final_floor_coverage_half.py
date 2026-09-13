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

"""`LETTER_FINAL_FLOOR` — the COVERAGE half (#547 letter half, #673).

Build 2 closed the TRUTH half: the floor may not discard a corrector repair that
removed an invented limit (`kept_corrector_limit_grounding`, ADR-076 clause 3
amended 2026-09-11). Everything else still went by the page count, and that is
where the coverage half lived — on 2026-09-10 the floor's own review round
surfaced `Arbeitsvorbereitung`, `5S` and `Supply Chain` (three terms the
reviewer's VERIFIED COVERAGE CHECK had demanded), the letter re-grew to two
pages, and the selection threw all three away
(`LETTER_FINAL_FLOOR … selection=reverted_to_condensed target_words=235`;
recurred 2026-09-11 with `target_words=254`).

Since 2026-09-13 (ADR-076 clause 3 amended, ruling L-2/L-2b; ADR-051 §6's
per-delivery condense bound 2 → 3 for this path and no other) the floor
RE-CONDENSES the repaired composition instead of restoring the older condensed
one. The revert survives as the fallback on exactly two conditions, both covered
here and one of them in `test_letter_final_floor_547.py`
(`test_final_floor_reverts_when_the_recondense_does_not_improve_547`).
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
    user = User(id=uuid.uuid4(), email=f"floor-cov-{unique}@test.com")
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash=f"floorcov{unique}",
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


# The three coverage terms of the 2026-09-10 delivery run's own instance.
_REPAIR = (
    "Arbeitsvorbereitung, 5S und Supply Chain habe ich bei Weberit verantwortet."
)


def _repair_regrow(draft: dict) -> dict:
    """The floor's review round: the corrector ADDS demanded coverage terms and
    the letter re-grows past the page norm. No limit is invented — this is the
    half build 2 left open."""
    body = dict(draft["body"])
    body["paragraphs"] = list(body["paragraphs"]) + [_REPAIR]
    return {**draft, "body": body}


async def _invoke(
    db, cl, *, measures, condense_payloads, script, condense_raises_on=None
):
    from applire.norms import REGION_NORMS
    from applire.services.cover_letter import _terminal_review_letter

    payloads = list(condense_payloads)
    # measures[0] is the SEED handed to the function; the rest are consumed one
    # per `_apply()` — i.e. per compose+persist+render+measure — in order.
    queue = list(measures[1:])
    prompts: list[str] = []

    async def _aparse(prompt, system=None, **kw):
        prompts.append(prompt)
        if condense_raises_on is not None and len(prompts) == condense_raises_on:
            raise RuntimeError("provider blew up on the re-condense")
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
        )
    return result, prompts


async def _delivered(db, cl):
    from applire.models.cover_letter import GeneratedCoverLetter

    row = await db.get(GeneratedCoverLetter, cl.id)
    return " ".join(row.letter_data["body"]["paragraphs"])


# ── 1. the rule ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_floor_recondenses_a_regrown_coverage_repair_instead_of_discarding_it(
    db, caplog
):
    """The 2026-09-10 instance, as a test: the floor's round adds three demanded
    coverage terms, the letter re-grows to 2 pages, and BOTH survive — the repair
    because it is re-condensed rather than reverted, the norm because the
    re-condense lands in it."""
    caplog.set_level(logging.INFO, logger="applire.services.cover_letter")
    caplog.set_level(logging.INFO, logger="applire.llm.review")
    _job, _profile, cl = await _seed(db)

    recondensed = _letter(f"RECONDENSED. {_REPAIR}")
    result, prompts = await _invoke(
        db, cl,
        measures=[
            _measure(2, 320),   # seed: over norm (the terminal round changes nothing)
            _measure(1, 240),   # floor condense: in norm
            _measure(2, 285),   # floor round's repair: re-grown
            _measure(1, 250),   # the re-condense of the repair: in norm
        ],
        condense_payloads=[_letter("FLOOR-CONDENSED"), recondensed],
        script=[_identity, _repair_regrow],
    )

    assert result.final_floor_fired is True
    assert result.final_floor_selection == "recondensed_repair"

    delivered = await _delivered(db, cl)
    assert "RECONDENSED" in delivered
    assert "Arbeitsvorbereitung" in delivered and "Supply Chain" in delivered, (
        "the corrector's coverage repair must survive the floor"
    )
    assert "FLOOR-CONDENSED" not in delivered

    # The subject of the second condense must be the REPAIRED composition, not
    # the condensed one: re-condensing the wrong subject delivers a shorter
    # letter that still lost the repair, and the selection value would lie.
    condense_prompts = [p for p in prompts if "=== CURRENT LETTER (JSON) ===" in p]
    assert len(condense_prompts) == 2, condense_prompts
    assert "Arbeitsvorbereitung" in condense_prompts[1], (
        "the re-condense must be handed the corrector's own composition"
    )
    assert "Arbeitsvorbereitung" not in condense_prompts[0]

    line = [r for r in caplog.records if "LETTER_FINAL_FLOOR cl_id=" in r.getMessage()]
    assert line and "selection=recondensed_repair" in line[-1].getMessage()
    warn = [
        r for r in caplog.records
        if "re-condensed the corrector's repair" in r.getMessage()
    ]
    assert warn and warn[0].levelno == logging.WARNING
    msg = warn[0].getMessage()
    for field in ("corrector_hash=", "corrector_pages=", "recondensed_hash=",
                  "recondensed_pages=", "target_words="):
        assert field in msg, msg


@pytest.mark.asyncio
async def test_the_recondense_is_the_third_condense_and_the_last(db):
    """ADR-051 §6's bound, as amended: three per delivery on THIS path, and the
    floor cannot mint a fourth. Two condense calls reach the provider inside the
    floor (its own + the re-condense); the pre-verdict condense was already spent
    by the caller (`condense_spent=True`)."""
    _job, _profile, cl = await _seed(db)

    result, prompts = await _invoke(
        db, cl,
        measures=[
            _measure(2, 320), _measure(1, 240), _measure(2, 285), _measure(1, 250),
        ],
        condense_payloads=[_letter("FLOOR-CONDENSED"), _letter(f"RE. {_REPAIR}")],
        script=[_identity, _repair_regrow],
    )
    assert result.final_floor_selection == "recondensed_repair"
    condense_prompts = [p for p in prompts if "=== CURRENT LETTER (JSON) ===" in p]
    assert len(condense_prompts) == 2, condense_prompts


@pytest.mark.asyncio
async def test_the_recondense_targets_the_same_calibrated_word_count(db):
    """Same target, same pins, same prompt builder as the floor's own condense —
    the re-condense is the SAME operation applied to the repaired subject, not a
    new kind of rewrite."""
    _job, _profile, cl = await _seed(db)

    import re

    _result, prompts = await _invoke(
        db, cl,
        measures=[
            _measure(2, 320), _measure(1, 240), _measure(2, 285), _measure(1, 250),
        ],
        condense_payloads=[_letter("FLOOR-CONDENSED"), _letter(f"RE. {_REPAIR}")],
        script=[_identity, _repair_regrow],
    )
    condense_prompts = [p for p in prompts if "=== CURRENT LETTER (JSON) ===" in p]
    assert len(condense_prompts) == 2
    targets = [re.search(r"AT MOST (\d+) words", p).group(1) for p in condense_prompts]
    assert targets[0] == targets[1], targets


@pytest.mark.asyncio
async def test_the_truth_half_still_short_circuits_before_any_recondense(db):
    """A repair that removed an invented limit is delivered WHOLE — it is never
    re-condensed, because a second instruction-only rewrite of a truth repair is
    exactly the exposure ADR-076 clause 3 refused on 2026-09-11."""
    from applire.services.cover_letter import _terminal_review_letter  # noqa: F401

    _job, _profile, cl = await _seed(db)

    limit_sentence = (
        "Mit Qualitätsmanagement habe ich keine Erfahrung."
    )

    def _remove_limit(draft: dict) -> dict:
        body = dict(draft["body"])
        body["paragraphs"] = [
            p for p in body["paragraphs"] if limit_sentence not in p
        ] + ["Die Repair-Fassung, laenger als die Norm erlaubt."]
        return {**draft, "body": body}

    condensed_with_limit = {
        "header": {"name": "Max Prober"},
        "recipient": {"name": None, "company": None, "date": None},
        "body": {"paragraphs": [
            "Sehr geehrte Damen und Herren,", limit_sentence,
        ]},
        "signature": {"closing": None, "name": "Max Prober"},
    }

    from applire.norms import REGION_NORMS
    from applire.services.cover_letter import _terminal_review_letter

    queue = [_measure(1, 240), _measure(2, 285)]
    payloads = [condensed_with_limit]
    prompts: list[str] = []
    script = [_identity, _remove_limit]

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
            draft={
                "header": {"name": "Max Prober"},
                "recipient": {"name": None, "company": None, "date": None},
                "body": {"paragraphs": [
                    "Sehr geehrte Damen und Herren,",
                    "Die lange Ausgangsfassung, deutlich ueber der Norm.",
                    limit_sentence,
                ]},
                "signature": {"closing": None, "name": "Max Prober"},
            },
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
            measured=_measure(2, 320),
            reviews_enabled=True,
            condense_spent=True,
            pins=[],
            final_floor=True,
            keyword_ledger=[{
                "concept": "Qualitätsmanagement",
                "surface_forms": ["Qualitätsmanagement"],
                "claimable": True, "status": "partial", "sources": ["required"],
                "fit_weight": 1.0, "evidence": "ISO-9001-Audits begleitet",
            }],
            denied_concepts=[],
        )

    assert result.final_floor_selection == "kept_corrector_limit_grounding"
    condense_prompts = [p for p in prompts if "=== CURRENT LETTER (JSON) ===" in p]
    assert len(condense_prompts) == 1, (
        "the truth branch returns before the re-condense — only the floor's own "
        "condense may have run"
    )


# ── 2. the two fallbacks ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_failed_recondense_falls_back_to_the_condensed_composition(db, caplog):
    """Fail-open, exactly like both condenses above it: a provider error on the
    re-condense leaves the delivery no worse than before this change, and says so
    in its own selection value."""
    caplog.set_level(logging.INFO, logger="applire.services.cover_letter")
    caplog.set_level(logging.INFO, logger="applire.llm.review")
    _job, _profile, cl = await _seed(db)

    result, _prompts = await _invoke(
        db, cl,
        measures=[
            _measure(2, 320), _measure(1, 240), _measure(2, 285), _measure(1, 240),
        ],
        condense_payloads=[_letter("FLOOR-CONDENSED")],
        script=[_identity, _repair_regrow],
        condense_raises_on=2,
    )

    assert result.final_floor_fired is True
    assert result.final_floor_selection == (
        "reverted_to_condensed_after_failed_recondense"
    )
    delivered = await _delivered(db, cl)
    assert "FLOOR-CONDENSED" in delivered
    assert "Arbeitsvorbereitung" not in delivered

    line = [r for r in caplog.records if "LETTER_FINAL_FLOOR cl_id=" in r.getMessage()]
    assert line
    assert "selection=reverted_to_condensed_after_failed_recondense" in (
        line[-1].getMessage()
    )


@pytest.mark.asyncio
async def test_a_recondense_that_improves_the_page_count_ships_even_over_norm(db):
    """"Still over the norm" is not by itself a revert condition — a re-condense
    that got the letter from 3 pages to 2 kept the repair AND improved the norm
    deviation, and reverting would trade both away for one page."""
    _job, _profile, cl = await _seed(db)

    result, _prompts = await _invoke(
        db, cl,
        measures=[
            _measure(3, 420),   # seed
            _measure(1, 240),   # floor condense
            _measure(3, 395),   # the repair: three pages
            _measure(2, 300),   # the re-condense: two — still over, but closer
        ],
        condense_payloads=[_letter("FLOOR-CONDENSED"), _letter(f"RE. {_REPAIR}")],
        script=[_identity, _repair_regrow],
    )
    assert result.final_floor_selection == "recondensed_repair"
    delivered = await _delivered(db, cl)
    assert "Arbeitsvorbereitung" in delivered


@pytest.mark.asyncio
async def test_an_unmeasurable_recondense_is_kept_fail_open(db):
    """`page_count is None` means the render or the page measurement failed —
    both are fail-open by `MeasuredLetter`'s own contract, so an unmeasurable
    re-condense is not evidence for throwing the repair away."""
    _job, _profile, cl = await _seed(db)

    result, _prompts = await _invoke(
        db, cl,
        measures=[
            _measure(2, 320), _measure(1, 240), _measure(2, 285),
            _measure(None, 250),
        ],
        condense_payloads=[_letter("FLOOR-CONDENSED"), _letter(f"RE. {_REPAIR}")],
        script=[_identity, _repair_regrow],
    )
    assert result.final_floor_selection == "recondensed_repair"


@pytest.mark.asyncio
async def test_an_in_norm_corrector_repair_is_never_recondensed(db):
    """The `kept_corrector` path is untouched: no re-grow, no third condense."""
    _job, _profile, cl = await _seed(db)

    result, prompts = await _invoke(
        db, cl,
        measures=[
            _measure(2, 320), _measure(1, 240),
            _measure(1, 255),   # the repair still fits
        ],
        condense_payloads=[_letter("FLOOR-CONDENSED")],
        script=[_identity, _repair_regrow],
    )
    assert result.final_floor_selection == "kept_corrector"
    condense_prompts = [p for p in prompts if "=== CURRENT LETTER (JSON) ===" in p]
    assert len(condense_prompts) == 1
    delivered = await _delivered(db, cl)
    assert "Arbeitsvorbereitung" in delivered

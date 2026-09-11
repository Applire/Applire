# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#664 — the three seams of ADR-075's 2026-09-11 grounding catch, in the letter
chain itself (the detector's own unit tests are `test_664_limit_grounding.py`).

One seam test per consumer, per the shared-helper rule:

* **clause 2a** — every terminal round hands `review_and_refine` a
  `signal_issues_fn` that raises the ungrounded limit as a BLOCKING issue, so the
  corrector is told about it through ADR-083 clause 4's single transport.
* **clause 2b** — `LETTER_FINAL_FLOOR`'s selection reads the limit fact BEFORE the
  page count. This is the 2026-09-05 delivery run reproduced: the condensed
  composition carries the manufactured limit, the corrector's round removed it and
  re-grew the letter past the norm, and the floor discarded the repair. It must
  now keep it.
* **clause 2c** — when no draft of the delivery could ground the sentence, it is
  CUT at settle, `limit_cuts` names it, and the ADR-039 `terminal-review` check's
  `details` tells the candidate what was removed and why.

Harness copied (not imported) from `test_letter_final_floor_547.py`, per that
file's own stated convention of keeping these local.
"""
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

# The 2026-09-05 run's facts, reduced to what this seam needs.
LEDGER = [
    {"concept": "Qualitätsmanagement", "surface_forms": ["Qualitätssicherung", "ISO 9001"],
     "claimable": True, "status": "partial"},
    {"concept": "Verbundverpackungen", "surface_forms": [], "claimable": False,
     "status": "denied"},
]
DENIED = [{"concept": "Direkte Erfahrung mit Verbundverpackungen", "statement": "…"}]

_UNGROUNDED = "Qualitätsmanagement beanspruche ich nicht."
_GROUNDED = "Direkte Erfahrung mit Verbundverpackungen habe ich nicht."


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


@pytest_asyncio.fixture
async def cl(db):
    from applire.models.cover_letter import CoverLetterStatus, GeneratedCoverLetter
    from applire.models.job import JobAnalysis
    from applire.models.user import User

    unique = uuid.uuid4().hex[:12]
    user = User(id=uuid.uuid4(), email=f"limit-664-{unique}@test.com")
    job = JobAnalysis(
        id=uuid.uuid4(), raw_text_hash=f"limit664{unique}",
        raw_text="Leiter Operations", role_title="Leiter Operations",
        company_name="Rheinwerk", required_skills=[], nice_to_have_skills=[],
        keywords=[], seniority_level="senior", company_culture_signals=[],
        language_requirement="de", jd_language="de",
    )
    profile = make_master_profile(profile_json=_MINIMAL_PROFILE_JSON)
    db.add_all([user, job, profile])
    await db.flush()
    row = GeneratedCoverLetter(
        job_analysis_id=job.id, profile_id=profile.id, template="classic_german",
        letter_data={}, pre_gen_inputs={}, status=CoverLetterStatus.pending.value,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


def _letter(*paragraphs: str) -> dict:
    return {
        "header": {"name": "Stefan Brandt"},
        "recipient": {"name": None, "company": None, "date": None},
        "body": {"paragraphs": ["Sehr geehrte Damen und Herren,", *paragraphs]},
        "signature": {"closing": None, "name": "Stefan Brandt"},
    }


def _measure(pages, words=200):
    from applire.services.cover_letter import MeasuredLetter
    return MeasuredLetter(page_count=pages, letter_pages=1, word_count=words, word_budget=300)


def _persist_from_queue(measures):
    async def fake(cl, db, composed, norm):
        cl.letter_data = composed
        await db.commit()
        return b"%PDF-fake", measures.pop(0) if measures else _measure(1)
    return fake


async def _invoke(db, cl, *, draft, script, measures, measured_seed,
                  condense_payloads=(), final_floor=True, condense_spent=False,
                  captured=None):
    from applire.norms import REGION_NORMS
    from applire.services.cover_letter import _terminal_review_letter

    payloads = list(condense_payloads)
    script = list(script)

    async def _aparse(prompt, system=None, **kw):
        return payloads.pop(0)

    async def fake_review(**kwargs):
        if captured is not None:
            captured.append(kwargs)
        action = script.pop(0) if script else None
        return action(kwargs["draft"]) if action else kwargs["draft"]

    provider = AsyncMock()
    provider.aparse_json = AsyncMock(side_effect=_aparse)

    with (
        patch("applire.services.cover_letter.review_and_refine", side_effect=fake_review),
        patch("applire.services.cover_letter._persist_and_measure",
              new=_persist_from_queue(list(measures))),
    ):
        return await _terminal_review_letter(
            cl, db,
            draft=draft, grounding_source="SOURCE", provider=provider,
            corrector_prompt_fn=lambda prev, fb, src: "corrector stub",
            wrap_reviewer=lambda base_fn: base_fn,
            norm=REGION_NORMS["DACH"], profile=None, cv_data={"contact": {}},
            pre_gen={}, language="de",
            load_bearing_fn=lambda d: frozenset(),
            within_budget_fn=lambda d: True, retain_if_fn=lambda d: True,
            pdf_bytes=b"%PDF-fake", measured=measured_seed,
            reviews_enabled=True, condense_spent=condense_spent,
            pins=[], final_floor=final_floor,
            keyword_ledger=LEDGER, denied_concepts=DENIED,
        )


# ── clause 2a — the per-round deterministic issue ───────────────────────────
@pytest.mark.asyncio
async def test_every_terminal_round_hands_the_corrector_the_ungrounded_limit(db, cl):
    draft = _letter(_GROUNDED + " " + _UNGROUNDED)
    cl.letter_data = draft
    await db.commit()
    captured: list = []
    await _invoke(
        db, cl, draft=draft, script=[lambda d: d], measures=[_measure(1)],
        measured_seed=_measure(1), final_floor=False, condense_spent=True,
        captured=captured,
    )
    assert captured, "the terminal loop ran no review round"
    signal_fn = captured[0]["signal_issues_fn"]
    issues = list(signal_fn(draft))
    assert [i.is_blocking for i in issues] == [True]
    assert "Qualitätsmanagement" in issues[0].text
    # and the GROUNDED limit is never raised
    assert "Verbundverpackungen" not in issues[0].text


# ── clause 2b — the floor's selection reads the limit fact first ────────────
@pytest.mark.asyncio
async def test_the_final_floor_keeps_a_corrector_repair_over_the_page_norm(db, cl):
    """The 2026-09-05 delivery run, reproduced: the condensed composition carries
    the manufactured limit; the floor's own review round removes it and the letter
    re-grows to 2 pages. Before this change the floor reverted to the condensed
    draft and the false sentence shipped."""
    seed = _letter(_GROUNDED, _UNGROUNDED)
    cl.letter_data = seed
    await db.commit()
    condensed = _letter(_GROUNDED + " " + _UNGROUNDED)
    repaired = _letter(_GROUNDED, "Als Bereichsverantwortlicher begleitete ich ISO-9001-Audits.")

    result = await _invoke(
        db, cl,
        draft=seed,
        # round 1 settles unchanged (loop breaks), then the floor's own round
        # returns the repaired draft
        script=[lambda d: d, lambda d: repaired],
        # seed measure is over-norm so the PRE-VERDICT condense fires first,
        # then the floor's condense, then the repaired draft re-grows to 2 pages
        measures=[_measure(2), _measure(2), _measure(2)],
        measured_seed=_measure(2),
        condense_payloads=[condensed, condensed],
    )
    assert result.final_floor_fired is True
    assert result.final_floor_selection == "kept_corrector_limit_grounding"
    delivered = " ".join(cl.letter_data["body"]["paragraphs"])
    assert "Qualitätsmanagement" not in delivered
    assert "ISO-9001-Audits" in delivered
    assert result.limit_cuts == ()


# ── clause 2c — the settle-time cut, and what the report says ───────────────
@pytest.mark.asyncio
async def test_a_limit_no_round_could_ground_is_cut_at_settle_and_reported(db, cl):
    draft = _letter(f"{_GROUNDED} {_UNGROUNDED} Bei Weberit leite ich zwei Bereiche.")
    cl.letter_data = draft
    await db.commit()
    result = await _invoke(
        db, cl, draft=draft, script=[lambda d: d],
        measures=[_measure(1)], measured_seed=_measure(1),
        final_floor=False, condense_spent=True,
    )
    assert result.limit_cuts == (_UNGROUNDED,)
    delivered = cl.letter_data["body"]["paragraphs"]
    body = " ".join(delivered)
    assert "Qualitätsmanagement" not in body
    # the grounded limit and the unrelated sentence either side survive
    assert _GROUNDED in body
    assert "Bei Weberit leite ich zwei Bereiche." in body

    from applire.services.terminal_review_outcome import build_terminal_review_check

    check = build_terminal_review_check(result.outcome, document="letter")
    assert "Qualitätsmanagement" in check.details
    assert _UNGROUNDED in check.details


@pytest.mark.asyncio
async def test_a_letter_with_only_grounded_limits_is_delivered_untouched(db, cl):
    draft = _letter(_GROUNDED)
    cl.letter_data = draft
    await db.commit()
    result = await _invoke(
        db, cl, draft=draft, script=[lambda d: d],
        measures=[_measure(1)], measured_seed=_measure(1),
        final_floor=False, condense_spent=True,
    )
    assert result.limit_cuts == ()
    assert _GROUNDED in " ".join(cl.letter_data["body"]["paragraphs"])

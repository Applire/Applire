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

"""#547 residual — the letter's FINAL length floor (ADR-076 amended
2026-08-29, clause 3-L1/3-L2/3-L3; ADR-051 §6 amended the same day).

The seam (the bounded pre-verdict condense, the terminal review loop) holds;
the DELIVERY did not: a terminal-corrector round after the single condense
can re-grow the letter past the page norm and nothing has a lever left
(2026-08-28 delivery `d1cdc53f`: 235 -> 258 -> 293 words -> 2 pages,
delivered with the ATS audit's ``page-length-letter`` check already failing).
This file tests the two-part fix:

* **3-L1** — a RENDER MEASURE block, code-computed and wired as a per-round
  prompt WRAPPER into every corrector round of both letter loops (words-only
  on the drafting mount; pages+words on the terminal mount), naming the same
  REQUIRED CONTENT list ``build_condense_prompt`` protects.
* **3-L2** — ``_final_length_floor``: the POST-LOOP tail of
  ``_terminal_review_letter`` — a calibrated SECOND condense (bounded to
  exactly one, ADR-051 §6 amended to <= 2 condenses per delivery), ONE direct
  review round, and a page-measured SELECTION between the condensed
  composition and the reviewed round's own result if it re-grows.

Two harnesses are used, deliberately:

* Tests 1, 5c, 6 and 8 drive ``_render_cover_letter_background`` end to end
  (the existing convention in ``test_letter_terminal_review.py`` /
  ``test_letter_salutation_floor_564.py``) — they prove the REAL call sites
  wire ``final_floor`` correctly (default True on the initial invocation,
  explicit False on the subject-identity re-entry).
* Tests 2, 3, 4, 5a, 5b and 7 call ``_terminal_review_letter`` DIRECTLY, with
  ``_persist_and_measure`` mocked to a QUEUE of exact ``MeasuredLetter``
  values — precise, fast unit coverage of the floor's own trigger/target/
  selection logic, decoupled from the drafting loop and from real paragraph
  word-counting. ``review_and_refine`` is mocked identically either way (the
  ``chain_id``-dispatching stub already established in
  ``test_letter_terminal_review.py``).
"""
import logging
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.support.profile_factory import make_master_profile

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


_MINIMAL_PROFILE_JSON = {"work_experience": [], "skills": []}


# ── shared fixture/data helpers (adapted from test_letter_terminal_review.py
#    — no cross-test-file imports elsewhere in this tree, kept local per that
#    file's own convention) ───────────────────────────────────────────────


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
    user = User(id=uuid.uuid4(), email=f"letter-floor-547-{unique}@test.com")
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash=f"letterfloor547{unique}",
        raw_text="Platform Engineer at Vector Analytics",
        role_title="Platform Engineer",
        company_name="Vector Analytics",
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
    """A guard-inert, structurally valid letter draft: a recognised
    salutation opener (so ``_inject_salutation`` never prepends one) plus one
    marker paragraph — no figures/employer names, so
    ``guard_letter_figures``/``guard_letter_outcome_preference`` no-op
    against the empty ``_MINIMAL_PROFILE_JSON`` vault."""
    return {
        "header": {"name": "Max Prober"},
        "recipient": {"name": None, "company": None, "date": None},
        "body": {"paragraphs": ["Sehr geehrte Damen und Herren,", marker]},
        "signature": {"closing": None, "name": "Max Prober"},
    }


def _identity(draft: dict) -> dict:
    """A script no-op: the round settles the draft UNCHANGED."""
    return draft


# ── direct-invocation harness (tests 2, 3, 4, 5a, 5b, 7) ────────────────────


def _fake_review_direct(script: list):
    async def fake(**kwargs):
        action = script.pop(0) if script else None
        return action(kwargs["draft"]) if action else kwargs["draft"]
    return fake


def _persist_from_queue(measures: list):
    async def fake_persist_and_measure(cl, db, composed, norm):
        cl.letter_data = composed
        await db.commit()
        return b"%PDF-fake", measures.pop(0)
    return fake_persist_and_measure


async def _invoke_terminal_review(
    db, cl, *, draft, condense_payloads, measures, measured_seed,
    pins=(), condense_spent=False, final_floor=True, script=None,
    reviews_enabled=True,
):
    from applire.norms import REGION_NORMS
    from applire.services.cover_letter import _terminal_review_letter

    condense_prompts: list[str] = []
    condense_payloads = list(condense_payloads)

    async def _aparse(prompt, system=None, **kw):
        condense_prompts.append(prompt)
        return condense_payloads.pop(0)

    provider = AsyncMock()
    provider.aparse_json = AsyncMock(side_effect=_aparse)
    norm = REGION_NORMS["DACH"]

    with (
        patch("applire.services.cover_letter.review_and_refine",
              side_effect=_fake_review_direct(list(script or []))),
        patch("applire.services.cover_letter._persist_and_measure",
              new=_persist_from_queue(list(measures))),
    ):
        result = await _terminal_review_letter(
            cl, db,
            draft=draft,
            grounding_source="SOURCE MATERIAL",
            provider=provider,
            corrector_prompt_fn=lambda prev, fb, src: "corrector prompt stub",
            wrap_reviewer=lambda base_fn: base_fn,
            norm=norm,
            profile=None,
            cv_data={"contact": {}},
            pre_gen={},
            language="de",
            load_bearing_fn=lambda d: frozenset(),
            within_budget_fn=lambda d: True,
            retain_if_fn=lambda d: True,
            pdf_bytes=b"%PDF-fake",
            measured=measured_seed,
            reviews_enabled=reviews_enabled,
            condense_spent=condense_spent,
            pins=list(pins),
            final_floor=final_floor,
        )
    return result, condense_prompts


# ── full-pipeline harness (tests 1, 5c, 6, 8 — adapted from
#    test_letter_terminal_review.py's _run_pipeline, extended with a
#    condense_payloads QUEUE so successive condense calls can be told apart)


def _fake_review_pipeline(script: list, captured: list):
    async def fake(**kwargs):
        if kwargs.get("chain_id") != "letter_terminal_review":
            return kwargs["draft"]
        captured.append({
            "draft": kwargs["draft"],
            "prompt": kwargs["reviewer_prompt_fn"](kwargs["source"], kwargs["draft"]),
        })
        action = script.pop(0) if script else None
        return action(kwargs["draft"]) if action else kwargs["draft"]
    return fake


async def _run_pipeline_547(
    db, ids, *, script=None, captured=None, extra_patches=(),
    review_retries=2, payload=None, pages=None, condense_payloads=None,
):
    from applire.services.cover_letter import _render_cover_letter_background

    job, profile, cl = ids
    captured = captured if captured is not None else []
    pages_queue = list(pages or [])
    condense_queue = list(condense_payloads or [])
    calls = {"writer": 0, "condense": 0}

    async def _aparse(prompt, system=None, **kw):
        if "=== CURRENT LETTER (JSON) ===" in prompt:
            idx = calls["condense"]
            calls["condense"] += 1
            if condense_queue:
                return condense_queue[min(idx, len(condense_queue) - 1)]
            return _letter(f"CONDENSED-{idx}")
        calls["writer"] += 1
        return payload if payload is not None else _letter("WRITER-SEED")

    provider = AsyncMock()
    provider.aparse_json = AsyncMock(side_effect=_aparse)
    extract = MagicMock(
        side_effect=lambda pdf: ("text", pages_queue.pop(0) if pages_queue else 1)
    )

    patches = [
        patch("applire.services.cover_letter.get_provider", return_value=provider),
        patch("applire.services.cover_letter.review_and_refine",
              side_effect=_fake_review_pipeline(script or [], captured)),
        patch("applire.services.cover_letter.LLM_REVIEW_MAX_RETRIES", review_retries),
        patch("applire.services.cover_letter_pdf.render_pdf",
              AsyncMock(return_value=b"%PDF-fake")),
        patch("applire.services.ats_audit.extract_text_and_pages", new=extract),
    ]
    if not any("update_ats_report_letter" in str(getattr(p, "attribute", "") or p)
               for p in extra_patches):
        patches.append(
            patch("applire.services.cover_letter._update_ats_report_letter",
                  new=AsyncMock())
        )
    with patch("applire.services.cover_letter.AsyncSessionLocal") as sl:
        sl.return_value.__aenter__.return_value = db
        from contextlib import ExitStack
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            for p in extra_patches:
                stack.enter_context(p)
            await _render_cover_letter_background(
                cl_id=cl.id, cv_id=None, job_id=job.id
            )
    return captured, calls


def _final_floor_lines(caplog):
    return [r for r in caplog.records if "LETTER_FINAL_FLOOR" in r.getMessage()]


# === 1. the floor fires end to end (full pipeline, default final_floor) ====


_REGROWN_547 = "REGROWN-BY-CORRECTOR, past the page norm again."


def _regrow_547(draft: dict) -> dict:
    body = dict(draft["body"])
    body["paragraphs"] = list(body["paragraphs"]) + [_REGROWN_547]
    return {**draft, "body": body, "recipient": {**draft["recipient"], "date": None}}


@pytest.mark.asyncio
async def test_final_floor_fires_on_post_condense_regrowth_547(db, caplog):
    """pages [2 (seed) -> condense -> 1 -> corrector regrows -> 2 -> loop
    exits at the LETTER_TERMINAL_REENTRY_MAX bound] -> the floor fires: a
    SECOND condense call, one review round, delivered measure in-norm."""
    caplog.set_level(logging.INFO, logger="applire.services.cover_letter")
    caplog.set_level(logging.INFO, logger="applire.llm.review")
    ids = await _seed(db)

    captured, calls = await _run_pipeline_547(
        db, ids,
        pages=[2, 1, 2, 1],
        condense_payloads=[_letter("CONDENSE-1-547"), _letter("CONDENSE-2-547")],
        script=[_regrow_547],
    )

    assert calls["condense"] == 2, "the floor's own second condense must fire"
    assert len(captured) == 3, \
        "loop round 1 (regrows) + loop round 2 (settles, the REENTRY bound) " \
        "+ the floor's own review round"

    from applire.models.cover_letter import GeneratedCoverLetter
    cl = await db.get(GeneratedCoverLetter, ids[2].id)
    assert cl.status == "ready"
    delivered = " ".join(cl.letter_data["body"]["paragraphs"])
    assert "CONDENSE-2-547" in delivered, "the floor's own condense ships"
    assert _REGROWN_547 not in delivered, \
        "the regrown corrector content must not survive to delivery"

    lines = _final_floor_lines(caplog)
    assert lines, "the structured LETTER_FINAL_FLOOR line must be present"
    msg = lines[-1].getMessage()
    assert "fired=True" in msg
    assert "pages_after=1" in msg, "the delivered measure is in-norm"


# === 2. the condense target is the largest in-norm word count seen ========


_LOOP_REGROW_547 = "LOOP-REGROWTH text, over the page norm."


def _loop_regrow_547(draft: dict) -> dict:
    body = dict(draft["body"])
    body["paragraphs"] = list(body["paragraphs"]) + [_LOOP_REGROW_547]
    return {**draft, "body": body}


@pytest.mark.asyncio
async def test_final_floor_target_is_largest_in_norm_word_count_547(db):
    from applire.services.cover_letter import MeasuredLetter

    ids = await _seed(db)
    cl = ids[2]

    # In-norm measure exists (181 words) -> that becomes the target.
    measured_seed = MeasuredLetter(
        page_count=2, letter_pages=1, word_count=310, word_budget=300
    )
    measures = [
        MeasuredLetter(page_count=1, letter_pages=1, word_count=181, word_budget=300),
        MeasuredLetter(page_count=2, letter_pages=1, word_count=260, word_budget=300),
        MeasuredLetter(page_count=1, letter_pages=1, word_count=140, word_budget=300),
    ]
    result, condense_prompts = await _invoke_terminal_review(
        db, cl,
        draft=_letter("SEED-547"),
        condense_payloads=[_letter("CONDENSE-1-547"), _letter("CONDENSE-2-547")],
        measures=measures,
        measured_seed=measured_seed,
        script=[_loop_regrow_547],
    )

    assert result.final_floor_fired is True
    assert len(condense_prompts) == 2
    assert "AT MOST 181 words" in condense_prompts[1], condense_prompts[1]

    # Fallback: nothing measured in-norm before the floor -> region budget.
    cl2 = (await _seed(db))[2]
    measured_seed_over = MeasuredLetter(
        page_count=2, letter_pages=1, word_count=320, word_budget=300
    )
    measures_over = [
        MeasuredLetter(page_count=2, letter_pages=1, word_count=280, word_budget=300),
    ]
    result2, condense_prompts2 = await _invoke_terminal_review(
        db, cl2,
        draft=_letter("SEED-547-B"),
        condense_payloads=[_letter("CONDENSE-1-547-B"), _letter("CONDENSE-2-547-B")],
        measures=measures_over,
        measured_seed=measured_seed_over,
    )
    assert result2.final_floor_fired is True
    assert len(condense_prompts2) == 2
    assert "AT MOST 300 words" in condense_prompts2[1], condense_prompts2[1]


# === 3. selection reverts a regrown post-condense corrector change ========


_FLOOR_REGROW_547 = "FLOOR-ROUND-REGROWTH, past the norm yet again."


def _floor_regrow_547(draft: dict) -> dict:
    body = dict(draft["body"])
    body["paragraphs"] = list(body["paragraphs"]) + [_FLOOR_REGROW_547]
    return {**draft, "body": body}


@pytest.mark.asyncio
async def test_final_floor_selection_reverts_regrown_corrector_change_547(db, caplog):
    from applire.services.cover_letter import MeasuredLetter

    caplog.set_level(logging.INFO, logger="applire.llm.review")
    ids = await _seed(db)
    cl = ids[2]

    measured_seed = MeasuredLetter(
        page_count=2, letter_pages=1, word_count=320, word_budget=300
    )
    measures = [
        MeasuredLetter(page_count=1, letter_pages=1, word_count=150, word_budget=300),  # loop condense
        MeasuredLetter(page_count=2, letter_pages=1, word_count=250, word_budget=300),  # loop round1 regrowth
        MeasuredLetter(page_count=1, letter_pages=1, word_count=140, word_budget=300),  # floor condense
        MeasuredLetter(page_count=2, letter_pages=1, word_count=245, word_budget=300),  # floor round regrowth
        MeasuredLetter(page_count=1, letter_pages=1, word_count=140, word_budget=300),  # revert re-apply
    ]
    result, _ = await _invoke_terminal_review(
        db, cl,
        draft=_letter("SEED-547-C"),
        condense_payloads=[_letter("LOOP-CONDENSE-547-C"), _letter("FLOOR-CONDENSE-547-C")],
        measures=measures,
        measured_seed=measured_seed,
        script=[_loop_regrow_547, _identity, _floor_regrow_547],
    )

    assert result.final_floor_fired is True
    assert result.final_floor_selection == "reverted_to_condensed"

    from applire.models.cover_letter import GeneratedCoverLetter
    delivered_row = await db.get(GeneratedCoverLetter, cl.id)
    delivered = " ".join(delivered_row.letter_data["body"]["paragraphs"])
    assert "FLOOR-CONDENSE-547-C" in delivered
    assert _FLOOR_REGROW_547 not in delivered

    lines = _final_floor_lines(caplog)
    assert lines and "fired=True" in lines[-1].getMessage()
    assert "selection=reverted_to_condensed" in lines[-1].getMessage()
    revert_lines = [
        r for r in caplog.records
        if "LETTER_FINAL_FLOOR selection reverted" in r.getMessage()
    ]
    assert revert_lines, "the discarded change must be logged with both hashes"
    assert revert_lines[0].levelno == logging.WARNING
    assert "corrector_hash=" in revert_lines[0].getMessage()
    assert "condensed_hash=" in revert_lines[0].getMessage()


# === 4. an in-norm post-condense corrector change ships ====================


_FLOOR_CHANGE_INORM_547 = "FLOOR-ROUND-CHANGE, still short."


def _floor_change_inorm_547(draft: dict) -> dict:
    body = dict(draft["body"])
    body["paragraphs"] = list(body["paragraphs"]) + [_FLOOR_CHANGE_INORM_547]
    return {**draft, "body": body}


@pytest.mark.asyncio
async def test_final_floor_keeps_in_norm_corrector_change_547(db):
    from applire.services.cover_letter import MeasuredLetter

    ids = await _seed(db)
    cl = ids[2]

    measured_seed = MeasuredLetter(
        page_count=2, letter_pages=1, word_count=320, word_budget=300
    )
    measures = [
        MeasuredLetter(page_count=2, letter_pages=1, word_count=250, word_budget=300),  # loop condense (still over)
        MeasuredLetter(page_count=1, letter_pages=1, word_count=150, word_budget=300),  # floor condense
        MeasuredLetter(page_count=1, letter_pages=1, word_count=145, word_budget=300),  # floor round change (in-norm)
    ]
    result, _ = await _invoke_terminal_review(
        db, cl,
        draft=_letter("SEED-547-D"),
        condense_payloads=[_letter("LOOP-CONDENSE-547-D"), _letter("FLOOR-CONDENSE-547-D")],
        measures=measures,
        measured_seed=measured_seed,
        script=[_identity, _floor_change_inorm_547],
    )

    assert result.final_floor_fired is True
    assert result.final_floor_selection == "kept_corrector"

    from applire.models.cover_letter import GeneratedCoverLetter
    delivered_row = await db.get(GeneratedCoverLetter, cl.id)
    delivered = " ".join(delivered_row.letter_data["body"]["paragraphs"])
    assert _FLOOR_CHANGE_INORM_547 in delivered, \
        "an in-norm post-condense corrector change ships, not reverted"


# === 5. the floor's three skip reasons =====================================


@pytest.mark.asyncio
async def test_final_floor_skips_when_in_norm_547(db):
    from applire.services.cover_letter import MeasuredLetter

    ids = await _seed(db)
    cl = ids[2]

    measured_seed = MeasuredLetter(
        page_count=1, letter_pages=1, word_count=200, word_budget=300
    )
    result, condense_prompts = await _invoke_terminal_review(
        db, cl,
        draft=_letter("SEED-547-E"),
        condense_payloads=[],
        measures=[],
        measured_seed=measured_seed,
        condense_spent=True,  # isolate the in-norm reason from "unspent"
    )

    assert result.final_floor_fired is False
    assert result.final_floor_selection == "none"
    assert condense_prompts == [], "no condense call when already in-norm"


@pytest.mark.asyncio
async def test_final_floor_skips_over_section_overrides_547(db):
    from applire.services.cover_letter import MeasuredLetter

    ids = await _seed(db)
    cl = ids[2]
    cl.section_overrides = {"summary": "user edited this section"}
    await db.commit()

    measured_seed = MeasuredLetter(
        page_count=2, letter_pages=1, word_count=320, word_budget=300
    )
    result, condense_prompts = await _invoke_terminal_review(
        db, cl,
        draft=_letter("SEED-547-F"),
        condense_payloads=[],
        measures=[],
        measured_seed=measured_seed,
        condense_spent=True,
    )

    assert result.final_floor_fired is False
    assert condense_prompts == [], "no condense call over user section overrides"


@pytest.mark.asyncio
async def test_final_floor_never_on_identity_reentry_547(db, caplog):
    """Extends test_identity_reentry_does_not_mint_a_second_condense
    (test_letter_terminal_review.py): even when the identity re-entry's
    re-render is STILL over the page norm, ``final_floor=False`` on that
    call site means condense stays spent at 1 (the loop's own pre-verdict
    condense) — the floor never fires a third generation."""
    caplog.set_level(logging.INFO, logger="applire.services.cover_letter")
    ids = await _seed(db)

    fired = {"n": 0}

    async def mutating_update(cl, db_, pdf=None, **kwargs):
        if fired["n"] == 0:
            fired["n"] = 1
            data = dict(cl.letter_data)
            body = dict(data["body"])
            body["paragraphs"] = list(body["paragraphs"]) + ["INJECTED-POST-VERDICT-547F"]
            data["body"] = body
            cl.letter_data = data

    # seed over -> loop condense -> 1 (in-norm, no scripted regrowth) ->
    # identity re-entry's re-render is STILL over the norm (2).
    captured, calls = await _run_pipeline_547(
        db, ids,
        pages=[2, 1, 2],
        condense_payloads=[_letter("CONDENSE-1-547-G")],
        extra_patches=[patch(
            "applire.services.cover_letter._update_ats_report_letter",
            new=mutating_update,
        )],
    )

    assert calls["condense"] == 1, \
        "the per-delivery condense budget stays at 1 — the floor never " \
        "fires on the final_floor=False re-entry invocation"

    from applire.models.cover_letter import GeneratedCoverLetter
    cl = await db.get(GeneratedCoverLetter, ids[2].id)
    assert cl.status == "ready"


# === 6. the per-delivery bound is exactly 2 condenses, 0 more on re-entry =


@pytest.mark.asyncio
async def test_final_floor_condense_bound_is_two_per_delivery_547(db):
    ids = await _seed(db)

    fired = {"n": 0}

    async def mutating_update(cl, db_, pdf=None, **kwargs):
        if fired["n"] == 0:
            fired["n"] = 1
            data = dict(cl.letter_data)
            body = dict(data["body"])
            body["paragraphs"] = list(body["paragraphs"]) + ["INJECTED-POST-VERDICT-547H"]
            data["body"] = body
            cl.letter_data = data

    captured, calls = await _run_pipeline_547(
        db, ids,
        pages=[2, 2, 2, 2],  # over norm on EVERY measure
        condense_payloads=[_letter("LOOP-CONDENSE-547H"), _letter("FLOOR-CONDENSE-547H")],
        extra_patches=[patch(
            "applire.services.cover_letter._update_ats_report_letter",
            new=mutating_update,
        )],
    )

    assert calls["condense"] == 2, \
        "the loop's pre-verdict condense + the floor's one second condense " \
        "— exactly two in the initial invocation"

    from applire.models.cover_letter import GeneratedCoverLetter
    cl = await db.get(GeneratedCoverLetter, ids[2].id)
    assert cl.status == "ready", "never a delivery gate, even shipped over norm"


# === 7. pin loss during the second condense lands in floor_hits ===========


_PIN_QUOTE_547 = "Cut deployment time by 70 percent across 12 teams"


def _pin_547():
    from applire.schemas.application import FactPin

    return FactPin(
        pin_id=str(uuid.uuid4()), entry_type="work", entry_id="w1",
        quote=_PIN_QUOTE_547, targets=["letter"], stale=False,
    )


@pytest.mark.asyncio
async def test_final_floor_pin_loss_lands_in_floor_hits_547(db):
    from applire.services.cover_letter import MeasuredLetter
    from applire.services.cover_letter import _compose_letter as _real_compose_letter

    ids = await _seed(db)
    cl = ids[2]
    pin = _pin_547()

    def _compose_dropping_pin(letter_data, **kwargs):
        composed = _real_compose_letter(letter_data, **kwargs)
        text = " ".join(letter_data.get("body", {}).get("paragraphs", []))
        if _PIN_QUOTE_547 in text:
            body = dict(composed["body"])
            body["paragraphs"] = [
                p for p in body["paragraphs"] if _PIN_QUOTE_547 not in p
            ]
            composed = {**composed, "body": body}
        return composed

    measured_seed = MeasuredLetter(
        page_count=2, letter_pages=1, word_count=320, word_budget=300
    )
    measures = [
        MeasuredLetter(page_count=2, letter_pages=1, word_count=250, word_budget=300),  # loop condense: still over
        MeasuredLetter(page_count=1, letter_pages=1, word_count=140, word_budget=300),  # floor condense: in-norm
    ]
    condense_payloads = [
        _letter("LOOP-CONDENSE-547I"),
        _letter(f"FLOOR-CONDENSE-547I {_PIN_QUOTE_547}"),
    ]

    with patch("applire.services.cover_letter._compose_letter",
               side_effect=_compose_dropping_pin):
        result, _ = await _invoke_terminal_review(
            db, cl,
            draft=_letter("SEED-547-I"),
            condense_payloads=condense_payloads,
            measures=measures,
            measured_seed=measured_seed,
            pins=[pin],
        )

    assert result.final_floor_fired is True
    assert pin.pin_id in result.pin_floor_hits


# === 8. the render-measure wrapper is wired at both mounts =================


def _fake_review_probe(script: list, captured: list, corrector_prompts: dict):
    async def fake(**kwargs):
        chain = kwargs.get("chain_id")
        if chain == "cover_letter":
            corrector_prompts["drafting"] = kwargs["generator_prompt_fn"](
                kwargs["draft"], "probe feedback", kwargs["source"]
            )
            return kwargs["draft"]
        if chain != "letter_terminal_review":
            return kwargs["draft"]
        captured.append({
            "draft": kwargs["draft"],
            "prompt": kwargs["reviewer_prompt_fn"](kwargs["source"], kwargs["draft"]),
        })
        corrector_prompts.setdefault("terminal", []).append(
            kwargs["generator_prompt_fn"](kwargs["draft"], "probe feedback", kwargs["source"])
        )
        action = script.pop(0) if script else None
        return action(kwargs["draft"]) if action else kwargs["draft"]
    return fake


@pytest.mark.asyncio
async def test_corrector_prompts_carry_render_measure_547(db):
    """Pipeline wiring: the DRAFTING mount's corrector prompt carries the
    words-only block (no page render exists yet); the TERMINAL mount's
    carries the page-aware block (over norm here -> the SHORTEN ceiling).
    ``generator_prompt_fn`` is probed directly (never invoked by the
    settle-by-substitution stub elsewhere in this tree) — the same technique
    already used to probe ``reviewer_prompt_fn``."""
    from applire.prompts.cover_letter import LETTER_REQUIRED_CONTENT
    from applire.services.cover_letter import _render_cover_letter_background

    job, profile, cl = await _seed(db)
    captured: list = []
    corrector_prompts: dict = {}

    provider = AsyncMock()
    provider.aparse_json = AsyncMock(side_effect=lambda prompt, system=None, **kw: _letter("WRITER-547J"))
    extract = MagicMock(side_effect=lambda pdf: ("text", 2))  # over the 1-page DACH norm

    with (
        patch("applire.services.cover_letter.AsyncSessionLocal") as sl,
        patch("applire.services.cover_letter.get_provider", return_value=provider),
        patch("applire.services.cover_letter.review_and_refine",
              side_effect=_fake_review_probe([], captured, corrector_prompts)),
        patch("applire.services.cover_letter_pdf.render_pdf",
              AsyncMock(return_value=b"%PDF-fake")),
        patch("applire.services.ats_audit.extract_text_and_pages", new=extract),
        patch("applire.services.cover_letter._update_ats_report_letter", new=AsyncMock()),
    ):
        sl.return_value.__aenter__.return_value = db
        await _render_cover_letter_background(cl_id=cl.id, cv_id=None, job_id=job.id)

    drafting_prompt = corrector_prompts["drafting"]
    assert "RENDER MEASURE" in drafting_prompt
    assert "word budget" in drafting_prompt
    assert "rendered pages" not in drafting_prompt, \
        "no render exists yet at the drafting mount"
    for item in LETTER_REQUIRED_CONTENT:
        assert item in drafting_prompt

    assert corrector_prompts.get("terminal"), "the terminal round must be probed too"
    terminal_prompt = corrector_prompts["terminal"][0]
    assert "rendered pages" in terminal_prompt
    assert "page norm" in terminal_prompt
    assert "SHORTEN" in terminal_prompt, "page_count > letter_pages here"
    for item in LETTER_REQUIRED_CONTENT:
        assert item in terminal_prompt


def test_render_measure_block_ceiling_and_shorten_547():
    """The block's text contract (ADR-076 3-L1), independent of wiring."""
    from applire.prompts.cover_letter import LETTER_REQUIRED_CONTENT, render_measure_block

    block_none = render_measure_block(
        word_count=250, word_budget=300, page_count=None, letter_pages=1
    )
    assert "body words 250" in block_none
    assert "word budget 300" in block_none
    assert "rendered pages" not in block_none
    assert "300 words" in block_none

    block_full = render_measure_block(
        word_count=258, word_budget=300, page_count=1, letter_pages=1
    )
    assert "rendered pages 1" in block_full and "page norm 1" in block_full
    assert "do not exceed 258" in block_full
    assert "SHORTEN" not in block_full

    block_over = render_measure_block(
        word_count=293, word_budget=300, page_count=2, letter_pages=1
    )
    assert "do not exceed 293" in block_over
    assert "SHORTEN" in block_over

    for block in (block_none, block_full, block_over):
        for item in LETTER_REQUIRED_CONTENT:
            assert item in block


def test_render_measure_block_shares_required_content_with_condense_prompt_547():
    """ADR-066: one vocabulary — the REQUIRED CONTENT list is a SHARED
    module constant, not two independently-maintained copies."""
    from applire.prompts.cover_letter import (
        LETTER_REQUIRED_CONTENT, build_condense_prompt, render_measure_block,
    )

    block = render_measure_block(
        word_count=100, word_budget=300, page_count=None, letter_pages=1
    )
    condense = build_condense_prompt({"body": {"paragraphs": []}}, 100, 2, 1)
    assert len(LETTER_REQUIRED_CONTENT) >= 4
    for item in LETTER_REQUIRED_CONTENT:
        assert item in block
        assert item in condense

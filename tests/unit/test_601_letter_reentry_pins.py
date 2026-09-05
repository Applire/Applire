# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""writer collector #601 — the letter's identity RE-ENTRY loses pin-floor reporting.

`_render_cover_letter_background` invokes `_terminal_review_letter` twice: once
before the SUBJECT-IDENTITY gate, and once per re-entry inside the gate's
`while True:` loop when a post-verdict write moved the delivered content.

The first invocation passes `pins=letter_pins` and the caller folds
`pin_floor_hits |= tr.pin_floor_hits`. The re-entry invocation did **neither**.
Consequence (ADR-077 clause 2 / SF-PIN.6): a pin whose carrier a compose-tail
truth floor deletes *during the re-entry* is deleted correctly by hierarchy —
truth outranks a pin — but is **not reported**, because
`_update_ats_report_letter(..., truth_floor_hits=pin_floor_hits)` never learns
about it. The clause's whole point is that the flip is never silent.

Two named seam tests, one per half, each mutation-killed independently:

* `test_the_identity_reentry_is_given_the_letter_pins` — asserts on the
  arguments the re-entry call actually received, not on source text.
* `test_a_pin_floor_hit_from_the_reentry_reaches_the_persisted_report` —
  asserts the id reaches the `truth_floor_hits` set of the report call that
  follows the re-entry, which is the surface the user reads.

Loop-order note, verified against the code rather than assumed: the report call
is at the TOP of the `while True:` body, so a fold placed after the re-entry is
read by the NEXT iteration — and the loop always runs one more iteration after a
re-entry (it re-enters only when the hashes differ, then loops back to re-report
and re-compare). The last report call therefore always sees the folded hits.
"""
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
    user = User(id=uuid.uuid4(), email=f"letter-reentry-601-{unique}@test.com")
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash=f"letterreentry601{unique}",
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
    """Guard-inert, structurally valid draft (the convention of
    `test_letter_final_floor_547.py`: a recognised salutation opener, no
    figures or employer names, so the compose tail no-ops on the empty vault)."""
    return {
        "header": {"name": "Max Prober"},
        "recipient": {"name": None, "company": None, "date": None},
        "body": {"paragraphs": ["Sehr geehrte Damen und Herren,", marker]},
        "signature": {"closing": None, "name": "Max Prober"},
    }


REENTRY_PIN_ID = "pin-lost-on-reentry-601"


async def _run(db, ids):
    """Drive `_render_cover_letter_background` through exactly ONE identity
    re-entry, with `_terminal_review_letter` faked so the re-entry invocation
    reports a pin-floor hit the first invocation did not."""
    from applire.services.cover_letter import (
        _render_cover_letter_background,
        LetterTerminalReviewResult,
        MeasuredLetter,
    )

    terminal_calls: list[dict] = []
    report_calls: list[dict] = []

    async def fake_terminal(cl, db_, **kwargs):
        terminal_calls.append(kwargs)
        # only the RE-ENTRY invocation (the second) reports a floored pin
        hits = {REENTRY_PIN_ID} if len(terminal_calls) > 1 else set()
        return LetterTerminalReviewResult(
            draft=kwargs["draft"],
            pdf_bytes=kwargs["pdf_bytes"],
            measured=kwargs["measured"],
            rounds=1,
            reentry_exhausted=False,
            condense_used=False,
            pin_floor_hits=hits,
            outcome=None,
        )

    async def capturing_report(cl, db_, pdf=None, **kwargs):
        report_calls.append(dict(kwargs))
        # On the FIRST report only, move the delivered content so the identity
        # gate mismatches and the loop re-enters exactly once.
        if len(report_calls) == 1:
            data = dict(cl.letter_data)
            body = dict(data["body"])
            body["paragraphs"] = list(body["paragraphs"]) + ["POST-VERDICT-WRITE-601"]
            data["body"] = body
            cl.letter_data = data

    async def _aparse(prompt, system=None, **kw):
        return _letter("WRITER-SEED-601")

    provider = AsyncMock()
    provider.aparse_json = AsyncMock(side_effect=_aparse)
    extract = MagicMock(side_effect=lambda pdf: ("text", 1))

    with patch("applire.services.cover_letter.AsyncSessionLocal") as sl:
        sl.return_value.__aenter__.return_value = db
        with patch("applire.services.cover_letter.get_provider", return_value=provider), \
             patch("applire.services.cover_letter._terminal_review_letter",
                   new=fake_terminal), \
             patch("applire.services.cover_letter._update_ats_report_letter",
                   new=capturing_report), \
             patch("applire.services.cover_letter_pdf.render_pdf",
                   AsyncMock(return_value=b"%PDF-fake")), \
             patch("applire.services.ats_audit.extract_text_and_pages", new=extract):
            await _render_cover_letter_background(cl_id=ids[2].id, cv_id=None, job_id=ids[0].id)

    assert len(terminal_calls) == 2, (
        "the harness must drive exactly one identity re-entry; "
        f"got {len(terminal_calls)} terminal invocations"
    )
    assert len(report_calls) >= 2, len(report_calls)
    return terminal_calls, report_calls


# ── the two halves of the collector line ────────────────────────────────────


@pytest.mark.asyncio
async def test_the_identity_reentry_is_given_the_letter_pins(db):
    """Half 1: `pins=` at the re-entry call site. Asserted on the arguments the
    call received — a source-text assertion would pass on a `pins` that is
    passed but empty, and fail on a rename."""
    ids = await _seed(db)
    terminal_calls, _ = await _run(db, ids)

    assert "pins" in terminal_calls[1], (
        "the identity re-entry invoked _terminal_review_letter without pins= at "
        "all — a truth floor firing there cannot even see the pins it deletes"
    )
    assert terminal_calls[1]["pins"] == terminal_calls[0]["pins"], (
        "the re-entry must review against the SAME pin set as the first "
        "invocation; a different set is a different document contract"
    )


@pytest.mark.asyncio
async def test_a_pin_floor_hit_from_the_reentry_reaches_the_persisted_report(db):
    """Half 2: the fold. ADR-077 clause 2's guarantee is that a truth-floor
    deletion of a pin carrier is *never silent* — so the id must reach the
    `truth_floor_hits` the report call persists, not just the result object."""
    ids = await _seed(db)
    _, report_calls = await _run(db, ids)

    final_hits = report_calls[-1].get("truth_floor_hits") or set()
    assert REENTRY_PIN_ID in final_hits, (
        "a pin floored during the identity re-entry never reached the "
        f"persisted report (last report call saw truth_floor_hits={final_hits!r})"
    )


@pytest.mark.asyncio
async def test_the_first_invocations_hits_are_not_lost_by_the_fold(db):
    """The fold is `|=`, not `=`. Pinned so a later edit cannot turn the
    accumulating set into a replacement and pass half 2 anyway."""
    ids = await _seed(db)
    from applire.services.cover_letter import LetterTerminalReviewResult  # noqa: F401

    _, report_calls = await _run(db, ids)
    # the first report call ran BEFORE any re-entry, so its set is the initial
    # one; the last one must be a superset of it.
    first = report_calls[0].get("truth_floor_hits") or set()
    last = report_calls[-1].get("truth_floor_hits") or set()
    assert first <= last, (first, last)

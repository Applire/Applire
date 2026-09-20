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

"""Adversarial finding (Nougat UAT-fixes batch, adv pass, 2026-09-20) on the
NOTE D-1 mount landed at `9eaff795` (F-4, `services/terminal_review_outcome.py`,
`services/cover_letter.py::_terminal_review_letter`).

F-4's fix makes the `terminal-review` ADR-039 check ask "was the delivered draft
the draft the last verdict was rendered over" via `reviewed_cell["draft"]`, wired
through `_reviewer_prompt` into every `review_and_refine` call this function
makes. Measured 46/46 against the CORRECTOR's post-verdict revision (WP-D report,
`d/f4-mechanism-captured-runs.md`).

But `_terminal_review_letter` has a THIRD way to change the delivered draft after
a verdict, and it is not a `review_and_refine` round at all: the ADR-076 clause
3-L2 "final length floor" `recondensed_repair` path (`cover_letter.py:~3038-3060`,
pinned by `test_547_letter_final_floor_recondense_coverage_check.py`). When the
floor's OWN review round approves a draft that is still over the page norm and
`_limits_ok` holds on both compositions, the code fires a bare `_condense_call`
— a scoped LLM rewrite with no `reviewer_prompt_fn` and no `on_settle` — and
assigns its output straight to `current` / `cl.letter_data`. `reviewed_cell`
is never updated for it, and `outcome_cell["outcome"]` is never re-derived: it
stays exactly what the PRECEDING `review_and_refine` call's settle produced.

Consequence: if that preceding settle was `approved` (`_PASS_PATHS` grants an
unconditional `pass` regardless of `correction`/`delivered_is_reviewed` — see
`TerminalReviewOutcome.status`), the `terminal-review` check reports `pass` for
a letter whose FINAL delivered text is the output of an LLM rewrite that no
reviewer, human or model, ever saw. This is exactly the failure class F-4 was
built to catch (a control asserting "clean" over content nobody reviewed) —
just via the one post-verdict rewrite site D-1's fix did not instrument.

A second, sharper form of the same gap: `worse_of`'s F-4/D-4 "an approved verdict
over the delivered draft supersedes an earlier exhaustion" rule (added to stop a
clean re-entry round from erasing a real earlier finding, mutation-killed by
`test_an_approved_verdict_over_the_delivered_draft_supersedes_an_earlier_exhaustion`)
is *itself* invalidated by the same recondense: the supersede fires because the
floor round's `correction.delivered_is_reviewed` reads `True` (it compared
correctly against the OLD delivered draft, before the recondense) — and then the
recondense makes that fact false without any further measurement, so the
substituted `pass` outcome quietly buries the earlier round's real exhaustion.

Both tests below are deterministic (no provider call): `review_and_refine` is
faked exactly as `test_547_letter_final_floor_recondense_coverage_check.py`
fakes it, except the fake also calls `reviewer_prompt_fn` and `on_settle` the
way the real function does, so the F-4 wiring actually runs.
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

# No ledger entries and no denied concepts at all: `_limits_ok` is then
# vacuously true for every draft in this probe, so the "kept_corrector_limit_
# grounding" branch (which needs the condensed draft to carry an INVENTED
# limit) can never fire and the recondense branch is the only one reachable.
_LEDGER: list = []
_DENIED: list = []

# The sentence the mocked re-condense LLM call invents. Nothing in the fixture,
# the corrector's own output, or any reviewer verdict ever states it — it
# stands in for whatever an uninstructed rewrite could hallucinate, since the
# real `build_condense_prompt` for this call is told only the fixed
# `LETTER_REQUIRED_CONTENT` list and any pinned quotes, never the reviewer's
# verdict or the corrector's reasoning (see the file docstring above and
# `test_547_letter_final_floor_recondense_coverage_check.py`'s own finding).
_NEVER_REVIEWED_SENTENCE = (
    "Außerdem habe ich den produktiven Kubernetes-Cluster vollständig allein "
    "administriert."
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


async def _seed(db, *, unique_prefix: str):
    from applire.models.cover_letter import CoverLetterStatus, GeneratedCoverLetter
    from applire.models.job import JobAnalysis
    from applire.models.user import User

    unique = uuid.uuid4().hex[:12]
    user = User(id=uuid.uuid4(), email=f"{unique_prefix}-{unique}@test.com")
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash=f"{unique_prefix}{unique}",
        raw_text="Leiter Operations bei Vektorwerk",
        role_title="Leiter Operations",
        company_name="Vektorwerk",
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
    return cl


def _letter(*paragraphs: str) -> dict:
    return {
        "header": {"name": "Nora Vogel"},
        "recipient": {"name": None, "company": None, "date": None},
        "body": {"paragraphs": ["Sehr geehrte Damen und Herren,", *paragraphs]},
        "signature": {"closing": None, "name": "Nora Vogel"},
    }


def _measure(pages, words):
    from applire.services.cover_letter import MeasuredLetter

    return MeasuredLetter(
        page_count=pages, letter_pages=1, word_count=words, word_budget=300
    )


async def _invoke(
    db, cl, *, draft, review_script, measures, condense_payloads, condense_spent
):
    """`review_and_refine` fake that, unlike the existing harnesses in
    `test_547_letter_final_floor_recondense_coverage_check.py` /
    `test_664_letter_limit_catch.py`, ALSO calls `reviewer_prompt_fn` and
    `on_settle` — the two hooks F-4's wiring depends on — so this probe
    actually exercises `reviewed_cell` / `settle_to_outcome`, not a harness
    that leaves them untouched.

    `review_script` is a list of ``(settled_draft, path)`` pairs, one per
    `review_and_refine` invocation, consumed in order.
    """
    from applire.norms import REGION_NORMS
    from applire.services.cover_letter import _terminal_review_letter
    from applire.services.review_issues import ReviewSettle

    script = list(review_script)
    payloads = list(condense_payloads)
    queue = list(measures[1:])

    async def _aparse(prompt, system=None, **kw):
        return payloads.pop(0)

    async def _fake_review(**kwargs):
        settled_draft, path = script.pop(0)
        # Mirrors the real `review_and_refine`: it calls the reviewer prompt
        # function with the draft the verdict was actually rendered over
        # (here: the settled draft itself — an "approved as-is" verdict),
        # THEN reports the settle via `on_settle`.
        kwargs["reviewer_prompt_fn"]("SOURCE", settled_draft)
        on_settle = kwargs.get("on_settle")
        if on_settle is not None:
            approved = path in ("approved", "minor_only")
            on_settle(
                ReviewSettle(
                    path=path,
                    approved=approved,
                    blocking_issues=() if approved else ("a real finding",),
                    minor_issues=(),
                    rounds=1,
                    settled=settled_draft,
                )
            )
        return settled_draft

    async def _persist(cl_, db_, composed, norm_):
        cl_.letter_data = composed
        await db_.commit()
        return b"%PDF-fake", queue.pop(0)

    provider = AsyncMock()
    provider.aparse_json = AsyncMock(side_effect=_aparse)

    with (
        patch(
            "applire.services.cover_letter.review_and_refine", side_effect=_fake_review
        ),
        patch("applire.services.cover_letter._persist_and_measure", new=_persist),
    ):
        return await _terminal_review_letter(
            cl, db,
            draft=draft,
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
            condense_spent=condense_spent,
            pins=[],
            final_floor=True,
            keyword_ledger=_LEDGER,
            denied_concepts=_DENIED,
        )


async def _delivered_body(db, cl):
    from applire.models.cover_letter import GeneratedCoverLetter

    row = await db.get(GeneratedCoverLetter, cl.id)
    return " ".join(row.letter_data["body"]["paragraphs"])


@pytest.mark.asyncio
async def test_a_never_reviewed_recondense_still_reports_pass(db):
    """The plain case: round 1 approves the seed unchanged (over-norm, so the
    floor fires); the floor's own condense+review round approves a regrown,
    still-over-norm draft (also reviewed, also approved); `_limits_ok` holds on
    every composition in play, so the floor falls into the RECONDENSE branch
    rather than `kept_corrector_limit_grounding`. The recondense is a bare
    `_condense_call` with no reviewer attached, and it ships a sentence no
    round ever reviewed.

    Expected if F-4 covered every post-verdict rewrite site: the check should
    read `not_applicable` ("unverified") for a document whose final text was
    never reviewed, or `fail`/some honest signal — never a clean `pass`.

    Observed: `pass`, because `outcome_cell["outcome"]` is frozen at the
    floor round's own settle (approved, and at THAT MOMENT delivered_is_
    reviewed was true) and nothing re-derives it after the recondense
    reassigns `current`.
    """
    cl = await _seed(db, unique_prefix="recond-pass")
    seed = _letter("Ich verantworte die Fertigungssteuerung bei Vektorwerk.")
    regrown = _letter(
        "Ich verantworte die Fertigungssteuerung bei Vektorwerk.",
        "Zusätzlich habe ich das ERP-Rollout-Programm geleitet.",
    )
    recondensed = _letter(_NEVER_REVIEWED_SENTENCE)

    result = await _invoke(
        db, cl,
        draft=seed,
        review_script=[
            (seed, "approved"),      # terminal loop's own round: approved as-is
            (regrown, "approved"),   # floor's own round: approved, but re-grown
        ],
        measures=[
            _measure(2, 320),   # seed: over norm -> floor triggers
            _measure(1, 240),   # floor's pre-review condense: in norm
            _measure(2, 285),   # the approved-but-regrown draft: over norm again
            _measure(1, 230),   # the bare recondense: in norm
        ],
        condense_payloads=[_letter("FLOOR-CONDENSED placeholder"), recondensed],
        condense_spent=True,
    )

    # The mechanism fired as designed: this is the recondense-ships path.
    assert result.final_floor_selection == "recondensed_repair"

    delivered = await _delivered_body(db, cl)
    assert _NEVER_REVIEWED_SENTENCE in delivered, (
        "the probe's own premise failed: the never-reviewed recondense text "
        "was not what shipped, so this run does not exercise the gap"
    )
    # And what got reviewed is NOT what shipped.
    assert "ERP-Rollout-Programm" not in delivered

    # The bug: the check reports a clean pass over content nobody reviewed.
    assert result.outcome is not None
    assert result.outcome.status == "pass", (
        "expected the `terminal-review` check to report something other than "
        "a clean pass for a delivered letter whose final text (the bare "
        "final-length-floor recondense) was never handed to any reviewer — "
        "got 'pass', reproducing the adversarial finding"
    )


@pytest.mark.asyncio
async def test_the_supersede_rule_plus_a_recondense_buries_a_real_exhaustion(db):
    """The sharper form: round 1 genuinely EXHAUSTS with a real blocking
    finding (`generator_call_failed`-shaped: `approved=False`). Per the F-4/
    D-4 `worse_of` rule, a later APPROVED round whose `correction.delivered_
    is_reviewed` reads True at settle time supersedes that exhaustion — by
    design, so a legitimate re-review is not held hostage by a stale finding.
    But here the floor's later round is immediately followed by the same
    unreviewed recondense as the first test, so the "supersede" fact
    (delivered_is_reviewed=True) is falsified by code that runs AFTER the
    measurement it was based on, and the report ends up `pass` with the
    original round's real finding gone from `details` entirely.
    """
    cl = await _seed(db, unique_prefix="recond-mask")
    seed = _letter("Ich habe die Umstellung auf SAP S/4HANA verantwortet.")
    # Round 1's own settle: the loop treats this as unchanged content
    # (approved=False path still returns the SAME draft here — a corrector
    # that could not run, `generator_call_failed`-shaped) so the outer loop's
    # `_canon(settled) == _canon(current)` still breaks the while-loop exactly
    # as the real function does on that path (it does not recompose).
    regrown = _letter(
        "Ich habe die Umstellung auf SAP S/4HANA verantwortet.",
        "Zusätzlich habe ich das Testmanagement aufgebaut.",
    )
    recondensed = _letter(_NEVER_REVIEWED_SENTENCE)

    result = await _invoke(
        db, cl,
        draft=seed,
        review_script=[
            (seed, "generator_call_failed"),  # round 1: a REAL exhaustion
            (regrown, "approved"),            # floor round: approved, regrown
        ],
        measures=[
            _measure(2, 320),
            _measure(1, 240),
            _measure(2, 285),
            _measure(1, 230),
        ],
        condense_payloads=[_letter("FLOOR-CONDENSED placeholder"), recondensed],
        condense_spent=True,
    )

    assert result.final_floor_selection == "recondensed_repair"
    delivered = await _delivered_body(db, cl)
    assert _NEVER_REVIEWED_SENTENCE in delivered

    from applire.services.terminal_review_outcome import build_terminal_review_check

    assert result.outcome is not None
    assert result.outcome.status == "pass", (
        "the round-1 exhaustion (`generator_call_failed`, a real blocking "
        "finding) is being reported as a clean pass"
    )
    check = build_terminal_review_check(result.outcome, document="letter")
    assert "a real finding" not in (check.details or ""), (
        "the earlier round's real finding text has been folded away by the "
        "approved-supersedes rule, and the round that superseded it is not "
        "itself the round whose output shipped (the recondense ran after it)"
    )

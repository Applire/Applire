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

"""M5.4.2 (3) — the letter's per-round coverage demand cap is a BOUND.

`prompts/review_cover_letter.py` check 5 asked the reviewer for *"DEMAND AT
MOST TWO terms per round"* while `keyword_ledger.render_verified_coverage_block`
handed the same call the FULL list of absent claimable terms and closed with
*"name the terms in your issues"* — two halves of one prompt stating opposite
instructions. Captured evidence (`Runs/Nougat/build-1/p/prompts/
review_cover_letter.md` §7, the 2026-09-05 `cv_stefan_brandt` run): the
drafting door's round-1 verdict raised **6 coverage demands** against the
stated cap of 2, and the terminal door's first round raised 10 issues.

The cap now bounds the LIST (`rank_coverage_demand`) and the block says the
list IS the demand set. The three CV call sites pass nothing and see a
byte-identical block (Nougat build-3 contract 3).
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from applire.services.keyword_ledger import (
    coverage_reviewer_prompt_fn,
    rank_coverage_demand,
    render_verified_coverage_block,
    verified_missing_claimable,
)

# Four absent claimable terms, ranked by the ledger's own fit_weight.
_LEDGER_FOUR = [
    {"concept": "Arbeitsvorbereitung", "surface_forms": ["Arbeitsvorbereitung"],
     "claimable": True, "status": "direct", "sources": ["keyword"],
     "fit_weight": 0.25, "evidence": "Fertigungssteuerung bei Weberit"},
    {"concept": "Instandhaltung", "surface_forms": ["Instandhaltung"],
     "claimable": True, "status": "direct", "sources": ["required"],
     "fit_weight": 1.0, "evidence": "Instandhaltungsbudget verantwortet"},
    {"concept": "Supply Chain", "surface_forms": ["Supply Chain"],
     "claimable": True, "status": "partial", "sources": ["nice_to_have"],
     "fit_weight": 0.5, "evidence": "Materialdisposition gesteuert"},
    {"concept": "5S", "surface_forms": ["5S"],
     "claimable": True, "status": "direct", "sources": ["keyword"],
     "fit_weight": 0.0, "evidence": "5S-Workshops moderiert"},
]

_DRAFT_MISSING_ALL = {
    "body": {"paragraphs": [
        "Sehr geehrte Damen und Herren,",
        "Als Produktionsleiter fuehre ich 38 Mitarbeitende im Dreischichtbetrieb.",
    ]},
}


def _absent(ledger=None):
    return verified_missing_claimable(_DRAFT_MISSING_ALL, ledger or _LEDGER_FOUR)


# ── 1. the ranked split itself ──────────────────────────────────────────────


class TestRankCoverageDemand:
    def test_no_cap_returns_every_entry_and_defers_nothing(self):
        """The CV call sites' contract: ``None`` is today's behaviour."""
        blocking = _absent()
        assert len(blocking) == 4
        demanded, deferred = rank_coverage_demand(blocking, None)
        assert demanded == blocking
        assert deferred == []

    def test_a_cap_wider_than_the_list_changes_nothing(self):
        blocking = _absent()
        demanded, deferred = rank_coverage_demand(blocking, 9)
        assert demanded == blocking
        assert deferred == []

    def test_the_cap_keeps_the_highest_fit_weight_terms(self):
        """Rank is the ledger's own ``fit_weight`` — the same key
        ``rank_gate_missing_claimable`` uses, so "central to this role" has ONE
        definition (ADR-066). The reviewer is no longer asked to rank."""
        demanded, deferred = rank_coverage_demand(_absent(), 2)
        assert [e["concept"] for e in demanded] == ["Instandhaltung", "Supply Chain"]
        assert {e["concept"] for e in deferred} == {"Arbeitsvorbereitung", "5S"}

    def test_nothing_is_dropped_only_deferred(self):
        demanded, deferred = rank_coverage_demand(_absent(), 2)
        assert len(demanded) + len(deferred) == 4

    def test_a_zero_cap_defers_everything_rather_than_silently_passing_all(self):
        demanded, deferred = rank_coverage_demand(_absent(), 0)
        assert demanded == []
        assert len(deferred) == 4


# ── 2. the rendered block ───────────────────────────────────────────────────


class TestBoundedCoverageBlock:
    def test_the_unbounded_block_is_byte_identical_to_today(self):
        """The CV side's guarantee: no ``bounded`` flag, no text change."""
        entries = _absent()
        assert render_verified_coverage_block(entries) == (
            render_verified_coverage_block(entries, bounded=False, deferred_count=0)
        )
        assert "THIS ROUND'S COMPLETE DEMAND SET" not in (
            render_verified_coverage_block(entries)
        )

    def test_the_bounded_block_states_the_list_is_the_demand_set(self):
        demanded, deferred = rank_coverage_demand(_absent(), 2)
        block = render_verified_coverage_block(
            demanded, bounded=True, deferred_count=len(deferred)
        )
        assert "THIS ROUND'S COMPLETE DEMAND SET" in block
        assert "do not add a coverage demand for a term the list does not carry" in block

    def test_the_bounded_block_names_the_held_back_terms_as_held_back_not_waived(self):
        """A deferred term must not read as waived: a waiver is a grounding
        judgement the reviewer owns (ADR-048 §8) and stops the term blocking
        for good, while a held-back term returns next round."""
        demanded, deferred = rank_coverage_demand(_absent(), 2)
        block = render_verified_coverage_block(
            demanded, bounded=True, deferred_count=len(deferred)
        )
        assert "2 further claimable term(s)" in block
        assert "neither demanded nor waived" in block

    def test_the_bounded_block_never_leaks_a_held_back_term_name(self):
        demanded, deferred = rank_coverage_demand(_absent(), 2)
        block = render_verified_coverage_block(
            demanded, bounded=True, deferred_count=len(deferred)
        )
        for entry in deferred:
            assert entry["concept"] not in block

    def test_no_deferred_terms_means_no_held_back_sentence(self):
        block = render_verified_coverage_block(
            _absent(), bounded=True, deferred_count=0
        )
        assert "HELD BACK" not in block


# ── 3. the wrapper ──────────────────────────────────────────────────────────


class TestCoverageReviewerPromptFnBound:
    def test_the_default_wrapper_renders_every_absent_term(self):
        fn = coverage_reviewer_prompt_fn(lambda s, d: "BASE", _LEDGER_FOUR)
        prompt = fn("src", _DRAFT_MISSING_ALL)
        for entry in _LEDGER_FOUR:
            assert entry["concept"] in prompt

    def test_the_bounded_wrapper_renders_exactly_the_cap(self):
        fn = coverage_reviewer_prompt_fn(
            lambda s, d: "BASE", _LEDGER_FOUR, max_terms_per_round=2
        )
        prompt = fn("src", _DRAFT_MISSING_ALL)
        assert "Instandhaltung" in prompt and "Supply Chain" in prompt
        assert "Arbeitsvorbereitung" not in prompt and "5S" not in prompt
        assert "THIS ROUND'S COMPLETE DEMAND SET" in prompt

    def test_the_bound_is_recomputed_per_round_so_a_deferred_term_returns(self):
        """The whole point of a bound over a prose cap: it rides the existing
        loop. Once the draft carries the two demanded terms, the same wrapper
        puts the next two in front of the reviewer — no verdict memory
        (ADR-021 clause 6)."""
        fn = coverage_reviewer_prompt_fn(
            lambda s, d: "BASE", _LEDGER_FOUR, max_terms_per_round=2
        )
        round_1 = fn("src", _DRAFT_MISSING_ALL)
        assert "Arbeitsvorbereitung" not in round_1

        round_2_draft = {"body": {"paragraphs": [
            "Sehr geehrte Damen und Herren,",
            "Ich verantworte die Instandhaltung und die Supply Chain der Werke.",
        ]}}
        round_2 = fn("src", round_2_draft)
        assert "Arbeitsvorbereitung" in round_2 and "5S" in round_2
        assert "Instandhaltung [forms" not in round_2

    def test_an_empty_absent_list_appends_nothing_under_the_bound(self):
        fn = coverage_reviewer_prompt_fn(
            lambda s, d: "BASE", _LEDGER_FOUR, max_terms_per_round=2
        )
        covered = {"body": {"paragraphs": [
            "Instandhaltung, Supply Chain, Arbeitsvorbereitung und 5S.",
        ]}}
        assert fn("src", covered) == "BASE"


# ── 4. the prompt half — the contradiction is gone ──────────────────────────


class TestReviewerPromptNoLongerAsksForTheCap:
    def test_check_5_no_longer_states_a_number_the_block_contradicts(self):
        from applire.prompts import review_cover_letter as rcl

        for text in (rcl.REVIEW_SYSTEM_PROMPT, rcl.TERMINAL_REVIEW_SYSTEM_PROMPT):
            flat = " ".join(text.split())
            assert "DEMAND AT MOST TWO" not in flat
            assert "AT MOST TWO terms per round" not in flat
            assert "already ranked and already capped for this round" in flat
            assert "demand the terms it lists and no others" in flat

    def test_the_flat_enumeration_rule_survives_the_edit(self):
        """The cap moved; the shape rule it sat next to did not — three or more
        claimable terms strung together is still a failure of check 5."""
        from applire.prompts import review_cover_letter as rcl

        flat = " ".join(rcl.REVIEW_SYSTEM_PROMPT.split())
        assert "flat enumeration is itself a failure of this check" in flat


# ── 5. the seam: the letter's ONE wiring point passes the bound ─────────────


_MINIMAL_PROFILE_JSON = {"work_experience": [], "skills": []}


@pytest.mark.asyncio
async def test_the_letter_wiring_point_passes_the_bound_and_the_cv_sites_do_not(db_m542):
    """Seam test for the letter's single call site
    (``cover_letter.py::_wrap_reviewer``) — the closure both letter loops
    share, so one site covers both doors. The CV's three call sites are
    covered by ``test_cv_generation_budget_wiring.py`` and by the
    byte-identical-block assertion above."""
    from applire.services.cover_letter import (
        LETTER_COVERAGE_TERMS_PER_ROUND,
        _render_cover_letter_background,
    )
    import applire.services.keyword_ledger as kl

    seen: list[dict] = []
    real = kl.coverage_reviewer_prompt_fn

    def spy(base_fn, keyword_ledger, budget=None, max_terms_per_round=None):
        seen.append({"budget": budget, "max_terms_per_round": max_terms_per_round})
        return real(base_fn, keyword_ledger, budget=budget,
                    max_terms_per_round=max_terms_per_round)

    job, cl = db_m542

    async def _aparse(prompt, system=None, **kw):
        return {
            "header": {"name": "Max Prober"},
            "recipient": {"name": None, "company": None, "date": None},
            "body": {"paragraphs": ["Sehr geehrte Damen und Herren,", "Ein Absatz."]},
            "signature": {"closing": None, "name": "Max Prober"},
        }

    provider = AsyncMock()
    provider.aparse_json = AsyncMock(side_effect=_aparse)

    with (
        patch("applire.services.cover_letter.get_provider", return_value=provider),
        patch("applire.services.keyword_ledger.coverage_reviewer_prompt_fn",
              side_effect=spy),
        patch("applire.services.cover_letter.review_and_refine",
              new=AsyncMock(side_effect=lambda **kw: kw["draft"])),
        patch("applire.services.cover_letter_pdf.render_pdf",
              AsyncMock(return_value=b"%PDF-fake")),
        patch("applire.services.ats_audit.extract_text_and_pages",
              new=MagicMock(return_value=("text", 1))),
        patch("applire.services.cover_letter._update_ats_report_letter",
              new=AsyncMock()),
        patch("applire.services.cover_letter.AsyncSessionLocal") as sl,
    ):
        sl.return_value.__aenter__.return_value = _M542_DB["session"]
        await _render_cover_letter_background(cl_id=cl.id, cv_id=None, job_id=job.id)

    assert seen, "coverage_reviewer_prompt_fn was never called on the letter path"
    assert all(c["max_terms_per_round"] == LETTER_COVERAGE_TERMS_PER_ROUND
               for c in seen), seen
    assert LETTER_COVERAGE_TERMS_PER_ROUND == 2


_M542_DB: dict = {}


@pytest_asyncio.fixture
async def db_m542():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
    from applire.models.cover_letter import CoverLetterStatus, GeneratedCoverLetter
    from applire.models.job import JobAnalysis
    from applire.models.user import User

    from tests.support.profile_factory import make_master_profile

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        unique = uuid.uuid4().hex[:12]
        user = User(id=uuid.uuid4(), email=f"m542-{unique}@test.com")
        job = JobAnalysis(
            id=uuid.uuid4(),
            raw_text_hash=f"m542{unique}",
            raw_text="Leiter Operations bei Rheinwerk",
            role_title="Leiter Operations",
            company_name="Rheinwerk",
            required_skills=["Instandhaltung"],
            nice_to_have_skills=["Supply Chain"],
            keywords=["Arbeitsvorbereitung", "5S"],
            seniority_level="senior",
            company_culture_signals=[],
            language_requirement="de",
            jd_language="de",
        )
        profile = make_master_profile(profile_json=_MINIMAL_PROFILE_JSON)
        session.add_all([user, job, profile])
        await session.flush()
        cl = GeneratedCoverLetter(
            job_analysis_id=job.id,
            profile_id=profile.id,
            template="classic_german",
            letter_data={},
            pre_gen_inputs={},
            status=CoverLetterStatus.pending.value,
        )
        session.add(cl)
        await session.commit()
        await session.refresh(cl)
        _M542_DB["session"] = session
        yield job, cl
    await engine.dispose()

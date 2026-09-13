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

"""M5.4.2 (2) — the letter corrector runs on the WRITER's token budget.

`reviewer.review_and_refine`'s ``generator_max_tokens`` defaults to 4,096. The
letter WRITER is called with ``CV_GENERATION_MAX_TOKENS`` (16,384). Until this
change none of the letter's three ``review_and_refine`` call sites passed the
kwarg, so every corrector round was asked to re-emit a whole letter — including
every paragraph it must carry forward unchanged — in a quarter of the room the
first draft had. A ``finish=length`` there is not a shorter letter: it is a
truncated JSON object and a lost round.

Founder ruling M5.4.2 (2), 2026-09-11 ("the fuller option"). One seam test per
call site, per the verification hierarchy: reverting any single site turns
exactly one of these red.
"""

import logging
import sys
import uuid
from contextlib import ExitStack
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
    user = User(id=uuid.uuid4(), email=f"m542-budget-{unique}@test.com")
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash=f"m542budget{unique}",
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
    return {
        "header": {"name": "Max Prober"},
        "recipient": {"name": None, "company": None, "date": None},
        "body": {"paragraphs": ["Sehr geehrte Damen und Herren,", marker]},
        "signature": {"closing": None, "name": "Max Prober"},
    }


def _regrow(draft: dict) -> dict:
    body = dict(draft["body"])
    body["paragraphs"] = list(body["paragraphs"]) + ["REGROWN past the page norm."]
    return {**draft, "body": body, "recipient": {**draft["recipient"], "date": None}}


# ── the drafting mount (call site 1) ────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_drafting_corrector_runs_on_the_writer_budget(db):
    """Seam 1 — ``chain_id="cover_letter"``."""
    from applire.constants import CV_GENERATION_MAX_TOKENS
    from applire.services.cover_letter import _render_cover_letter_background

    job, _profile, cl = await _seed(db)
    seen: list[dict] = []

    async def _fake_review(**kwargs):
        seen.append({"chain_id": kwargs.get("chain_id"),
                     "budget": kwargs.get("generator_max_tokens")})
        return kwargs["draft"]

    provider = AsyncMock()
    provider.aparse_json = AsyncMock(return_value=_letter("WRITER-SEED"))

    with patch("applire.services.cover_letter.AsyncSessionLocal") as sl:
        sl.return_value.__aenter__.return_value = db
        with ExitStack() as stack:
            stack.enter_context(patch(
                "applire.services.cover_letter.get_provider", return_value=provider))
            stack.enter_context(patch(
                "applire.services.cover_letter.review_and_refine",
                side_effect=_fake_review))
            stack.enter_context(patch(
                "applire.services.cover_letter_pdf.render_pdf",
                AsyncMock(return_value=b"%PDF-fake")))
            stack.enter_context(patch(
                "applire.services.ats_audit.extract_text_and_pages",
                new=MagicMock(return_value=("text", 1))))
            stack.enter_context(patch(
                "applire.services.cover_letter._update_ats_report_letter",
                new=AsyncMock()))
            await _render_cover_letter_background(
                cl_id=cl.id, cv_id=None, job_id=job.id
            )

    drafting = [c for c in seen if c["chain_id"] == "cover_letter"]
    assert drafting, seen
    assert all(c["budget"] == CV_GENERATION_MAX_TOKENS for c in drafting), drafting


# ── the terminal mount (call sites 2 and 3) ─────────────────────────────────


async def _invoke_terminal(db, cl, *, measures, condense_payloads, script, seen):
    from applire.norms import REGION_NORMS
    from applire.services.cover_letter import _terminal_review_letter

    payloads = list(condense_payloads)
    queue = list(measures)

    async def _aparse(prompt, system=None, **kw):
        return payloads.pop(0)

    async def _fake_review(**kwargs):
        seen.append({"budget": kwargs.get("generator_max_tokens")})
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
        return await _terminal_review_letter(
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


def _measure(pages: int, words: int = 200):
    from applire.services.cover_letter import MeasuredLetter

    return MeasuredLetter(
        page_count=pages, letter_pages=1, word_count=words, word_budget=300
    )


@pytest.mark.asyncio
async def test_the_terminal_corrector_runs_on_the_writer_budget(db):
    """Seam 2 — the terminal loop's own round, the in-norm path (no floor)."""
    from applire.constants import CV_GENERATION_MAX_TOKENS

    _job, _profile, cl = await _seed(db)
    seen: list[dict] = []
    await _invoke_terminal(
        db, cl,
        measures=[_measure(1), _measure(1)],
        condense_payloads=[],
        script=[lambda d: d],
        seen=seen,
    )
    assert len(seen) == 1, seen
    assert seen[0]["budget"] == CV_GENERATION_MAX_TOKENS


@pytest.mark.asyncio
async def test_the_final_floor_round_runs_on_the_writer_budget(db, caplog):
    """Seam 3 — the ``LETTER_FINAL_FLOOR``'s own single review round. It is the
    call site with the most to carry (a condensed composition being repaired)
    and was the last one still on 4,096."""
    from applire.constants import CV_GENERATION_MAX_TOKENS

    caplog.set_level(logging.INFO, logger="applire.services.cover_letter")
    _job, _profile, cl = await _seed(db)
    seen: list[dict] = []
    await _invoke_terminal(
        db, cl,
        # seed 2 pages -> terminal round leaves it at 2 -> floor condenses -> 1
        measures=[_measure(2), _measure(2), _measure(1), _measure(1)],
        condense_payloads=[_letter("FLOOR-CONDENSED")],
        script=[lambda d: d, lambda d: d],
        seen=seen,
    )
    assert len(seen) == 2, seen  # terminal round + the floor's round
    assert all(c["budget"] == CV_GENERATION_MAX_TOKENS for c in seen), seen
    assert any("LETTER_FINAL_FLOOR" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_every_letter_review_and_refine_call_site_passes_the_budget(db):
    """The enumeration behind the three seam tests: no letter call site may be
    left on ``review_and_refine``'s 4,096 default. A fourth call site added to
    this module without the kwarg turns this red."""
    import ast
    from pathlib import Path

    src = Path(__file__).parent.parent.parent / "backend/applire/services/cover_letter.py"
    tree = ast.parse(src.read_text())
    sites = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "review_and_refine"
    ]
    assert len(sites) == 3, f"expected 3 letter review_and_refine call sites, got {len(sites)}"
    for site in sites:
        kwargs = {kw.arg for kw in site.keywords}
        assert "generator_max_tokens" in kwargs, (
            f"review_and_refine at cover_letter.py:{site.lineno} still runs the "
            "corrector on the 4,096 default (M5.4.2 (2))"
        )

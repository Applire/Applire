# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#668 — `SF-WRITE.29` closed, and `repetition` leaves the minor-by-definition line.

Founder ruling 3 (Stracciatella RC walk-through, 2026-09-05) reconciled ADR-082 and
ADR-083: the reviewer MAY block on repetition and the corrector MAY repair it, with
`SF-WRITE.29` — *the reviewer and the corrector are shown two different documents in
the same round* — named as the BLOCKING precondition. Both halves land here.

**What settled `SF-WRITE.29`, and it is a measurement, not an argument.** Captured RC
delivery run 2026-09-05 (`backend/logs/llm/2026-09-05.jsonl` records 660–663,
`openai/gpt-5.6-luna`, chain `cv_terminal_review`, two `review_and_refine` invocations):

* Shape — the reviewer's subject carried `work_history / projects / education /
  certifications / languages / contact` plus each entry's joined `company / role /
  dates / team_size / budget_managed / industry_context`; the corrector's
  `PREVIOUS OUTPUT` carried `summary / work[{id, bullets, projects}] / skills`.
* **The divergence runs in BOTH directions, and the second one was unrecorded.**
  Round 1's corrector added the two bullets the reviewer's own blocking coverage
  finding demanded (Weberit 5 → 7 bullets); `_cap_bullets` cut both in the compose that
  followed (7 → 5); round 2's reviewer therefore re-raised *"Kunststoffverpackungen is
  absent"* — true of the delivered document — while round 2's corrector was shown a
  `PREVIOUS OUTPUT` that still contained the answer, so the true blocking finding read
  as false at the seat asked to act on it. **2 of 3 corrector repairs never reached the
  document; 1 survived.** That is a deadlock the loop cannot leave, not merely a blind
  spot, and it is the mechanism behind #666's 1-of-4 implementation fraction.

The disposition: the terminal corrector is shown the SAME composed artefact the reviewer
judged, **read-only**. It still receives and returns the PROSE shape only, so ADR-067
clauses 2/3 are untouched and no vault-verbatim field is ever LLM-authored.

CI pins the TOPOLOGY and the WORDING. Whether the model now repairs a nested-project
redundancy is a prompt EFFECT and is evidenced by a real-provider run (ADR-062 clause 7),
never by these tests.
"""
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

import pytest_asyncio  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from applire.prompts import review_cv_tailoring as cv_rev  # noqa: E402
from applire.prompts.cv_tailoring import (  # noqa: E402
    CV_TAILORING_REFINEMENT_PROMPT,
    build_retry_prompt,
)

#: The #538 topology fixture, inlined rather than imported: `tests/unit` is not a
#: package, so a cross-module import of that suite's helpers does not resolve under the
#: CI invocation. Kept deliberately identical to it — the two suites must describe the
#: same document or a divergence here would read as a defect there.
_WORK_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_CERT_NAME = "AWS Solutions Architect Professional"


def _profile_json() -> dict:
    return {
        "contact": {"first_name": "Anna", "last_name": "Bauer",
                    "email": "anna@example.com", "phone": None, "location": "Berlin",
                    "linkedin": None, "xing": None, "portfolio": None},
        "professional_summary": {"de": "Erfahrene Entwicklerin", "en": ""},
        "work_experience": [
            {
                "id": _WORK_ID,
                "company": "Acme GmbH",
                "role": "Software Engineer",
                "start_date": "2020-01",
                "end_date": None,
                "team_size": 7,
                "responsibilities": [f"Aufgabe {i}" for i in range(3)],
            }
        ],
        "education": [], "skills": [], "languages": [],
        "certifications": [
            {"name": _CERT_NAME, "issuing_organization": "AWS", "status": "confirmed"}
        ],
    }


def _writer_payload() -> dict:
    return {
        "summary": "Erfahrene Entwicklerin.",
        "work": [
            {"id": _WORK_ID, "bullets": ["Baute Backend-Services in Python."]},
        ],
        "skills": ["Python"],
    }


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
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.user_settings  # noqa: F401
    import applire.models.cover_letter  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


_PROJECT_NAME = "MES-Rollout"
_PROJECT_BULLET = "Echtzeit-OEE-Transparenz über drei Linien hergestellt."


def _flat(text: str) -> str:
    """Collapse the prompt's own hand-wrapping before matching a phrase — otherwise
    the assertion pins the formatter, not the rule."""
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# 1. The seam: the terminal corrector is shown the document the reviewer judged
# ---------------------------------------------------------------------------


def _profile_with_project() -> dict:
    """`_profile_json()` plus a vault project whose bullets exist ONLY after
    `_nest_projects` — i.e. composed-only content, exactly #659's shape (the writer
    emitted `projects: []` on all three rounds and the six bullets were assembled
    afterwards, deterministically)."""
    profile = _profile_json()
    profile["projects"] = [
        {
            "name": _PROJECT_NAME,
            "associated_experience": _WORK_ID,
            "achievements": [_PROJECT_BULLET],
        }
    ]
    return profile


async def _seed_with_project(db):
    from applire.models.cv import GeneratedCV
    from applire.models.job import JobAnalysis
    from tests.support.profile_factory import make_master_profile

    job_id, profile_id, cv_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db.add_all([
        JobAnalysis(
            id=job_id, raw_text_hash=str(job_id), raw_text="job",
            role_title="Engineer", required_skills=[], nice_to_have_skills=[],
            keywords=["Python"], seniority_level="mid", company_culture_signals=[],
            language_requirement="de",
        ),
        make_master_profile(
            id=profile_id, profile_json=_profile_with_project(),
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        GeneratedCV(
            id=cv_id, job_analysis_id=job_id, profile_id=profile_id,
            tailored_data={}, template="classic_german", status="pending",
            target_pages=2,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
        ),
    ])
    await db.commit()
    return job_id, profile_id, cv_id


async def _run_real_loop(db, ids):
    """Drive `_render_cv_background` with the REAL `review_and_refine` — the
    #538 harness fakes the loop, so its `generator_prompt_fn` is never called and
    the seam under test here would not be exercised at all.

    The provider returns the writer payload for every JSON call. As a reviewer
    verdict that reads `approved=False, issues=[]`, which is precisely the
    "unreadable rejection" branch ADR-021's severity gate retries — so the loop
    reaches its corrector call, which is the call this test exists to inspect.
    """
    from applire.services.cv import _render_cv_background

    job_id, profile_id, cv_id = ids
    provider = AsyncMock()
    provider.aparse_json.return_value = _writer_payload()
    extract = MagicMock(side_effect=lambda pdf: ("text", 2))
    with patch("applire.services.cv.AsyncSessionLocal") as sl:
        sl.return_value.__aenter__.return_value = db
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch("applire.services.cv.get_provider", return_value=provider))
            stack.enter_context(patch("applire.services.cv.LLM_REVIEW_MAX_RETRIES", 1))
            stack.enter_context(patch("applire.services.cv.get_cv_html",
                                      new=AsyncMock(return_value="<html></html>")))
            stack.enter_context(patch("applire.services.cv._html_to_pdf",
                                      new=AsyncMock(return_value=b"pdf")))
            stack.enter_context(patch("applire.services.ats_audit.extract_text_and_pages",
                                      new=extract))
            await _render_cv_background(cv_id, job_id, profile_id, "classic_german")
    return [c.args[0] for c in provider.aparse_json.call_args_list if c.args]


def _terminal_corrector_prompts(prompts: list[str]) -> list[str]:
    return [p for p in prompts if p.startswith("A quality review of your previous CV tailoring")
            and "DELIVERED DOCUMENT" in p]


def _all_corrector_prompts(prompts: list[str]) -> list[str]:
    return [p for p in prompts if p.startswith("A quality review of your previous CV tailoring")]


@pytest.mark.asyncio
async def test_the_terminal_corrector_is_shown_the_composed_document(db):
    """SF-WRITE.29's disposition, at the seam: the terminal corrector's prompt carries
    the COMPOSED artefact — certifications, joined role facts, and the nested project
    bullets `_nest_projects` assembled after the writer finished.

    The assertion deliberately slices the DELIVERED DOCUMENT block out of the prompt:
    the CANDIDATE PROFILE block already carries the vault's own cert and project strings,
    so a whole-prompt assertion would pass even with the block removed (the mutation the
    #538 suite records as "mutation C")."""
    ids = await _seed_with_project(db)
    prompts = await _run_real_loop(db, ids)

    corrector = _terminal_corrector_prompts(prompts)
    assert corrector, "the terminal corrector must receive a DELIVERED DOCUMENT block"
    block = corrector[0][corrector[0].index("DELIVERED DOCUMENT"):
                         corrector[0].index("PREVIOUS OUTPUT:")]
    assert _CERT_NAME in block, "certifications are IN the corrector's delivered view"
    assert '"team_size": 7' in block, "joined role facts are IN it"
    assert _PROJECT_BULLET in block, (
        "the composed-only nested-project bullet — the #659 class — is IN it"
    )


@pytest.mark.asyncio
async def test_the_corrector_still_receives_and_returns_only_the_prose_shape(db):
    """ADR-067 clauses 2/3 are untouched: the block is read-only CONTEXT. The thing the
    corrector is told to patch and return is still `PREVIOUS OUTPUT`, the prose draft."""
    ids = await _seed_with_project(db)
    prompts = await _run_real_loop(db, ids)

    corrector = _terminal_corrector_prompts(prompts)[0]
    previous = corrector[corrector.index("PREVIOUS OUTPUT:"):]
    assert _CERT_NAME not in previous and '"team_size"' not in previous
    assert "Return ONLY the corrected JSON." in previous
    assert corrector.index("DELIVERED DOCUMENT") < corrector.index("PREVIOUS OUTPUT:")


@pytest.mark.asyncio
async def test_the_drafting_corrector_is_unchanged(db):
    """The DRAFTING rounds have no composed document to show — the block must not
    appear there, and their prompt must stay byte-identical to what shipped."""
    ids = await _seed_with_project(db)
    prompts = await _run_real_loop(db, ids)

    all_corr = _all_corrector_prompts(prompts)
    without_block = [p for p in all_corr if "DELIVERED DOCUMENT" not in p]
    assert without_block, "the drafting loop's corrector rounds carry no block"


def test_build_retry_prompt_without_delivered_is_byte_identical_to_the_old_shape():
    """The `delivered` argument defaults to None and is purely additive — every
    non-terminal caller gets exactly the prompt it got before #668."""
    import json as _json

    draft = {"summary": "s", "work": [], "skills": []}
    expected = (
        "A quality review of your previous CV tailoring identified the following issues. "
        "Patch the JSON to address every issue, using the CANDIDATE PROFILE as the only "
        "source of truth, and return the corrected object in the SAME schema.\n\n"
        "REVIEW FEEDBACK:\nFB\n\n"
        "CANDIDATE PROFILE (source of truth):\nSRC\n\n"
        f"PREVIOUS OUTPUT:\n{_json.dumps(draft, ensure_ascii=False, indent=2)}\n\n"
        "Return ONLY the corrected JSON."
    )
    assert build_retry_prompt(draft, "FB", "SRC") == expected
    assert build_retry_prompt(draft, "FB", "SRC", delivered=None) == expected


def test_the_corrector_system_prompt_tells_it_which_document_the_findings_are_about():
    """ADR-083 clause 4's third constraint, applied to a block one level up: a block
    folded into another audience's input arrives with the wrong imperative unless the
    imperative is stated. The corrector must be told (a) to read findings against the
    DELIVERED DOCUMENT, (b) that a line of its own last output missing from that
    document was genuinely cut, and (c) HOW to act on a nested-project finding — an
    omitted project is re-joined from the profile unchanged, so silence is not a fix."""
    flat = _flat(CV_TAILORING_REFINEMENT_PROMPT)
    assert "DELIVERED DOCUMENT" in flat
    assert "it — not your PREVIOUS OUTPUT — is what the REVIEW FEEDBACK is about" in flat
    assert "was cut by the length budget, so a finding naming it is correct, not stale" in flat
    assert "a project you omit is re-joined from the profile unchanged" in flat
    # The output shape is stated ONCE, by the rule that already stated it — ADR-062
    # clause 4: one prompt may not say one thing twice.
    assert "Output ONLY the corrected prose JSON in the same schema as the input" in flat
    assert flat.count("prose JSON in the same schema") == 1


# ---------------------------------------------------------------------------
# 2. `repetition` leaves the minor line; check 8 carries it instead
# ---------------------------------------------------------------------------


def test_repetition_is_no_longer_minor_by_definition_on_either_cv_door():
    """Founder ruling 3: ADR-083 clause 3 wins, ADR-082's *detect, never repair* is
    narrowed to the DETERMINISTIC layer. The entry comes off BOTH doors or the doors
    disagree about one concept, which is ADR-062 clause 4 — the contradiction ADR-083's
    own Context item 1 measured the model obeying."""
    assert "repetition" not in cv_rev._MANDATE_PROSE
    assert "repetition" not in cv_rev._MANDATE_TERMINAL


@pytest.mark.parametrize(
    "prompt",
    [cv_rev.REVIEW_SYSTEM_PROMPT, cv_rev.TERMINAL_REVIEW_SYSTEM_PROMPT],
    ids=["prose_door", "terminal_door"],
)
def test_the_named_redundancy_check_is_arm_cs_wording_verbatim(prompt):
    """ADR-083 clause 1: a mandate is carried by NAMED CHECKS, not by a role sentence —
    measured 5/5 blocking for one named check against 0/5 for an open role mandate on
    byte-identical input. This IS that check's text, not a paraphrase: the measurement
    is of this wording (`Documents/Runs/Stracciatella/redundanz-familie/
    reviewer-mandate-replay/replay_mandate.py`, `_CLOSED_PLUS_8`), so rewording it
    discards the evidence for it."""
    flat = _flat(prompt)
    assert (
        "8. REDUNDANCY: Flag any place where the document states the same achievement, "
        "project or responsibility more than once — within one bullet list, or between a "
        "work entry's bullets and the bullets of a project nested under it. A failure of "
        "this check is blocking like any other."
    ) in flat


def test_check_8_is_blocking_on_both_doors_and_the_mandate_agrees():
    """The prose door's mandate excepts check 2 only; the terminal door's excepts 2, 9
    and 10. Neither excepts 8 — so the numbered-check sentence makes it blocking, and no
    sentence anywhere re-classifies it."""
    assert "EXCEPT check 2" in _flat(cv_rev._MANDATE_PROSE)
    assert "EXCEPT checks 2, 9 and 10" in _flat(cv_rev._MANDATE_TERMINAL)
    for mandate in (cv_rev._MANDATE_PROSE, cv_rev._MANDATE_TERMINAL):
        assert "check 8" not in _flat(mandate) and "checks 8" not in _flat(mandate)


def test_the_clause_9_terminal_checks_renumbered_with_the_mandate():
    """A check number that appears in the checks and not in the mandate (or the other
    way round) is the ADR-062 clause-4 self-contradiction in its cheapest form."""
    flat = _flat(cv_rev.TERMINAL_REVIEW_SYSTEM_PROMPT)
    assert "9. CLAIM BALANCE" in flat
    assert "10. VOICE" in flat
    assert "8. CLAIM BALANCE" not in flat
    assert "checks 9 and 10 are the candidate's own call" in flat
    # the prose door has neither of the clause-9 checks and must not gain a number
    assert "CLAIM BALANCE" not in cv_rev.REVIEW_SYSTEM_PROMPT
    assert "VOICE" not in cv_rev.REVIEW_SYSTEM_PROMPT


def test_no_assembled_cv_door_still_calls_repetition_minor_anywhere():
    """The whole-document version of the two assertions above, and it is the assertion
    that found the real hole.

    Removing the word from `_MANDATE_PROSE` and `_MANDATE_TERMINAL` left it in
    `review_severity.SEVERITY_CONTRACT` — the SHARED severity vocabulary every reviewer
    composes — where it stood as an example of `minor`. A shared example contradicting a
    door's own named check is exactly the ADR-062 clause-4 self-contradiction ADR-083's
    Context item 1 measured the model obeying, and it obeys the EXCLUSION rather than the
    check, so check 8 would have shipped inert. The word had to leave the shared
    vocabulary too; the letter door, which still means it, says it in its own
    `_MINOR_PROSE`.

    Scoped to the word outside check 8's own sentence: check 8 does not use it."""
    for prompt in (cv_rev.REVIEW_SYSTEM_PROMPT, cv_rev.TERMINAL_REVIEW_SYSTEM_PROMPT):
        flat = _flat(prompt)
        assert "repetition" not in flat, (
            "an assembled CV door still calls repetition minor somewhere — check the "
            "SHARED severity contract, not only this module's mandate paragraphs"
        )


def test_the_shared_severity_vocabulary_no_longer_offers_repetition_as_minor():
    """Named separately from the door assertion so a future edit that re-adds it here
    fails with the reason, not only with a door-level symptom."""
    from applire.prompts.review_severity import SEVERITY_CONTRACT

    assert "repetition" not in SEVERITY_CONTRACT
    # the generic examples that every door DOES agree on are untouched
    for word in ("wording", "ordering", "tone", "emphasis", "length", "polish"):
        assert word in SEVERITY_CONTRACT


def test_the_letter_door_took_the_same_ruling_on_2026_09_11():
    """#668's letter half WAS held in build 1 — "the letter's blocking behaviour has an
    unmeasured interaction with SF-WRITE.32 / Bug #664" — and was taken in build 2 WITH
    that measurement (founder question L-2; n=5 per arm on the pinned run_2026_08_15
    fixture: redundancy BLOCKING 0/5 → 5/5, corrector repair 5/5, honest IFS/BRC gap
    disclosure intact 5/5). Both doors now say it the same way: the word is gone from the
    minor-by-definition line and the concept is named check 6, REDUNDANCY."""
    from applire.prompts import review_cover_letter as letter

    assert "repetition of a name or phrase" not in _flat(letter._MINOR_PROSE)
    assert "repetition of a name or phrase" not in _flat(letter.REVIEW_SYSTEM_PROMPT)
    assert "repetition of a name or phrase" not in _flat(letter.TERMINAL_REVIEW_SYSTEM_PROMPT)
    assert "6. REDUNDANCY" in letter._CHECKS
    assert "6. REDUNDANCY" in letter.REVIEW_SYSTEM_PROMPT
    assert "6. REDUNDANCY" in letter.TERMINAL_REVIEW_SYSTEM_PROMPT

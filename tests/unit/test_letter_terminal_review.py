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

"""#539 (ADR-076 clause 3): the LETTER terminal review closes over the COMPOSED
letter, the bounded condense rewrite re-enters the SAME review, and the
subject-identity instrument proves "the reviewed subject IS the delivered
letter" — the letter mount of test_cv_terminal_review.py.

What is under test here is the TOPOLOGY — the skeleton #539 builds:

* the terminal round's review subject is the composed letter: content a
  composition-site guard AUTHORED (the #261 outcome reframe) lies IN the
  subject, with the real render measure attached;
* a terminal corrector change re-composes (all seven guards re-applied by the
  single composition site) and RE-ENTERS review;
* the condense rewrite re-enters the SAME chain (``letter_terminal_review``)
  inside the shared budget — the ``cover_letter_condense`` chain is retired,
  and with the review layer disabled the page-budget condense STILL applies;
* there is exactly ONE composition site (the #539 tail-collapse absence test);
* the always-on ``REVIEW_SUBJECT_IDENTITY cl_id=…`` line matches on the clean
  path, and an injected post-verdict mutation breaks the hash AND triggers the
  clause-3 re-entry (reviewed, never reverted).

The #539 mutation matrix (evidence layer 1), run 2026-08-16 against the
committed baseline:

* Mutation A — re-entry rule deleted in ``_render_cover_letter_background``
  → ``test_post_verdict_mutation_breaks_hash_and_reenters`` red.
* Mutation B — hash comparison neutralised (``match = True``)
  → ``test_post_verdict_mutation_breaks_hash_and_reenters`` red.
* Mutation C — terminal subject swapped to the raw draft (composition
  bypassed in ``_reviewer_prompt``)
  → ``test_terminal_corrector_change_recomposes_and_reenters`` and
  ``test_condense_reenters_the_same_review_before_the_verdict`` red.
  The FIRST version of these assertions survived mutation C: the letter's
  terminal draft is already composed on the clean path, so date/reframe
  checks were vacuously green — the tell only exists where raw and composed
  diverge (a corrector emission with ``date: null``; the raw condense
  output, which carries no guard reframe). Assertions are therefore pinned
  to those divergence points, and on the COMPOSED-LETTER slice of the
  prompt, never the whole prompt (the #538 mutation-C lesson: the source
  block carries the same strings).

``review_and_refine`` is faked with a chain-dispatching stub — its loop
mechanics have their own tests; here it must only hand the reviewer_prompt_fn
a round, so the closure that builds the composed subject actually runs.
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


_TARGET_SENTENCE = (
    "At Alpha Systems GmbH, I built an internal LLM-assisted document "
    "classification service, targeting a 60% reduction in manual processing time."
)
# The #261 guard pairs the target with this achievement from the vault — text
# that is NOT in the writer's draft, so its presence in the review subject
# proves guard-authored content lies inside the terminal verdict's scope.
_OUTCOME_MARKER = "very first review round"


def _profile_json() -> dict:
    return {
        "personal_info": {"name": "Max Prober", "email": "max@example.com"},
        "work_experience": [
            {
                "id": "w-alpha",
                "company": "Alpha Systems GmbH",
                "role": "Principal Platform Engineer",
                "responsibilities": [
                    "Built an internal LLM-assisted document classification "
                    "service, targeting a 60% reduction in manual processing "
                    "time."
                ],
                "achievements": [
                    "Documents pre-classified by the service passed the very "
                    "first review round in most cases, confirming the 60% "
                    "reduction target is conservative."
                ],
            },
        ],
        "skills": [],
    }


def _writer_payload() -> dict:
    return {
        "header": {"name": "Max Prober"},
        "recipient": {"name": None, "company": None, "date": None},
        "body": {
            "paragraphs": [
                "Dear team,",
                _TARGET_SENTENCE,
                "I would welcome the opportunity to discuss my experience "
                "with you. Sincerely, Max.",
            ]
        },
        "signature": {"closing": None, "name": None},
    }


def _condense_payload() -> dict:
    payload = _writer_payload()
    payload["body"]["paragraphs"] = [
        "Dear team,",
        "Condensed: " + _TARGET_SENTENCE,
        "I would welcome a conversation. Sincerely, Max.",
    ]
    return payload


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

    user = User(id=uuid.uuid4(), email="letter-terminal@test.com")
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash="letterterminal539",
        raw_text="Platform Engineer at Vector Analytics",
        role_title="Platform Engineer",
        company_name="Vector Analytics",
        required_skills=[],
        nice_to_have_skills=[],
        keywords=[],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement="en",
        jd_language="en",
    )
    profile = make_master_profile(profile_json=_profile_json())
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


def _fake_review(script, captured):
    """Chain-dispatching review_and_refine stub. Non-terminal chains settle the
    draft untouched. Terminal chains run the reviewer_prompt_fn (so the
    subject-building closure executes, exactly like the real loop's first
    reviewer call) and then apply the next scripted corrector action, if any."""
    async def fake(**kwargs):
        if kwargs.get("chain_id") != "letter_terminal_review":
            return kwargs["draft"]
        captured.append({
            "draft": kwargs["draft"],
            "prompt": kwargs["reviewer_prompt_fn"](kwargs["source"], kwargs["draft"]),
            "system": kwargs["reviewer_system"],
            "retain_if": kwargs.get("retain_if"),
            "prefer_if": kwargs.get("prefer_if"),
            "load_bearing_fn": kwargs.get("load_bearing_fn"),
        })
        action = script.pop(0) if script else None
        return action(kwargs["draft"]) if action else kwargs["draft"]
    return fake


async def _run_pipeline(db, ids, *, script=None, captured=None, extra_patches=(),
                        review_retries=2, payload=None, pages=None):
    """Drive _render_cover_letter_background end to end.

    ``pages``: queue of page counts the measure yields, one per render
    (defaults to 1 forever — no overflow). The condense generation is routed
    by its own prompt marker, so provider call order never breaks a test.
    """
    from applire.services.cover_letter import _render_cover_letter_background

    job, profile, cl = ids
    captured = captured if captured is not None else []
    pages_queue = list(pages or [])
    calls = {"writer": 0, "condense": 0}

    async def _aparse(prompt, system=None, **kw):
        if "=== CURRENT LETTER (JSON) ===" in prompt:
            calls["condense"] += 1
            return _condense_payload()
        calls["writer"] += 1
        return payload if payload is not None else _writer_payload()

    provider = AsyncMock()
    provider.aparse_json = AsyncMock(side_effect=_aparse)
    extract = MagicMock(
        side_effect=lambda pdf: ("text", pages_queue.pop(0) if pages_queue else 1)
    )

    patches = [
        patch("applire.services.cover_letter.get_provider", return_value=provider),
        patch("applire.services.cover_letter.review_and_refine",
              side_effect=_fake_review(script or [], captured)),
        patch("applire.services.cover_letter.LLM_REVIEW_MAX_RETRIES", review_retries),
        patch("applire.services.cover_letter_pdf.render_pdf",
              AsyncMock(return_value=b"%PDF-fake")),
        patch("applire.services.ats_audit.extract_text_and_pages", new=extract),
    ]
    # The audit tail is measurement-only by contract and heavyweight — stub it
    # unless a test injects its own (mutating) stand-in via extra_patches.
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


def _identity_lines(caplog):
    return [
        r for r in caplog.records
        if "REVIEW_SUBJECT_IDENTITY" in r.getMessage() and "cl_id=" in r.getMessage()
    ]


def _subject_slice(prompt: str) -> str:
    """The COMPOSED-COVER-LETTER section of the terminal reviewer prompt —
    deliberately excludes the CANDIDATE SOURCE block, which always carries the
    vault's own achievement strings and would make any whole-prompt assertion
    pass vacuously (the #538 mutation-C lesson)."""
    start = prompt.index("COMPOSED COVER LETTER (the delivered letter):")
    end = prompt.index("RENDER MEASURE")
    return prompt[start:end]


# --- the reviewed subject is the composed letter -----------------------------

@pytest.mark.asyncio
async def test_terminal_review_subject_is_composed_letter(db, caplog):
    """#539 core claim: the terminal reviewer's subject carries the
    composition site's guard-authored content (#261 outcome reframe — vault
    text the writer never emitted), the system-stamped date, and the real
    render measure; pre-#539 no reviewer ever saw any guard output."""
    caplog.set_level(logging.INFO, logger="applire.services.cover_letter")
    ids = await _seed(db)
    captured, _ = await _run_pipeline(db, ids)

    assert len(captured) == 1, "exactly one terminal round on the clean path"
    prompt = captured[0]["prompt"]
    subject = _subject_slice(prompt)
    assert _OUTCOME_MARKER in subject, \
        "guard-authored reframe content must lie IN the review subject"
    assert _TARGET_SENTENCE.rstrip(".") in subject or "targeting a 60%" in subject
    assert '"date": null' not in subject, "the system-stamped date is in the subject"
    assert "measured pages: 1, page norm: 1" in prompt, "real render measure attached"
    assert "SHAPE NOTE — TERMINAL ROUND" in captured[0]["system"]

    from applire.models.cover_letter import GeneratedCoverLetter
    cl = await db.get(GeneratedCoverLetter, ids[2].id)
    assert cl.status == "ready"
    delivered = " ".join(cl.letter_data["body"]["paragraphs"])
    assert _OUTCOME_MARKER in delivered, "delivered == reviewed subject (same reframe)"


# --- a terminal corrector change re-enters review ----------------------------

@pytest.mark.asyncio
async def test_terminal_corrector_change_recomposes_and_reenters(db, caplog):
    """Clause 3's re-entry rule, corrector direction: a changed draft is
    re-composed (all seven guards re-applied by the single composition site)
    and re-reviewed."""
    caplog.set_level(logging.INFO, logger="applire.services.cover_letter")
    ids = await _seed(db)

    def change_body(draft):
        # A realistic corrector emission: fresh content, recipient.date left
        # null (the corrector prompt's own rule) — so the re-entered subject
        # carries the date ONLY if the re-composition actually ran. This is
        # what separates the composed subject from the raw settle (mutation C).
        body = dict(draft["body"])
        body["paragraphs"] = list(body["paragraphs"]) + [
            "A distinctly improved terminal correction."
        ]
        return {
            **draft,
            "body": body,
            "recipient": {**draft["recipient"], "date": None},
        }

    captured, _ = await _run_pipeline(db, ids, script=[change_body])

    assert len(captured) == 2, "the changed draft must re-enter review"
    reentered_subject = _subject_slice(captured[1]["prompt"])
    assert "A distinctly improved terminal correction." in reentered_subject
    assert '"date": null' not in reentered_subject, \
        "the re-entered subject is COMPOSED again — date re-stamped by code"

    from applire.models.cover_letter import GeneratedCoverLetter
    cl = await db.get(GeneratedCoverLetter, ids[2].id)
    assert any(
        "distinctly improved" in p for p in cl.letter_data["body"]["paragraphs"]
    )
    assert cl.letter_data["recipient"]["date"], \
        "re-entered content is COMPOSED again — the date is re-stamped by code"

    lines = _identity_lines(caplog)
    assert lines and "match=True" in lines[-1].getMessage()
    assert "terminal_rounds=2" in lines[-1].getMessage()


@pytest.mark.asyncio
async def test_reentry_bound_exhaustion_ships_recomposed_and_flags_structured(db, caplog):
    """The bounded clause-3 exception (the #538 refuter lesson, built in from
    the start here): when the terminal corrector changes the draft in EVERY
    allowed round, the final change ships re-composed but never re-reviewed.
    That state must be (a) delivered (never a gate), (b) re-composed with the
    guards re-applied, and (c) flagged STRUCTURALLY on the always-on identity
    line (``reentry_exhausted=True``, WARNING) — an unstructured warning alone
    is bookkeeping, not testimony."""
    caplog.set_level(logging.INFO, logger="applire.services.cover_letter")
    ids = await _seed(db)

    def change1(draft):
        body = dict(draft["body"])
        body["paragraphs"] = list(body["paragraphs"]) + ["First terminal correction."]
        return {**draft, "body": body}

    def change2(draft):
        body = dict(draft["body"])
        body["paragraphs"] = list(body["paragraphs"]) + ["Second terminal correction."]
        return {**draft, "body": body}

    captured, _ = await _run_pipeline(db, ids, script=[change1, change2])

    assert len(captured) == 2, "bound=1 allows exactly two terminal invocations"

    from applire.models.cover_letter import GeneratedCoverLetter
    cl = await db.get(GeneratedCoverLetter, ids[2].id)
    assert cl.status == "ready", "never a delivery gate"
    assert any(
        "Second terminal correction." in p for p in cl.letter_data["body"]["paragraphs"]
    ), "the final (unreviewed) change ships re-composed"
    assert cl.letter_data["recipient"]["date"], "guards re-applied on the final recomposition"

    lines = _identity_lines(caplog)
    assert len(lines) == 1
    assert "reentry_exhausted=True" in lines[0].getMessage()
    assert lines[0].levelno == logging.WARNING, \
        "an unreviewed final change is a WARNING even when the hash matches"


# --- subject-identity instrument (evidence layer 1) --------------------------

@pytest.mark.asyncio
async def test_subject_identity_line_always_on_and_matching(db, caplog):
    """The always-on REVIEW_SUBJECT_IDENTITY line fires on EVERY delivery —
    letter mount, ``cl_id=`` discriminator — match=True on the clean path."""
    caplog.set_level(logging.INFO, logger="applire.services.cover_letter")
    ids = await _seed(db)
    await _run_pipeline(db, ids)

    lines = _identity_lines(caplog)
    assert len(lines) == 1
    msg = lines[0].getMessage()
    assert "match=True" in msg and "terminal_rounds=1" in msg and "reentered=0" in msg
    assert lines[0].levelno == logging.INFO


@pytest.mark.asyncio
async def test_post_verdict_mutation_breaks_hash_and_reenters(db, caplog):
    """#539 evidence layer 1, the MUTATION TEST: an artificial post-verdict
    mutation pass (injected into the audit window, after the terminal verdict)
    must (a) break the hash comparison — a WARNING match=False line — and
    (b) fire the clause-3 re-entry: the CHANGE is re-reviewed (the mutated
    content appears in a fresh terminal-review subject), never silently
    reverted, and the delivery settles to a matching final state.

    Deleting the re-entry rule or the hash comparison in
    ``_render_cover_letter_background`` turns exactly this test red."""
    caplog.set_level(logging.INFO, logger="applire.services.cover_letter")
    ids = await _seed(db)

    fired = {"n": 0}

    async def mutating_update(cl, db_, pdf=None):
        if fired["n"] == 0:
            fired["n"] = 1
            data = dict(cl.letter_data)
            body = dict(data["body"])
            body["paragraphs"] = list(body["paragraphs"]) + ["INJECTED-POST-VERDICT"]
            data["body"] = body
            cl.letter_data = data

    captured, _ = await _run_pipeline(
        db, ids,
        extra_patches=[patch(
            "applire.services.cover_letter._update_ats_report_letter",
            new=mutating_update,
        )],
    )

    lines = _identity_lines(caplog)
    msgs = [ln.getMessage() for ln in lines]
    levels = [ln.levelno for ln in lines]
    assert len(lines) == 2, "one mismatch line + one post-re-entry line"
    assert "match=False" in msgs[0] and levels[0] == logging.WARNING
    assert "match=True" in msgs[1] and "reentered=1" in msgs[1]

    assert len(captured) == 2, "the mutation must re-enter the terminal review"
    assert "INJECTED-POST-VERDICT" in _subject_slice(captured[1]["prompt"]), \
        "the re-entered review subject carries the CHANGE (reviewed, not reverted)"

    from applire.models.cover_letter import GeneratedCoverLetter
    cl = await db.get(GeneratedCoverLetter, ids[2].id)
    assert any(
        p == "INJECTED-POST-VERDICT" for p in cl.letter_data["body"]["paragraphs"]
    ), "the reviewed change ships — re-entry reviews, it does not revert"
    assert cl.status == "ready"


# --- the condense rewrite re-enters the SAME review (shared budget) ----------

@pytest.mark.asyncio
async def test_condense_reenters_the_same_review_before_the_verdict(db, caplog):
    """#539 / clause 6 coordination: a page overrun triggers exactly ONE
    condense generation, its output is COMPOSED via the single site and
    reviewed by the SAME terminal chain BEFORE the verdict falls — no
    ``cover_letter_condense`` chain, no second guard tail, no separate retry
    budget. The word-budget prefer_if rides only this condense-entered
    invocation (#420's boundary)."""
    caplog.set_level(logging.INFO, logger="applire.services.cover_letter")
    ids = await _seed(db)

    # First render measures 2 pages (over the 1-page DACH norm); the
    # post-condense re-render measures 1.
    captured, calls = await _run_pipeline(db, ids, pages=[2, 1])

    assert calls["condense"] == 1, "exactly one bounded condense generation"
    assert len(captured) == 1, \
        "the condense fires BEFORE the verdict round — one terminal invocation"
    subject = _subject_slice(captured[0]["prompt"])
    assert "Condensed:" in subject, \
        "the terminal verdict closes over the CONDENSED composition"
    # The condense generation emits a RAW letter (no date, no guard output) —
    # the subject carries these only if the single composition site re-ran
    # over the condense output (the assertions that separate the composed
    # subject from the raw condense emission — mutation C's tell).
    assert _OUTCOME_MARKER in subject, \
        "the #261 guard re-applied to the condense output, IN the subject"
    assert '"date": null' not in subject, \
        "the condensed subject is composed — date re-stamped by code"
    assert captured[0]["prefer_if"] is not None, \
        "the condense-entered invocation carries the word-budget prefer_if"
    assert captured[0]["retain_if"] is not None
    assert captured[0]["load_bearing_fn"] is not None

    from applire.models.cover_letter import GeneratedCoverLetter
    cl = await db.get(GeneratedCoverLetter, ids[2].id)
    assert any("Condensed:" in p for p in cl.letter_data["body"]["paragraphs"])
    assert cl.letter_data["recipient"]["date"], \
        "the condense output went through the single composition site"

    lines = _identity_lines(caplog)
    assert lines and "match=True" in lines[-1].getMessage()


@pytest.mark.asyncio
async def test_clean_terminal_round_carries_no_prefer_if(db):
    """#420's boundary in the new topology: a terminal invocation NOT entered
    via condense must not carry the word-budget preference."""
    ids = await _seed(db)
    captured, calls = await _run_pipeline(db, ids)

    assert calls["condense"] == 0
    assert len(captured) == 1
    assert captured[0]["prefer_if"] is None
    assert captured[0]["retain_if"] is not None
    assert captured[0]["load_bearing_fn"] is not None


@pytest.mark.asyncio
async def test_review_layer_disabled_still_condenses_but_skips_the_verdict(db, caplog):
    """LLM_REVIEW_MAX_RETRIES=0 disables the review layer, not the page-budget
    mechanism (the pre-#539 behaviour, preserved): the bounded condense still
    fires and is still composed via the single site; no verdict round runs and
    the identity line reports terminal_rounds=0 honestly."""
    caplog.set_level(logging.INFO, logger="applire.services.cover_letter")
    ids = await _seed(db)
    captured, calls = await _run_pipeline(db, ids, review_retries=0, pages=[2, 1])

    assert captured == [], "no terminal verdict round with the review layer off"
    assert calls["condense"] == 1, "the page-budget condense still applies"

    from applire.models.cover_letter import GeneratedCoverLetter
    cl = await db.get(GeneratedCoverLetter, ids[2].id)
    assert cl.status == "ready"
    assert any("Condensed:" in p for p in cl.letter_data["body"]["paragraphs"])
    assert cl.letter_data["recipient"]["date"], "composed via the single site"

    lines = _identity_lines(caplog)
    assert len(lines) == 1
    assert "terminal_rounds=0" in lines[0].getMessage()
    assert "match=True" in lines[0].getMessage()


@pytest.mark.asyncio
async def test_identity_reentry_does_not_mint_a_second_condense(db, caplog):
    """ADR-051 §6's bound is per DELIVERY, not per invocation (adversarial
    pre-propagation finding, 2026-08-16): when a post-verdict mutation forces
    the identity re-entry AND the mutated content still renders over the page
    norm, the second terminal invocation must NOT fire a second condense
    generation — the single bounded rewrite is already spent
    (``condense_spent`` threaded across invocations)."""
    caplog.set_level(logging.INFO, logger="applire.services.cover_letter")
    ids = await _seed(db)

    fired = {"n": 0}

    async def mutating_update(cl, db_, pdf=None):
        if fired["n"] == 0:
            fired["n"] = 1
            data = dict(cl.letter_data)
            body = dict(data["body"])
            body["paragraphs"] = list(body["paragraphs"]) + ["INJECTED-POST-VERDICT"]
            data["body"] = body
            cl.letter_data = data

    # initial render: 2 pages (over) → condense; post-condense: 1; the identity
    # re-entry's re-render: 2 again (over) — the second invocation must skip.
    captured, calls = await _run_pipeline(
        db, ids,
        pages=[2, 1, 2, 2],
        extra_patches=[patch(
            "applire.services.cover_letter._update_ats_report_letter",
            new=mutating_update,
        )],
    )

    assert calls["condense"] == 1, \
        "the per-delivery condense budget is spent — no second rewrite on re-entry"
    assert len(captured) == 2, "the mutation still re-enters the terminal review"

    from applire.models.cover_letter import GeneratedCoverLetter
    cl = await db.get(GeneratedCoverLetter, ids[2].id)
    assert cl.status == "ready"


# --- #525 replay (evidence layer 2a): run A, 2026-08-14, operations_marcus_de


# The condense-chain generator's own welded sentence, captured verbatim from
# run A's cover_letter_condense round 3 (2026-08-14; the chain exhausted 5/5).
# The vault's evidence keeps SAP PP as the daily-use system and scopes SAP MM
# to Disposition/Bestellanforderungen; the condense rewrite welded "tägliche
# Nutzung" onto SAP MM — the #525 class: authored under length pressure,
# delivered without any reviewer ever seeing the final composition.
_RUN_A_WELDED_SENTENCE = (
    "Meine 15-jährige SAP-PP-Erfahrung und neun Jahre tägliche Nutzung von "
    "SAP MM bei Weberit bilden meinen praktischen Bezug zur Arbeitsvorbereitung."
)


def _marcus_profile_json() -> dict:
    """The vault slice behind run A's SAP sentences (operations_marcus_de,
    synthetic PQ persona) — enough grounding that the #254 figure guard keeps
    the sentence (both figures are vault-backed and Weberit-anchored), so the
    weld reaches the terminal subject exactly as it reached delivery in run A."""
    return {
        "personal_info": {"name": "Marcus Weber", "email": "marcus@example.com"},
        "work_experience": [
            {
                "id": "w-weberit",
                "company": "Weberit Kunststofftechnik GmbH",
                "role": "Produktionsleiter",
                "responsibilities": [
                    "Key-User für SAP PP beim Rollout und tägliche Nutzung für "
                    "Fertigungsaufträge und Rückmeldungen; SAP PP mit "
                    "Advanced-Proficiency und 15 Jahren Erfahrung.",
                    "Neun Jahre Nutzung von SAP MM für Disposition und "
                    "Bestellanforderungen für Instandhaltungsmaterial.",
                ],
                "achievements": [],
            },
        ],
        "skills": [],
    }


def _run_a_condense_payload() -> dict:
    payload = _writer_payload()
    payload["header"] = {"name": "Marcus Weber"}
    payload["body"]["paragraphs"] = [
        "Sehr geehrte Damen und Herren,",
        _RUN_A_WELDED_SENTENCE,
        "Ich freue mich auf ein persönliches Gespräch. Mit freundlichen Grüßen.",
    ]
    return payload


@pytest.mark.asyncio
async def test_run_a_525_replay_weld_lies_in_the_terminal_subject(db, caplog):
    """#525 replay (deterministic — the captured content, not the model):
    under the new topology (1) NO ``cover_letter_condense`` chain fires — the
    condense rewrite has no loop of its own and no tail copy to hide behind —
    and (2) run A's welded SAP-MM sentence, replayed as the condense rewrite's
    output, LIES IN the terminal review subject and is exactly what ships.
    Visibility is the criterion; whether the reviewer flags the weld is
    judgement (checks 1/2 of the terminal door — ADR-062 clause 7, the
    clause-7 evidence run's question, not this test's)."""
    caplog.set_level(logging.INFO, logger="applire.services.cover_letter")

    from applire.models.cover_letter import CoverLetterStatus, GeneratedCoverLetter
    from applire.models.job import JobAnalysis
    from applire.models.user import User

    user = User(id=uuid.uuid4(), email="marcus-replay@test.com")
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash="runA525replay",
        raw_text="Leiter Arbeitsvorbereitung bei Rheinwerk Verpackungen",
        role_title="Leiter Arbeitsvorbereitung",
        company_name="Rheinwerk Verpackungen",
        required_skills=[],
        nice_to_have_skills=[],
        keywords=[],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement="de",
        jd_language="de",
    )
    profile = make_master_profile(profile_json=_marcus_profile_json())
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

    chains_seen: list[str] = []
    captured: list[dict] = []

    async def fake_review(**kwargs):
        chains_seen.append(kwargs.get("chain_id"))
        if kwargs.get("chain_id") != "letter_terminal_review":
            return kwargs["draft"]
        captured.append({
            "prompt": kwargs["reviewer_prompt_fn"](kwargs["source"], kwargs["draft"]),
        })
        return kwargs["draft"]

    pages_queue = [2, 1]  # over the norm → condense fires; condensed fits
    calls = {"condense": 0}

    async def _aparse(prompt, system=None, **kw):
        if "=== CURRENT LETTER (JSON) ===" in prompt:
            calls["condense"] += 1
            return _run_a_condense_payload()
        return _run_a_condense_payload()  # writer output — irrelevant, condensed replaces it

    provider = AsyncMock()
    provider.aparse_json = AsyncMock(side_effect=_aparse)
    extract = MagicMock(
        side_effect=lambda pdf: ("text", pages_queue.pop(0) if pages_queue else 1)
    )

    from applire.services.cover_letter import _render_cover_letter_background

    with (
        patch("applire.services.cover_letter.AsyncSessionLocal") as sl,
        patch("applire.services.cover_letter.get_provider", return_value=provider),
        patch("applire.services.cover_letter.review_and_refine", side_effect=fake_review),
        patch("applire.services.cover_letter_pdf.render_pdf",
              AsyncMock(return_value=b"%PDF-fake")),
        patch("applire.services.ats_audit.extract_text_and_pages", new=extract),
        patch("applire.services.cover_letter._update_ats_report_letter", new=AsyncMock()),
    ):
        sl.return_value.__aenter__.return_value = db
        await _render_cover_letter_background(cl_id=cl.id, cv_id=None, job_id=job.id)

    # (1) The condense-with-its-own-loop is structurally gone.
    assert "cover_letter_condense" not in chains_seen, chains_seen
    assert calls["condense"] == 1, "the bounded condense generation itself still ran"

    # (2) The weld lies IN the terminal review subject — run A delivered it
    # with no reviewer ever seeing the final composition; now the verdict
    # closes over it.
    assert captured, "a terminal verdict round must have run"
    subject = _subject_slice(captured[-1]["prompt"])
    assert _RUN_A_WELDED_SENTENCE in subject, \
        "the welded SAP-MM sentence must lie IN the review subject"

    # ...and the reviewed subject IS the delivered letter (identity line).
    cl_row = await db.get(GeneratedCoverLetter, cl.id)
    assert any(
        _RUN_A_WELDED_SENTENCE in p for p in cl_row.letter_data["body"]["paragraphs"]
    )
    lines = _identity_lines(caplog)
    assert lines and "match=True" in lines[-1].getMessage()


# --- the tail collapse: exactly ONE composition site (#539 evidence 2b) ------

def test_exactly_one_composition_site():
    """The #539 absence test: every one of the seven guards is called exactly
    once in the module, inside ``_compose_letter`` — the duplicated
    post-condense tail (the old ~:1345–:1360) does not exist, and neither the
    render pipeline nor the terminal loop calls a guard directly. A future
    pass added to only one place fails this test's counterpart run (the
    composed-subject tests above) rather than shipping a forgotten twin."""
    import inspect

    import applire.services.cover_letter as mod

    guards = [
        "guard_letter_figures",
        "guard_letter_outcome_preference",
        "_apply_recipient_overrides",
        "_inject_letter_date",
        "_normalize_signature_closing",
        "_backfill_sender_name",
        "_split_inline_salutation",
    ]
    compose_src = inspect.getsource(mod._compose_letter)
    render_src = inspect.getsource(mod._render_cover_letter_background)
    terminal_src = inspect.getsource(mod._terminal_review_letter)
    for guard in guards:
        assert compose_src.count(f"{guard}(") == 1, \
            f"{guard} must be applied exactly once, in _compose_letter"
        assert f"{guard}(" not in render_src, \
            f"{guard} must not be called outside the composition site"
        assert f"{guard}(" not in terminal_src, \
            f"{guard} must not be called outside the composition site"

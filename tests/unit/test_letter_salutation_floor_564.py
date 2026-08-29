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

"""#564 — the pipeline door's Anrede floor (bug-batch 3, W1).

Attribution (issue #564, per-round, 2026-08-28): the salutation was NEVER
generated on the pipeline door — ``_has_salutation`` is False from the
writer's first draft on, in EVERY drafting and terminal round of the
2026-08-28 run (synthetic panel case ``controlling_emma_de``). Three causes:

1. The #224 floor (``_inject_salutation``) had exactly ONE call site —
   ``render_agent_letter`` (the agent door). The pipeline door's own
   composition tail, ``_compose_letter``, never called it.
2. ``_split_inline_salutation`` (#307) only reformats an EXISTING inline
   salutation — it cannot supply one that is entirely absent.
3. The writer prompt (``prompts/cover_letter.py``) never asked for one at
   all (category B per ``applire-prompt-first``: never asked, but possible).

The fix (design decided in the #564 W1 brief) is TWO-PART: a deterministic
floor call added to ``_compose_letter`` (the single composition site every
recomposition — condense, terminal corrector, subject-identity re-entry —
runs through, so the floor cannot be outrun the way #547 was), plus a
written SALUTATION rule in the writer prompt so the model supplies its own,
possibly personalised, Anrede in the first place. The floor only ever
supplies the GENERIC form (ADR-062: a guessed gendered/named Anrede is a
judgement, not a deterministic floor's job).

This file tests the PIPELINE door and the ``_compose_letter``-level
contract directly (one seam test per door, on the PERSISTED ``letter_data``,
per the brief). The agent door (which has ALWAYS run this floor, since
#224) is pinned in ``backend/tests/unit/test_render_agent.py`` instead —
its harness already exists and is the more natural home for that side.

Fixture provenance (design F): ``_DRAFT_564_PARAGRAPHS`` is a trimmed
reshaping of the writer's actual first draft, extracted read-only from
``applire-core/backend/logs/llm/2026-08-28.jsonl`` line 27 (1-based; ts
09:13:57.872030+00:00, system prompt starts "You are an expert DACH career
coach...") — the exact call #564's own attribution names. That record's
``response`` is a 4-paragraph DE cover letter for "Senior Controller" at
"Arnold Antriebstechnik GmbH" whose ``body.paragraphs[0]`` starts
mid-sentence ("mit großem Interesse bewerbe ich mich...", lower-case —
clearly written to follow a salutation that was never there). The
paragraphs below keep that exact shape (recipient set, no Anrede, 4 body
paragraphs, a signed name) but drop every figure the real draft carried
(financial amounts, headcounts, dates) — irrelevant to this floor and would
otherwise engage ``guard_letter_figures``/``guard_letter_outcome_preference``,
which is not what these tests are about. This is synthetic panel test data
(``controlling_emma_de``), not a real person's CV — usable per the brief.
"""
import copy
import json
import logging
import sys
import uuid
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.support.profile_factory import make_master_profile

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


# ── the #564 fixture (design F) ──────────────────────────────────────────────

_DRAFT_564_PARAGRAPHS = [
    "mit großem Interesse bewerbe ich mich als Senior Controller (m/w/d) bei "
    "der Arnold Antriebstechnik GmbH.",
    "Als Financial Controllerin verfüge ich über langjährige Erfahrung im "
    "industriellen Mittelstand, unter anderem in Monats- und Jahresabschluss "
    "sowie Produktionscontrolling.",
    "Meine Erfahrung umfasst zudem Kostenstellen- und Kostenträgerrechnung in "
    "SAP CO sowie meine Tätigkeit als SAP-CO/FI-Key-Userin.",
    "Gerne erläutere ich Ihnen im persönlichen Gespräch, wie ich meine "
    "Erfahrung bei der Arnold Antriebstechnik einbringen kann. Ich freue "
    "mich auf Ihre Einladung zu einem Gespräch.",
]


def _draft_564() -> dict:
    """The #564 writer draft's shape: recipient set, NO Anrede, 4 body
    paragraphs, a signed name — matching record 27's actual response
    (header.name/signature.name "KATRIN HOFFMANN", recipient.company "Arnold
    Antriebstechnik GmbH", recipient.name null)."""
    return {
        "header": {
            "name": "Katrin Hoffmann",
            "address": "Stuttgart",
            "phone": "+49 711 0000000",
            "email": "katrin.hoffmann@example.com",
            "photo_url": None,
        },
        "recipient": {
            "name": None,
            "title": None,
            "company": "Arnold Antriebstechnik GmbH",
            "address": "Stuttgart-Vaihingen",
            "date": None,
        },
        "body": {"paragraphs": list(_DRAFT_564_PARAGRAPHS)},
        "signature": {"closing": None, "name": "Katrin Hoffmann"},
    }


_MINIMAL_PROFILE_JSON = {"work_experience": [], "skills": []}


# ── harness (adapted from tests/unit/test_letter_terminal_review.py — no
#    cross-test-file imports elsewhere in this tree, so the minimal harness
#    is kept local rather than shared) ────────────────────────────────────


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


async def _seed(db, *, document_language: str | None = None):
    from applire.models.cover_letter import CoverLetterStatus, GeneratedCoverLetter
    from applire.models.job import JobAnalysis
    from applire.models.user import User

    user = User(id=uuid.uuid4(), email="letter-564@test.com")
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash="letter564salutation",
        raw_text="Senior Controller bei der Arnold Antriebstechnik GmbH",
        role_title="Senior Controller",
        company_name="Arnold Antriebstechnik GmbH",
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
        document_language=document_language,
    )
    db.add(cl)
    await db.commit()
    await db.refresh(cl)
    return job, profile, cl


def _fake_review(script, captured):
    """Chain-dispatching review_and_refine stub (verbatim pattern from
    test_letter_terminal_review.py). Non-terminal chains settle the draft
    untouched. Terminal chains run reviewer_prompt_fn (so the composed
    subject actually gets built) and then apply the next scripted corrector
    action, if any."""
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


async def _run_pipeline(db, ids, *, script=None, captured=None, payload=None,
                        review_retries=2):
    """Drive _render_cover_letter_background end to end with the writer
    provider mocked to return ``payload`` (the #564 draft)."""
    from applire.services.cover_letter import _render_cover_letter_background

    job, profile, cl = ids
    captured = captured if captured is not None else []
    calls = {"writer": 0, "condense": 0}

    async def _aparse(prompt, system=None, **kw):
        if "=== CURRENT LETTER (JSON) ===" in prompt:
            calls["condense"] += 1
            return payload if payload is not None else _draft_564()
        calls["writer"] += 1
        return payload if payload is not None else _draft_564()

    provider = AsyncMock()
    provider.aparse_json = AsyncMock(side_effect=_aparse)
    extract = MagicMock(side_effect=lambda pdf: ("text", 1))  # always 1 page — no condense

    with (
        patch("applire.services.cover_letter.AsyncSessionLocal") as sl,
        patch("applire.services.cover_letter.get_provider", return_value=provider),
        patch("applire.services.cover_letter.review_and_refine",
              side_effect=_fake_review(script or [], captured)),
        patch("applire.services.cover_letter.LLM_REVIEW_MAX_RETRIES", review_retries),
        patch("applire.services.cover_letter_pdf.render_pdf",
              AsyncMock(return_value=b"%PDF-fake")),
        patch("applire.services.ats_audit.extract_text_and_pages", new=extract),
        patch("applire.services.cover_letter._update_ats_report_letter", new=AsyncMock()),
    ):
        sl.return_value.__aenter__.return_value = db
        await _render_cover_letter_background(cl_id=cl.id, cv_id=None, job_id=job.id)
    return captured, calls


def _subject_slice(prompt: str) -> str:
    """The COMPOSED-COVER-LETTER section of the terminal reviewer prompt —
    excludes the CANDIDATE SOURCE block (same reasoning as the #538 mutation-C
    lesson in test_letter_terminal_review.py)."""
    start = prompt.index("COMPOSED COVER LETTER (the delivered letter):")
    end = prompt.index("RENDER MEASURE")
    return prompt[start:end]


# --- 1. pipeline door: the floor supplies the missing Anrede -----------------


@pytest.mark.asyncio
async def test_pipeline_door_supplies_missing_anrede_564(db):
    """The #564 core claim: driven through the REAL generation path with the
    provider mocked to return the #564 draft (no Anrede), the PERSISTED
    letter_data carries the generic DE Anrede as its own first paragraph,
    with the writer's actual opening pushed to paragraphs[1]."""
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.templates.labels import cover_letter_labels

    ids = await _seed(db, document_language="de")
    await _run_pipeline(db, ids, payload=_draft_564())

    cl = await db.get(GeneratedCoverLetter, ids[2].id)
    assert cl.status == "ready"
    paragraphs = cl.letter_data["body"]["paragraphs"]
    assert paragraphs[0] == cover_letter_labels("de")["salutation"]
    assert paragraphs[0] == "Sehr geehrte Damen und Herren,"
    assert paragraphs[1] == _DRAFT_564_PARAGRAPHS[0]
    assert len(paragraphs) == len(_DRAFT_564_PARAGRAPHS) + 1


# --- 2. terminal-loop property: the floor cannot be outrun -------------------


def _corrector_drops_anrede_again(draft: dict) -> dict:
    """A realistic terminal-corrector regression: round 1 hands the
    corrector the COMPOSED draft (the Anrede floor already ran once, so
    paragraphs[0] is the injected salutation) — this simulates a corrector
    rewrite that drops it again and rewrites the (new) opening, exactly the
    #547-class failure ("a control outrun by a later stage of the same
    pipeline") design A rules out by construction."""
    body = dict(draft["body"])
    paragraphs = list(body["paragraphs"])[1:]  # drop the injected Anrede
    paragraphs[0] = "Überarbeitet: " + paragraphs[0]
    body["paragraphs"] = paragraphs
    return {**draft, "body": body, "recipient": {**draft["recipient"], "date": None}}


@pytest.mark.asyncio
async def test_pipeline_door_floor_survives_terminal_corrector_564(db, caplog):
    """Through _terminal_review_letter with a fake review_and_refine whose
    corrector returns a CHANGED raw draft still without an Anrede: the
    RECOMPOSED persisted letter_data carries the Anrede again after the
    corrector round — the floor re-runs on every re-entry because it lives
    inside _compose_letter, the single composition site every re-entry
    (corrector, condense, subject-identity) is re-run through."""
    caplog.set_level(logging.INFO, logger="applire.services.cover_letter")
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.templates.labels import cover_letter_labels

    ids = await _seed(db, document_language="de")
    captured, _ = await _run_pipeline(
        db, ids, payload=_draft_564(), script=[_corrector_drops_anrede_again],
    )

    assert len(captured) == 2, "the corrector's change must re-enter review"
    salutation = cover_letter_labels("de")["salutation"]
    reentered_subject = _subject_slice(captured[1]["prompt"])
    assert salutation in reentered_subject, \
        "the re-entered review subject is re-composed — floor re-applied"

    cl = await db.get(GeneratedCoverLetter, ids[2].id)
    paragraphs = cl.letter_data["body"]["paragraphs"]
    assert paragraphs[0] == salutation, \
        "the floor cannot be outrun by a later corrector round (#547 class)"
    assert "Überarbeitet:" in paragraphs[1]


# --- 4. idempotence at the _compose_letter level ------------------------------


@pytest.mark.parametrize(
    "salutation",
    [
        "Sehr geehrte Damen und Herren,",  # the floor's own generic form
        "Sehr geehrte Frau Dr. Weber,",  # a personalised, author-written Anrede
    ],
)
def test_compose_letter_is_idempotent_when_salutation_already_present_564(salutation):
    """Design A: _inject_salutation is a strict no-op whenever _has_salutation
    is already True — both the generic floor form AND a personalised,
    author-written Anrede compose byte-identically on a second pass (the
    guarantee _terminal_review_letter's subject cache relies on)."""
    from applire.services.cover_letter import _compose_letter

    def _compose(data: dict) -> dict:
        return _compose_letter(
            copy.deepcopy(data),
            profile_json=_MINIMAL_PROFILE_JSON,
            cv_data={"contact": {}},
            profile=None,
            pre_gen={},
            language="de",
            today=date(2026, 8, 29),
        )

    letter_data = {
        "header": {"name": "Katrin Hoffmann"},
        "recipient": {"name": None, "company": None, "date": None},
        "body": {"paragraphs": [salutation, *_DRAFT_564_PARAGRAPHS]},
        "signature": {"closing": None, "name": "Katrin Hoffmann"},
    }

    first = _compose(letter_data)
    second = _compose(first)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert first["body"]["paragraphs"][0] == salutation


def test_compose_letter_still_splits_an_inline_salutation_564():
    """Design A's inject-then-split order must not disturb #307: an INLINE
    Anrede is recognised by _has_salutation (so _inject_salutation no-ops)
    and is then still split into its own paragraph by
    _split_inline_salutation exactly as before."""
    from applire.services.cover_letter import _compose_letter

    inline = (
        "Sehr geehrte Damen und Herren, mit großem Interesse bewerbe ich "
        "mich als Senior Controller (m/w/d)."
    )
    letter_data = {
        "header": {"name": "Katrin Hoffmann"},
        "recipient": {"name": None, "company": None, "date": None},
        "body": {"paragraphs": [inline, *_DRAFT_564_PARAGRAPHS[1:]]},
        "signature": {"closing": None, "name": "Katrin Hoffmann"},
    }
    composed = _compose_letter(
        letter_data,
        profile_json=_MINIMAL_PROFILE_JSON,
        cv_data={"contact": {}},
        profile=None,
        pre_gen={},
        language="de",
        today=date(2026, 8, 29),
    )
    paragraphs = composed["body"]["paragraphs"]
    assert paragraphs[0] == "Sehr geehrte Damen und Herren,"
    assert paragraphs[1].startswith("mit großem Interesse")


# --- 5. EN path ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_door_supplies_missing_anrede_en_564(db):
    """Same as test 1, language="en": the floor supplies "Dear Sir or
    Madam," — proving the injected label is language-routed, not hard-coded
    German (_compose_letter passes the pipeline's detected_language through
    to _inject_salutation, exactly as it already does for the date stamp and
    the sign-off label)."""
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.templates.labels import cover_letter_labels

    ids = await _seed(db, document_language="en")
    await _run_pipeline(db, ids, payload=_draft_564())

    cl = await db.get(GeneratedCoverLetter, ids[2].id)
    assert cl.status == "ready"
    paragraphs = cl.letter_data["body"]["paragraphs"]
    assert paragraphs[0] == cover_letter_labels("en")["salutation"]
    assert paragraphs[0] == "Dear Sir or Madam,"
    assert paragraphs[1] == _DRAFT_564_PARAGRAPHS[0]

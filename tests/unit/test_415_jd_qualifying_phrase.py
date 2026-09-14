# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#415 (ruling W-4) — the ledger entry carries the posting's OWN qualifying sentence.

The defect, from a real charter run: the JD's first duty is *"Eigenverantwortliche
Erstellung von Monats- und **Jahresabschluss**-Reporting (HGB)"*, the vault holds
*"Betreuung der Wirtschaftsprüfer im Jahresabschluss (HGB)"*, and the delivered CV
contained **neither** `Jahresabschluss` nor `Wirtschaftsprüfer`. Nothing was dishonest and
no control was broken: the ledger concept is the coarse token `HGB`, the document says
"HGB" repeatedly, so every coverage instrument reported success. **The concept's
granularity is what fails** — by the time the ledger exists, the posting's sentence is
gone and only the extracted term survives, so the writer cannot know WHICH HGB evidence
answers the requirement.

Ruling W-4 takes the cheaper of #415's own two directions: carry the posting's qualifying
phrase now, leave ADR-065 decomposition to Strawberry. Two properties are load-bearing and
both are pinned here — the phrase is **verbatim** from the posting (PR #623's grounding
rule, so it cannot itself become an inflated requirement), and `jd_text=None` reproduces
today's entries **byte-for-byte** (every other caller, every persisted row and every
published #688 matrix fixture is untouched).
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.support.profile_factory import make_master_profile

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.keyword_ledger import (  # noqa: E402
    build_keyword_ledger,
    jd_qualifying_phrase,
    render_ledger_prompt_block,
)

# The posting, in the shape a scraper delivers one: a bulleted duty list, a benefits
# section, and the requirement term appearing in BOTH.
_JD_TEXT = """Financial Controller (m/w/d) — Schwarzwald Präzision GmbH

Ihre Aufgaben:
- Eigenverantwortliche Erstellung von Monats- und Jahresabschluss-Reporting (HGB) für die Unternehmensgruppe.
- Betreuung der Wirtschaftsprüfer sowie Ansprechpartner für die Steuerberatung.
- Weiterentwicklung der Kostenrechnung.

Wir bieten:
- Sicherer Umgang mit HGB.
- Flexible Arbeitszeiten.
"""

_CLASSIFICATIONS = [
    {
        "concept": "HGB",
        "status": "direct",
        "evidence": "Verantwortlich für Monatsabschluss und Reporting nach HGB",
        "surface_forms": ["HGB", "Handelsgesetzbuch"],
    },
    {
        "concept": "Kostenrechnung",
        "status": "direct",
        "evidence": "Kostenstellenrechnung für zwei Gesellschaften verantwortet",
        "surface_forms": ["Kostenrechnung"],
    },
]


# ── 1. The phrase itself ──────────────────────────────────────────────────────


def test_the_phrase_is_the_sentence_that_QUALIFIES_the_concept():
    """The selection rule, and the first draft of it had this backwards.

    "Shortest matching sentence" picks *"Sicherer Umgang mit HGB."* from the benefits
    section — which says exactly what the bare concept already said, and is therefore
    worth nothing against a defect whose whole content is "the bare token is not enough".
    The useful sentence is the one carrying the most qualifying words under the cap.
    """
    phrase = jd_qualifying_phrase("HGB", ["HGB", "Handelsgesetzbuch"], _JD_TEXT)
    assert phrase is not None
    assert "Jahresabschluss" in phrase, phrase
    assert "Sicherer Umgang" not in phrase, phrase


def test_the_phrase_is_a_verbatim_span_of_the_posting():
    """W-4's own condition (PR #623's grounding rule): a paraphrased or assembled phrase
    could itself become an inflated requirement. The only edits allowed are whitespace
    collapse and the leading list marker, which are the posting's FORMATTING."""
    phrase = jd_qualifying_phrase("HGB", ["HGB"], _JD_TEXT)
    collapsed = " ".join(_JD_TEXT.split())
    assert phrase in collapsed, phrase


def test_a_concept_the_posting_never_states_gets_no_phrase():
    """Absent, not empty — "the posting qualified it" must stay distinguishable from
    "there was nothing to quote"."""
    assert jd_qualifying_phrase("Kubernetes", ["Kubernetes"], _JD_TEXT) is None


def test_a_bare_restatement_of_the_term_is_not_a_qualifying_phrase():
    """A "sentence" that IS the term adds nothing the `concept` field does not already
    carry, and would make the writer's input view longer for no information."""
    assert jd_qualifying_phrase("HGB", ["HGB"], "HGB\nHGB.\n") is None


def test_an_over_long_paragraph_is_skipped_rather_than_truncated():
    """A truncated quote is no longer verbatim, which is the one property this whole
    feature rests on."""
    long_para = "HGB " + ("Blindtext " * 80)
    assert jd_qualifying_phrase("HGB", ["HGB"], long_para) is None


def test_a_surface_form_finds_the_sentence_when_the_concept_itself_does_not():
    jd = "Erstellung des Jahresabschlusses nach Handelsgesetzbuch für die Gruppe."
    phrase = jd_qualifying_phrase("HGB", ["HGB", "Handelsgesetzbuch"], jd)
    assert phrase is not None and "Jahresabschluss" in phrase


# ── 2. The ledger entry, and the byte-identity guarantee ─────────────────────


def test_the_claimable_entry_carries_the_phrase():
    ledger = build_keyword_ledger(
        _CLASSIFICATIONS, ["HGB", "Kostenrechnung"], [], [], jd_text=_JD_TEXT
    )
    hgb = next(e for e in ledger if e["concept"] == "HGB")
    assert "Jahresabschluss" in hgb["jd_phrase"]


def test_without_jd_text_the_ledger_is_byte_identical_to_today():
    """W-5's second requirement. The LinkedIn/agent-door callers, every persisted
    `GapAnalysis.keyword_ledger` row and every published #688 model-matrix fixture read
    this structure; a new key appearing unconditionally would invalidate all of them."""
    import json

    with_none = build_keyword_ledger(_CLASSIFICATIONS, ["HGB", "Kostenrechnung"], [], [])
    assert all("jd_phrase" not in e for e in with_none), with_none
    # And the default is genuinely the old behaviour, not a same-shaped rebuild.
    positional = build_keyword_ledger(_CLASSIFICATIONS, ["HGB", "Kostenrechnung"], [], [])
    assert json.dumps(with_none, sort_keys=True) == json.dumps(positional, sort_keys=True)


def test_a_non_claimable_entry_never_carries_the_phrase():
    """Quoting the posting's demand beside "never claim this" reads as an instruction to
    claim it — the ADR-074 shape. A gap has nothing to surface either way."""
    gap_only = [{"concept": "HGB", "status": "gap", "evidence": "", "surface_forms": ["HGB"]}]
    ledger = build_keyword_ledger(gap_only, ["HGB"], [], [], jd_text=_JD_TEXT)
    assert all("jd_phrase" not in e for e in ledger), ledger


# ── 3. The writer's input view — where the defect is actually repaired ───────


def test_the_writer_input_view_names_which_evidence_answers_the_requirement():
    """The delivery-shaped assertion. The writer was previously told only
    `- HGB [forms: HGB] — evidence: …`, from which `Jahresabschluss` is unreachable."""
    ledger = build_keyword_ledger(
        _CLASSIFICATIONS, ["HGB", "Kostenrechnung"], [], [], jd_text=_JD_TEXT
    )
    block = render_ledger_prompt_block(ledger)
    assert "the posting asks for it as:" in block
    assert "Jahresabschluss" in block


def test_the_block_is_unchanged_for_a_ledger_without_phrases():
    """Back-compatible by construction: a persisted pre-#415 ledger row renders exactly
    as it did, so a recompute is not needed to keep generating."""
    ledger = build_keyword_ledger(_CLASSIFICATIONS, ["HGB", "Kostenrechnung"], [], [])
    block = render_ledger_prompt_block(ledger)
    assert "the posting asks for it as:" not in block
    assert "- HGB [forms: HGB, Handelsgesetzbuch] — evidence:" in block


# ── 4. The SEAM: the real `services/gap.py` call path ────────────────────────
#
# W-5's first requirement. The parameter's own unit tests do not prove the call site
# passes it, and the call site is where every previous "measured but never wired" finding
# in this codebase came from.


@pytest_asyncio.fixture
async def db():
    import applire.models.application  # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.uploads  # noqa: F401
    import applire.models.user  # noqa: F401
    from applire.db.session import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


class _Provider:
    """The gap pass-2 classifier, stubbed: the LLM's judgement is not what is under
    test here — the plumbing of the posting's own text to the ledger is."""

    async def aparse_json(self, _prompt, **_kw):
        return {
            # The classifier's own field names (`requirement`/`reason`), as
            # `gap.ledger_input_from_classification` reads them — not the ledger's.
            "classifications": [
                {
                    "requirement": "HGB",
                    "status": "direct",
                    "reason": "Betreuung der Wirtschaftsprüfer im Jahresabschluss (HGB).",
                    "surface_forms": ["HGB"],
                }
            ],
            "strengths": [],
        }

    async def acomplete(self, _prompt, **_kw):  # pragma: no cover - not reached
        return ""


@pytest.mark.asyncio
async def test_the_gap_service_passes_the_postings_own_text_to_the_ledger(db):
    """Seam test: drive `analyze_gaps` and read `jd_phrase` off the PERSISTED row.

    Revert `jd_text=job.raw_text` at `services/gap.py:583` and exactly this goes red.
    """
    from applire.models.job import JobAnalysis
    from applire.models.user import User
    from applire.services.gap import analyze_gaps

    db.add(User(id=uuid.uuid4(), email="jd-phrase-415@test.com"))
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash="jdphrase415",
        raw_text=_JD_TEXT,
        role_title="Financial Controller",
        company_name="Schwarzwald Präzision GmbH",
        required_skills=["HGB"],
        nice_to_have_skills=[],
        keywords=[],
        seniority_level="mid",
        company_culture_signals=[],
        language_requirement="de",
    )
    db.add(job)
    db.add(
        make_master_profile(
            profile_json={
                "personal_info": {"name": "Katrin Hoffmann"},
                "work_experience": [
                    {
                        "id": "w1",
                        "company": "Schwarzwald Präzision GmbH",
                        "position": "Financial Controller",
                        "responsibilities": [
                            "Betreuung der Wirtschaftsprüfer im Jahresabschluss (HGB)."
                        ],
                        "achievements": [],
                    }
                ],
                "skills": [],
                "metadata": {"denied_concepts": []},
            }
        )
    )
    await db.commit()

    await analyze_gaps(job.id, db, _Provider())

    from sqlalchemy import select

    from applire.models.gap import GapAnalysis

    row = (
        await db.execute(
            select(GapAnalysis)
            .where(GapAnalysis.job_analysis_id == job.id)
            .order_by(GapAnalysis.created_at.desc())
            .limit(1)
        )
    ).scalar_one()
    hgb = next(e for e in (row.keyword_ledger or []) if e.get("concept") == "HGB")
    import json as _j
    assert "jd_phrase" in hgb, _j.dumps(hgb, ensure_ascii=False, indent=1)
    assert "Jahresabschluss" in hgb["jd_phrase"], hgb["jd_phrase"]

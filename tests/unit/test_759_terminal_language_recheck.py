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

"""#759 (ADR-076 clause 3 / ADR-038 amended 2026-10-08): the terminal-review
corrector writes AFTER the ``cv_language`` pass, so what it writes must be in the
document language — stated to it (prompt) and re-checked before it is composed.

The defect, as captured (build-2 delivery run, CV 1680cd28, records 174→176):
``cv_language`` settled a German draft (0 of 11 bullets foreign); the terminal
corrector, never told the document language and asked for "the candidate's own
terms" with English vault evidence quoted, returned two English vault sentences
and rewrote the skills list into the vault's English names. Nothing after it
translated a word.

What these tests pin:

* the TRIGGER is computed from facts with the #724 preseed's own gates — a new or
  changed prose item ``item_language_mismatch`` flags; on a cross-language vault
  any new chip (the per-item detector cannot judge a 2-word chip);
* a same-language generation is untouched (no call, byte-identical);
* the delivered document carries the TRANSLATION, not the English vault text
  (faithful translator double; the baseline arm without the re-check delivers the
  English bullet — mutation-checked by name);
* the settle guard never drops a bullet the refiner lost (ADR-072: no silent cut);
* both terminal seats are told the document language; the drafting rounds are
  byte-identical.

Every German and English string here is synthetic and was checked against
``item_language_mismatch`` before being used.
"""
from __future__ import annotations

import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.support.profile_factory import make_master_profile

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

WORK = "cd51cb57-b084-4bce-af68-3aaa59f69960"

# --- English vault (the candidate's records) ---------------------------------
EN_CAMPAIGN = (
    "Campaign development, including end-to-end seasonal campaigns across shoots, "
    "motion, web and retail materials."
)
EN_TEAM = "Built and led a four-person in-house design team for the brand."
EN_REBRAND = "Led the full rebrand of a consumer brand with the management team."

# --- German draft as the cv_language pass settled it ------------------------
DE_TEAM = "Führte ein vierköpfiges internes Designteam und verantwortete die Markenidentität."
DE_REBRAND = "Steuerte die Neupositionierung der Marke gemeinsam mit dem Management."
DE_CAMPAIGN = "Kampagnenentwicklung von der Idee bis zu den Einzelhandelsmaterialien."

# --- the faithful translator double's dictionary (EN → DE) -------------------
TRANSLATE = {
    EN_CAMPAIGN: DE_CAMPAIGN,
    "Campaign development": "Kampagnenentwicklung",
    "Team leadership & mentoring": "Teamführung und Mentoring",
    "Motion Design": "Bewegungsdesign",
}


def _en_profile() -> dict:
    return {
        "contact": {"first_name": "Milan", "last_name": "Novak",
                    "email": "milan@example.com", "phone": None, "location": "Wien",
                    "linkedin": None, "xing": None, "portfolio": None},
        "professional_summary": {"en": "Creative lead with fifteen years of agency work."},
        "work_experience": [{
            "id": WORK,
            "company": "Nivalo GmbH",
            "role": "Brand Design Lead",
            "start_date": "2022-05",
            "end_date": None,
            "responsibilities": [EN_TEAM, EN_REBRAND, EN_CAMPAIGN],
        }],
        "education": [], "languages": [], "certifications": [],
        "skills": [
            {"name": "Campaign development", "status": "confirmed"},
            {"name": "Team leadership & mentoring", "status": "confirmed"},
            {"name": "Motion Design", "status": "confirmed"},
            {"name": "Figma", "status": "confirmed"},
        ],
    }


def _de_profile() -> dict:
    p = _en_profile()
    p["professional_summary"] = {"de": "Kreativleitung mit fünfzehn Jahren Agenturerfahrung."}
    p["work_experience"][0]["responsibilities"] = [DE_TEAM, DE_REBRAND, DE_CAMPAIGN]
    p["skills"] = [
        {"name": "Kampagnenentwicklung", "status": "confirmed"},
        {"name": "Teamführung und Mentoring", "status": "confirmed"},
        {"name": "Figma", "status": "confirmed"},
    ]
    return p


def _german_draft() -> dict:
    return {
        "summary": "Kreativleitung mit fünfzehn Jahren Erfahrung in Agenturen und Inhouse-Teams.",
        "work": [{"id": WORK, "bullets": [DE_TEAM, DE_REBRAND], "projects": []}],
        "projects": [],
        "skills": ["Kampagnenentwicklung", "Teamführung und Mentoring", "Figma"],
    }


def _corrector_inserts_english(draft: dict) -> dict:
    """What the captured corrector did (record 176): the English vault sentence as
    a new bullet, and the chips replaced by the vault's English names."""
    out = {**draft, "work": [dict(w) for w in draft["work"]]}
    out["work"][0]["bullets"] = list(out["work"][0]["bullets"]) + [EN_CAMPAIGN]
    out["skills"] = ["Campaign development", "Team leadership & mentoring", "Figma",
                     "Motion Design"]
    return out


def _translate_draft(draft: dict) -> dict:
    """Faithful translator double: translates exactly the strings it knows, keeps
    every container's shape (what the language refiner is instructed to do)."""
    out = {**draft}
    out["summary"] = TRANSLATE.get(draft.get("summary", ""), draft.get("summary", ""))
    out["work"] = [
        {**w, "bullets": [TRANSLATE.get(b, b) for b in w.get("bullets") or []]}
        for w in draft.get("work") or []
    ]
    out["skills"] = [TRANSLATE.get(s, s) for s in draft.get("skills") or []]
    return out


# =============================================================================
# 1. The trigger — pure, facts only
# =============================================================================

def test_trigger_fires_on_an_english_vault_bullet_the_corrector_inserted():
    from applire.services.cv import _terminal_language_trigger

    before = _german_draft()
    after = _corrector_inserts_english(before)
    trig = _terminal_language_trigger(before, after, _en_profile(), "de")

    assert trig.fired
    assert [t for _, t in trig.foreign_prose] == [EN_CAMPAIGN]
    assert set(trig.new_skills) == {"Campaign development", "Team leadership & mentoring",
                                    "Motion Design"}
    assert trig.cross_language is True


def test_trigger_skills_half_fires_on_a_cross_language_vault_alone():
    """A 2-word English chip carries no English function word — the per-item
    detector reads it as German (#724's measured limit). On a cross-language vault
    any NEW chip fires the re-check (the preseed's vault-level gate)."""
    from applire.services.cv import _terminal_language_trigger
    from applire.utils.language_detection import item_language_mismatch

    assert not item_language_mismatch("Motion Design", "de"), "fixture premise"
    before = _german_draft()
    after = {**before, "skills": before["skills"] + ["Motion Design"]}
    trig = _terminal_language_trigger(before, after, _en_profile(), "de")

    assert trig.foreign_prose == []
    assert trig.new_skills == ["Motion Design"]
    assert trig.fired


def test_trigger_inert_on_a_same_language_vault_with_a_german_correction():
    from applire.services.cv import _terminal_language_trigger

    before = _german_draft()
    after = {**before, "work": [{**before["work"][0],
                                 "bullets": before["work"][0]["bullets"] + [DE_CAMPAIGN]}],
             "skills": before["skills"] + ["Bewegungsdesign"]}
    trig = _terminal_language_trigger(before, after, _de_profile(), "de")

    assert trig.cross_language is False
    assert not trig.fired


def test_trigger_ignores_items_the_corrector_did_not_write():
    """An English item that was ALREADY in the draft before this corrector round
    is not this round's write (the language pass already had its chance)."""
    from applire.services.cv import _terminal_language_trigger

    before = _german_draft()
    before["work"][0]["bullets"].append(EN_TEAM)
    after = {**before, "summary": "Kreativleitung mit Agentur- und Inhouse-Erfahrung seit 2010."}
    trig = _terminal_language_trigger(before, after, _en_profile(), "de")

    assert trig.foreign_prose == []
    assert not trig.fired


def test_trigger_reads_project_bullets_and_the_summary():
    from applire.services.cv import _terminal_language_trigger

    before = _german_draft()
    after = {**before, "summary": EN_REBRAND,
             "projects": [{"name": "Relaunch", "bullets": [EN_TEAM]}]}
    trig = _terminal_language_trigger(before, after, _en_profile(), "de")

    assert {w for w, _ in trig.foreign_prose} == {"summary", "projects[Relaunch]"}


# =============================================================================
# 2. The settle guard — no silent drop (ADR-072)
# =============================================================================

def test_settle_guard_keeps_the_correctors_bullets_when_the_refiner_dropped_one(caplog):
    from applire.services.cv import _settle_language_recheck

    caplog.set_level(logging.WARNING, logger="applire.services.cv")
    corrected = _corrector_inserts_english(_german_draft())
    lossy = _translate_draft(corrected)
    lossy["work"][0]["bullets"] = lossy["work"][0]["bullets"][:-1]  # merged/dropped

    settled = _settle_language_recheck(lossy, corrected)

    assert settled["work"][0]["bullets"] == corrected["work"][0]["bullets"], \
        "the entry falls back to the corrector's own text — never one bullet fewer"
    assert settled["skills"] == lossy["skills"], "an intact container keeps the translation"
    assert any("LANGUAGE_RECHECK_SETTLE_FALLBACK" in r.getMessage() for r in caplog.records)


def test_settle_guard_keeps_ids_and_skills_count():
    from applire.services.cv import _settle_language_recheck

    corrected = _corrector_inserts_english(_german_draft())
    bad = _translate_draft(corrected)
    bad["work"] = []  # the refiner lost the entry
    bad["skills"] = bad["skills"][:2]

    settled = _settle_language_recheck(bad, corrected)

    assert [w["id"] for w in settled["work"]] == [WORK]
    assert settled["work"][0]["bullets"] == corrected["work"][0]["bullets"]
    assert settled["skills"] == corrected["skills"]


def test_settle_guard_passes_a_shape_preserving_translation_through():
    from applire.services.cv import _settle_language_recheck

    corrected = _corrector_inserts_english(_german_draft())
    good = _translate_draft(corrected)
    assert _settle_language_recheck(good, corrected) == good


# =============================================================================
# 3. Through the real terminal loop (seam: services/cv.py _terminal_review)
# =============================================================================

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


async def _seed(db, profile_json):
    from applire.models.cv import GeneratedCV
    from applire.models.job import JobAnalysis

    job_id, profile_id, cv_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db.add_all([
        JobAnalysis(
            id=job_id, raw_text_hash=str(job_id), raw_text="Stelle", jd_language="de",
            role_title="Creative Director", required_skills=[], nice_to_have_skills=[],
            keywords=[], seniority_level="senior", company_culture_signals=[],
            language_requirement="de",
        ),
        make_master_profile(
            id=profile_id, profile_json=profile_json,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        GeneratedCV(
            id=cv_id, job_analysis_id=job_id, profile_id=profile_id,
            tailored_data={}, template="creative_sidebar", status="pending",
            target_pages=2, document_language="de",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
        ),
    ])
    await db.commit()
    return job_id, profile_id, cv_id


def _fake_review(calls: list, terminal_script: list):
    """Chain-dispatching ``review_and_refine`` stub.

    * ``cv_terminal_review`` — renders the reviewer prompt AND the corrector
      prompt (so both closures run), then applies the next scripted corrector
      action (the captured English insertion);
    * ``cv_language_recheck`` — the faithful translator double;
    * everything else — settles the draft untouched (the writer payload is
      already German, as ``cv_language`` left it on the captured run).
    """
    async def fake(**kwargs):
        chain = kwargs.get("chain_id")
        entry = {"chain": chain, "draft": kwargs["draft"]}
        calls.append(entry)
        if chain == "cv_terminal_review":
            entry["reviewer_prompt"] = kwargs["reviewer_prompt_fn"](kwargs["source"], kwargs["draft"])
            entry["corrector_prompt"] = kwargs["generator_prompt_fn"](
                kwargs["draft"], "fix it", kwargs["source"])
            action = terminal_script.pop(0) if terminal_script else None
            return action(kwargs["draft"]) if action else kwargs["draft"]
        if chain == "cv_language_recheck":
            return _translate_draft(kwargs["draft"])
        if chain == "cv_tailoring":
            entry["corrector_prompt"] = kwargs["generator_prompt_fn"](
                kwargs["draft"], "fix it", kwargs["source"])
        return kwargs["draft"]
    return fake


async def _run(db, profile_json, *, terminal_script, extra_patches=()):
    from contextlib import ExitStack

    from applire.services.cv import _render_cv_background

    ids = await _seed(db, profile_json)
    calls: list = []
    provider = AsyncMock()
    provider.aparse_json.return_value = _german_draft()
    patches = [
        patch("applire.services.cv.get_provider", return_value=provider),
        patch("applire.services.cv.review_and_refine",
              side_effect=_fake_review(calls, list(terminal_script))),
        patch("applire.services.cv.LLM_REVIEW_MAX_RETRIES", 2),
        patch("applire.services.cv.get_cv_html", new=AsyncMock(return_value="<html></html>")),
        patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"pdf")),
        patch("applire.services.ats_audit.extract_text_and_pages",
              new=MagicMock(side_effect=lambda pdf: ("text", 2))),
        *extra_patches,
    ]
    with patch("applire.services.cv.AsyncSessionLocal") as sl:
        sl.return_value.__aenter__.return_value = db
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            await _render_cv_background(ids[2], ids[0], ids[1], "creative_sidebar")
    from applire.models.cv import GeneratedCV

    record = await db.get(GeneratedCV, ids[2])
    return record, calls


def _delivered_bullets(record) -> list[str]:
    return [b for w in record.tailored_data["work_history"] for b in w.get("bullets") or []]


def _doc_language_check(record) -> dict:
    return next(c for c in record.ats_report["checks"] if c["id"] == "document-language")


@pytest.mark.asyncio
async def test_delivered_cv_carries_the_translation_not_the_english_vault_bullet(db):
    """The #759 delivery assertion, on the delivered document: EN vault → DE CV,
    the terminal corrector inserts the English vault sentence and the English
    chips, and the delivered CV carries neither. Baseline (re-check removed):
    the English bullet ships and ``document-language`` fails — see the mutation
    record in the WP report."""
    from applire.utils.language_detection import item_language_mismatch

    record, calls = await _run(db, _en_profile(), terminal_script=[_corrector_inserts_english])

    assert record.status == "ready"
    bullets = _delivered_bullets(record)
    assert EN_CAMPAIGN not in bullets
    assert DE_CAMPAIGN in bullets, "the fact is delivered, translated — never dropped"
    assert not [b for b in bullets if item_language_mismatch(b, "de")]
    assert "Campaign development" not in record.tailored_data["skills"]
    assert "Kampagnenentwicklung" in record.tailored_data["skills"]
    assert _doc_language_check(record)["status"] == "pass"

    rechecks = [c for c in calls if c["chain"] == "cv_language_recheck"]
    assert len(rechecks) == 1, "one re-check for the one corrector round that wrote English"
    assert EN_CAMPAIGN in rechecks[0]["draft"]["work"][0]["bullets"], \
        "the re-check received the corrector's output, before composition"


@pytest.mark.asyncio
async def test_rechecked_draft_is_what_the_next_terminal_round_reviews(db):
    """Position: the re-check runs INSIDE the loop, so the re-entered terminal
    round reviews the translated document, not the English one."""
    _record, calls = await _run(db, _en_profile(), terminal_script=[_corrector_inserts_english])

    terminal = [c for c in calls if c["chain"] == "cv_terminal_review"]
    assert len(terminal) == 2
    subject = terminal[1]["reviewer_prompt"].split("COMPOSED CV (the delivered document):")[1]
    assert DE_CAMPAIGN in subject and EN_CAMPAIGN not in subject


@pytest.mark.asyncio
async def test_same_language_generation_spends_no_recheck(db):
    """A German vault feeding a German document, corrector writes German: no
    re-check call — the pipeline is exactly what it was."""
    def german_fix(draft):
        out = {**draft, "work": [dict(w) for w in draft["work"]]}
        out["work"][0]["bullets"] = list(out["work"][0]["bullets"]) + [DE_CAMPAIGN]
        return out

    record, calls = await _run(db, _de_profile(), terminal_script=[german_fix])

    assert not [c for c in calls if c["chain"] == "cv_language_recheck"]
    assert DE_CAMPAIGN in _delivered_bullets(record)


@pytest.mark.asyncio
async def test_terminal_seats_are_told_the_document_language(db):
    """Prompt half: the terminal corrector and the terminal reviewer both carry
    the DOCUMENT LANGUAGE; the drafting rounds' corrector prompt does not
    (byte-identical to what shipped)."""
    _record, calls = await _run(db, _en_profile(), terminal_script=[])

    terminal = next(c for c in calls if c["chain"] == "cv_terminal_review")
    assert "DOCUMENT LANGUAGE: German" in terminal["corrector_prompt"]
    assert "never copy a sentence or a skill name" in terminal["corrector_prompt"]
    assert "DOCUMENT LANGUAGE: German" in terminal["reviewer_prompt"]

    drafting = [c for c in calls if c["chain"] == "cv_tailoring" and "corrector_prompt" in c]
    assert drafting, "the drafting loop ran"
    assert "DOCUMENT LANGUAGE" not in drafting[0]["corrector_prompt"]


@pytest.mark.asyncio
async def test_settle_guard_is_wired_at_the_recheck_call_site(db):
    """Seam test for the settle guard's ONE call site (`_terminal_language_recheck`
    → `_review_cv_language(settle_guard=…)`): a refiner that merges the new bullet
    away must not cost the delivered document a fact. The fake applies whatever
    `settle_guard` the real call site passed, exactly as `review_and_refine`'s
    `_settle` does."""
    calls: list = []

    def lossy_then_guard(**kwargs):
        lossy = _translate_draft(kwargs["draft"])
        lossy["work"][0]["bullets"] = lossy["work"][0]["bullets"][:-1]
        guard = kwargs.get("settle_guard")
        return guard(lossy, []) if guard else lossy

    base = _fake_review(calls, [_corrector_inserts_english])

    async def fake(**kwargs):
        if kwargs.get("chain_id") == "cv_language_recheck":
            calls.append({"chain": "cv_language_recheck", "draft": kwargs["draft"]})
            return lossy_then_guard(**kwargs)
        return await base(**kwargs)

    record, _ = await _run(
        db, _en_profile(), terminal_script=[],
        extra_patches=[patch("applire.services.cv.review_and_refine", side_effect=fake)],
    )

    assert EN_CAMPAIGN in _delivered_bullets(record), \
        "the refiner lost the bullet; the guard keeps the corrector's text (reported, not cut)"
    assert _doc_language_check(record)["status"] == "fail", \
        "and the document-language check reports it"


# =============================================================================
# 4. The #376 skills-list gap guard on a cross-language document
# =============================================================================

def _ledger_pitches() -> list[dict]:
    return [{
        "concept": "Client presentations & pitches", "surface_forms": ["Pitches"],
        "claimable": True, "status": "direct", "sources": ["required"], "fit_weight": 1.0,
        "evidence": "Won 4 of 6 new-business pitches as pitch creative lead.",
    }]


def _profile_with_pitches(*, english: bool = True) -> dict:
    p = _en_profile() if english else _de_profile()
    p["skills"].append({"name": "Client presentations & pitches", "status": "confirmed"})
    return p


def _tailored_narrating_pitches():
    from applire.schemas.cv import TailoredCVData

    return TailoredCVData.model_validate({
        "contact": {"name": "Milan Novak"},
        "work_history": [{"id": WORK, "company": "Nivalo GmbH", "role": "Brand Design Lead",
                          "bullets": ["Gewann als Pitch Creative Lead vier von sechs Pitches."]}],
        "skills": ["Kampagnenentwicklung"],
    })


def test_376_guard_adds_the_narrated_form_not_the_vault_concept_on_a_cross_language_cv():
    """Found by the deterministic replay of CV 1680cd28 on the fixed tree: once the
    re-check translated the corrector's chips, the #376 guard re-added the ledger
    concept in the VAULT's language ("Client presentations & pitches") to the
    German CV. On a cross-language document the chip is the narrated string."""
    from applire.services.cv import _restore_narrative_named_skills

    out = _restore_narrative_named_skills(
        _tailored_narrating_pitches(), _profile_with_pitches(), _ledger_pitches(),
        document_language="de",
    )
    assert "Client presentations & pitches" not in out.skills
    assert "Pitches" in out.skills


def test_376_guard_keeps_concept_name_first_on_a_same_language_cv():
    """F-9's rule is untouched where the vault and the document share a language
    (here: no document language stated — the pre-#759 call shape)."""
    from applire.services.cv import _restore_narrative_named_skills

    out = _restore_narrative_named_skills(
        _tailored_narrating_pitches(), _profile_with_pitches(), _ledger_pitches(),
    )
    assert "Client presentations & pitches" in out.skills

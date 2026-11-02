# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-099 am. 2026-11-02 (founder UAT-3, rulings M-1a…d; SF-VAULT.23/.26/.27):
"Zusammenführen" across two languages translates FIRST, and two differing
values become a question. Synthetic data only."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.prompts.profile_decisions import (  # noqa: E402
    DUPLICATES_SYSTEM,
    MERGE_SYSTEM,
    PIVOT_SYSTEM,
    build_merge_prompt,
)
from applire.services.profile import decisions  # noqa: E402
from applire.services.profile.merge_verifier import (  # noqa: E402
    choice_violations,
    invariant_tokens,
    merge_violations,
    value_in,
)
from tests.support.profile_factory import make_master_profile  # noqa: E402

DE = "Leitung eines Teams von 5 Entwicklern im Zahlungsverkehr innerhalb von 7 Monaten"
EN = "Delivered the PSD2 payment platform with the SAP team in 7 months."
PIVOT = "Lieferung der PSD2-Zahlungsplattform mit dem SAP-Team in 7 Monaten."
MERGED_DE = ("Leitung eines Teams von 5 Entwicklern im Zahlungsverkehr und Lieferung der "
             "PSD2-Zahlungsplattform mit dem SAP-Team innerhalb von 7 Monaten")
EN8 = "Delivered the PSD2 payment platform with the SAP team in 8 months."
PIVOT8 = "Lieferung der PSD2-Zahlungsplattform mit dem SAP-Team in 8 Monaten."


class Script:
    """A provider that answers per system prompt, in order; records the calls."""

    def __init__(self, **by_system):
        self.queues = {k: list(v) for k, v in by_system.items()}
        self.calls: list[str] = []
        self.prompts: list[str] = []

    async def aparse_json(self, prompt, *, system=None, **kw):
        key = {MERGE_SYSTEM: "merge", PIVOT_SYSTEM: "pivot", DUPLICATES_SYSTEM: "dups"}[system]
        self.calls.append(key)
        self.prompts.append(prompt)
        q = self.queues.get(key) or []
        if not q:
            raise AssertionError(f"unexpected {key} call")
        reply = q.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


# ── the deterministic facts ───────────────────────────────────────────────────


def test_the_shipped_verifier_cannot_accept_a_translated_merge():
    """The defect this amendment exists for: a German merge of an English
    bullet carries translated words that no source writes."""
    assert merge_violations(MERGED_DE, [DE, EN]) != []
    assert merge_violations(MERGED_DE, [DE, PIVOT]) == []


@pytest.mark.parametrize(
    "a, b, equal",
    [
        ("Developed a strategy with the QC-LIMS vendor, 8-month completion.",
         "Strategie mit dem QC-LIMS-Anbieter entwickelt, Abschluss nach 8 Monaten.", True),
        (EN, PIVOT, True),
        ("Saved 7,500 EUR at BioNTech.", "7.500 EUR bei BioNTech eingespart.", True),
        (EN, "Lieferung der Zahlungsplattform mit dem SAP-Team in 7 Monaten.", False),  # PSD2 dropped
        (EN, "Lieferung der PSD2-Plattform mit dem SAP-Team in 9 Monaten.", False),  # number changed
        (EN, "Lieferung der PSD2-Plattform mit dem SAP-Team und ACME in 7 Monaten.", False),  # name added
    ],
)
def test_invariant_tokens_survive_only_a_faithful_translation(a, b, equal):
    assert (invariant_tokens(a) == invariant_tokens(b)) is equal


def test_a_capitalised_german_noun_is_not_an_invariant():
    assert invariant_tokens("Lieferung der Plattform") == set()


def test_value_in_reads_numbers_or_the_whole_wording():
    assert value_in("8 Monaten", "achieving 8-month project completion")
    assert not value_in("9 Monaten", "achieving 8-month project completion")
    assert value_in("Leipzig", "Standort Leipzig aufgebaut")
    assert not value_in("Dresden", "Standort Leipzig aufgebaut")
    # the digit inside a name is not a value
    assert not value_in("2 years", "Delivered the PSD2 platform")
    assert value_in("7,500 EUR", "saved 7.500 EUR")


def test_choice_violations():
    s7 = "Lieferung der Plattform in 7 Monaten."
    assert choice_violations(s7, ["7 Monaten"], rejected="8 Monaten") == []
    # the rejected number kept → refused
    assert choice_violations("Lieferung in 7 Monaten, Abschluss nach 8 Monaten.",
                             ["7 Monaten"], rejected="8 Monaten") == ["8"]
    # the chosen value missing → refused
    assert choice_violations("Lieferung der Plattform.", ["7 Monaten"], rejected="8 Monaten") == ["7 Monaten"]
    # "both": every value must stay
    assert choice_violations(s7, ["7 Monaten", "8 Monaten"]) == ["8 Monaten"]
    # a non-numeric rejected value: its wording
    assert choice_violations("Standort Leipzig aufgebaut.", ["Dresden"], rejected="Leipzig") == [
        "Dresden", "Leipzig"]


def test_the_prompt_carries_the_candidates_pick():
    p = build_merge_prompt(DE, EN8, "Lead @ Pay", {"kind": "value", "value": "7 Monaten", "other": "8 months"})
    assert '"7 Monaten" is right and "8 months" is wrong' in p
    p = build_merge_prompt(DE, EN8, None, {"kind": "both", "values": ["7 Monaten", "8 months"]})
    assert "both right" in p


# ── propose_merge: the rounds ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_same_language_pass_costs_one_call():
    good = "Leitung eines Teams von 5 Entwicklern im Zahlungsverkehr"
    s = Script(merge=[{"merged": good}])
    p = await decisions.propose_merge(s, DE, "Zahlungsverkehr: Leitung eines Teams von 5 Entwicklern")
    assert (p.proposal, p.verifier, s.calls) == (good, "passed", ["merge"])


@pytest.mark.asyncio
async def test_cross_language_translates_first_then_merges():
    s = Script(
        merge=[{"merged": None}, {"merged": MERGED_DE}],
        pivot=[{"text": PIVOT}],
    )
    p = await decisions.propose_merge(s, DE, EN, "Lead @ Pay")
    assert (p.proposal, p.verifier) == (MERGED_DE, "passed")
    assert s.calls == ["merge", "pivot", "merge"]
    assert f"Bullet 2: {PIVOT}" in s.prompts[2]  # round 2 merges the same-language pair
    assert p.sources == (DE, EN)  # the candidate still sees the originals


@pytest.mark.asyncio
async def test_a_round_one_sentence_failing_the_check_also_translates_first():
    s = Script(merge=[{"merged": MERGED_DE}, {"merged": MERGED_DE}], pivot=[{"text": PIVOT}])
    p = await decisions.propose_merge(s, DE, EN)
    assert (p.verifier, s.calls) == ("passed", ["merge", "pivot", "merge"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pivot_reply",
    [
        {"text": EN},  # unchanged → the same language, nothing to translate
        {"text": "Lieferung der Zahlungsplattform mit dem SAP-Team in 7 Monaten."},  # PSD2 dropped
        {"text": None},
        RuntimeError("down"),
    ],
)
async def test_a_pivot_that_is_not_a_faithful_translation_ends_the_merge(pivot_reply):
    s = Script(merge=[{"merged": None}], pivot=[pivot_reply])
    p = await decisions.propose_merge(s, DE, EN)
    assert (p.proposal, p.verifier, p.question) == (None, "failed", None)
    assert s.calls == ["merge", "pivot"]  # no second merge on an unfaithful pivot


@pytest.mark.asyncio
async def test_round_two_is_verified_against_the_pivot_not_trusted():
    s = Script(
        merge=[{"merged": None}, {"merged": MERGED_DE + " in Frankfurt"}],
        pivot=[{"text": PIVOT}],
    )
    p = await decisions.propose_merge(s, DE, EN)
    assert (p.proposal, p.verifier) == (None, "failed")


# ── the value question (M-1c) ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_differing_values_become_a_question_in_one_call():
    s = Script(merge=[{"merged": None, "differing": ["7 Monaten", "8 months"]}])
    p = await decisions.propose_merge(s, DE, EN8)
    assert (p.proposal, p.question, s.calls) == (None, ("7 Monaten", "8 months"), ["merge"])


@pytest.mark.asyncio
async def test_the_same_number_worded_twice_is_never_asked():
    """Measured on gpt-6-luna (4/5): "innerhalb von 7 Monaten" vs "7-month
    project completion" was offered as a choice although both say 7."""
    s = Script(
        merge=[{"merged": None, "differing": ["innerhalb von 7 Monaten", "7-month project completion"]},
               {"merged": MERGED_DE}],
        pivot=[{"text": PIVOT}],
    )
    en7 = "Delivered the PSD2 payment platform with the SAP team, 7-month project completion."
    p = await decisions.propose_merge(s, DE, en7)
    assert p.question is None and s.calls == ["merge", "pivot", "merge"]


@pytest.mark.asyncio
async def test_a_value_no_bullet_writes_is_never_asked():
    s = Script(merge=[{"merged": None, "differing": ["7 Monaten", "9 months"]}], pivot=[{"text": EN8}])
    p = await decisions.propose_merge(s, DE, EN8)
    assert p.question is None and p.verifier == "failed"


@pytest.mark.asyncio
async def test_the_question_can_come_from_the_translated_round():
    s = Script(
        merge=[{"merged": None}, {"merged": None, "differing": ["7 Monaten", "8 Monaten"]}],
        pivot=[{"text": PIVOT8}],
    )
    p = await decisions.propose_merge(s, DE, EN8)
    assert p.question == ("7 Monaten", "8 Monaten")


@pytest.mark.asyncio
async def test_a_value_pick_writes_only_the_chosen_value():
    choice = {"kind": "value", "value": "7 Monaten", "other": "8 Monaten"}
    keeps_both = MERGED_DE + " und in 8 Monaten"
    s = Script(merge=[{"merged": keeps_both}, {"merged": keeps_both}], pivot=[{"text": PIVOT8}])
    assert (await decisions.propose_merge(s, DE, EN8, choice=choice)).verifier == "failed"
    s = Script(merge=[{"merged": None}, {"merged": MERGED_DE}], pivot=[{"text": PIVOT8}])
    p = await decisions.propose_merge(s, DE, EN8, choice=choice)
    assert (p.proposal, p.verifier) == (MERGED_DE, "passed")
    assert '"7 Monaten" is right' in s.prompts[0]


@pytest.mark.asyncio
async def test_a_both_pick_keeps_both_values():
    choice = {"kind": "both", "values": ["7 Monaten", "8 Monaten"]}
    both = MERGED_DE + " und in 8 Monaten"
    s = Script(merge=[{"merged": None}, {"merged": MERGED_DE}], pivot=[{"text": PIVOT8}])
    assert (await decisions.propose_merge(s, DE, EN8, choice=choice)).verifier == "failed"
    s = Script(merge=[{"merged": None}, {"merged": both}], pivot=[{"text": PIVOT8}])
    assert (await decisions.propose_merge(s, DE, EN8, choice=choice)).proposal == both


@pytest.mark.asyncio
async def test_after_a_pick_the_model_does_not_ask_again():
    choice = {"kind": "value", "value": "7 Monaten", "other": "8 months"}
    s = Script(merge=[{"merged": None, "differing": ["7 Monaten", "8 months"]}], pivot=[{"text": EN8}])
    p = await decisions.propose_merge(s, DE, EN8, choice=choice)
    assert (p.question, p.verifier) == (None, "failed")


# ── U-11 entry merge (M-1d): translate-first, never the question ──────────────


@pytest.mark.asyncio
async def test_entry_merge_pair_without_a_proposal_is_translated_first():
    s = Script(
        dups=[{"pairs": [{"t": "T1", "s": "S1", "merged": None}]}],
        pivot=[{"text": PIVOT}],
        merge=[{"merged": MERGED_DE}],
    )
    pairs = await decisions.find_duplicate_pairs(s, [DE], [EN])
    assert [(p.target_index, p.source_index, p.proposal) for p in pairs] == [(0, 0, MERGED_DE)]
    assert s.calls == ["dups", "pivot", "merge"]


@pytest.mark.asyncio
async def test_entry_merge_never_asks():
    s = Script(
        dups=[{"pairs": [{"t": "T1", "s": "S1", "merged": None}]}],
        pivot=[{"text": PIVOT8}],
        merge=[{"merged": None, "differing": ["7 Monaten", "8 Monaten"]}],
    )
    pairs = await decisions.find_duplicate_pairs(s, [DE], [EN8])
    assert [p.proposal for p in pairs] == [None]


# ── the doors: a forged pick is refused, the response carries the question ────


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base  # noqa: F401
    import applire.models  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _seed(db):
    profile = {
        "personal_info": {"name": "Erika Probe", "email": "erika@example.de", "location": "Köln"},
        "work_experience": [{
            "id": "w1", "company": "Lumen Pay", "role": "Lead",
            "start_date": "2019-01", "end_date": "2023-06", "achievements": [DE],
        }],
        "metadata": {"pending_conflicts": [{
            "conflict_id": "c1", "section": "work_experience", "field": "achievements",
            "entity_id": "w1", "existing_value": DE, "incoming_value": EN8,
            "source": "cv_upload", "resolved": False,
        }], "enrichment_history": []},
    }
    db.add(make_master_profile(profile_json=profile))
    await db.commit()


@pytest.mark.asyncio
async def test_a_pick_the_stored_items_do_not_hold_is_refused(db):
    from applire.services.profile import propose_conflict_merge

    await _seed(db)
    forged = {"kind": "value", "value": "3 Monaten", "other": "8 months"}
    with pytest.raises(ValueError, match="choice_not_in_sources"):
        await propose_conflict_merge("c1", db, Script(), choice=forged)


@pytest.mark.asyncio
async def test_the_rest_door_returns_the_question(db, monkeypatch):
    from applire.routers import profile as router
    from applire.schemas.profile import ConflictMergeProposalRequest, MergeChoice

    await _seed(db)
    s = Script(merge=[{"merged": None, "differing": ["7 Monaten", "8 months"]}])
    monkeypatch.setattr(router, "_uid", lambda _u: None)
    r = await router.conflict_merge_proposal("c1", None, db=db, current_user=None, provider=s)
    assert r.question is not None and r.question.values == ["7 Monaten", "8 months"]
    assert r.proposal is None

    s = Script(merge=[{"merged": DE}])
    body = ConflictMergeProposalRequest(choice=MergeChoice(kind="value", value="7 Monaten", other="8 months"))
    r = await router.conflict_merge_proposal("c1", body, db=db, current_user=None, provider=s)
    assert r.verifier == "passed" and '"7 Monaten" is right' in s.prompts[0]

    from fastapi import HTTPException

    body = ConflictMergeProposalRequest(choice=MergeChoice(kind="both", values=["7 Monaten", "2 years"]))
    with pytest.raises(HTTPException) as exc:
        await router.conflict_merge_proposal("c1", body, db=db, current_user=None, provider=Script())
    assert exc.value.status_code == 422

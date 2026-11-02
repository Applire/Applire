# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-099 cl. 11 / SF-VAULT.23 — the decision helpers around the profile
hub's merge / typed-answer calls. (The fact-presence verifier these tests once
pinned was removed by ADR-099 am. 2026-11-02, M-1e.)"""
from __future__ import annotations

import json

import pytest

from applire.prompts.reconcile import RECONCILE_SYSTEM_PROMPT
from applire.services.profile import decisions

A = "Introduced a supplier scorecard for vendor selection and platform design."
B = "Merged 12 reporting tools into one central lakehouse, streamlining vendor selection and platform scalability."


def test_conflict_shape_per_field():
    assert decisions.conflict_shape("work_experience", "achievements", "w1") == (
        "list", ["keep_current", "take_new", "keep_both", "merge"])
    # RULING A1-i1 — an entry identity field may be two entries
    assert decisions.conflict_shape("work_experience", "role", "w1") == (
        "scalar", ["keep_current", "take_new", "keep_both"])
    assert decisions.conflict_shape("work_experience", "team_size", "w1") == (
        "scalar", ["keep_current", "take_new"])
    assert decisions.conflict_shape("personal_info", "email", None) == (
        "scalar", ["keep_current", "take_new"])


class _Stub:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    async def aparse_json(self, prompt, **kw):
        self.calls += 1
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


@pytest.mark.asyncio
async def test_propose_merge_is_one_call_and_offers_the_models_sentence():
    """ADR-099 am. 2026-11-02 (M-1e): no code check of the sentence, no retry."""
    stub = _Stub({"merged": "Merged 12 reporting tools and introduced a supplier scorecard."})
    p = await decisions.propose_merge(stub, A, B, "Head @ Lumen")
    assert (p.verifier, stub.calls) == ("passed", 1)


@pytest.mark.asyncio
async def test_propose_merge_passes_a_clean_sentence():
    good = "Introduced a supplier scorecard and merged 12 reporting tools into one central lakehouse."
    p = await decisions.propose_merge(_Stub({"merged": good}), A, B)
    assert (p.proposal, p.verifier) == (good, "passed")


@pytest.mark.asyncio
async def test_propose_merge_provider_error_is_a_failed_proposal():
    p = await decisions.propose_merge(_Stub(RuntimeError("down")), A, B)
    assert p.verifier == "failed" and p.proposal is None


@pytest.mark.parametrize(
    "model_reply, expect",
    [
        ({"outcome": "keep_both"}, decisions.ReplyReading(outcome="keep_both")),
        # an outcome NOT offered for this dispute is never taken
        ({"outcome": "merge"}, None),
        ({"corrected_value": "2020-03"}, decisions.ReplyReading(corrected_value="2020-03")),
        # a value the model composed (not in the reply) is never written
        ({"corrected_value": "2020-04"}, None),
        ({"follow_up": "Meinst du X oder Y?"}, decisions.ReplyReading(follow_up="Meinst du X oder Y?")),
    ],
)
@pytest.mark.asyncio
async def test_interpret_reply_keeps_only_verifiable_answers(model_reply, expect):
    r = await decisions.interpret_reply(
        _Stub(model_reply), reply="Nein, es war 2020-03 — beide stimmen nicht ganz",
        entry_label="Dev @ Acme", field="end_date", existing="2019-12", incoming="2020-01",
        outcomes=["keep_current", "take_new", "keep_both"], lang="de",
    )
    if expect is None:
        assert r.outcome is None and r.corrected_value is None and r.follow_up
    else:
        assert r == expect


@pytest.mark.asyncio
async def test_find_duplicate_pairs_verifies_each_proposal_and_drops_bad_indices():
    stub = _Stub({"pairs": [
        {"t": "T1", "s": "S1", "merged": "Led the team of six engineers."},
        {"t": "T2", "s": "S9", "merged": None},
        {"t": "T1", "s": "S2", "merged": None},  # T1 already used
    ]})
    pairs = await decisions.find_duplicate_pairs(
        stub, ["Led the team of six engineers.", "Owned ETL."], ["Leitete das Team (sechs Engineers).", "x"])
    assert len(pairs) == 1 and pairs[0].target_index == 0 and pairs[0].source_index == 0
    assert pairs[0].proposal == "Led the team of six engineers."


def test_reconcile_rule_13_carries_the_conflict_test():
    """U-1 (SF-PROFILE.21): the measured wording is in the prompt the model
    gets (the measurement itself is in the version header, not here)."""
    assert "THE TEST BEFORE EVERY BULLET CONFLICT" in RECONCILE_SYSTEM_PROMPT
    assert "two DIFFERENT pieces of work that share words" in RECONCILE_SYSTEM_PROMPT
    assert "a target and a result" in RECONCILE_SYSTEM_PROMPT


def test_synthetic_u1_fixture_has_the_shape():
    fx = json.load(open("tests/files/reconcile_list_conflict/shape_u1.json"))
    for key in ("profile", "incoming", "profile_large", "incoming_large", "control"):
        assert key in fx


def test_decision_prompts_carry_the_measured_rules():
    """The wording measured on gpt-6-luna (2026-10-28, report §Evidence) is
    what ships: every system prompt names JSON (OpenAI JSON mode 400s
    otherwise); the reply prompt fixes the value order; the duplicates
    prompt asks for one language and every fact."""
    from applire.prompts import profile_decisions as pd

    for system in (pd.MERGE_SYSTEM, pd.REPLY_SYSTEM, pd.IDENTITY_SYSTEM, pd.DUPLICATES_SYSTEM):
        assert "JSON" in system
    assert '"the first"\nmeans CURRENT' in pd.REPLY_SYSTEM
    assert "the language of the T bullet" in pd.DUPLICATES_SYSTEM
    assert "keeps EVERY fact of both" in pd.DUPLICATES_SYSTEM

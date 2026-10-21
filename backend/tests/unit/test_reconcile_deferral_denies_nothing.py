# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hazelnut build-2 delivery F1 — "Zur OEE-Steuerung sage ich gleich mehr, wenn es um
unser MES geht" was recorded as the denial "OEE-Steuerung"; the ledger floor then
sank the JD's "OEE" (a whole word inside the denied compound) and the signature result
"OEE 61 % → 73 %" reached neither document.

Prompt-first, category B: rule 9's "A DENIAL NAMES AN ITEM, NEVER MISSING EVIDENCE"
covered a sentence withholding more evidence about what was STATED, not one postponing
an item to a later answer. Replay of the verbatim captured reconcile prompts
(gpt-5.6-luna, Requesty, thinking OFF): the captured deferral 15/30 → 0/10, a held-out
synthetic deferral 12/30 → 0/10; plain-denial controls kept on every replay (the version
header in prompts/reconcile.py carries the full numbers and the accepted side effect).
"""
from applire.prompts.reconcile import RECONCILE_SYSTEM_PROMPT


def _rule() -> str:
    i = RECONCILE_SYSTEM_PROMPT.index("A DENIAL NAMES AN ITEM, NEVER MISSING EVIDENCE")
    return RECONCILE_SYSTEM_PROMPT[i:RECONCILE_SYSTEM_PROMPT.index("A DENIAL WITH AN EXCEPTION", i)]


def test_rule_9_says_a_postponed_item_denies_nothing():
    rule = " ".join(_rule().split())
    assert "or that only postpones an item to a later answer" in rule
    assert "denies nothing" in rule.split("postpones", 1)[1]


def test_the_deferral_examples_are_held_out_of_the_captured_case():
    rule = _rule().lower()
    for captured in ("oee", "gleich mehr", "mes geht", "kafka"):
        assert captured not in rule

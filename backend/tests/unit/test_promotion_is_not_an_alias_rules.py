# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Founder ruling V-3 = A (Hazelnut build 2, 783:L88): a promotion is not an alias.

The fold was the model following the prompt: "multiple titles within the same employer
→ ONE entry, most senior title as role, others in role_aliases" turned a LinkedIn
export's separately dated "Senior Designer 2014-03–2015-12" + "Art Director
2016-01–2018-07" into "Art Director 2014-03–2018-07" (+ alias), and the CV then
claimed the senior title from 2014. Prompt-first: these rules are the fix. Replay on
the persisted input (gpt-5.6-luna, n=5 per arm): dated titles kept apart 0/5 → 5/5,
a one-span progression kept verbatim 0/5 → 5/5, a partly-covering dated title kept as
its own entry in reconcile 1/5 → 5/5. This test pins that the rules stay in every
prompt that writes or merges work entries.
"""
from applire.prompts.cv_extraction import GENERIC_CV_EXTRACTION_PROMPT
from applire.prompts.cv_extraction_segmented import __dict__ as _seg
from applire.prompts.profile_extraction import SYSTEM_PROMPT as PROFILE_EXTRACTION_PROMPT
from applire.prompts.reconcile import RECONCILE_SYSTEM_PROMPT


def _segmented_texts() -> str:
    return "\n".join(v for v in _seg.values() if isinstance(v, str))


def test_both_extraction_prompts_say_a_promotion_is_not_an_alias():
    for prompt in (GENERIC_CV_EXTRACTION_PROMPT, PROFILE_EXTRACTION_PROMPT, _segmented_texts()):
        assert "A PROMOTION IS NOT AN ALIAS" in prompt
        assert "verbatim" in prompt and "never the senior title alone" in prompt


def test_the_old_fold_instruction_is_gone():
    for prompt in (GENERIC_CV_EXTRACTION_PROMPT, PROFILE_EXTRACTION_PROMPT, _segmented_texts()):
        assert "Never create a separate entry per title" not in prompt
        assert "count the distinct employer positions" not in prompt


def test_reconcile_asks_about_a_partly_covering_differently_titled_position():
    """Founder ruling V-6 = B: neither fold it in (the old behaviour) nor add it
    silently (V-3 as first built — three entries for one employer): ask, with the
    existing merge / add-separate choices the V-5 engagement applier resolves."""
    i = RECONCILE_SYSTEM_PROMPT.index("A PROMOTION IS NOT A DIFFERENT TITLE")
    rule = RECONCILE_SYSTEM_PROMPT[i:RECONCILE_SYSTEM_PROMPT.index("8. CURRENT POSITION", i)]
    assert "covers only PART of the existing entry's span" in rule
    assert "request_confirmation" in rule and '["merge", "add_separate"]' in rule
    assert '"existing_ids"' in rule and '"incoming"' in rule

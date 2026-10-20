# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""783:L43 — the interview reconciler appended a restatement bullet per answer
(the founder's Applire role held 22 bullets after a 12-question interview).

Measured on captured October interview reconcile calls (gpt-5.6-luna): 83 of 250
bullets added to an existing entity restate one it already holds. Rule 7 forbade
add_bullets "merely to restate", but the add_bullets op itself said nothing, and an
answer that re-anchors a fact reads as new. Replay of six such calls with an
out-of-sample worked example (n=5/arm): restated bullets 39 -> 17, other bullets
40 -> 37, already_known 3 -> 7. Partial: ADR-082 forbids a deterministic delete, so
the rest stays a collector line. This pins the clause on the op.
"""
from applire.prompts.reconcile import RECONCILE_SYSTEM_PROMPT


def test_the_add_bullets_op_says_an_existing_bullet_is_not_added_again():
    i = RECONCILE_SYSTEM_PROMPT.index("- add_bullets")
    j = RECONCILE_SYSTEM_PROMPT.index("- upsert_skill", i)
    block = RECONCILE_SYSTEM_PROMPT[i:j]
    assert "NOT A SECOND TIME (783:L43)" in block
    assert '"empty_reason": "already_known"' in block
    # the worked example must stay out of the replay set (delivery-2/-3 Weberit data)
    assert "Termintreue" not in block

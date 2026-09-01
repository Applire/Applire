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

"""ADR-080 — the interview's question budget, derived from its gap plan.

This module holds the ONE derivation every mode plan shares (targeted, guided,
profile-review, Mode C enrich). Before ADR-080 there were three: a flat constant
for targeted/guided and `len(gaps) * 3` in the enrich router — the ADR-066 class,
three implementations of one responsibility that disagreed.
"""

from applire.constants import INTERVIEW_MAX_QUESTIONS_PER_GAP

# ADR-080 clause 2. The budget must EXCEED the worst-case question count, and
# the worst case is `1 + per_gap * n`:
#
#   * `questions_asked` is 1 the moment the session exists — the opening
#     question is counted before any answer;
#   * each of the `n` gaps then costs at most `per_gap` answers (its question
#     plus, today, one follow-up or denial probe — INTERVIEW_MAX_QUESTIONS_PER_GAP).
#
# So the final turn of a fully-worked interview lands on `questions_asked ==
# 1 + per_gap * n`, and `send_message` tests `questions_asked >= hard_ceiling`
# BEFORE the turn's advance/sufficiency decision. A budget equal to that value
# therefore reports `max_questions_reached` for a session that in fact answered
# everything — telling the candidate a limit stopped an interview that finished.
#
# This is measured, not derived: at `+1` the simulation closes every gap and
# still completes with `max_questions_reached` at n = 5, 8 and 12; at `+2` the
# same runs complete with `gaps_resolved`. Hence two, not one, and it is a
# correctness term rather than slack — see test_interview_budget.py, which
# asserts the property against the real loop rather than against this constant.
_TERMINAL_HEADROOM = 2


def derive_hard_ceiling(
    gap_count: int,
    *,
    per_gap: int = INTERVIEW_MAX_QUESTIONS_PER_GAP,
    cap: int | None = None,
) -> int:
    """The question budget for a session that must work through ``gap_count`` gaps.

    ``gap_count`` is the length of the plan the session will actually walk —
    i.e. ``critical_gaps`` AFTER ``filter_answered_concepts`` and INCLUDING any
    prepended US163 gate clusters — never the raw cluster count off the analysis.

    ``cap`` is the operator's ceiling (``INTERVIEW_MAX_QUESTIONS_TARGETED`` /
    ``_GUIDED``). Since ADR-080 clause 4 it is an upper bound applied AFTER the
    derivation, not the budget itself: a self-hoster on a tight provider bill can
    still buy a shorter interview, and doing so knowingly reintroduces truncation.
    ``None`` means uncapped.

    A ``gap_count`` of 0 still yields a positive budget. Such a session is
    recorded ``complete`` at creation and never reaches the ceiling check, but
    returning 0 would make the first turn of any session that somehow did reach
    it complete instantly on ``questions_asked >= 0``.
    """
    ceiling = per_gap * max(gap_count, 0) + _TERMINAL_HEADROOM
    if cap is not None:
        # A cap below the headroom would make the session terminate on its own
        # opening question; one question is the floor whatever the operator set.
        ceiling = max(min(ceiling, cap), 2)
    return ceiling

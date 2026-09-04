# Copyright (C) 2026 Tobias Rosenbaum
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

"""ADR-083 clause 4 — the reviewer's structured findings reach the corrector.

``services/reviewer.py``'s shared ADR-021 loop (used by all five of
``cv_tailoring``, ``cv_language``, ``cv_terminal_review``, ``cover_letter`` and
``letter_terminal_review``) parses every reviewer verdict into normalized
``ReviewIssue`` objects (``services/review_issues.py``) but, before this module
existed, only ever forwarded the verdict's free-text ``feedback`` string to the
corrector — the structured ``issues[]`` fed exactly one log line and the #306(a)/
#537 measurement passes and were then discarded. A real-provider replay measured
the consequence: a finding the reviewer raised 5 times out of 5 reached
``feedback`` only 2 times out of 5 — the reviewer's own prose summary is not a
reliable transcript of its own structured verdict.

This module renders the loop's already-normalized issues into a block and folds
it into the ``feedback`` string at the ONE shared call site in
``review_and_refine`` (ADR-066: one implementation, never five per-chain
copies) — ``generator_prompt_fn``'s ``(draft, feedback, source)`` signature is
unchanged; only the VALUE of the middle argument gains a section.

Design constraints, each inherited from an existing rule already governing this
loop or this codebase's other reviewer-authored blocks:

1. **Blocking issues only.** By the time ``review_and_refine`` reaches its
   corrector call site, an issues list that has something to raise but nothing
   BLOCKING has already settled and shipped without a rewrite (ADR-021's
   severity gate, ``reviewer.py``'s ``if issues and not blocking`` early
   return) — the only way this call site is reached with ``blocking`` empty is
   an issues list that is ALSO empty (a rejection that named nothing at all,
   the fail-safe "unreadable verdict" path). Folding minor findings in here
   would invite the corrector to spend a memoryless rewrite on polish ADR-021
   deliberately judged not worth one, so :func:`render_blocking_issues` filters
   to blocking issues itself rather than trusting a caller to pre-filter.
2. **Written for the CORRECTOR's audience, not the reviewer's.** The
   2026-08-26 real-provider incident documented on
   ``services/pin_reach.py``'s ``_BLOCK_HEADER_CV_CORRECTOR`` is the precedent
   this module follows, not repeats: a block authored for the WRITER
   ("reproduce WORD-FOR-WORD") reached the corrector, which obeyed that
   imperative over the reviewer's own feedback. So every line rendered here
   reads as something TO DO ("Fix: ..."), never as a report of what was
   checked. And unlike this codebase's other deterministic corrector/reviewer
   blocks (``PINNED FACTS CHECK``, ``VERIFIED COVERAGE CHECK`` and siblings in
   ``pin_reach.py`` / ``keyword_ledger.py``), this one is never framed as
   "ground truth, do not re-derive it" — those blocks are code-computed facts;
   this one carries ANOTHER MODEL'S judgement, so it is presented as a finding
   to act on, not a measurement to trust blindly.
3. **Prose stays, and stays FIRST.** The reviewer's own ``feedback`` string
   carries its ordering and emphasis, which a flat enumeration loses — the
   rendered block is strictly additive, appended after the prose, never a
   replacement for it.
4. **Back-compatible by construction.** No issues, or no blocking issues among
   them: :func:`render_blocking_issues` returns ``""`` and
   :func:`fold_issues_into_feedback` returns ``feedback`` completely
   unchanged — byte-identical to today's string, for every existing chain, on
   every round that does not carry a blocking finding. (When ``feedback``
   itself is empty but a blocking issue DOES exist, the corrector receives the
   block alone rather than an empty string — that gap, not a byte-identical
   no-op, is exactly what this clause exists to close.)
5. **No ``location``/``check`` rendering.** ``ReviewIssue`` normalizes an issue
   down to ``text`` and ``severity`` only: ``location`` and ``check`` are
   accepted in the reviewer's JSON schema (``prompts/review_severity.py``) but
   ``normalize_issues`` never captures them — see that function's
   ``_ISSUE_TEXT_KEYS`` and ``test_review_issues.py``'s
   ``test_the_new_keys_never_win_over_the_prose_key``, which pins the discard.
   They are gone before a ``ReviewIssue`` ever exists. Rendering them here
   would mean either reaching around ``normalize_issues`` to the raw reviewer
   dict — bypassing its deliberate fail-safe severity coercion — or extending
   the shared dataclass, which is a schema decision for a future clause, not
   this one. This module therefore renders from ``.text`` and ``.is_blocking``
   alone.

Never an LLM call. Never changes the loop's control flow — which issue is
blocking, which draft ships, and the retry count are computed exactly as
before this module existed; it only changes what the corrector is TOLD once
the loop has already decided to ask it for another round.
"""
from __future__ import annotations

from collections.abc import Sequence

from applire.services.review_issues import ReviewIssue

#: Header for the appended block. Deliberately NOT phrased as this codebase's
#: usual code-computed "ground truth" check (constraint 2) — this is the
#: reviewer's OWN judgement, named as such, with an instruction verb so the
#: corrector reads it as work to do rather than a report to trust.
_BLOCK_HEADER = "REVIEWER FINDINGS — fix each of the following in this revision:"


def render_blocking_issues(issues: Sequence[ReviewIssue]) -> str:
    """Render the loop's BLOCKING issues into a corrector-facing instruction block.

    ``""`` when ``issues`` is empty or carries nothing blocking (constraints 1
    and 4) — never a header with an empty body. Minor issues are filtered out
    HERE, not merely trusted to be pre-filtered upstream, so this function is
    correct even if a future caller hands it an unfiltered verdict.

    See the module docstring for why this renders only ``issue.text`` (never
    ``location``/``check``, which no ``ReviewIssue`` carries — constraint 5)
    and why the wording is instructional rather than this codebase's usual
    "ground truth" framing (constraint 2).
    """
    blocking = [issue for issue in issues if issue.is_blocking]
    if not blocking:
        return ""
    lines = [_BLOCK_HEADER]
    lines.extend(f"- Fix: {issue.text}" for issue in blocking)
    return "\n".join(lines)


def fold_issues_into_feedback(feedback: str, issues: Sequence[ReviewIssue]) -> str:
    """Fold the loop's blocking issues into the ``feedback`` string the corrector
    receives as ``generator_prompt_fn``'s second argument.

    Prose first (constraint 3): the rendered block, when non-empty, is appended
    after ``feedback`` with a blank line between them. Back-compatible by
    construction (constraint 4): with no blocking issues,
    :func:`render_blocking_issues` returns ``""`` and this function returns
    ``feedback`` completely unchanged — not even a trailing newline is added —
    so every one of the five chains sharing this loop is unaffected on every
    round that settles or approves without a blocking finding. When
    ``feedback`` itself is empty (or absent) but a blocking issue exists, the
    corrector receives the block alone rather than an empty string — the
    coverage gap this clause exists to close.
    """
    block = render_blocking_issues(issues)
    if not block:
        return feedback
    if not feedback:
        return block
    return f"{feedback}\n\n{block}"

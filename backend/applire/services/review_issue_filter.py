# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#306 (a) — a deterministic sanity check on the REVIEWER's own output, run
BEFORE an issue is spent as a retry.

Charter run #7, case 2 (German, ``operations_marcus_de``): the cover-letter
reviewer generated false issues faster than they could be fixed and burned
all five retries on them. Verbatim from that run (``chain=cover_letter``,
retry attempt 5 of 5):

* Self-contradiction, "X is not in T" whose own evidence quote contains X:
  "Paragraph 1: Invented employer fact — 'Verbundverpackungen' and
  'Lebensmittelkunden' are not in the job_description text (only
  'Kunststoff- und Verbundverpackungen für Konsumgüter- und
  Lebensmittelkunden' appears; ...)" — the parenthetical contains both terms
  it just called absent.
* Same shape, byte-identical "absent" and "only ... appears" quotes:
  "'Konsumgüter- und Lebensmittelkunden' is not in the job_description text
  (only 'Konsumgüter- und Lebensmittelkunden' appears as a single phrase;
  ...)".
* A figure called both minted and grounded in the same sentence:
  "Minted figure — '38 Mitarbeitenden' is grounded, but the repetition of
  the employer name ... is the issue."
* A checkable count claim that was simply wrong: "'Bei Weberit
  Kunststofftechnik GmbH' is repeated 6 times in a single paragraph" — the
  actual count in the draft under review was 4 (never 6, and the SHIPPED
  letter names Weberit once in the whole document).
* An issue the reviewer itself annotated as non-blocking: "... which is
  stylistically poor but not a grounding issue" / "... which is allowed".

Raising ``max_retries`` again only buys more rounds of this — the fix is a
cheap, deterministic filter over the reviewer's OWN issue text (and, for the
count check, the draft it is reviewing), never a second LLM call. This
module only ever DISCARDS an issue; it never invents, rewrites, or
"fixes" one — a genuine issue that doesn't match one of the checks below
survives untouched.

The ``job_analysis`` chain exhausted on 2 issues in the same run (see the
issue), so this filter is deliberately chain-agnostic — it consumes plain
issue strings and a stringified draft (see
:func:`applire.services.load_bearing.stringify_draft`), never a
letter-specific or CV-specific shape.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# --- Check 1: self-refuting issues -----------------------------------------
#
# Shape A: "'X' [and 'Y' ...] is/are not in the job_description text (only
# 'Z' appears; ...)" where X (or Y) is literally a substring of — or equal
# to — Z. The issue's own supporting quote proves the term it calls absent
# is present.
_QUOTED_RE = re.compile(r"'([^']+)'")
_ONLY_APPEARS_RE = re.compile(r"only\s+'([^']+)'\s+appears", re.IGNORECASE)
_NOT_IN_CUE_RE = re.compile(r"\b(?:is|are)\s+not\s+in\b", re.IGNORECASE)

# Shape B: "Minted/invented/fabricated figure — 'X' is grounded" — the SAME
# quoted figure is called both ungrounded (by category) and grounded (in the
# very next clause).
_MINTED_GROUNDED_RE = re.compile(
    r"(?:minted|invented|fabricat\w*)\s+figure.*?'([^']+)'\s+is\s+grounded",
    re.IGNORECASE | re.DOTALL,
)

# --- Check 2: checkable count claims ----------------------------------------
_REPEATED_RE = re.compile(r"'([^']+)'\s+is\s+repeated\s+(\d+)\s+times?", re.IGNORECASE)

# --- Check 3: reviewer-annotated non-blocking -------------------------------
_NON_BLOCKING_CUES = (
    "not a grounding issue",
    "which is allowed",
)


def _self_refuting_containment(issue: str) -> bool:
    """Shape A — see module docstring.

    Every OTHER quoted substring in the issue (i.e. every quote that isn't
    the "only '...' appears" supporting quote itself) is checked for
    containment in that supporting quote. A quote equal to the supporting
    quote counts too (a string is trivially a substring of itself) — that is
    exactly the byte-identical second verbatim example.
    """
    match = _ONLY_APPEARS_RE.search(issue)
    if not match or not _NOT_IN_CUE_RE.search(issue):
        return False
    supporting_span = match.span(1)
    supporting_quote = match.group(1)
    for quote_match in _QUOTED_RE.finditer(issue):
        if quote_match.span(1) == supporting_span:
            continue  # skip the supporting quote matching itself
        quoted = quote_match.group(1)
        if quoted and quoted in supporting_quote:
            return True
    return False


def _self_refuting_minted_grounded(issue: str) -> bool:
    """Shape B — see module docstring."""
    return bool(_MINTED_GROUNDED_RE.search(issue))


def _wrong_count(issue: str, draft_text: str) -> bool:
    """Check 2 — a "'X' is repeated N times" claim checked against the draft
    the reviewer was actually looking at. A wrong count discards the issue."""
    match = _REPEATED_RE.search(issue)
    if not match:
        return False
    quoted, claimed = match.group(1), int(match.group(2))
    actual = draft_text.count(quoted)
    return actual != claimed


def _self_annotated_non_blocking(issue: str) -> bool:
    """Check 3 — the reviewer's own text says this isn't a blocking problem."""
    lowered = issue.lower()
    return any(cue in lowered for cue in _NON_BLOCKING_CUES)


@dataclass(frozen=True)
class IssueVerdict:
    """The deterministic verdict on ONE reviewer-raised issue."""

    issue: str
    discard: bool
    reason: str | None  # "self_refuting" | "wrong_count" | "self_annotated_non_blocking"


def evaluate_issue(issue: str, draft_text: str) -> IssueVerdict:
    """Apply all three deterministic checks to a single issue string."""
    if _self_refuting_containment(issue) or _self_refuting_minted_grounded(issue):
        return IssueVerdict(issue, True, "self_refuting")
    if _wrong_count(issue, draft_text):
        return IssueVerdict(issue, True, "wrong_count")
    if _self_annotated_non_blocking(issue):
        return IssueVerdict(issue, True, "self_annotated_non_blocking")
    return IssueVerdict(issue, False, None)


def filter_reviewer_issues(
    issues: list[str], draft_text: str
) -> tuple[list[str], list[IssueVerdict]]:
    """Run the deterministic sanity check over every issue a reviewer round
    raised, BEFORE that round is spent as a retry.

    ``draft_text`` must be the stringified form of the draft the reviewer was
    actually reviewing (see
    :func:`applire.services.load_bearing.stringify_draft`) — the count check
    (Check 2) is meaningless against any other text.

    Returns ``(surviving_issue_texts, all_verdicts)``. Never an LLM call;
    never invents or rewrites an issue — only ever discards one that fails a
    check. A genuine issue with no match to any check survives untouched, so
    this degrades gracefully: worst case it is a no-op.
    """
    verdicts = [evaluate_issue(issue, draft_text) for issue in issues]
    survivors = [v.issue for v in verdicts if not v.discard]
    return survivors, verdicts

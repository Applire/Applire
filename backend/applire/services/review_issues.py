# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The reviewer's issue list: parsing it, and measuring its precision.

Two jobs, both deterministic, neither an LLM call.

**1. Parsing (ADR-021 amended 2026-07-28).** A reviewer issue is now an object
carrying a severity — ``{"severity": "blocking"|"minor", "issue": "..."}`` — and only
a *blocking* issue makes the writer run again. :func:`normalize_issues` accepts that
shape and every degenerate form a real model produces around it (a bare string, a
missing or misspelled severity, a dict using ``text``/``description`` for the prose).
**Anything it cannot read as explicitly minor is treated as blocking**, which is the
fail-safe direction: an unparsed verdict behaves exactly as it did before severity
existed, so no caller, mock, or provider silently loses a rewrite it used to get.
See ``prompts/review_severity.py`` for the contract the reviewer is held to.

**2. Measurement (#306 (a), demoted to measurement 2026-07-28).** Charter run #7 case 2
(German, ``operations_marcus_de``) had the cover-letter reviewer generating false
issues faster than they could be fixed, burning all five retries. Verbatim from that
run (``chain=cover_letter``, attempt 5 of 5):

* Self-contradiction, "X is not in T" whose own evidence quote contains X:
  "Paragraph 1: Invented employer fact — 'Verbundverpackungen' and 'Lebensmittelkunden'
  are not in the job_description text (only 'Kunststoff- und Verbundverpackungen für
  Konsumgüter- und Lebensmittelkunden' appears; ...)" — the parenthetical contains both
  terms it just called absent.
* A figure called both minted and grounded in one sentence: "Minted figure — '38
  Mitarbeitenden' is grounded, but the repetition of the employer name ... is the issue."
* A checkable count claim that was simply wrong: "'Bei Weberit Kunststofftechnik GmbH'
  is repeated 6 times in a single paragraph" — the actual count was 4.

The checks below detect those two shapes. They are **measurement only**: they emit a
per-round precision signal and never change what the loop does. The founder's ruling
(2026-07-27) is why — a deterministic matcher for a specific model's specific mistake
is catch-up, and the next model makes a different one. The structural fix is severity
(job 1 above) plus a reviewer prompt that states what it is checking for; this module
keeps a *number* on how well that is working, so the next regression is visible rather
than inferred.

A third check — "the reviewer's own text says this isn't blocking" (cue-matching
"not a grounding issue" / "which is allowed") — was **removed** when severity landed.
It was reading, out of English prose, exactly the field the reviewer now sets.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from applire.prompts.review_severity import SEVERITY_BLOCKING, SEVERITY_MINOR

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

#: Public aliases (#537) — ``services/review_compliance.py`` reuses these EXACT
#: pattern objects (not a re-derived copy) so its quoted-term / repetition-count shape
#: detectors stay byte-for-byte in sync with this module's own soundness checks rather
#: than drifting into a second, subtly different notion of "quoted" or "repeated".
QUOTED_RE = _QUOTED_RE
REPEATED_RE = _REPEATED_RE

# Keys a model plausibly uses for the prose half of an issue object, in
# preference order. ``issue`` is what the schema asks for.
_ISSUE_TEXT_KEYS = ("issue", "text", "description", "detail", "message")


@dataclass(frozen=True)
class ReviewIssue:
    """One issue from a reviewer verdict, after severity parsing."""

    text: str
    severity: str  # SEVERITY_BLOCKING | SEVERITY_MINOR

    @property
    def is_blocking(self) -> bool:
        return self.severity == SEVERITY_BLOCKING


def _coerce_severity(raw: object) -> str:
    """Read a severity value, defaulting to blocking.

    Only an explicit, recognisable "minor" downgrades an issue. Everything else —
    absent, ``None``, an unknown word, a non-string — stays blocking, so a model that
    ignores the severity instruction gets exactly the pre-severity behaviour rather
    than a silently skipped rewrite.
    """
    if isinstance(raw, str) and raw.strip().lower() == SEVERITY_MINOR:
        return SEVERITY_MINOR
    return SEVERITY_BLOCKING


def _coerce_text(raw: object) -> str:
    """Extract the prose half of an issue entry."""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        for key in _ISSUE_TEXT_KEYS:
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                return value
        # A dict with no recognised prose key: keep it visible rather than drop it.
        return str(raw)
    return str(raw)


def normalize_issues(raw_issues: object) -> list[ReviewIssue]:
    """Turn a reviewer verdict's ``issues`` value into typed, severity-carrying issues.

    Tolerates the pre-severity shape (a list of plain strings) and partial adoption
    (some objects, some strings) — both read as blocking. A non-list value yields an
    empty list, matching how the loop treated an unusable ``issues`` field before.
    """
    if not isinstance(raw_issues, list):
        return []
    issues: list[ReviewIssue] = []
    for entry in raw_issues:
        severity = _coerce_severity(entry.get("severity") if isinstance(entry, dict) else None)
        issues.append(ReviewIssue(text=_coerce_text(entry), severity=severity))
    return issues


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
    the reviewer was actually looking at."""
    match = _REPEATED_RE.search(issue)
    if not match:
        return False
    quoted, claimed = match.group(1), int(match.group(2))
    actual = draft_text.count(quoted)
    return actual != claimed


@dataclass(frozen=True)
class IssueVerdict:
    """The deterministic measurement verdict on ONE reviewer-raised issue.

    ``unsound`` records that the issue failed a cheap, checkable test. It is a
    *signal*, not a decision — nothing in the loop acts on it (see module docstring).
    """

    issue: str
    unsound: bool
    reason: str | None  # "self_refuting" | "wrong_count"


def evaluate_issue(issue: str, draft_text: str) -> IssueVerdict:
    """Apply the deterministic soundness checks to a single issue string."""
    if _self_refuting_containment(issue) or _self_refuting_minted_grounded(issue):
        return IssueVerdict(issue, True, "self_refuting")
    if _wrong_count(issue, draft_text):
        return IssueVerdict(issue, True, "wrong_count")
    return IssueVerdict(issue, False, None)


def measure_reviewer_issues(
    issues: list[ReviewIssue], draft_text: str
) -> tuple[int, list[IssueVerdict]]:
    """Measure how many of a reviewer round's issues are demonstrably unsound.

    ``draft_text`` must be the stringified form of the draft the reviewer was actually
    reviewing (see :func:`applire.services.load_bearing.stringify_draft`) — the count
    check is meaningless against any other text.

    Returns ``(unsound_count, all_verdicts)``. Never an LLM call, and never changes
    which issues the loop acts on — the caller logs this and moves on.
    """
    verdicts = [evaluate_issue(i.text, draft_text) for i in issues]
    return sum(1 for v in verdicts if v.unsound), verdicts

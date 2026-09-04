# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#563 part D — the terminal review's own outcome becomes a reported ADR-039 check.

ADR-076 clause 2 requires every migrated SIGNAL to name an exhaustion disposition, and
clause 3's terminal review declares its own: *ship-and-report — never a delivery gate*
(``services/cv.py``'s own comment). The **ship** half was built; the **report** half was
not. Positive set exhausted on 2026-09-04: the only readers of ``REVIEW_EXHAUSTED``,
``REVIEW_CYCLE_DETECTED``, ``REVIEW_MINOR_ONLY`` and ``REVIEW_CALL_FAILED`` anywhere in
``backend/``, ``frontend/``, ``tests/``, ``scripts/`` or ``.github/`` were their own
producer in ``providers/llm/debug_log.py`` and the unit tests pinning that producer's log
format. No router, no column, no frontend surface, no alert. So a CV could ship with its
terminal reviewer's blocking finding open — #563: *"the project bullet for LucaNet …
omits the profile's explicit limitation that the candidate is operational number two"* —
and every door the user or their agent can reach said nothing at all.

This module is the mapping from ``review_and_refine``'s settle report
(``services/review_issues.ReviewSettle``) onto the vocabulary ADR-039 already has. Three
design points, each answering a question that has a wrong-looking easy answer:

1. **No fourth status.** ``ATSCheck.status`` is
   ``Literal["pass", "fail", "not_applicable"]``, read by two REST routes, two MCP tools,
   the ATS panel and E058's group 4, and constrained by a persisted-report back-compat
   contract. A ``warn`` value would make every existing reader's exhaustiveness
   assumption wrong for a distinction the three values already express: *the findings are
   open* (``fail``), *the loop settled clean* (``pass``), *the question could not be
   evaluated* (``not_applicable``).

2. **``not_applicable`` means "unknown", and it is never absent.** ADR-079 clause 4 and
   ADR-081 clause 9 both say a producer that did not run renders as *unknown, never 0*,
   and ``schemas/ats.py`` records why an absent check is worse than an explicit one: it
   is invisible to both counters and reads as a clean, complete audit of something that
   was never examined (the #634 class). :func:`build_terminal_review_check` therefore
   ALWAYS returns a check.

3. **A minor-only settle is a ``pass`` that still names its observations.** The ADR-021
   severity gate settling a round on minor issues is a legitimate ship, so the status is
   ``pass``. But ADR-076 clause 9's whole-document checks are visibility-only precisely
   *because* this check gives ``minor`` findings a reader — before it, ``minor`` meant
   discarded. Dropping them from ``details`` would take that reader away again and make
   the clause-9 severity decision indefensible.

Never an LLM call. Reads a settle report and returns a report row; changes no draft.
"""
from __future__ import annotations

from dataclasses import dataclass

from applire.schemas.ats import ATSCheck
from applire.services.review_issues import ReviewSettle

#: Stable machine id. Frontend labels are keyed by check id (``schemas/ats.py``).
TERMINAL_REVIEW_CHECK_ID = "terminal-review"

#: Bound on ``details``. A reviewer verdict is capped at ``REVIEW_VERDICT_MAX_TOKENS``
#: (2048) and can legitimately enumerate several findings; the persisted report is read
#: by a panel, a tool payload and a `.docx` twin, so the diagnostic is bounded here
#: rather than at each door. Chosen to hold ~3 full findings; overflow is counted, never
#: silently dropped.
_DETAILS_MAX_CHARS = 1200

#: Settle paths that ship a draft whose last verdict still carried BLOCKING findings.
#: `exhausted` is #563's own case; `cycle_detected` is the likeliest exit for a demand
#: the corrector structurally cannot satisfy (ADR-076 clause 2's 2026-08-15 amendment
#: says so explicitly); `generator_call_failed` means the corrector never even ran.
_FAIL_PATHS = frozenset({"exhausted", "cycle_detected", "generator_call_failed"})

#: Settle paths where the loop ended without an outstanding blocking finding.
_PASS_PATHS = frozenset({"approved", "minor_only"})

#: The loop ran and could not obtain a verdict at all — "unknown", not "clean" and not
#: "broken document". A reviewer call that truncates or times out on attempt 1 leaves no
#: verdict to report, and `review_and_refine`'s own contract is to ship the draft
#: un-reviewed rather than crash.
_UNKNOWN_PATHS = frozenset({"reviewer_call_failed"})


@dataclass(frozen=True)
class TerminalReviewOutcome:
    """A terminal review's settle, reduced to what the report needs.

    Deliberately a separate type from :class:`ReviewSettle`: the settle carries the
    delivered draft (large, and none of the report's business), and a terminal review
    may be entered SEVERAL times per delivery — the ADR-076 clause-3 re-entry loop and,
    on the letter, the ADR-076 2026-08-29 final-length-floor round. :meth:`worse_of`
    is how those fold into one reported outcome.
    """

    chain_id: str
    path: str | None
    approved: bool
    blocking_issues: tuple[str, ...]
    minor_issues: tuple[str, ...]
    rounds: int

    @property
    def status(self) -> str:
        """The ADR-039 check status this outcome maps to."""
        if self.path is None:
            return "not_applicable"
        if self.path in _UNKNOWN_PATHS:
            return "not_applicable"
        if self.path in _FAIL_PATHS:
            # Fail-safe in the reporting direction: an unrecognised blocking-path
            # settle with no issues recorded is still not evidence of a clean review.
            return "fail"
        if self.path in _PASS_PATHS:
            return "pass"
        # A settle path this module has not been taught. Never claim it was clean.
        return "not_applicable"

    def worse_of(self, other: "TerminalReviewOutcome | None") -> "TerminalReviewOutcome":
        """Fold a second invocation of the same delivery's terminal review into this
        one, keeping the WORSE status and summing the rounds.

        A delivery can invoke the terminal loop more than once (clause 3's re-entry;
        the letter's final-length-floor round). Reporting only the last one would let a
        clean final round erase an earlier exhaustion that shipped content — the
        "seam evidence is not delivery evidence" failure in the reporting layer.
        """
        if other is None:
            return self
        order = {"fail": 2, "not_applicable": 1, "pass": 0}
        keep, drop = (self, other) if order[self.status] >= order[other.status] else (other, self)
        return TerminalReviewOutcome(
            chain_id=keep.chain_id,
            path=keep.path,
            approved=keep.approved,
            blocking_issues=keep.blocking_issues,
            minor_issues=keep.minor_issues,
            rounds=self.rounds + other.rounds,
        )


def settle_to_outcome(settle: ReviewSettle, *, chain_id: str) -> TerminalReviewOutcome:
    """Project a loop settle onto the reportable outcome. Pure."""
    return TerminalReviewOutcome(
        chain_id=chain_id,
        path=settle.path,
        approved=settle.approved,
        blocking_issues=settle.blocking_issues,
        minor_issues=settle.minor_issues,
        rounds=settle.rounds,
    )


def _truncate(text: str) -> str:
    if len(text) <= _DETAILS_MAX_CHARS:
        return text
    return text[: _DETAILS_MAX_CHARS - 1].rstrip() + "…"


def _details(outcome: TerminalReviewOutcome) -> str:
    """The EN diagnostic. Names the mechanism AND the open findings — a status
    without the finding tells the user something is wrong and not what."""
    status = outcome.status
    if status == "not_applicable":
        if outcome.path is None:
            return (
                "The terminal review did not run for this document, so its verdict is "
                "unknown — not clean."
            )
        return (
            "The terminal review ran but no verdict could be obtained "
            f"(settle path: {outcome.path}); its result is unknown, not clean."
        )
    if status == "fail":
        head = {
            "exhausted": (
                "The terminal review exhausted its retries with findings still open, and "
                "the document was delivered unreviewed"
            ),
            "cycle_detected": (
                "The terminal review stopped early because the corrector reproduced an "
                "earlier draft (a cycle), with findings still open"
            ),
            "generator_call_failed": (
                "The terminal review's correction call failed, so its findings were never "
                "acted on"
            ),
        }.get(outcome.path or "", "The terminal review settled with findings still open")
        body = "; ".join(outcome.blocking_issues) or "(the verdict named no issue text)"
        return _truncate(f"{head} after {outcome.rounds} round(s). Open findings: {body}")
    # pass
    if outcome.minor_issues:
        return _truncate(
            "The terminal review raised no blocking finding. Observations recorded for "
            "your judgement (never acted on automatically): "
            + "; ".join(outcome.minor_issues)
        )
    return (
        "The terminal review approved the delivered document with no findings."
        if outcome.approved
        else "The terminal review settled with no blocking finding."
    )


def build_terminal_review_check(
    outcome: TerminalReviewOutcome | None,
    *,
    previous: dict | None = None,
    document: str = "cv",
) -> ATSCheck:
    """The ADR-039 check for this document's terminal review. NEVER returns ``None``.

    ``outcome`` is the fresh settle from THIS invocation's terminal review; ``previous``
    is the ``terminal-review`` check dict from the report already persisted on the row,
    when there is one.

    **Carry-forward.** The audit-and-persist seam is also reached by the section-editor
    re-audit and the agent-authored re-audit, where no terminal review ran in that
    invocation. Recomputing ``not_applicable`` there would let any later edit launder a
    document that shipped on an exhausted review into one that reads as cleanly audited
    (the #634 class again, from the other side). So a fresh outcome always wins, and in
    its absence the previously persisted check is re-emitted verbatim. This is the one
    place an ADR-039 report deliberately carries a row it did not compute in this
    invocation — the check is a statement about the review that produced the document,
    not about the text as it now stands, and the ADR-039 amendment of 2026-09-04 records
    the exception rather than leaving it to be discovered.
    """
    del document  # both documents share the id and the vocabulary; kept for call-site clarity
    if outcome is not None:
        return ATSCheck(
            id=TERMINAL_REVIEW_CHECK_ID,
            status=outcome.status,
            details=_details(outcome),
        )
    if previous:
        try:
            carried = ATSCheck.model_validate(previous)
        except Exception:
            carried = None
        if carried is not None and carried.id == TERMINAL_REVIEW_CHECK_ID:
            return carried
    return ATSCheck(
        id=TERMINAL_REVIEW_CHECK_ID,
        status="not_applicable",
        details=(
            "No terminal-review outcome is recorded for this document, so its verdict is "
            "unknown — not clean."
        ),
    )


def previous_check(report: dict | None, check_id: str) -> dict | None:
    """The named check out of a previously persisted ``ats_report`` dict, or ``None``.

    Tolerant by design: a persisted report predating a check, a malformed blob and a
    NULL column must all read as "nothing to carry forward", never as an exception on
    the audit path (ADR-039: an audit failure may never fail or alter generation).
    """
    if not isinstance(report, dict):
        return None
    checks = report.get("checks")
    if not isinstance(checks, list):
        return None
    for check in checks:
        if isinstance(check, dict) and check.get("id") == check_id:
            return check
    return None

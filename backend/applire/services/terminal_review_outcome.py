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

3a. **A finding is only "open" against the document it was raised against** (F-4,
   founder UAT 2026-09-20). ``review_and_refine`` reviews draft N, and when the verdict
   carries a blocking finding the CORRECTOR runs in the same iteration and produces
   draft N+1 — which the ``exhausted`` return then delivers, un-re-reviewed
   (``services/reviewer.py:1011-1018``; ``CV_TERMINAL_REVIEW_MAX_RETRIES`` is 1, so
   there is no later reviewer iteration inside one invocation). Measured over the
   captured real-provider terminal reviews of 2026-08-16…2026-09-19: in **46 of 46**
   documents whose last verdict was un-approved with blocking findings, the corrector
   ran after that verdict. Reporting those findings as "open" and the document as
   "delivered unreviewed" is therefore a FALSE ALARM — and a control's false positive is
   its own failure mode: the founder was told to distrust a document in which all four
   named findings had in fact been corrected.

   The discriminator is DRAFT IDENTITY, not text presence. "Is the finding's quoted
   phrase still in the document" was measured and rejected as a status driver: of 181
   blocking findings in those last rounds only 42 quote any phrase at all (139 carry
   none), and where it is measurable it points BOTH ways — a coverage finding ("the
   claimable keyword \"Performance-Optimierung\" is absent") is RESOLVED when its quote
   becomes present, while an overstatement finding's quoted noun phrase legitimately
   survives the rewrite. ``services/review_compliance.py``'s own docstring names that
   one-sidedness. So the status asks the one question that is 100 % decidable — *was the
   delivered draft the draft this verdict read?* — via the shared
   ``services.subject_identity.subject_hash`` (ADR-066, one hash for both mounts), and
   the per-finding detail comes from the instrument that ALREADY measures this exact
   adjacency every round, ``review_compliance.measure_corrector_compliance`` (#537),
   with its ``unmeasurable`` bucket reported so the number's scope is never overstated.

4. **A minor-only settle is a ``pass`` that still names its observations.** The ADR-021
   severity gate settling a round on minor issues is a legitimate ship, so the status is
   ``pass``. But ADR-076 clause 9's whole-document checks are visibility-only precisely
   *because* this check gives ``minor`` findings a reader — before it, ``minor`` meant
   discarded. Dropping them from ``details`` would take that reader away again and make
   the clause-9 severity decision indefensible.

Never an LLM call. Reads a settle report and returns a report row; changes no draft.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from applire.schemas.ats import ATSCheck
from applire.services.review_issues import ReviewSettle

logger = logging.getLogger(__name__)

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
#: `review_malformed` (#688) is a reviewer or corrector call that returned malformed
#: (non-truncated) JSON — treated like exhaustion rather than `reviewer_call_failed`'s
#: "unknown" bucket below, because unlike a truncation/timeout on attempt 1 this can
#: also happen on the CORRECTOR call, after a verdict already raised a real blocking
#: finding that never got acted on — the same shape `generator_call_failed` reports.
_FAIL_PATHS = frozenset(
    {"exhausted", "cycle_detected", "generator_call_failed", "review_malformed"}
)

#: Settle paths where the loop ended without an outstanding blocking finding.
_PASS_PATHS = frozenset({"approved", "minor_only"})

#: The loop ran and could not obtain a verdict at all — "unknown", not "clean" and not
#: "broken document". A reviewer call that truncates or times out on attempt 1 leaves no
#: verdict to report, and `review_and_refine`'s own contract is to ship the draft
#: un-reviewed rather than crash.
_UNKNOWN_PATHS = frozenset({"reviewer_call_failed"})

#: F-4: the ONE blocking-settle path on which the delivered draft is provably NOT the
#: draft the reported findings were raised against, even when a producer has not wired
#: :class:`CorrectionFacts`. ``review_and_refine`` reaches its ``exhausted`` return only
#: after the corrector call in the last iteration RETURNED A NEW DRAFT: a failed or
#: malformed corrector call returns under its own path, and a corrector that reproduced
#: an earlier draft returns ``cycle_detected``. Every other blocking path stays ``fail``
#: without the measurement — `generator_call_failed` proves the corrector produced
#: nothing, `cycle_detected` means it reproduced a draft already seen (possibly the
#: reviewed one), and `review_malformed` cannot say which of its two call sites raised —
#: which is the fail-safe direction for a reporting layer. A producer that DOES supply
#: :class:`CorrectionFacts` overrides this table with the measured fact.
_CORRECTED_AFTER_VERDICT_PATHS = frozenset({"exhausted"})


@dataclass(frozen=True)
class CorrectionFacts:
    """What the delivery did with the last verdict's findings. Facts, never judgements.

    Produced by :func:`settle_to_outcome` when the caller can name the draft the last
    verdict was rendered over (``reviewed_draft``). Optional by design: a chain that has
    not wired it keeps byte-identical behaviour through
    :data:`_CORRECTED_AFTER_VERDICT_PATHS`.
    """

    #: Is the DELIVERED draft the draft the last verdict read? ``True`` means the
    #: findings stand against the delivered document; ``False`` means a correction
    #: landed after the verdict and was never re-reviewed — UNVERIFIED, not confirmed.
    delivered_is_reviewed: bool
    #: Per-finding outcomes of ``review_compliance.measure_corrector_compliance`` over
    #: the reviewed→delivered adjacency. ``unmeasurable`` is first-class: the other
    #: three counts describe only the findings whose SHAPE this instrument recognises.
    implemented: int = 0
    not_implemented: int = 0
    indeterminate: int = 0
    unmeasurable: int = 0

    @property
    def measured(self) -> int:
        """Findings whose shape the compliance instrument could recognise."""
        return self.implemented + self.not_implemented + self.indeterminate


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
    #: #664 / ADR-075 amended 2026-09-11 — statements about what the DELIVERY
    #: PIPELINE did to this document after the verdict, not about what a reviewer
    #: found: today, the settle-time cut of a sentence carrying a limit the
    #: candidate never stated. Never affects :attr:`status` (a cut is the
    #: remedy, not the finding), always reaches ``details`` so the candidate is
    #: told what was removed from their letter and why. Defaults to empty, so
    #: every existing producer and the whole CV side stay byte-identical.
    notes: tuple[str, ...] = ()
    #: F-4 — the measured facts about what the DELIVERY did with this verdict's
    #: findings. ``None`` = not measured by this producer; the status then falls back to
    #: :data:`_CORRECTED_AFTER_VERDICT_PATHS`, so every existing producer keeps working.
    correction: CorrectionFacts | None = None

    @property
    def findings_stand_against_delivered(self) -> bool:
        """Were this outcome's findings raised against the DELIVERED draft? (F-4)

        A finding is only *open* against the document it was read from. When a
        correction landed after the verdict and was never re-reviewed, the honest word
        is UNVERIFIED — see the module docstring's design point 3a and the 46-of-46
        measurement behind it.
        """
        if self.correction is not None:
            return self.correction.delivered_is_reviewed
        return (self.path or "") not in _CORRECTED_AFTER_VERDICT_PATHS

    @property
    def status(self) -> str:
        """The ADR-039 check status this outcome maps to.

        Adversarial finding (Nougat UAT-fixes batch, 2026-09-20) generalised the
        F-4 draft-identity gate from `fail` paths to EVERY path: a `pass` is only
        honest when the delivered draft is the one the approving verdict actually
        read. Before this, a `_PASS_PATHS` settle reported `pass` unconditionally,
        so a post-verdict rewrite nobody reviewed (the letter's final-length-floor
        recondense, `cover_letter.py`) still shipped as a clean `terminal-review`
        check — the exact false-positive-control failure mode F-4 was built to
        catch, just on the approved side rather than the exhausted one. For every
        producer that has not wired ``correction`` (unchanged: `cv.py`, and any
        letter invocation before this fix), ``findings_stand_against_delivered``
        already defaults to ``True`` outside :data:`_CORRECTED_AFTER_VERDICT_PATHS`,
        so this generalisation is byte-identical there.
        """
        if self.path is None:
            return "not_applicable"
        if self.path in _UNKNOWN_PATHS:
            return "not_applicable"
        if not self.findings_stand_against_delivered:
            # The delivered draft is provably NOT the draft this outcome's last
            # verdict was rendered over — a correction (or, on the letter, a bare
            # post-verdict recondense) landed afterward and nobody re-reviewed
            # it. Neither "open" nor "clean" is honest; report unknown/unverified,
            # whichever path this was.
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
        # F-4: an APPROVED verdict rendered over the DELIVERED draft supersedes every
        # earlier outcome of this delivery. The clause-3 re-entry loop exists precisely
        # to re-review an earlier round's correction; when that re-review approved the
        # delivered document, reporting the earlier round's finding — as open OR as
        # unverified — is the same false alarm one level up. Only ever on the MEASURED
        # fact: without `correction` nothing is superseded and the fold below is
        # byte-identical to its pre-F-4 behaviour for every unwired producer.
        for winner, loser in ((self, other), (other, self)):
            if (
                winner.approved
                and winner.status == "pass"
                and winner.correction is not None
                and winner.correction.delivered_is_reviewed
            ):
                return TerminalReviewOutcome(
                    chain_id=winner.chain_id,
                    path=winner.path,
                    approved=True,
                    blocking_issues=winner.blocking_issues,
                    minor_issues=winner.minor_issues,
                    rounds=self.rounds + other.rounds,
                    notes=tuple(dict.fromkeys(winner.notes + loser.notes)),
                    correction=winner.correction,
                )
        order = {"fail": 2, "not_applicable": 1, "pass": 0}

        def _rank(o: "TerminalReviewOutcome") -> tuple[int, int]:
            # Adversarial finding, 2026-09-20: once `status` (above) can demote
            # a `pass`-path outcome to `not_applicable` on the draft-identity
            # fact, two outcomes can tie on status while one still carries a
            # real earlier finding the other does not (e.g. a genuine
            # exhaustion vs. an approved-but-now-unverified later round). A
            # bare status tie must not silently drop that finding's text, so
            # the tie-break's second key prefers the outcome that still names
            # one.
            return (order[o.status], 1 if o.blocking_issues else 0)

        keep, drop = (self, other) if _rank(self) >= _rank(other) else (other, self)
        return TerminalReviewOutcome(
            chain_id=keep.chain_id,
            path=keep.path,
            approved=keep.approved,
            blocking_issues=keep.blocking_issues,
            minor_issues=keep.minor_issues,
            rounds=self.rounds + other.rounds,
            # A note describes something that HAPPENED to the document, so unlike
            # the verdict fields it is never dropped by the fold — both
            # invocations' notes survive, deduped, order kept.
            notes=tuple(dict.fromkeys(keep.notes + drop.notes)),
            correction=keep.correction,
        )


def measure_correction(
    settle: ReviewSettle,
    reviewed_draft: dict[str, Any],
    *,
    structured_output: bool = False,
    delivered_draft: dict[str, Any] | None = None,
) -> CorrectionFacts:
    """The F-4 facts about this settle: was the delivered draft the reviewed one, and
    what did the existing corrector-compliance instrument make of each finding?

    Pure, never an LLM call. ``reviewed_draft`` is the draft the LAST verdict was
    rendered over — the caller inside the chain is the only code that knows it, because
    ``review_and_refine`` hands the reviewer prompt function that draft and keeps no
    record of it afterwards.

    ``delivered_draft`` (adversarial finding, Nougat UAT-fixes batch, 2026-09-20):
    the ACTUAL final delivered draft, when the caller can supply one that is not
    ``settle.settled`` — the letter's final-length-floor recondense
    (``cover_letter.py``) is a bare rewrite that reassigns the delivered content
    AFTER this settle already happened, so ``settle.settled`` alone is stale by
    the time the report is built. ``None`` (the default) keeps the pre-existing
    behaviour of comparing against ``settle.settled`` exactly, so every producer
    that has not wired this (``cv.py``, and the letter before this fix) is
    byte-identical.

    Two shared instruments, no new ones (ADR-066):
    ``services.subject_identity.subject_hash`` — the SAME canonicalisation the #538/#539
    subject-identity instrument already uses on both mounts — and
    ``services.review_compliance.measure_corrector_compliance`` (#537), which already
    runs on exactly this reviewed→corrected adjacency every round and is already
    polarity-aware (see its docstring on one-sided shapes). ``blocking_issues`` carries
    texts, so the ``ReviewIssue`` objects are rebuilt at blocking severity — the only
    severity ``measure_corrector_compliance`` measures.
    """
    from applire.prompts.review_severity import SEVERITY_BLOCKING
    from applire.services.load_bearing import stringify_draft
    from applire.services.review_compliance import (
        ComplianceOutcome,
        measure_corrector_compliance,
    )
    from applire.services.review_issues import ReviewIssue
    from applire.services.subject_identity import subject_hash

    if delivered_draft is not None:
        delivered = delivered_draft if isinstance(delivered_draft, dict) else {}
    else:
        delivered = settle.settled if isinstance(settle.settled, dict) else {}
    same = subject_hash(delivered) == subject_hash(reviewed_draft)
    counts: dict[ComplianceOutcome, int] = {o: 0 for o in ComplianceOutcome}
    if not same and settle.blocking_issues:
        verdicts = measure_corrector_compliance(
            [ReviewIssue(text=t, severity=SEVERITY_BLOCKING) for t in settle.blocking_issues],
            stringify_draft(reviewed_draft),
            stringify_draft(delivered),
            structured_output=structured_output,
        )
        for verdict in verdicts:
            counts[verdict.outcome] += 1
    return CorrectionFacts(
        delivered_is_reviewed=same,
        implemented=counts[ComplianceOutcome.IMPLEMENTED],
        not_implemented=counts[ComplianceOutcome.NOT_IMPLEMENTED],
        indeterminate=counts[ComplianceOutcome.INDETERMINATE],
        unmeasurable=counts[ComplianceOutcome.UNMEASURABLE],
    )


def settle_to_outcome(
    settle: ReviewSettle,
    *,
    chain_id: str,
    reviewed_draft: dict[str, Any] | None = None,
    structured_output: bool = False,
    delivered_draft: dict[str, Any] | None = None,
) -> TerminalReviewOutcome:
    """Project a loop settle onto the reportable outcome. Pure.

    ``reviewed_draft`` (F-4) is the draft the LAST verdict of this invocation was
    rendered over. Supplying it turns the "are these findings open against the DELIVERED
    document" question from a settle-path inference into a measured fact; omitting it
    keeps the pre-F-4 behaviour exactly (see :data:`_CORRECTED_AFTER_VERDICT_PATHS`), so
    a chain can wire it independently of every other chain. A measurement that raises is
    never allowed to cost the report: this is a reporting layer, and it may not become a
    new way for generation to fail (ADR-039).

    ``delivered_draft`` (adversarial finding, 2026-09-20) overrides ``settle.settled``
    as the "delivered" side of :func:`measure_correction`'s identity check — see that
    function's docstring. ``None`` keeps this call byte-identical to before.
    """
    correction: CorrectionFacts | None = None
    if reviewed_draft is not None:
        try:
            correction = measure_correction(
                settle,
                reviewed_draft,
                structured_output=structured_output,
                delivered_draft=delivered_draft,
            )
        except Exception:  # pragma: no cover - fail-safe, logged by the caller's chain
            logger.exception(
                "terminal_review_outcome: correction measurement failed for chain=%s "
                "path=%s; reporting from the settle path alone",
                chain_id,
                settle.path,
            )
            correction = None
    return TerminalReviewOutcome(
        chain_id=chain_id,
        path=settle.path,
        approved=settle.approved,
        blocking_issues=settle.blocking_issues,
        minor_issues=settle.minor_issues,
        rounds=settle.rounds,
        correction=correction,
    )


def _truncate(text: str) -> str:
    if len(text) <= _DETAILS_MAX_CHARS:
        return text
    return text[: _DETAILS_MAX_CHARS - 1].rstrip() + "…"


#: Below this many characters, naming a finding by a first-chars excerpt stops
#: being useful — the finding is counted in a trailing "(+k more)" marker
#: instead of shown as an unreadable fragment (adversarial finding, blind
#: Kaile probe on the integrated tree, 2026-09-20: a 4-finding `not_applicable`
#: report's whole-string `_truncate` at `_DETAILS_MAX_CHARS` cut off mid-word
#: and silently dropped 2 of the 4 raised findings — the fact report's own
#: contract, do-not #1, is that every finding is still named).
_MIN_FINDING_EXCERPT_CHARS = 60


def _findings_budget(outcome: "TerminalReviewOutcome", preamble: str) -> int:
    """How many characters :func:`_join_findings_bounded` may spend on the
    findings list, so the FULLY ASSEMBLED details string (preamble + findings
    + notes) stays within `_DETAILS_MAX_CHARS` without the whole-string
    `_truncate` (still the backstop for a pathological case this cannot
    foresee, e.g. an unusually long note — never removed) needing to cut into
    the findings text at all under realistic report sizes.

    ``preamble`` is everything the caller has already composed for THIS body
    (the head sentence, the compliance sentence when present, and the
    findings-list label) — reserved in full, because dropping or truncating
    the mechanism sentence would make the report unreadable, never the
    findings. ``outcome.notes`` (#664) are reserved too: `_details` appends
    them AFTER this body returns, so they must be budgeted for here or the
    outer `_truncate` could still cut a finding to make room for them.
    """
    notes_text = " ".join(outcome.notes)
    reserved = len(preamble) + (len(notes_text) + 1 if notes_text else 0)
    return max(_DETAILS_MAX_CHARS - reserved, _MIN_FINDING_EXCERPT_CHARS)


def _join_findings_bounded(findings: tuple[str, ...], *, budget: int) -> str:
    """Every finding named, within `budget` characters — never a whole-string
    truncation that silently drops the findings nearer the end of the join.

    Each finding gets an EQUAL share of `budget` first (typically well over
    the fix brief's own floor of "~150 chars" for a realistic finding count
    and budget). When even `_MIN_FINDING_EXCERPT_CHARS` per finding will not
    fit ALL of them, later findings are dropped from the TEXT and counted in
    a trailing "(+k more)" marker instead — the report's "every finding named"
    contract (do-not #1) is then satisfied by the COUNT, never by silence.
    """
    if not findings:
        return "(the verdict named no issue text)"
    sep = "; "
    joined = sep.join(findings)
    budget = max(budget, 0)
    if len(joined) <= budget:
        return joined
    total = len(findings)
    n = total
    while n > 1:
        tail = "" if n == total else f" (+{total - n} more)"
        available = budget - len(tail) - len(sep) * (n - 1)
        if available // n >= _MIN_FINDING_EXCERPT_CHARS:
            break
        n -= 1
    tail = "" if n == total else f" (+{total - n} more)"
    available = max(budget - len(tail) - len(sep) * max(n - 1, 0), n)
    share = max(available // n, 1)
    parts = []
    for text in findings[:n]:
        if len(text) <= share:
            parts.append(text)
        else:
            cut = max(share - 1, 1)
            parts.append(text[:cut].rstrip() + "…")
    return sep.join(parts) + tail


def _details(outcome: TerminalReviewOutcome) -> str:
    """The EN diagnostic. Names the mechanism AND the open findings — a status
    without the finding tells the user something is wrong and not what.

    ``notes`` (#664) are appended on EVERY status: a sentence the delivery
    pipeline removed from the letter is something the candidate must be told
    regardless of how the verdict itself settled."""
    return _truncate(_with_notes(_body(outcome), outcome.notes))


def _with_notes(body: str, notes: tuple[str, ...]) -> str:
    if not notes:
        return body
    return f"{body} " + " ".join(notes)


def _compliance_sentence(outcome: TerminalReviewOutcome) -> str:
    """What the deterministic corrector-compliance instrument could and could not say.

    Its scope is stated in the same breath as its numbers: a compliance count computed
    over the findings whose SHAPE is mechanically checkable must never read as covering
    the whole round (``review_compliance``'s own contract, and the
    "instrument silence is scoped to what it examines" rule).
    """
    facts = outcome.correction
    total = len(outcome.blocking_issues)
    if facts is None or not total:
        return ""
    if facts.measured == 0:
        return (
            f"None of the {total} finding(s) has a mechanically checkable shape, so "
            "whether the correction addressed them cannot be decided deterministically "
            "— read them against the document yourself."
        )
    return (
        f"A deterministic check of the correction could decide {facts.measured} of "
        f"{total} finding(s) mechanically ({facts.implemented} implemented, "
        f"{facts.not_implemented} not implemented, {facts.indeterminate} inconclusive); "
        f"{facts.unmeasurable} carry no mechanically checkable shape."
    )


def _unverified_body(outcome: TerminalReviewOutcome) -> str:
    """F-4: the document changed after the last verdict and the revision was never
    re-reviewed. Neither open nor clean is honest, so this is UNVERIFIED.

    Two shapes (adversarial finding, 2026-09-20 generalised :meth:`TerminalReviewOutcome.
    status` from fail paths to every path): a blocking verdict whose corrector-revised
    draft was never re-reviewed (the original F-4 shape, findings named) — or an
    APPROVED verdict whose delivered draft is a LATER, unreviewed rewrite (the letter's
    final-length-floor recondense: no findings to name, but the "clean" verdict itself
    no longer describes what shipped)."""
    if not outcome.blocking_issues:
        return (
            f"The terminal review settled after {outcome.rounds} round(s) with no "
            "blocking finding, but the document changed again after that verdict — a "
            "later rewrite (e.g. the letter's final-length-floor re-condense) revised "
            "it and that revision was never reviewed. This document's review status is "
            "UNVERIFIED against what was actually delivered — not confirmed clean."
        )
    head = (
        f"The terminal review settled after {outcome.rounds} round(s) with "
        f"{len(outcome.blocking_issues)} finding(s) raised against the draft it last "
        "read. The corrector then revised the document and that revision was not "
        "re-reviewed, so these findings are UNVERIFIED against the delivered document "
        "— not confirmed."
    )
    parts = [head]
    sentence = _compliance_sentence(outcome)
    if sentence:
        parts.append(sentence)
    label = "Unverified findings: "
    preamble = " ".join(parts) + " " + label
    budget = _findings_budget(outcome, preamble)
    body = _join_findings_bounded(outcome.blocking_issues, budget=budget)
    parts.append(f"{label}{body}")
    return " ".join(parts)


def _body(outcome: TerminalReviewOutcome) -> str:
    status = outcome.status
    if status == "not_applicable":
        if outcome.path is None:
            return (
                "The terminal review did not run for this document, so its verdict is "
                "unknown — not clean."
            )
        if not outcome.findings_stand_against_delivered:
            # Adversarial finding, 2026-09-20: was scoped to `_FAIL_PATHS` only —
            # widened so a `_PASS_PATHS` settle demoted by the generalised
            # `status` property (a clean verdict over a draft that is not the
            # delivered one) gets the same honest UNVERIFIED wording, not the
            # generic "result is unknown" fallback below.
            return _unverified_body(outcome)
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
            "review_malformed": (
                "The terminal review's reviewer or correction call returned malformed "
                "output that could not be parsed, so the document was delivered without "
                "a completed review"
            ),
        }.get(outcome.path or "", "The terminal review settled with findings still open")
        label = "Open findings: "
        preamble = f"{head} after {outcome.rounds} round(s). {label}"
        budget = _findings_budget(outcome, preamble)
        body = _join_findings_bounded(outcome.blocking_issues, budget=budget)
        return f"{head} after {outcome.rounds} round(s). {label}{body}"
    # pass
    if outcome.minor_issues:
        return (
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

# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#537 (ADR-076 clause 2, the floor) — corrector IMPLEMENTATION compliance.

**The gap this closes.** The presence amendment (#530/#531, 2026-08-14) measured that
the corrector *acknowledges* a reviewer issue 87.5% of the time — the issue stops being
raised in the next round. ADR-076 clause 2 requires a different, stricter number before
any deterministic post-review pass may migrate its edit into the corrector round:
**implementation** compliance — did the next draft actually change in the way the issue
demanded, not merely did the issue go quiet. An issue can vanish because the reviewer
forgot it, moved on to something else, or hit its own retry budget — none of those is
the corrector doing the fix.

**Why this is not a general-purpose grader (design honesty, ADR-076 clause 4).**
Arbitrary reviewer prose ("the summary overstates seniority", "the transfer argument for
Digitalisierung is missing") is a *judgement* about whether a rewrite addressed it —
exactly the class of question ADR-062/ADR-076 forbid settling by string comparison or a
growing dictionary. This module does NOT attempt that. It recognises a small, closed set
of **mechanically checkable issue SHAPES** — quoted-term presence/absence demands and a
checkable repetition-count demand, the same class of shape ``services/review_issues.py``
already parses for soundness — and measures ONLY those. Every issue that does not match
one of those shapes is ``unmeasurable``: a first-class outcome, not a failure, and never
silently folded into either the compliant or non-compliant count. A compliance number
computed over a fraction of a round's issues must never be presented as covering the
whole round; :func:`aggregate_by_signal_class` reports the ``unmeasurable`` bucket's size
alongside every other so nobody has to take that on faith.

**A shape that only points one way is a fourth outcome, not a third.** One of the three
shapes (forbidden-claim removal) is itself structurally one-sided: it can prove a term
was removed, but a term still present could mean the corrector ignored the demand OR
that it legitimately reframed the term as an honest aspiration (the reviewer's own
prompt permits exactly that) — this module cannot tell those apart without reading what
the sentence asserts, which is the judgement it refuses to fake. Folding that
"structurally can't say no" case into ``unmeasurable`` would be its own quiet defect:
every verdict a one-sided shape contributes could only ever help the compliant count,
never the non-compliant one, so a reader computing a plain compliance fraction over
"every checkable shape" would get a number biased upward by construction, with nothing
in the data to reveal it — the same one-sided-control failure mode this codebase keeps
finding elsewhere, here inside the very measurement ADR-076 clause 2 gates migrations
on. :data:`ComplianceOutcome.INDETERMINATE` exists to keep that asymmetry visible:
:class:`SignalClassBucket` and the ``REVIEW_COMPLIANCE`` log line carry it as a FOURTH,
separate counter, and :func:`aggregate_by_signal_class`'s docstring states the bounds a
nonzero ``indeterminate`` count forces on any compliance rate read from the bucket.
(The other two shapes — missing-term-add and repetition-reduction — were checked for
the same asymmetry and are genuinely two-sided: neither has a branch where a
plausible-but-unresolvable case is quietly routed toward "compliant"; both directions
are exercised by tests. See ``tests/unit/test_review_compliance.py`` for the pinning
tests either way.)

**Measurement, not judgement (ADR-062 clause 5 exemption).** Like
``measure_reviewer_issues``, everything here is a pure function over two already-produced
drafts — never an LLM call, never a rewrite of either draft, never a signal read back into
`services/reviewer.py`'s control flow. Its output is a log line
(``providers/llm/debug_log.py``'s ``log_review_compliance``); ADR-062 clause 5 names
exactly this shape ("a log, a metric, or a report — never an instruction") as exempt from
the facts/judgements line, and ``measure_reviewer_issues`` is cited there as the
precedent this module extends.

**The measurement opportunity (ADR-076 clause 2 / #537's brief).** Inside
``review_and_refine``'s loop, attempt N's reviewer raises ``issues`` against the draft
that attempt N reviewed (``current_draft`` at that point); if any issue is blocking, the
generator produces a NEW draft, which becomes ``current_draft`` for attempt N+1. That
adjacency — issues raised at round N, draft that exists after round N's corrector call —
is the only pair this module ever compares. Only BLOCKING issues are measured: a minor
issue never reaches the corrector (the severity gate settles the draft instead, see
``reviewer.py``), so there is nothing for the corrector to have implemented.

**Signal classes (aggregation, #537 deliverable 2).** ADR-076 clause 2 wants each future
SIGNAL migration to read its OWN implementation-compliance number before it may migrate.
:func:`classify_signal` sorts a blocking issue into the class of DETERMINISTIC BLOCK it
most plausibly answers, using the same real, stable vocabulary those blocks render into
the reviewer prompt with (grep-verified against ``services/keyword_ledger.py``,
``services/letter_figure_guard.py``, ``services/cross_document.py`` — see
:class:`SignalClass` for the file:line each class traces to). This is intentionally a
TEXT-CUE classifier, not a ``check``-field lookup: the reviewer's optional ``check``
field is a per-prompt-version number (cv_tailoring's checks were renumbered at least
twice — v4, v5, v7 — "later checks are renumbered, not reworded", per that prompt's own
changelog) and letter check 5 alone covers three different deterministic blocks under one
number, so a check-id table would either misclassify silently after the next prompt
edit or be too coarse to separate coverage from presence within one check. Block TITLES
("VERIFIED COVERAGE CHECK", "DO-NOT-CLAIM PRESENCE", "FIGURE OWNERSHIP", "UNADDRESSED
HARD REQUIREMENTS") are the more stable key: they are rendered verbatim by the block's
OWN render function regardless of prompt version, and reviewer prompts explicitly
instruct the model to reference that vocabulary when raising the corresponding issue
("cite that term's own evidence from the block inside your issue text"). Like the
compliance shapes above, classification is best-effort and log-only: an issue matching no
cue lands in ``other``, never in a class it does not belong to.

``under_claim`` is registered as a KNOWN-EMPTY class (ADR-076 clause 5's under-claiming
signal): ``cv_gap_hints.build_gap_hints`` computes exactly the "claimable, in vault,
absent from document" detection this future signal needs, but today it is wired only
into ``cv_section_editor.py`` as an on-demand UI chip, never into any reviewer or
corrector prompt — so no reviewer issue can ever carry this class today. It stays in
:class:`SignalClass` and in every aggregate so its emptiness is visible rather than the
class silently not existing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from applire.services.oracle.matchers.figures import extract_figures
from applire.services.review_issues import QUOTED_RE, REPEATED_RE, ReviewIssue

# --- Signal classes (deliverable 2) -----------------------------------------


class SignalClass(str, Enum):
    """The per-signal-class buckets a future ADR-076 SIGNAL migration reads its own
    implementation-compliance number from. Every value here traces to a REAL emitter
    reaching the reviewer prompt today, except ``UNDER_CLAIM`` (registered known-empty —
    see the module docstring) and ``OTHER`` (the catch-all for issues that answer a real
    blocking check but not one of the named deterministic blocks, e.g. a fabricated
    bullet/certification, an invented employer fact, or a cross-document contradiction —
    every one of those is a genuine ADR-062 clause-1 JUDGEMENT with no deterministic
    block behind it to name)."""

    COVERAGE = "coverage"  # keyword_ledger.py:1234 render_verified_coverage_block,
    # :1412 render_coverage_retention_block — claimable term ABSENT from the draft
    PRESENCE = "presence"  # keyword_ledger.py:1348 render_forbidden_presence_block —
    # DO-NOT-CLAIM term PRESENT in the draft
    ANCHORING = "anchoring"  # letter_figure_guard.py:494 render_figure_ownership_block,
    # cover_letter_positioning.py:412 render_role_titles_block — wrong/missing owner
    FIGURE = "figure"  # review_cv_tailoring.py check 5 (oversell/reattached-figure/
    # agency-vs-proximity), review_cover_letter.py check 1 (ungrounded figure) — a
    # figure's MEANING/framing, distinct from ANCHORING's "whose figure is it"
    UNADDRESSED_REQUIREMENT = "unaddressed_requirement"  # cross_document.py:356
    # render_unaddressed_hard_requirements_block, and letter check 4 REQUIRED CONTENT
    # NOT DELIVERED (positioning_requested) — folded together: both are "required
    # content the draft did not deliver", just from a JD-requirement vs. a
    # positioning-block source respectively
    UNDER_CLAIM = "under_claim"  # ADR-076 clause 5 — NOT YET EMITTED, see module
    # docstring. Registered so its emptiness is visible, never simply absent.
    OTHER = "other"  # a real blocking finding with no deterministic block behind it


#: Ordered (most-specific-first) text cues for :func:`classify_signal`. Each cue is
#: vocabulary the corresponding render function or reviewer-prompt check literally uses,
#: so a reviewer that follows its own prompt's instructions to name what it is checking
#: will trip the matching cue. Order matters: PRESENCE is checked before COVERAGE (both
#: read the Keyword Ledger; "do not claim" is the more specific phrase), and ANCHORING
#: before FIGURE (an anchor complaint often also names a figure).
_SIGNAL_CUES: tuple[tuple[SignalClass, re.Pattern[str]], ...] = (
    (
        SignalClass.PRESENCE,
        re.compile(r"do[\s-]*not[\s-]*claim|forbidden claim|do-not-claim presence", re.IGNORECASE),
    ),
    (
        SignalClass.ANCHORING,
        re.compile(
            r"\bunanchor|\banchor(?:ed|ing)?\b|figure ownership|wrong[\w\s]{0,20}owner|"
            r"missing owner|unattributed|misattribut",
            re.IGNORECASE,
        ),
    ),
    (
        SignalClass.UNADDRESSED_REQUIREMENT,
        re.compile(
            r"unaddressed|hard requirement|positioning_requested|required content|"
            r"gap[_\s]transfer|company_domain_engagement|scope_positioning",
            re.IGNORECASE,
        ),
    ),
    (
        SignalClass.COVERAGE,
        re.compile(
            r"verified coverage|coverage (?:check|already achieved)|claimable (?:term|keyword)|"
            r"absent claimable",
            re.IGNORECASE,
        ),
    ),
    (
        SignalClass.FIGURE,
        re.compile(
            r"\boversell\b|overstat|reattached figure|\bagency\b.{0,30}\bproximity\b|"
            r"minted figure|invented figure|fabricated figure|ungrounded figure",
            re.IGNORECASE,
        ),
    ),
)


def classify_signal(issue_text: str) -> SignalClass:
    """Best-effort, log-only classification of a reviewer issue into the
    deterministic-block SIGNAL class it most plausibly answers (see module docstring).

    Falls back to :data:`SignalClass.FIGURE` when the issue quotes a real figure (via
    the canonical :func:`extract_figures` detector) and none of the named-block cues
    matched, and to :data:`SignalClass.OTHER` otherwise. Never raises; never returns
    ``UNDER_CLAIM`` (nothing emits that shape of issue today — see the module
    docstring)."""
    for signal_class, pattern in _SIGNAL_CUES:
        if pattern.search(issue_text):
            return signal_class
    if extract_figures(issue_text):
        return SignalClass.FIGURE
    return SignalClass.OTHER


# --- Implementation-compliance outcomes (deliverable 1) ---------------------


class ComplianceOutcome(str, Enum):
    IMPLEMENTED = "implemented"
    NOT_IMPLEMENTED = "not_implemented"
    #: A checkable shape MATCHED, but that shape is structurally incapable of
    #: distinguishing "the corrector ignored this" from "the corrector satisfied it in
    #: a way this shape cannot see" (today: the forbidden-claim shape's still-present
    #: branch — see its docstring). Distinct from UNMEASURABLE on purpose: folding the
    #: two together would let a shape that can only ever answer "compliant" or "I
    #: can't tell" quietly inflate the compliant side of the fraction by construction
    #: — the exact one-sided-control failure mode this codebase keeps re-finding,
    #: now in a measurement. Never compute `implemented / (implemented +
    #: not_implemented)` and treat it as a point estimate while any INDETERMINATE
    #: verdicts exist in the same pool — see :func:`aggregate_by_signal_class`.
    INDETERMINATE = "indeterminate"
    #: No checkable shape matched this issue's text at all — genuinely different from
    #: INDETERMINATE ("we recognised the shape but this shape can't resolve this
    #: instance"). Kept separate so the two failure-to-measure reasons stay
    #: individually auditable rather than merging into one catch-all.
    UNMEASURABLE = "unmeasurable"


@dataclass(frozen=True)
class ComplianceVerdict:
    """The deterministic measurement verdict on whether ONE blocking reviewer issue was
    IMPLEMENTED by the corrector's next draft — never a decision the loop acts on (see
    module docstring)."""

    issue: str
    signal_class: SignalClass
    outcome: ComplianceOutcome
    shape: str | None  # which checkable shape matched, or None for `unmeasurable`


# Shape 2 (checked FIRST — more specific): a quoted forbidden term the issue says is
# wrongly present as a candidate claim/fabrication. Absence in the next draft is an
# unambiguous fix (IMPLEMENTED). PRESENCE is deliberately scored INDETERMINATE, never
# NOT_IMPLEMENTED and never UNMEASURABLE, because the reviewer's own prompt
# (review_cover_letter.py check 5, KEYWORD LEDGER — DO NOT CLAIM) permits the SAME
# term to legitimately remain reframed as an aspiration ("wanting to grow into X") —
# telling that apart from an unaddressed fabrication requires reading what the
# sentence asserts, exactly the judgement this module refuses to fake.
#
# THIS SHAPE IS STRUCTURALLY ONE-SIDED: it can only ever return IMPLEMENTED or
# INDETERMINATE, never NOT_IMPLEMENTED — there is no branch where "the term is still
# there" is confidently scored non-compliant. That is a deliberate, unavoidable
# property of the shape (it cannot fake the judgement it is refusing to fake), but it
# means every verdict this shape contributes can only ever help the compliant count,
# never the non-compliant one. INDETERMINATE exists as ITS OWN outcome (not folded
# into UNMEASURABLE) specifically so this asymmetry stays visible in the aggregate
# instead of silently inflating a computed compliance fraction — see
# :func:`aggregate_by_signal_class` for the bounds this forces on any reader.
# test_forbidden_claim_shape_never_returns_not_implemented pins this property so a
# future edit cannot reintroduce a NOT_IMPLEMENTED branch here without a failing test
# forcing a conscious decision about it.
_FORBIDDEN_CLAIM_CUE = re.compile(
    r"do[\s-]*not[\s-]*claim|forbidden claim|fabrication|presented as (?:something )?"
    r"the candidate|as a candidate (?:competence|claim)",
    re.IGNORECASE,
)

# Shape 1: a quoted term the issue says is absent/missing/not delivered/not surfaced —
# the VERIFIED COVERAGE CHECK / UNADDRESSED HARD REQUIREMENTS / REQUIRED CONTENT shape.
# Direction is unambiguous: the demand is always "make it present".
_MISSING_TERM_CUE = re.compile(
    r"\b(?:is|are)\s+(?:not\s+(?:in|present|delivered|surfaced|addressed)|absent|missing)\b|"
    r"does not (?:appear|deliver|address|surface)|not (?:explicitly )?deliver|"
    r"not (?:explicitly )?address",
    re.IGNORECASE,
)


def _quoted_terms(issue_text: str) -> list[str]:
    return [m for m in QUOTED_RE.findall(issue_text) if m.strip()]


def _check_forbidden_claim_shape(issue_text: str, next_text: str) -> ComplianceVerdict | None:
    if not _FORBIDDEN_CLAIM_CUE.search(issue_text):
        return None
    terms = _quoted_terms(issue_text)
    if not terms:
        return None
    still_present = any(term in next_text for term in terms)
    outcome = ComplianceOutcome.INDETERMINATE if still_present else ComplianceOutcome.IMPLEMENTED
    return ComplianceVerdict(issue_text, classify_signal(issue_text), outcome, "forbidden_claim_removed")


def _check_missing_term_shape(issue_text: str, next_text: str) -> ComplianceVerdict | None:
    if not _MISSING_TERM_CUE.search(issue_text):
        return None
    terms = _quoted_terms(issue_text)
    if not terms:
        return None
    now_present = all(term in next_text for term in terms)
    outcome = ComplianceOutcome.IMPLEMENTED if now_present else ComplianceOutcome.NOT_IMPLEMENTED
    return ComplianceVerdict(issue_text, classify_signal(issue_text), outcome, "missing_term_added")


def _check_repetition_shape(
    issue_text: str, current_text: str, next_text: str
) -> ComplianceVerdict | None:
    match = REPEATED_RE.search(issue_text)
    if not match:
        return None
    quoted = match.group(1)
    before = current_text.count(quoted)
    after = next_text.count(quoted)
    outcome = ComplianceOutcome.IMPLEMENTED if after < before else ComplianceOutcome.NOT_IMPLEMENTED
    return ComplianceVerdict(issue_text, classify_signal(issue_text), outcome, "repetition_reduced")


def evaluate_compliance(issue_text: str, current_text: str, next_text: str) -> ComplianceVerdict:
    """Apply the closed set of mechanically-checkable shapes to ONE blocking issue.

    ``current_text``/``next_text`` MUST be :func:`applire.services.load_bearing.
    stringify_draft` of the draft the issue was raised against and the draft the
    corrector produced in response, respectively — exactly the same stringification
    discipline ``review_issues.measure_reviewer_issues`` requires, for the same reason:
    the comparison is meaningless against any other text.

    Order matters: the forbidden-claim shape is checked before the missing-term shape
    because "do not claim" is the more specific cue — an issue matching both would
    otherwise be misread as a coverage demand.

    Returns :data:`ComplianceOutcome.UNMEASURABLE` (shape=None) for any issue that
    matches none of the closed shapes — this is the expected, common case for prose
    judgements (fabricated bullet, oversell, cross-document contradiction, invented
    employer fact) that this module does not attempt to grade. Returns
    :data:`ComplianceOutcome.INDETERMINATE` for the one shape (forbidden-claim) that
    DID match but cannot resolve this instance one-sidedly — see that shape's own
    comment for why UNMEASURABLE would be the wrong label for it."""
    for checker in (_check_forbidden_claim_shape, _check_missing_term_shape):
        verdict = checker(issue_text, next_text)
        if verdict is not None:
            return verdict
    verdict = _check_repetition_shape(issue_text, current_text, next_text)
    if verdict is not None:
        return verdict
    return ComplianceVerdict(issue_text, classify_signal(issue_text), ComplianceOutcome.UNMEASURABLE, None)


@dataclass(frozen=True)
class SignalClassBucket:
    """Aggregated compliance outcomes for ONE signal class, over ONE round's blocking
    issues. A class with zero measured issues this round is a genuine empty bucket
    (``total == 0``), never simply absent from the aggregate — see
    :func:`aggregate_by_signal_class`.

    Four counters, not three, and ``indeterminate`` is NOT folded into
    ``unmeasurable``: the two are different reasons a verdict cannot resolve
    cleanly, and merging them would hide a real bias (see :func:`aggregate_by_
    signal_class` for the bounds this forces on any compliance fraction read from
    this bucket).
    """

    signal_class: SignalClass
    implemented: int
    not_implemented: int
    indeterminate: int
    unmeasurable: int

    @property
    def total(self) -> int:
        return self.implemented + self.not_implemented + self.indeterminate + self.unmeasurable

    @property
    def lower_bound_rate(self) -> float | None:
        """The CONSERVATIVE implementation-compliance rate: every ``indeterminate``
        verdict counted AGAINST compliance, alongside every genuine
        ``not_implemented``. ``None`` when there is nothing to divide by (no
        implemented/not_implemented/indeterminate verdict at all — a bucket holding
        only ``unmeasurable`` verdicts, or none). This is the number ADR-076 clause 2
        migration decisions must be read against — see the module docstring."""
        denom = self.implemented + self.not_implemented + self.indeterminate
        return None if denom == 0 else self.implemented / denom

    @property
    def upper_bound_rate(self) -> float | None:
        """The OPTIMISTIC implementation-compliance rate: every ``indeterminate``
        verdict excluded from the denominator entirely, as if it had never been
        raised. ``None`` when there is no implemented/not_implemented verdict at all.
        Never read this alone — it is only meaningful paired with
        :attr:`lower_bound_rate` as the width of the gap the ``indeterminate`` count
        leaves open."""
        denom = self.implemented + self.not_implemented
        return None if denom == 0 else self.implemented / denom


def measure_corrector_compliance(
    issues: list[ReviewIssue], current_text: str, next_text: str
) -> list[ComplianceVerdict]:
    """Measure implementation compliance for every BLOCKING issue in a reviewer round
    against the draft the corrector produced in response (#537, ADR-076 clause 2).

    Minor issues are excluded by construction: they never reach the corrector (the
    severity gate settles the draft instead of retrying, ``services/reviewer.py``), so
    there is nothing for "the next draft" to have implemented.

    Never an LLM call, never mutates either draft, never returned to
    ``review_and_refine``'s control flow — the caller logs this and moves on, exactly
    like ``review_issues.measure_reviewer_issues``."""
    return [
        evaluate_compliance(issue.text, current_text, next_text)
        for issue in issues
        if issue.is_blocking
    ]


def aggregate_by_signal_class(verdicts: list[ComplianceVerdict]) -> dict[SignalClass, SignalClassBucket]:
    """Roll per-issue compliance verdicts up into a bucket per :class:`SignalClass`.

    Every class in :data:`SignalClass` is present in the result, even with
    ``total == 0`` — a future SIGNAL migration reads its own class's bucket and must be
    able to tell "zero issues of this class were raised this round" from "this class
    does not exist", and ``UNDER_CLAIM`` in particular must always report zero (see
    module docstring) rather than being silently absent from the map.

    **This produces a BOUND, not a point estimate, whenever a bucket's
    ``indeterminate`` count is nonzero** (today: only possible via the forbidden-claim
    shape — see its comment). A reader computing ``implemented / (implemented +
    not_implemented)`` and ignoring ``indeterminate`` gets :attr:`SignalClassBucket.
    upper_bound_rate` — a number biased upward by exactly the fraction of verdicts
    that shape could only ever call "compliant or I can't tell". ADR-076 clause 2
    migration decisions MUST be read against :attr:`SignalClassBucket.
    lower_bound_rate` instead (every ``indeterminate`` counted against compliance) —
    the whole point of the floor is that an unmeasured probabilistic replacement is
    not an upgrade, and reading the optimistic bound would silently reintroduce
    exactly that unmeasured gap under a number that looks measured.
    """
    counts: dict[SignalClass, list[int]] = {sc: [0, 0, 0, 0] for sc in SignalClass}
    for verdict in verdicts:
        row = counts[verdict.signal_class]
        if verdict.outcome is ComplianceOutcome.IMPLEMENTED:
            row[0] += 1
        elif verdict.outcome is ComplianceOutcome.NOT_IMPLEMENTED:
            row[1] += 1
        elif verdict.outcome is ComplianceOutcome.INDETERMINATE:
            row[2] += 1
        else:
            row[3] += 1
    return {
        sc: SignalClassBucket(
            sc,
            implemented=row[0],
            not_implemented=row[1],
            indeterminate=row[2],
            unmeasurable=row[3],
        )
        for sc, row in counts.items()
    }

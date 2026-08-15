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
of **mechanically checkable issue SHAPES** — term presence/absence demands (quoted or
carried by the named coverage vocabulary), figure↔anchor sentence co-occurrence, an
ungrounded-value removal demand on structured output, and a checkable repetition-count
demand — and measures ONLY those, using only ADR-076 clause-4-sanctioned normalisation
(unicode, quotes, dashes, whitespace; token boundaries, never substrings — see
``_term_present`` for the compound trap this closes). Every issue that does not match
one of those shapes is ``unmeasurable``: a first-class outcome, not a failure, and never
silently folded into either the compliant or non-compliant count. A compliance number
computed over a fraction of a round's issues must never be presented as covering the
whole round; :func:`aggregate_by_signal_class` reports the ``unmeasurable`` bucket's size
alongside every other so nobody has to take that on faith.

**A shape that only points one way is a fourth outcome, not a third.** Two shapes are
structurally one-sided, in OPPOSITE directions, and each carries its
:class:`CheckSidedness` on every verdict so the asymmetry is data, not lore.
Forbidden-claim removal is POSITIVE_ONLY: it can prove a term was removed, but a term
still present could mean the corrector ignored the demand OR legitimately reframed the
term as an honest aspiration (the reviewer's own prompt permits exactly that) — telling
those apart requires reading what the sentence asserts, the judgement this module
refuses to fake. The grounded-presence proxy is its mirror, NEGATIVE_ONLY: a demand
like "surface X in a grounded way" is certainly NOT implemented when X is absent, but
X's presence proves nothing — a stuffed keyword satisfies the string and fails the
demand (#250), so present scores INDETERMINATE, never IMPLEMENTED. Folding either
"structurally can't say" case into ``unmeasurable`` would be its own quiet defect:
every verdict a one-sided shape contributes can only ever move the fraction in its
reachable direction, so a reader computing a plain compliance fraction over "every
checkable shape" would get a number biased by construction, with nothing in the data
to reveal it — the same one-sided-control failure mode this codebase keeps finding
elsewhere, here inside the very measurement ADR-076 clause 2 gates migrations on.
:data:`ComplianceOutcome.INDETERMINATE` keeps that visible: :class:`SignalClassBucket`
and the ``REVIEW_COMPLIANCE`` log line carry it as a FOURTH, separate counter, the
``REVIEW_COMPLIANCE_SHAPE`` line breaks every class count down per shape (see
:func:`aggregate_by_shape`), and :func:`aggregate_by_signal_class`'s docstring states
the bounds a nonzero ``indeterminate`` count forces on any compliance rate read from
the bucket. (The remaining shapes — missing-term-add, ungrounded-value-removal,
figure-anchor co-occurrence, repetition-reduction — were checked for the same
asymmetry and are genuinely two-sided; both directions are exercised by tests. See
``tests/unit/test_review_compliance.py`` for the pinning tests either way.)

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
import unicodedata
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
    #: a way this shape cannot see" (the forbidden-claim shape's still-present branch,
    #: and the grounded proxy's present-but-grounding-unknown branch — see each
    #: shape's docstring). Distinct from UNMEASURABLE on purpose: folding the
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


class CheckSidedness(str, Enum):
    """Which verdicts a shape can structurally reach — carried per verdict so the log
    can report it and no reader has to reverse-engineer a shape's reach from its name.

    The ceiling measurement over the 2026-08-15 corpus (#537 follow-up) found the
    one-sidedness problem is not one shape's quirk but a three-way property: a shape is
    either genuinely two-sided, can only ever CONFIRM compliance (forbidden-claim
    removal: term gone proves the fix, term present proves nothing), or can only ever
    CONFIRM non-compliance (grounded-presence proxy: term absent proves the demand was
    not met, term present does NOT prove it was met groundedly — the #250
    keyword-stuffing hole). Folding either one-sided kind into the two-sided counts
    biases the aggregate in that shape's reachable direction, invisibly."""

    TWO_SIDED = "two_sided"
    #: Can return IMPLEMENTED or INDETERMINATE, never NOT_IMPLEMENTED.
    POSITIVE_ONLY = "positive_only"
    #: Can return NOT_IMPLEMENTED or INDETERMINATE, never IMPLEMENTED.
    NEGATIVE_ONLY = "negative_only"


@dataclass(frozen=True)
class ComplianceVerdict:
    """The deterministic measurement verdict on whether ONE blocking reviewer issue was
    IMPLEMENTED by the corrector's next draft — never a decision the loop acts on (see
    module docstring)."""

    issue: str
    signal_class: SignalClass
    outcome: ComplianceOutcome
    shape: str | None  # which checkable shape matched, or None for `unmeasurable`
    sidedness: CheckSidedness | None = None  # None exactly when shape is None


# --- Clause-4 normalisation and token-level term matching --------------------
#
# ADR-076 clause 4 sanctions a CLOSED normalisation set — digit grouping/decimal
# separators, unicode normalisation, punctuation — and nothing more. Everything below
# stays inside that set. What it deliberately does NOT do: stemming, compound splitting,
# synonym/paraphrase matching — those are judgements. Consequence (measured on the
# 2026-08-15 corpus, verified adversarially): a term realised as a morphological variant
# ("Management der Instandhaltung" for `Instandhaltungsmanagement`) scores as absent.
# The matcher therefore errs BOTH ways — up on keyword stuffing (a stuffed term counts
# as present), down on legitimate rephrasing — which is exactly why the grounded-presence
# proxy shape below never returns IMPLEMENTED and every verdict carries its sidedness.

_QUOTE_CHARS = str.maketrans(
    {
        "‘": "'", "’": "'", "‚": "'", "‛": "'",
        "“": '"', "”": '"', "„": '"', "‟": '"',
        "–": " ", "—": " ", "-": " ",
    }
)


def _normalize(text: str) -> str:
    """Clause-4 normalisation only: NFKC, typographic→ASCII quotes, dashes/hyphens to
    spaces (so `ISO-45001` and `ISO 45001` compare equal), whitespace collapsed."""
    text = unicodedata.normalize("NFKC", text).translate(_QUOTE_CHARS)
    return re.sub(r"\s+", " ", text)


def _term_present(term: str, text: str) -> bool:
    """Token-boundary presence of ``term`` in ``text`` after clause-4 normalisation.

    Token-boundary, NOT substring: the 2026-08-15 corpus contains an issue demanding
    `Arbeitssicherheit` whose own text names `Arbeitssicherheitsmanagement` — a
    substring check is satisfied by the compound (German compounds have no separator),
    and `Deutsch` is a substring of `Deutschland`. ``(?<!\\w)``/``(?!\\w)`` instead of
    ``\\b`` so terms that start or end in a non-word character still anchor correctly.
    """
    needle = re.escape(_normalize(term).strip())
    if not needle:
        return False
    return bool(re.search(rf"(?<!\w){needle}(?!\w)", _normalize(text), re.IGNORECASE))


#: Double-quoted values ("Mittelstand") — QUOTED_RE covers only single quotes, but the
#: job_analysis reviewer quotes field values with double quotes, and typographic quotes
#: are normalised to ASCII before this runs (the U+2019 lesson, 2026-07-11).
_DQUOTED_RE = re.compile(r'"([^"]+)"')

#: Unquoted term extraction for the named coverage/requirement vocabulary. The original
#: parser required quoted terms; the 2026-08-15 corpus (gpt-5.6-luna) quotes almost
#: nothing — "the verified claimable keyword Koblenz is absent" carries its term bare.
#: Each pattern captures a short span whose END is delimited by the sentence's own
#: verb/stop token, never an open-ended grab; extraction stays conservative — an issue
#: yielding no term from any of these matches NO shape and stays unmeasurable.
_BARE_TERM_PATTERNS: tuple[re.Pattern[str], ...] = (
    # "the (verified/required) claimable term/keyword X is absent/…"
    re.compile(
        r"claimable\s+(?:term|keyword)s?\s+(?:of\s+)?([^,;.]+?)"
        r"(?=\s+(?:is|are|as|remains?|was|were)\b|[,;.])",
        re.IGNORECASE,
    ),
    # "the deterministic coverage check identifies X as absent"
    re.compile(r"identifies\s+([^,;.]+?)\s+as\s+absent", re.IGNORECASE),
)


def _extract_demand_terms(issue_text: str) -> list[str]:
    """Terms the issue demands present/absent: quoted (single, double, typographic —
    normalised first) preferred; the named bare-term vocabulary as fallback. Order of
    preference, not union: when the reviewer quotes a term, the quoted form is the
    demand, and bare-pattern captures from the same sentence would only add noise."""
    normalized = _normalize(issue_text)
    quoted = [m.strip() for m in QUOTED_RE.findall(normalized) if m.strip()]
    quoted += [m.strip() for m in _DQUOTED_RE.findall(normalized) if m.strip()]
    if quoted:
        return quoted
    bare: list[str] = []
    for pattern in _BARE_TERM_PATTERNS:
        bare += [m.strip() for m in pattern.findall(normalized) if m.strip()]
    return bare


#: Grounding qualifiers that turn a presence demand into a JUDGEMENT demand ("surface X
#: in a grounded way", "accurately", "without claiming sole responsibility"). 10 of the
#: 18 coverage-class issues in the 2026-08-15 corpus carry one. Presence of the term
#: does NOT prove such a demand implemented (the #250 keyword-stuffing hole) — see
#: the grounded-presence proxy branch in `_check_missing_term_shape`.
_GROUNDING_QUALIFIER_CUE = re.compile(
    r"grounded|accurately|without\s+(?:claiming|implying|asserting|adding)|"
    r"non.?inflated|honest\s+positioning|rather\s+than|no\s+unsupported|"
    r"do\s+not\s+add|as\s+required\b",
    re.IGNORECASE,
)

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

# Shape 1: a term the issue says is absent/missing/not delivered/not surfaced —
# the VERIFIED COVERAGE CHECK / UNADDRESSED HARD REQUIREMENTS / REQUIRED CONTENT shape.
# Direction is unambiguous: the demand is always "make it present".
#
# The verb list is CORPUS-DERIVED, not invented: the original three-verb cue measured
# 1 of 50 real issues because real reviewer prose says "does not explicitly state" /
# "omits the verified claimable keyword" / "identifies X as absent" — shapes drawn from
# one prior incident are not a sample of the population (2026-08-15 ceiling
# measurement; design set logs/llm/2026-08-15.jsonl, held-out cross-model set
# 2026-08-14). Extend this ONLY against captured corpora, never from imagination.
_MISSING_TERM_CUE = re.compile(
    r"\b(?:is|are)\s+(?:not\s+(?:in|present|delivered|surfaced|addressed|stated|mentioned)|"
    r"absent|missing|omitted)\b|"
    r"\bas\s+absent\b|\bomits?\b|"
    r"does not (?:appear|deliver|address|surface|state|mention|include|name|present|provide)|"
    r"not (?:explicitly )?(?:deliver|address|state|mention|include|name|present|surface)",
    re.IGNORECASE,
)


def _check_forbidden_claim_shape(issue_text: str, next_text: str) -> ComplianceVerdict | None:
    if not _FORBIDDEN_CLAIM_CUE.search(issue_text):
        return None
    terms = _extract_demand_terms(issue_text)
    if not terms:
        return None
    still_present = any(_term_present(term, next_text) for term in terms)
    outcome = ComplianceOutcome.INDETERMINATE if still_present else ComplianceOutcome.IMPLEMENTED
    return ComplianceVerdict(
        issue_text,
        classify_signal(issue_text),
        outcome,
        "forbidden_claim_removed",
        CheckSidedness.POSITIVE_ONLY,
    )


# Shape 4 (structured output ONLY, opt-in via ``structured_output=True``): a value the
# issue says has no basis in the source — the job_analysis grounding complaint ("The
# keyword \"Mittelstand\" is not present or clearly implied by the posting"). On PROSE
# output this exact demand is the forbidden-claim shape and stays POSITIVE_ONLY: a term
# still present in a sentence may be a legitimate aspiration reframe. On STRUCTURED
# output (classification fields, keyword lists) there is no such escape — a keyword is
# in the list or it is not, a field holds the contested value or it does not — so
# still-present is confidently NOT_IMPLEMENTED and the shape is genuinely two-sided.
# That asymmetry is exactly why the flag is threaded from the caller (job.py knows its
# draft is schema JSON) instead of sniffed from the text: guessing "looks structured"
# would be a judgement.
_UNGROUNDED_VALUE_CUE = re.compile(
    r"ha(?:s|ve) no basis|(?:is|are) not (?:stated|present)|"
    r"not\s+(?:stated|established)\s+or\s+unambiguously\s+implied|unsupported",
    re.IGNORECASE,
)


def _check_ungrounded_value_shape(issue_text: str, next_text: str) -> ComplianceVerdict | None:
    if not _UNGROUNDED_VALUE_CUE.search(issue_text):
        return None
    terms = _extract_demand_terms(issue_text)
    if not terms:
        return None
    still_present = any(_term_present(term, next_text) for term in terms)
    outcome = (
        ComplianceOutcome.NOT_IMPLEMENTED if still_present else ComplianceOutcome.IMPLEMENTED
    )
    return ComplianceVerdict(
        issue_text,
        classify_signal(issue_text),
        outcome,
        "ungrounded_value_removed",
        CheckSidedness.TWO_SIDED,
    )


def _check_missing_term_shape(issue_text: str, next_text: str) -> ComplianceVerdict | None:
    if not _MISSING_TERM_CUE.search(issue_text):
        return None
    terms = _extract_demand_terms(issue_text)
    if not terms:
        return None
    now_present = all(_term_present(term, next_text) for term in terms)
    # A grounding qualifier splits this shape in two. Plain presence demand
    # ("claimable keyword Koblenz is absent"): two-sided, presence IS the fix.
    # Qualified demand ("surface X in a grounded way / accurately / without claiming
    # sole responsibility"): the presence half is checkable, the grounding half is a
    # judgement — a present term may be keyword-stuffed (#250), so presence proves
    # NOTHING about compliance and scoring it IMPLEMENTED would bias the aggregate
    # upward by construction. The proxy therefore only ever CONFIRMS non-compliance
    # (term absent → the demand was certainly not met) and returns INDETERMINATE for
    # the present case — the mirror image of the forbidden-claim shape's one-sidedness,
    # kept visible the same way (own shape name, own sidedness, pinned by test).
    if _GROUNDING_QUALIFIER_CUE.search(issue_text):
        outcome = (
            ComplianceOutcome.INDETERMINATE if now_present else ComplianceOutcome.NOT_IMPLEMENTED
        )
        return ComplianceVerdict(
            issue_text,
            classify_signal(issue_text),
            outcome,
            "grounded_term_present_proxy",
            CheckSidedness.NEGATIVE_ONLY,
        )
    outcome = ComplianceOutcome.IMPLEMENTED if now_present else ComplianceOutcome.NOT_IMPLEMENTED
    return ComplianceVerdict(
        issue_text,
        classify_signal(issue_text),
        outcome,
        "missing_term_added",
        CheckSidedness.TWO_SIDED,
    )


# Shape 5: an anchoring demand — a figure (or claim) must co-occur with its owning
# entity IN THE SAME SENTENCE (the #530/#531 presence-amendment contract; the reviewer
# raises it as "the figure 90 is not anchored to Weberit in the same sentence …").
# Mechanics: the anchor entity is extracted from the issue's own "anchored to X"
# phrase, the figures via the canonical `extract_figures` detector, and the check is
# sentence-level co-occurrence after clause-4 normalisation — string mechanics
# throughout, no reading of what the sentence asserts. Two-sided: some sentence carries
# both → implemented; no sentence does → not implemented. An anchoring issue naming NO
# figure and NO quoted claim (e.g. "this responsibility is not anchored to Weberit")
# yields no checkable pair and stays unmeasurable rather than half-checked.
#: A capitalized entity phrase (employer/proper name), up to four tokens.
_ENTITY = r"((?:[A-ZÄÖÜ][\w&.']*)(?:\s+[A-ZÄÖÜ][\w&.']*){0,3})"
#: Corpus-derived anchor phrasings (2026-08-15/-14): "is not anchored to Weberit …",
#: "anchor that project and its 14 machines directly to Weberit", "does not name
#: Weberit in the same sentence", "Add Weberit as the employer anchor". The entity
#: must be CAPITALIZED — which is precisely what keeps the judgement variant ("re-anchor
#: to the recorded project context") out: no proper name, no mechanical check.
_ANCHOR_ENTITY_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"anchor(?:ed|ing)?\b[^.;]{0,60}?\bto\s+" + _ENTITY),
    re.compile(r"nam(?:e|es|ing)\s+" + _ENTITY + r"\s+in\s+the\s+same\s+sentence"),
    re.compile(r"add\s+" + _ENTITY + r"\s+as\s+the\s+(?:employer\s+)?anchor", re.IGNORECASE),
    # "… not anchored to its (owner) employer (Weberit Kunststofftechnik GmbH) …" —
    # the 2026-08-14 corpus (mistral-medium-3-5) parenthesises the entity.
    re.compile(r"employer\s*\(\s*" + _ENTITY + r"\s*\)"),
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?:;])\s+|\n+")


def _check_anchor_shape(issue_text: str, next_text: str) -> ComplianceVerdict | None:
    normalized_issue = _normalize(issue_text)
    entity = next(
        (m.group(1).strip() for p in _ANCHOR_ENTITY_RES if (m := p.search(normalized_issue))),
        None,
    )
    if entity is None:
        return None
    # Canonical figure VALUES, not raw substrings: extract_figures folds digit
    # grouping/decimal separators into one canonical form (clause 4's sanctioned
    # digit normalisation), so "1.000" in the issue matches "1000" in the draft.
    issue_figures = {f.value for f in extract_figures(issue_text)}
    if not issue_figures:
        return None
    # PER-OCCURRENCE, not per-figure: the demand is that every sentence CARRYING one
    # of the issue's figures names the anchor — "their respective sentences" in the
    # corpus phrasing. An `any()`-style "some sentence carries both" check would score
    # a seven-figure issue implemented after anchoring one of them. A figure the
    # corrector dropped entirely has no carrying sentence and is vacuously anchored
    # (removing the occurrence removes the ownership ambiguity). Known noise, both
    # directions: figure values are matched by canonical VALUE, so an unrelated
    # occurrence of the same number in another sentence is checked too.
    violating = any(
        (issue_figures & {f.value for f in extract_figures(sentence)})
        and not _term_present(entity, sentence)
        for sentence in _SENTENCE_SPLIT_RE.split(next_text)
    )
    outcome = ComplianceOutcome.NOT_IMPLEMENTED if violating else ComplianceOutcome.IMPLEMENTED
    return ComplianceVerdict(
        issue_text,
        classify_signal(issue_text),
        outcome,
        "figure_anchored_in_sentence",
        CheckSidedness.TWO_SIDED,
    )


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
    return ComplianceVerdict(
        issue_text,
        classify_signal(issue_text),
        outcome,
        "repetition_reduced",
        CheckSidedness.TWO_SIDED,
    )


def evaluate_compliance(
    issue_text: str,
    current_text: str,
    next_text: str,
    *,
    structured_output: bool = False,
) -> ComplianceVerdict:
    """Apply the closed set of mechanically-checkable shapes to ONE blocking issue.

    ``current_text``/``next_text`` MUST be :func:`applire.services.load_bearing.
    stringify_draft` of the draft the issue was raised against and the draft the
    corrector produced in response, respectively — exactly the same stringification
    discipline ``review_issues.measure_reviewer_issues`` requires, for the same reason:
    the comparison is meaningless against any other text.

    ``structured_output=True`` (threaded from a caller whose draft is schema JSON —
    classification fields and keyword lists, not prose; today: ``job.py``'s JD
    analysis) unlocks the two-sided ungrounded-value shape and checks it FIRST: on
    structured output a removal demand has no legitimate keep-the-term reframing, so
    scoring still-present as NOT_IMPLEMENTED is safe there and only there — see that
    shape's comment.

    Order matters: ungrounded-value (structured only) before forbidden-claim, because
    on structured output the two-sided reading of the same demand is strictly more
    informative; forbidden-claim before missing-term because "do not claim" is the more
    specific cue — an issue matching both would otherwise be misread as a coverage
    demand; the anchor shape before missing-term because an anchoring complaint often
    also contains missing-term vocabulary about the same sentence.

    Returns :data:`ComplianceOutcome.UNMEASURABLE` (shape=None) for any issue that
    matches none of the closed shapes — this is the expected case for prose judgements
    (fabricated bullet, oversell, cross-document contradiction, the availability/
    notice-period family that names no checkable token at all) that this module does
    not attempt to grade. Returns :data:`ComplianceOutcome.INDETERMINATE` for the two
    one-sided shapes' unresolvable branches (forbidden-claim's still-present, the
    grounded proxy's present-but-grounding-unknown) — see each shape's comment for why
    UNMEASURABLE would be the wrong label."""
    checkers = [_check_forbidden_claim_shape, _check_anchor_shape, _check_missing_term_shape]
    if structured_output:
        checkers.insert(0, _check_ungrounded_value_shape)
    for checker in checkers:
        verdict = checker(issue_text, next_text)
        if verdict is not None:
            return verdict
    verdict = _check_repetition_shape(issue_text, current_text, next_text)
    if verdict is not None:
        return verdict
    return ComplianceVerdict(
        issue_text, classify_signal(issue_text), ComplianceOutcome.UNMEASURABLE, None, None
    )


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
    issues: list[ReviewIssue],
    current_text: str,
    next_text: str,
    *,
    structured_output: bool = False,
) -> list[ComplianceVerdict]:
    """Measure implementation compliance for every BLOCKING issue in a reviewer round
    against the draft the corrector produced in response (#537, ADR-076 clause 2).

    Minor issues are excluded by construction: they never reach the corrector (the
    severity gate settles the draft instead of retrying, ``services/reviewer.py``), so
    there is nothing for "the next draft" to have implemented.

    ``structured_output`` — see :func:`evaluate_compliance`; threaded from the chain
    whose draft is schema JSON rather than prose (today: JD analysis).

    Never an LLM call, never mutates either draft, never returned to
    ``review_and_refine``'s control flow — the caller logs this and moves on, exactly
    like ``review_issues.measure_reviewer_issues``."""
    return [
        evaluate_compliance(
            issue.text, current_text, next_text, structured_output=structured_output
        )
        for issue in issues
        if issue.is_blocking
    ]


def aggregate_by_shape(
    verdicts: list[ComplianceVerdict],
) -> dict[tuple[SignalClass, str], SignalClassBucket]:
    """Roll verdicts up per ``(signal_class, shape)`` — the breakdown the plain
    per-class aggregate cannot show, and the reason the ``REVIEW_COMPLIANCE_SHAPE``
    log line exists: a class's counts can mix genuinely two-sided verdicts with
    one-sided ones, and a reader of the class-level number alone cannot tell how much
    of it a one-sided shape contributed (the exact defect the INDETERMINATE split
    fixed one level up — see ``feedback_onesided_measurement_biases_aggregate``'s
    incident). Unmeasurable verdicts appear under the shape key ``"none"`` so the
    per-shape totals still sum to the class totals."""
    counts: dict[tuple[SignalClass, str], list[int]] = {}
    for verdict in verdicts:
        key = (verdict.signal_class, verdict.shape or "none")
        row = counts.setdefault(key, [0, 0, 0, 0])
        if verdict.outcome is ComplianceOutcome.IMPLEMENTED:
            row[0] += 1
        elif verdict.outcome is ComplianceOutcome.NOT_IMPLEMENTED:
            row[1] += 1
        elif verdict.outcome is ComplianceOutcome.INDETERMINATE:
            row[2] += 1
        else:
            row[3] += 1
    return {
        key: SignalClassBucket(
            key[0],
            implemented=row[0],
            not_implemented=row[1],
            indeterminate=row[2],
            unmeasurable=row[3],
        )
        for key, row in counts.items()
    }


def aggregate_by_signal_class(verdicts: list[ComplianceVerdict]) -> dict[SignalClass, SignalClassBucket]:
    """Roll per-issue compliance verdicts up into a bucket per :class:`SignalClass`.

    Every class in :data:`SignalClass` is present in the result, even with
    ``total == 0`` — a future SIGNAL migration reads its own class's bucket and must be
    able to tell "zero issues of this class were raised this round" from "this class
    does not exist", and ``UNDER_CLAIM`` in particular must always report zero (see
    module docstring) rather than being silently absent from the map.

    **This produces a BOUND, not a point estimate, whenever a bucket's
    ``indeterminate`` count is nonzero** (reachable via the two one-sided shapes:
    forbidden-claim and the grounded proxy — see their comments; a per-shape breakdown
    is available via :func:`aggregate_by_shape`). A reader computing ``implemented / (implemented +
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

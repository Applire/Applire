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

"""LLM Review Layer — reviewer-guided retry loop (ADR-021, Sprint 20;
amended 2026-06-29 / E036 for cap-safety).

review_and_refine() runs a reviewer LLM call after the initial generator output.
If the reviewer rejects the draft it feeds the critique back to the generator
and retries, up to max_retries times.

Severity gate (ADR-021 amended 2026-07-28): a reviewer issue carries
``severity: "blocking" | "minor"`` (see ``prompts/review_severity.py``), and **only a
blocking issue makes the writer run again**. A round that rejects the draft while
raising nothing but minor observations settles it instead — the wave-6 amendment
established that each rewrite is a memoryless regeneration which can erode content an
earlier round had right, so an unnecessary rewrite is a truthfulness risk, not just a
latency one. Parsing is fail-safe in both directions: an issue whose severity cannot be
read as explicitly minor is blocking, and a rejection that enumerates no issue at all
still retries (see ``services/review_issues.py``).

Cap-safety (ADR-021 amended / ADR-047 call-shape taxonomy):
  * The reviewer is **bounded-output-by-contract** — it reads the full draft +
    source (large INPUT is fine) but only ever emits a small {approved, issues,
    feedback} verdict, capped at ``reviewer_max_tokens``. It must never re-emit the
    document, so a capped model can't truncate the verdict (the Mistral-8k crash).
  * Critique is **referential**, not verbatim: the reviewer points at the offending
    location and the **refiner re-reads the source** (``generator_prompt_fn`` now
    takes ``source``) to fix it. This keeps the verdict small.
  * A reviewer or refiner call that still blows the output cap (or times out) is
    caught — the last validated draft ships rather than crashing the flow.

Never raises — on retry exhaustion, reviewer failure, or refiner truncation the last
known-good draft is returned and a WARNING is logged so the issue stays observable.

Observability (#264): every reviewer verdict, and any exhaustion or call failure, is
also logged via the standard (always-on, PII-free) ``applire.llm.review`` logger with
a stable ``REVIEW_VERDICT`` / ``REVIEW_EXHAUSTED`` / ``REVIEW_CALL_FAILED`` prefix, and
each call within the loop is tagged with its role/attempt on the debug-log record (see
``providers/llm/debug_log.py``) — an exhausted review is countable after the fact
without heuristic prompt-matching, and stays visible even when the (dev-only,
prompt-content-bearing) debug log is off.

Retention (#272 Task 3, ADR-058 freeze): the loop above has no no-regression
invariant — each round is a memoryless corrector rewrite, so a reviewer mistake
(or an over-eager correction) can erode content a PRIOR round had right, and later
rounds never recover it. ``retain_if`` is an OPTIONAL, opt-in deterministic
predicate over a settled draft; when supplied, the loop tracks every draft it
produces and, if the FINAL settled draft fails the predicate while an earlier
round's draft satisfied it, substitutes that earlier draft back in (loudly
logged). This never adds an LLM call — it only chooses among drafts the existing
bounded loop already produced. Default ``None`` reproduces today's behaviour for
every existing caller bit-for-bit (proven by test).

Wave-6 loop oscillation fix (ADR-058 freeze — deterministic Python only, no new LLM
pass, no LLM-visible memory; the reviewer prompt stays memoryless):

  * **Cycle detection** (general, applies to every chain): a reviewer mistake in
    round N can be re-applied as damage in round N+1 with nothing in the loop
    noticing, because neither the reviewer nor the loop remembers anything. The
    cheapest deterministic signal that the loop is going in circles: a generator
    retry that reproduces a draft ALREADY produced earlier in this same loop
    (compared via a stable canonical form, ``json.dumps(sort_keys=True)``) can only
    mean the reviewer/generator pair is oscillating — further rounds cannot
    converge. On detection the loop stops immediately (instead of burning the
    remaining ``max_retries``), settles the draft via the existing selection rules
    below, and logs a stable, always-on ``REVIEW_CYCLE_DETECTED`` line (distinct from
    ``REVIEW_EXHAUSTED``) so a document that shipped because of a cycle-stop stays
    countable after the fact, exactly as #264 made exhaustion countable.
  * **``required_fields`` no-regression floor**: an opt-in sequence of field names
    that, once populated in ANY draft this loop produces, must never ship absent
    from the final draft. "Missing" means genuinely absent/empty (``None``, ``""``,
    or an absent key) — never a value the reviewer legitimately changed. When the
    settled draft is missing a declared field, the loop restores that field's value
    from the most recent earlier draft that had it (subject to ``retain_if`` too,
    when both are supplied — a restored value's source draft must also satisfy
    ``retain_if``), and logs the substitution loudly. Fails open (ships the settled
    draft as-is) if no earlier draft ever had the field, rather than fabricate a
    value. This is deterministic SELECTION among already-produced drafts — never a
    quality score or ranking — and adds no LLM call. Default ``None`` reproduces
    today's behaviour bit-for-bit (proven by test).

Retention design v2 (wave-6 follow-up, charter run #6 — cover-letter closing vs.
page-norm conflict): ``retain_if`` alone can express only ONE non-negotiable
structural gate. Run #6 pinned a real conflict the single predicate couldn't
reconcile: the cover-letter condense pass (services/cover_letter.py, ADR-051 §6)
needs a draft that BOTH keeps the closing paragraph (``retain_if``) AND fits the
page-norm word budget — and when a round satisfies only one, closing wins, but that
tradeoff needs to be loud, not silent. ``prefer_if`` is an OPTIONAL second
deterministic, STRUCTURAL-ONLY predicate — never a quality score, never an LLM
call — that only ever narrows the choice among drafts ``retain_if`` already
accepts; it can never select a draft ``retain_if`` rejects, and it is a no-op
when ``retain_if`` is not supplied. Selection order at settle time:
  1. If the settled draft satisfies BOTH ``retain_if`` and ``prefer_if``, ship it.
  2. Else, among ``draft_history``, prefer the most recent EARLIER draft that
     satisfies BOTH — no new LLM call, just a choice among drafts already produced.
  3. Else, fall back to today's ``retain_if``-only selection (most recent draft
     satisfying ``retain_if`` alone, or the settled draft itself if it already
     does) — logged loudly when ``prefer_if`` went unmet, since the loop is
     shipping the non-negotiable structural floor without its secondary
     preference (e.g. over budget but with a genuine closing).
Default ``prefer_if=None`` reproduces the pre-existing ``retain_if``-only
behaviour bit-for-bit (proven by test) — every other ``retain_if`` caller is
unaffected.

Corrector implementation-compliance measurement (#537, ADR-076 clause 2 — the floor
every future SIGNAL migration is gated behind): the #306(a) precision check above
measures whether a reviewer's ISSUE was demonstrably sound; it says nothing about
whether the corrector's NEXT draft actually implemented it. Each round that retries,
the draft reviewed this round and the draft the corrector produces in response are
compared via ``services/review_compliance.py``'s closed set of mechanically checkable
issue shapes, aggregated per ADR-076 signal class, and logged via
``log_review_compliance``. Measurement only, exactly like the precision check: never an
LLM call, never mutates a draft, never read back into this function's control flow —
which issue is blocking, which draft ships, and the retry count are all unchanged by
this measurement (ADR-062 clause 5's exemption; see that module's docstring for why a
compliance grader over arbitrary reviewer prose would itself be the clause-1 violation
ADR-076 exists to stop). FOUR outcomes per issue, not three: one checkable shape is
structurally one-sided (it can prove compliance or say "can't tell", never prove
non-compliance) and its verdicts are counted separately as ``indeterminate`` rather
than folded into ``unmeasurable`` — see ``review_compliance.py`` for why conflating
the two would bias any compliance fraction upward by construction.

Signal fallback wiring (#540, ADR-076 clause 2 amendment, 2026-08-15): clause 2's floor
lets an unmeasurable signal migrate as FALLBACK_APPLY — see
``services/signal_disposition.py`` — but the amendment tightened WHEN that fallback may
fire: "proven to fire on EVERY early-settle path of the review loop — not just literal
retry-exhaustion." This function has SEVEN return points that ship a draft: the
review-disabled short-circuit (``max_retries<=0``), and — inside the loop — reviewer
call failure, approval, minor-only rejection, generator/refiner call failure, cycle
detection, and retry exhaustion. ``signal_ids`` (optional, default ``None``) is the
explicit, per-call opt-in that tells THIS loop which registered signals to consider at
settle time; a signal named there whose ``issue_matches`` still matches something in
``last_issues`` when the draft settles has its ``fallback_fn`` applied, exactly once,
AFTER ``retain_if``/``required_fields``/``settle_guard`` (it is the last resort, not a
competing selection rule) — see ``_apply_signal_fallbacks`` below for the mechanics and
``SignalDispositionRecord`` for why matching runs against reviewer-authored issue text
and what to do about the paraphrase risk that creates.

Explicit-parameter design, not "match every registered signal": a loop that silently
picked up every process-global registration would fire a fallback for a signal this
particular chain never injected an issue for (a false positive by construction — an
unrelated signal's marker string could coincidentally appear in this chain's prose).
Requiring the caller to name which signals THIS invocation cares about keeps the
registry's global scope from leaking into a single chain's behaviour.

The ``max_retries<=0`` path is deliberately EXCLUDED from signal-fallback
consideration, not merely empty of matches. Two independent reasons, either one
sufficient: (1) ``last_issues`` is not just empty there, it does not exist yet — the
review layer never ran, so no issue was ever raised for anything to match against; (2)
ADR-076's fallback is "the bounded sanctioned exception" for when the CORRECTOR could
not implement a signal the reviewer raised — with review disabled, the corrector never
had a round to fail at. Firing the fallback there would make it the PRIMARY mechanism
for every ``max_retries=0`` caller, which is exactly the silent-default clause 2
exists to forbid. The exclusion is structural (that call site never passes a settle
``path``), not incidental — a future ``issue_matches`` written carelessly (matching an
empty string, say) cannot accidentally reach it.

Findings reach the corrector (ADR-083 clause 4, 2026-09): every ``issue`` above already
carries the reviewer's structured verdict — severity-classified, blocking or minor — but
until this amendment only the free-text ``feedback`` string was ever handed to
``generator_prompt_fn``; ``issues[]`` fed exactly one log line and the #306(a)/#537
measurement passes and was then discarded. A real-provider replay measured the gap: a
finding the reviewer raised 5 times out of 5 reached ``feedback`` only 2 times out of 5.
``services/corrector_feedback.py``'s ``fold_issues_into_feedback`` now renders the
BLOCKING subset of ``issues`` into a corrector-facing instruction block and appends it to
``feedback`` (prose stays first) before the retry call — see that module's docstring for
the full design rationale (audience-correct wording, why ``location``/``check`` are not
rendered, and the byte-identical-when-nothing-blocking guarantee). No change to
``generator_prompt_fn``'s signature; only the VALUE of its ``feedback`` argument gains a
section on rounds that carry a blocking finding.
"""

import json
import logging
from collections.abc import Callable, Sequence
from typing import Any

from applire.constants import REVIEW_VERDICT_MAX_TOKENS
from applire.exceptions import LLMTimeoutError, LLMTruncatedError
from applire.providers.llm.base import LLMProvider
from applire.providers.llm.debug_log import (
    log_review_call_failed,
    log_review_compliance,
    log_review_compliance_shape,
    log_review_cycle_detected,
    log_review_exhausted,
    log_review_minor_only,
    log_review_precision,
    log_review_substitution_diff,
    log_review_substitution_refused,
    log_review_verdict,
    log_signal_fallback_applied,
    set_review_call_meta,
)
from applire.providers.llm.debug_log import set_stage as set_llm_log_stage
from applire.services.corrector_feedback import fold_issues_into_feedback
from applire.services.load_bearing import stringify_draft
from applire.services.review_compliance import (
    aggregate_by_shape,
    aggregate_by_signal_class,
    measure_corrector_compliance,
)
from applire.services.review_issues import (
    ReviewIssue,
    ReviewSettle,
    measure_reviewer_issues,
    normalize_issues,
)
from applire.services.signal_disposition import ExhaustionDisposition, get_signal_disposition

logger = logging.getLogger(__name__)


async def review_and_refine(
    source: str,
    draft: dict[str, Any],
    generator_prompt_fn: Callable[[dict[str, Any], str, str], str],
    generator_system: str,
    reviewer_prompt_fn: Callable[[str, dict[str, Any]], str],
    reviewer_system: str,
    provider: LLMProvider,
    max_retries: int,
    generator_max_tokens: int = 4096,
    reviewer_max_tokens: int = REVIEW_VERDICT_MAX_TOKENS,
    chain_id: str = "unknown",
    disable_thinking: bool | None = None,
    retain_if: Callable[[dict[str, Any]], bool] | None = None,
    required_fields: Sequence[str] | None = None,
    prefer_if: Callable[[dict[str, Any]], bool] | None = None,
    load_bearing_fn: Callable[[dict[str, Any]], frozenset[str]] | None = None,
    settle_guard: Callable[[dict[str, Any], list[dict[str, Any]]], dict[str, Any]] | None = None,
    structured_output: bool = False,
    signal_ids: Sequence[str] | None = None,
    signal_issues_fn: Callable[[dict[str, Any]], Sequence[ReviewIssue]] | None = None,
    on_settle: Callable[[ReviewSettle], None] | None = None,
) -> dict[str, Any]:
    """Run a reviewer-guided retry loop over an LLM generator output.

    The reviewer reads source + draft but emits only a small bounded verdict
    (``reviewer_max_tokens``). On rejection the refiner is given the previous draft,
    the reviewer's feedback, AND the source so it can act on referential critique.

    Args:
        source: The original source material — passed to the reviewer AND, since the
                ADR-021 amendment, to the refiner so it can re-read the ground truth.
        draft: The initial generator output to be reviewed.
        generator_prompt_fn: Called as fn(previous_draft, feedback, source) -> str.
        generator_system: The refinement-mode system prompt (NOT the extraction prompt).
        reviewer_prompt_fn: Called as fn(source, draft) -> str.
        reviewer_system: The reviewer's system prompt.
        provider: LLM provider — same instance used by the calling service.
        max_retries: Maximum number of generator retries. 0 = review layer disabled.
        generator_max_tokens: Token budget for the generator retry calls.
        reviewer_max_tokens: Bounded output budget for the verdict (default
                  REVIEW_VERDICT_MAX_TOKENS) — keep it far below generator_max_tokens.
        chain_id: Identifier for the calling chain (cv_extraction, profile_extraction,
                  cv_tailoring, interview_response, interview_question). Used for log dimensionality.
        disable_thinking: Suppress reasoning on both reviewer and generator calls.
                  Set True for short "chrome" loops (e.g. interview-question language
                  review) so a small token budget reaches the answer, not the reasoning
                  trace, under thinking models. Leave None for serious content (CV,
                  cover letter) where reasoning improves quality (ADR-009 amendment).
        retain_if: Optional (#272 Task 3) deterministic, STRUCTURAL-ONLY predicate over
                  a draft — never a quality score, never an LLM call. Default None
                  reproduces today's behaviour exactly (no history tracking overhead
                  beyond a plain list append, no behavioural change). When supplied,
                  every draft produced by this loop (the initial draft, and each
                  generator retry) is tracked; if the draft this call would otherwise
                  return fails ``retain_if`` but an EARLIER draft in this same loop
                  satisfied it, that earlier draft is returned instead, and the
                  substitution is logged at WARNING so it stays observable.
        required_fields: Optional (wave-6 Task 2) sequence of field names that, once
                  populated (non-empty) in ANY draft this loop produces, must never
                  ship absent from the settled draft. Default None reproduces
                  today's behaviour exactly. When supplied, a settled draft missing a
                  declared field has that field's VALUE restored from the most
                  recent earlier draft that had it (that candidate draft must also
                  satisfy ``retain_if`` when one is supplied); fails open (ships the
                  settled draft as-is) if no earlier draft ever had the field.
                  Logged at WARNING when a substitution happens. This is deterministic
                  selection among already-produced drafts — no new LLM call, no
                  scoring or ranking.
        prefer_if: Optional (wave-6 follow-up, charter run #6 Task 2) SECONDARY
                  deterministic, structural-only predicate — a tie-breaker among
                  drafts ``retain_if`` already accepts, never a way to accept a
                  draft ``retain_if`` rejects, and a no-op when ``retain_if`` is
                  None. When the settled draft satisfies ``retain_if`` but not
                  ``prefer_if``, the loop looks (among ``draft_history``) for the
                  most recent earlier draft satisfying BOTH; if found, that draft
                  ships instead. If none exists, the ``retain_if``-only choice
                  ships as before, logged loudly (structure — ``retain_if`` — always
                  wins over this secondary preference). No new LLM call, never a
                  quality score. Default None reproduces today's ``retain_if``-only
                  behaviour bit-for-bit.
        load_bearing_fn: Optional (#306 (b), charter run #7 case 2 — see
                  ``services/load_bearing.py``) deterministic, STRUCTURAL-ONLY
                  measure — never an LLM call, never a general quality score — of
                  how much load-bearing evidence (quantified figures backed by a
                  ``direct``+``claimable`` keyword-ledger concept) a draft
                  retains. A no-op when ``retain_if`` is None (mirrors
                  ``prefer_if``'s contract). When supplied, EVERY candidate the
                  ``retain_if``/``prefer_if`` scan considers substituting in is
                  additionally required not to be STRICTLY evidence-poorer (by
                  count) than the settled draft — a candidate that satisfies the
                  structural predicate(s) but would lose load-bearing figures the
                  settled draft has is skipped (the scan keeps looking further
                  back), and if NO eligible, non-poorer candidate exists the
                  settled draft ships as-is (its structural complaint outstanding
                  beats a clean draft that dropped the numbers). Every actual
                  substitution is logged as a diff (retained/lost/gained), and
                  every refused-for-evidence candidate is logged too — see
                  ``providers/llm/debug_log.py``'s ``log_review_substitution_diff``
                  / ``log_review_substitution_refused``. Default None reproduces
                  today's ``retain_if``/``prefer_if`` behaviour bit-for-bit.
        settle_guard: Optional (ADR-069 clause 4) deterministic, STRUCTURAL-ONLY
                  transform called as ``fn(settled_draft, draft_history)`` on the
                  draft this loop is about to return — after ``retain_if`` and
                  the ``required_fields`` floor. Never an LLM call, never a
                  score. The caller owns the semantics (e.g. the job-analysis
                  level guard: revert level moves the corrector performed but
                  did not declare in ``level_changes`` — a level move between
                  rounds is a computable FACT, and a prompt rule alone is a
                  dead control, #229). Default None is a pure pass-through.
        structured_output: MEASUREMENT-ONLY flag (#537). True when this chain's
                  draft is schema JSON — classification fields and keyword lists,
                  not prose (today: JD analysis). Threaded into
                  ``measure_corrector_compliance`` to unlock the two-sided
                  ungrounded-value compliance shape, which is only safe on
                  structured output (see ``services/review_compliance.py``).
                  Never read by this loop's control flow; changes only what the
                  ``REVIEW_COMPLIANCE`` log lines can measure.
        signal_ids: Optional (#540, ADR-076 clause 2 amendment) explicit list of
                  ``services/signal_disposition.py`` signal ids this INVOCATION cares
                  about. Default ``None`` reproduces today's behaviour exactly — no
                  registry lookup happens, no fallback can fire, every existing caller
                  is bit-identical (also true today because the registry itself still
                  ships empty — nothing has migrated). When supplied, each named
                  FALLBACK_APPLY signal whose ``issue_matches`` matches something in
                  ``last_issues`` at settle time has its ``fallback_fn`` applied to the
                  settled draft, once, on every early-settle path EXCEPT
                  ``max_retries<=0`` (see the module docstring). SHIP_AND_REPORT
                  signals are looked up but never fire anything. Naming a signal_id
                  that never registered raises ``UndeclaredSignalDispositionError``
                  immediately — a caller-side migration bug, not a state to paper
                  over — which is the ONE way this parameter can make the loop raise
                  where it otherwise never does; it only triggers when a caller
                  explicitly opts in with a bad id, never for the production default.
        signal_issues_fn: Optional (#542, ADR-076 clause 5) DETERMINISTIC signal source.
                  Called once per round with the draft that round reviewed, and its
                  ``ReviewIssue``s are folded into the corrector's ``feedback``
                  alongside the reviewer's own blocking issues, through ADR-083 clause
                  4's single transport (``corrector_feedback.fold_issues_into_feedback``
                  — one implementation, five chains, ADR-066). Never an LLM call.

                  **Evaluated only AFTER the ``approved`` and ``minor_only`` early
                  returns** — i.e. only once this loop has already decided to call the
                  corrector. That placement is the whole safety argument and it is
                  structural, not a convention: a deterministic signal here can never
                  create a round, flip ``approved``, change the retry count, or supply
                  exhaustion fuel. The 2026-08-13 precedent ADR-076 restates
                  ("no structural gate on ``approved`` — signals enter as issues for the
                  model to judge, they do not force verdicts") is therefore preserved by
                  construction. The visible consequence, stated rather than hidden: on a
                  round the reviewer APPROVES, the signal never fires at all, and its
                  finding ships — which is exactly why every signal wired here must name
                  a ship-and-report surface (ADR-076 clause 2's floor).

                  Its issues are deliberately kept OUT of ``last_issues``,
                  ``measure_reviewer_issues`` and ``measure_corrector_compliance``: all
                  three read REVIEWER-authored issues, and mixing a deterministic
                  population into them would silently redefine ``REVIEW_EXHAUSTED
                  issues=N``, the #306(a) precision fraction and the #537 compliance
                  fractions. They get their own always-on ``REVIEW_SIGNAL_ISSUES`` line.
                  Fail-safe: a raising ``signal_issues_fn`` is logged and treated as
                  "no signal issues" (the direction that loses a signal, never the
                  round). Default ``None`` reproduces today's behaviour bit-for-bit.
        on_settle: Optional (#563 part D, ADR-021 amended 2026-09-04) report hook,
                  invoked EXACTLY ONCE per call, at the single ``_settle`` site, with a
                  ``ReviewSettle`` describing how this loop ended: the settle path
                  (``None`` for the ``max_retries<=0`` short-circuit — the review layer
                  did not run), whether the last verdict approved, the last verdict's
                  blocking and minor issue texts, the rounds used, and the draft that
                  actually ships. It runs AFTER every selection rule, so ``settled`` is
                  the delivered draft. It returns nothing and cannot change what ships;
                  a raising hook is logged and swallowed, because ADR-021's "never
                  raises" contract must extend to reporting in the direction that loses
                  the report rather than the document. Default ``None`` is a no-op.

    Returns:
        The approved draft, or the last known-good draft if retries are exhausted, the
        reviewer fails, a refiner call truncates/times out, or a cycle is detected —
        subject to the ``retain_if`` substitution and the ``required_fields``
        no-regression floor above, when those are supplied.
    """
    # #272 Task 3 / wave-6 Task 1&2: track every draft this loop produces so the
    # opt-in retain_if / required_fields / cycle-detection logic can consider them at
    # settle time. Cheap (a list append) and inert when none of those are used — the
    # settle helper below short-circuits immediately, and the cycle-canonical set is
    # only ever consulted, never changing behaviour.
    draft_history: list[dict[str, Any]] = [draft]

    def _canonical(d: dict[str, Any]) -> str:
        """Stable string form of a draft for cycle-comparison. The drafts are plain
        JSON-able dicts (per contract); ``sort_keys`` makes key order irrelevant."""
        return json.dumps(d, sort_keys=True, default=str)

    def _is_missing(d: dict[str, Any], field: str) -> bool:
        """'Missing' means genuinely absent/empty — None, '', or an absent key.
        Never a value the reviewer legitimately changed to something else."""
        if field not in d:
            return True
        value = d[field]
        return value is None or value == ""

    def _apply_required_fields(final: dict[str, Any]) -> dict[str, Any]:
        """Restore any declared field's value from the most recent earlier draft
        that had it, if the settled draft is missing it. Deterministic SELECTION
        among already-produced drafts only — no new LLM call, no scoring/ranking."""
        if not required_fields:
            return final
        missing = [f for f in required_fields if _is_missing(final, f)]
        if not missing:
            return final
        result = dict(final)
        restored: list[str] = []
        for field in missing:
            for candidate in reversed(draft_history):
                if _is_missing(candidate, field):
                    continue
                if retain_if is not None and not retain_if(candidate):
                    continue
                result[field] = candidate[field]
                restored.append(field)
                break
        if restored:
            logger.warning(
                "review_and_refine: chain=%s required_fields regression detected — "
                "%r were present in an earlier draft but missing from the settled "
                "draft; restoring each from the last draft that had it (ADR-058 "
                "freeze: choosing among already-produced drafts, no new LLM call).",
                chain_id,
                restored,
            )
        return result

    def _is_evidence_poorer(candidate_score: frozenset[str] | None, final_score: frozenset[str] | None) -> bool:
        """#306 (b): STRICTLY poorer means fewer load-bearing figures retained,
        by count — the cheapest deterministic reading of "poorer ... on that
        measure". A no-op (never poorer) when load_bearing_fn is None."""
        if load_bearing_fn is None or candidate_score is None or final_score is None:
            return False
        return len(candidate_score) < len(final_score)

    def _select_retained_draft(final: dict[str, Any]) -> dict[str, Any]:
        """Choose which draft ships, subject to ``retain_if`` (non-negotiable),
        ``prefer_if`` (a secondary, tie-break-only preference — see the wave-6
        retention-design-v2 docstring above), and ``load_bearing_fn`` (#306 (b) —
        a candidate that satisfies the structural predicate(s) but is STRICTLY
        evidence-poorer than ``final`` is skipped, not substituted). ``retain_if``
        alone reproduces the exact pre-existing algorithm bit-for-bit;
        ``prefer_if`` and ``load_bearing_fn`` only ever narrow the choice among
        drafts ``retain_if`` already accepts."""
        final_retains = retain_if(final)
        final_prefers = prefer_if(final) if prefer_if is not None else True
        if final_retains and final_prefers:
            return final

        final_score = load_bearing_fn(final) if load_bearing_fn is not None else None

        def _log_diff(candidate_score: frozenset[str] | None) -> None:
            if load_bearing_fn is None or candidate_score is None or final_score is None:
                return
            log_review_substitution_diff(
                chain_id,
                retained=list(final_score & candidate_score),
                lost=list(final_score - candidate_score),
                gained=list(candidate_score - final_score),
            )

        # Earlier rounds only — mirrors the original single-predicate scan, most
        # recent first.
        earlier = list(reversed(draft_history[:-1]))

        if prefer_if is not None:
            for candidate in earlier:
                if retain_if(candidate) and prefer_if(candidate):
                    candidate_score = load_bearing_fn(candidate) if load_bearing_fn is not None else None
                    if _is_evidence_poorer(candidate_score, final_score):
                        log_review_substitution_refused(
                            chain_id, "retain_if/prefer_if", list(final_score - candidate_score)
                        )
                        continue
                    logger.warning(
                        "review_and_refine: chain=%s retain_if/prefer_if: the "
                        "settled draft did not satisfy both; substituting an "
                        "earlier round's draft that satisfies BOTH the retention "
                        "predicate and the secondary preference (ADR-058 freeze: "
                        "no new LLM call, only a choice among already-produced "
                        "drafts).",
                        chain_id,
                    )
                    _log_diff(candidate_score)
                    return candidate

        if final_retains:
            # retain_if is satisfied and nothing satisfied both — ship the
            # settled draft, but say so loudly when a secondary preference was
            # supplied and went unmet: structure always wins over the tie-break
            # (wave-6 Task 2 — e.g. the letter ships over its word budget with a
            # genuine closing rather than on-budget without one).
            if prefer_if is not None:
                logger.warning(
                    "review_and_refine: chain=%s retain_if is satisfied but "
                    "prefer_if is not, and no earlier draft in this loop "
                    "satisfied both; shipping the retain_if-satisfying draft "
                    "as-is — the non-negotiable structural floor wins over the "
                    "secondary preference (ADR-058 freeze).",
                    chain_id,
                )
            return final

        for candidate in earlier:
            if retain_if(candidate):
                candidate_score = load_bearing_fn(candidate) if load_bearing_fn is not None else None
                if _is_evidence_poorer(candidate_score, final_score):
                    log_review_substitution_refused(
                        chain_id, "retain_if", list(final_score - candidate_score)
                    )
                    continue
                if prefer_if is not None and not prefer_if(candidate):
                    logger.warning(
                        "review_and_refine: chain=%s retain_if rejected the "
                        "settled draft; substituting an earlier round's draft "
                        "that satisfies the retention predicate instead (ADR-058 "
                        "freeze: no new LLM call, only a choice among already-"
                        "produced drafts). That substitute does not satisfy "
                        "prefer_if either — the structural floor still wins.",
                        chain_id,
                    )
                else:
                    logger.warning(
                        "review_and_refine: chain=%s retain_if rejected the settled "
                        "draft; substituting an earlier round's draft that satisfied "
                        "the retention predicate instead (ADR-058 freeze: no new LLM "
                        "call, only a choice among already-produced drafts).",
                        chain_id,
                    )
                _log_diff(candidate_score)
                return candidate

        logger.warning(
            "review_and_refine: chain=%s retain_if rejected the settled draft "
            "and no earlier draft in this loop satisfied it either; shipping "
            "the settled draft as-is (fail-open — never fabricate to satisfy "
            "the predicate).",
            chain_id,
        )
        return final

    def _apply_signal_fallbacks(final: dict[str, Any], path: str) -> dict[str, Any]:
        """#540 (ADR-076 clause 2 amendment): fire each ``signal_ids`` entry whose
        registered FALLBACK_APPLY disposition's ``issue_matches`` still matches
        something in ``last_issues`` — the caller-named signals THIS invocation
        opted into, checked against the loop's own record of what is still open.

        A no-op when ``signal_ids`` is falsy (default ``None``) — this is the
        behaviour-neutrality guarantee: for any caller that does not pass
        ``signal_ids`` explicitly (every caller today) this function returns its
        input untouched before reaching the registry, the matchers, or any logging.

        Looks up EVERY named id via ``get_signal_disposition`` — which raises
        ``UndeclaredSignalDispositionError`` for an id nobody registered. That is
        deliberate: naming a signal here is a promise this invocation makes about
        what it injected, and an unregistered id means either the migration forgot
        clause 2's declaration or the caller mistyped the id — both are bugs the
        registry's OWN philosophy (fail loudly, never silently default) says should
        surface immediately, not be swallowed into a quiet no-op.

        SHIP_AND_REPORT signals are looked up (so a caller listing a mixed set of
        ids doesn't need to pre-filter) but never act — that disposition's contract
        is that the defect ships and is reported elsewhere (ADR-076 clause 7), never
        patched here.

        Fires each signal AT MOST ONCE: structurally guaranteed, because this
        function itself runs at most once per ``review_and_refine`` call (every
        return point calls ``_settle`` exactly once) — ``dict.fromkeys`` below only
        guards against a caller listing the same id twice in one ``signal_ids``.

        Fail-safe on BOTH migration-authored hooks: ``fallback_fn`` runs inside a
        bare ``except Exception`` — a raising fallback is logged loudly and the
        UN-fallbacked draft ships — and ``issue_matches`` is wrapped the same way,
        with a raise treated as no-match (the missed-fallback direction ships
        exactly what the loop would have shipped anyway; a wrongly-fired fallback
        would apply an edit nobody asked for, the worse direction). Neither hook
        can become a NEW way for this loop to crash (ADR-021's long-standing
        "never raises" contract extends to this step, deliberately)."""
        if not signal_ids:
            return final
        result = final
        for signal_id in dict.fromkeys(signal_ids):
            record = get_signal_disposition(signal_id)
            if record.disposition is not ExhaustionDisposition.FALLBACK_APPLY:
                continue
            if record.issue_matches is None or record.fallback_fn is None:
                # Defensive only — the registry enforces both non-None for
                # FALLBACK_APPLY at registration time; this should never trip.
                continue
            try:
                matched = any(record.issue_matches(text) for text in last_issues)
            except Exception:
                logger.error(
                    "review_and_refine: chain=%s signal_id=%s issue_matches raised "
                    "on settle path=%s; treating as no-match and shipping without "
                    "the fallback (fail-safe: a missed fallback ships exactly what "
                    "the loop would have shipped anyway, while a wrongly-fired one "
                    "would apply an edit nobody asked for).",
                    chain_id,
                    signal_id,
                    path,
                    exc_info=True,
                )
                continue
            if not matched:
                continue  # this signal's issue was resolved before settling
            try:
                result = record.fallback_fn(result)
            except Exception:
                logger.error(
                    "review_and_refine: chain=%s signal_id=%s fallback_fn raised "
                    "on settle path=%s; shipping the un-fallbacked draft rather "
                    "than crash the loop (ADR-076 clause 2 amendment: fail-safe "
                    "on the sanctioned exception itself).",
                    chain_id,
                    signal_id,
                    path,
                    exc_info=True,
                )
                continue
            log_signal_fallback_applied(chain_id, signal_id, path)
        return result

    # #563 (D): the last verdict's normalized issues, split by the ADR-021 severity
    # gate, plus the rounds used — the state `on_settle` reports. A cell rather than a
    # closure variable because `_settle` is defined before the loop that sets it.
    verdict_cell: dict[str, Any] = {"blocking": (), "minor": (), "approved": False, "rounds": 0}

    def _report_settle(settled: dict[str, Any], path: str | None) -> None:
        """Deliver the ``ReviewSettle`` report, once, without ever becoming a new
        way for this function to raise (ADR-021's standing contract)."""
        if on_settle is None:
            return
        try:
            on_settle(
                ReviewSettle(
                    path=path,
                    approved=bool(verdict_cell["approved"]),
                    blocking_issues=tuple(verdict_cell["blocking"]),
                    minor_issues=tuple(verdict_cell["minor"]),
                    rounds=int(verdict_cell["rounds"]),
                    settled=settled,
                )
            )
        except Exception:
            logger.error(
                "review_and_refine: chain=%s on_settle raised on settle path=%s; the "
                "document ships and the report is lost — never the other way round.",
                chain_id,
                path,
                exc_info=True,
            )

    def _settle(final: dict[str, Any], path: str | None = None) -> dict[str, Any]:
        """Apply the optional retention predicate(s), required-fields floor, and
        (#540) signal fallbacks to a draft this function is about to return.

        ``path`` identifies WHICH settle path is calling, and is the trigger for
        signal-fallback consideration: ``None`` (the ``max_retries<=0`` call site,
        the only one that omits it) means fallbacks are never even considered — see
        the module docstring for why that path is structurally excluded. Every
        other call site passes a stable path label.

        With no ``retain_if``/``required_fields``/``settle_guard``/``signal_ids``
        supplied, this is a pure pass-through — behaviour is bit-identical to
        pre-wave-6 (and pre-#540) for every existing caller."""
        settled = final
        if retain_if is not None:
            settled = _select_retained_draft(settled)
        settled = _apply_required_fields(settled)
        if settle_guard is not None:
            settled = settle_guard(settled, list(draft_history))
        if path is not None:
            settled = _apply_signal_fallbacks(settled, path)
        # #563 (D): last, so the report describes the draft that actually ships.
        _report_settle(settled, path)
        return settled

    if max_retries <= 0:
        return _settle(draft)

    # Tag every reviewer/refiner LLM call in this loop for the debug log (no-op in prod).
    set_llm_log_stage(chain_id)

    current_draft = draft
    last_issues: list[str] = []
    # Wave-6 Task 1: canonical forms of every draft seen so far in THIS loop (the
    # initial draft plus every generator retry), so a repeated draft can be
    # recognised the moment it recurs. A set lookup, not a rescan of draft_history.
    seen_canonicals: set[str] = {_canonical(draft)}

    try:
        for attempt in range(max_retries):
            # #264: label this call's role/attempt on the debug-log record — `stage`
            # alone can't tell a reviewer-verdict call apart from a corrector-retry
            # call in the same chain.
            set_review_call_meta("reviewer", attempt + 1)
            try:
                review: dict = await provider.aparse_json(
                    reviewer_prompt_fn(source, current_draft),
                    system=reviewer_system,
                    temperature=0.1,
                    max_tokens=reviewer_max_tokens,
                    disable_thinking=disable_thinking,
                )
            except (LLMTruncatedError, LLMTimeoutError) as exc:
                # The bounded verdict should never blow the cap; if it somehow does (or the
                # call times out) ship the current draft un-reviewed rather than crash.
                log_review_call_failed(chain_id, "reviewer", attempt + 1, type(exc).__name__)
                logger.warning(
                    "review_and_refine: chain=%s reviewer call failed (%s) on attempt %d; "
                    "shipping current draft un-reviewed",
                    chain_id,
                    type(exc).__name__,
                    attempt + 1,
                )
                return _settle(current_draft, path="reviewer_call_failed")

            approved = bool(review.get("approved", False))
            issues = normalize_issues(review.get("issues", []))
            blocking = [i for i in issues if i.is_blocking]
            last_issues = [i.text for i in issues]
            # #563 (D): keep the severity split for the settle report. `last_issues`
            # keeps its flat, unsplit meaning — it feeds the exhaustion log line and
            # the #540 fallback matcher, neither of which may change here.
            verdict_cell.update(
                blocking=tuple(i.text for i in blocking),
                minor=tuple(i.text for i in issues if not i.is_blocking),
                approved=approved,
                rounds=attempt + 1,
            )
            # #264: structured, always-on verdict line — every attempt, approved or
            # not, so retry-round distributions are countable without heuristic
            # prompt-matching over the (dev-only) debug log.
            log_review_verdict(
                chain_id, attempt + 1, max_retries, approved=approved, issues_count=len(last_issues)
            )

            # #306 (a), measurement only since 2026-07-28: how many of this round's
            # issues are demonstrably unsound (self-refuting, or a checkable count
            # claim that is simply wrong). Never an LLM call, and deliberately does
            # NOT change what the loop does — see services/review_issues.py.
            unsound, _verdicts = measure_reviewer_issues(issues, stringify_draft(current_draft))
            log_review_precision(
                chain_id, attempt + 1, raised=len(issues), survived=len(issues) - unsound
            )

            if approved:
                return _settle(current_draft, path="approved")

            if issues and not blocking:
                # ADR-021 amended 2026-07-28: the writer runs again only for a
                # BLOCKING issue. A round that rejected the draft over nothing but
                # minor observations ships it instead — every rewrite is a memoryless
                # regeneration that can erode content an earlier round had right, so
                # it is a truthfulness risk, and one not worth taking to satisfy a
                # wording preference.
                #
                # `issues and` is deliberate and fail-safe: a rejection that
                # enumerates NOTHING (all the substance in `feedback`) is not a
                # minor-only round — it is an unreadable one, and it retries exactly
                # as it did before severity existed.
                log_review_minor_only(chain_id, attempt + 1, minor=len(issues))
                logger.info(
                    "review_and_refine: chain=%s attempt=%d/%d — the reviewer "
                    "rejected the draft but raised no blocking issue (%d minor); "
                    "shipping it rather than spending a rewrite on polish "
                    "(ADR-021 severity gate). Minor issues: %r",
                    chain_id,
                    attempt + 1,
                    max_retries,
                    len(issues),
                    last_issues,
                )
                return _settle(current_draft, path="minor_only")

            feedback = review.get("feedback", "")
            logger.debug(
                "review_and_refine attempt %d/%d rejected. Issues: %r",
                attempt + 1,
                max_retries,
                last_issues,
            )

            # ADR-083 clause 4: `feedback` above is the reviewer's raw prose —
            # it never carried the normalized `issues[]` (severity, blocking
            # gate) computed above, only prose. `corrector_feedback` folds the
            # BLOCKING issues in (prose first, block appended — see
            # services/corrector_feedback.py); when nothing is blocking it is
            # `feedback` unchanged, byte-identical. This is what actually
            # reaches `generator_prompt_fn` — `feedback` itself is untouched.
            corrector_feedback = fold_issues_into_feedback(feedback, issues)

            # #542 (ADR-076 clause 5): deterministic SIGNAL issues join the corrector's
            # feedback through the SAME transport, and only HERE — past the `approved`
            # and `minor_only` early returns, so a signal can never create a round or
            # force a verdict (see the `signal_issues_fn` docstring above). They are
            # rendered by `fold_issues_into_feedback` exactly like reviewer findings
            # because to the corrector they ARE findings; they are kept out of
            # `last_issues` and out of both measurement passes so no existing metric's
            # population changes underneath it.
            signal_issues: list[ReviewIssue] = []
            if signal_issues_fn is not None:
                try:
                    signal_issues = [i for i in signal_issues_fn(current_draft) if i.is_blocking]
                except Exception:
                    logger.error(
                        "review_and_refine: chain=%s signal_issues_fn raised on attempt "
                        "%d; continuing without deterministic signal issues (fail-safe: "
                        "a lost signal ships what the loop would have shipped anyway).",
                        chain_id,
                        attempt + 1,
                        exc_info=True,
                    )
                    signal_issues = []
            if signal_issues:
                corrector_feedback = fold_issues_into_feedback(corrector_feedback, signal_issues)
                logger.info(
                    "REVIEW_SIGNAL_ISSUES chain=%s attempt=%d raised=%d texts=%r",
                    chain_id,
                    attempt + 1,
                    len(signal_issues),
                    [i.text for i in signal_issues],
                )

            retry_prompt = generator_prompt_fn(current_draft, corrector_feedback, source)
            logger.info(
                "review_and_refine: chain=%s attempt=%d retry_input_chars=%d feedback_chars=%d "
                "corrector_feedback_chars=%d",
                chain_id,
                attempt + 1,
                len(retry_prompt),
                # `feedback_chars` deliberately keeps measuring the raw reviewer
                # prose ONLY — unchanged meaning, kept for metric continuity
                # (ADR-083 clause 4 does not silently redefine an existing
                # metric). `corrector_feedback_chars` is the new field: what
                # generator_prompt_fn's `feedback` argument actually contains
                # (prose + folded REVIEWER FINDINGS block, when one exists).
                len(feedback),
                len(corrector_feedback),
            )

            # #537 (ADR-076 clause 2): the draft THIS round's issues were raised
            # against, captured before the generator call below reassigns
            # `current_draft` to the corrector's response — the measurement
            # opportunity is exactly this adjacency (issues raised at round N vs.
            # the draft that exists after round N's corrector call).
            draft_reviewed_this_round = current_draft

            set_review_call_meta("generator", attempt + 1)
            try:
                current_draft = await provider.aparse_json(
                    retry_prompt,
                    system=generator_system,
                    temperature=0.1,
                    max_tokens=generator_max_tokens,
                    disable_thinking=disable_thinking,
                )
            except (LLMTruncatedError, LLMTimeoutError) as exc:
                # The refiner regenerates the document and can blow a small output cap.
                # The pre-refinement draft was already validated (e.g. the segmented
                # generation that produced it), so ship that rather than a truncated
                # refinement or a crash (ADR-021 amended / ADR-047 cap-safety).
                log_review_call_failed(chain_id, "generator", attempt + 1, type(exc).__name__)
                logger.warning(
                    "review_and_refine: chain=%s refiner call truncated/timed out (%s) on "
                    "attempt %d; keeping last known-good draft. Last issues: %r",
                    chain_id,
                    type(exc).__name__,
                    attempt + 1,
                    last_issues,
                )
                return _settle(current_draft, path="generator_call_failed")

            # #537 (ADR-076 clause 2, the floor): did the corrector's NEW draft
            # actually IMPLEMENT this round's blocking issues, not merely make
            # them stop being raised? Measurement only — see
            # services/review_compliance.py for the closed set of mechanically
            # checkable issue shapes and the honest `unmeasurable` bucket for
            # everything else. Never an LLM call, never changes what the loop
            # does or which draft ships; the caller (here) only logs it, exactly
            # like the #306(a) precision measurement two blocks above.
            compliance_verdicts = measure_corrector_compliance(
                issues,
                stringify_draft(draft_reviewed_this_round),
                stringify_draft(current_draft),
                structured_output=structured_output,
            )
            for signal_class, bucket in aggregate_by_signal_class(compliance_verdicts).items():
                if bucket.total == 0:
                    continue
                log_review_compliance(
                    chain_id,
                    attempt + 1,
                    signal_class.value,
                    implemented=bucket.implemented,
                    not_implemented=bucket.not_implemented,
                    indeterminate=bucket.indeterminate,
                    unmeasurable=bucket.unmeasurable,
                )
            # Per-(class, shape) breakdown: the class-level line above cannot show how
            # much of a count a one-sided shape contributed (the same defect the
            # INDETERMINATE split fixed, one level down) — see aggregate_by_shape.
            for (signal_class, shape), bucket in aggregate_by_shape(compliance_verdicts).items():
                log_review_compliance_shape(
                    chain_id,
                    attempt + 1,
                    signal_class.value,
                    shape,
                    implemented=bucket.implemented,
                    not_implemented=bucket.not_implemented,
                    indeterminate=bucket.indeterminate,
                    unmeasurable=bucket.unmeasurable,
                )

            # Wave-6 Task 1: a generator retry that reproduces a draft ALREADY
            # produced earlier in this same loop (the initial draft or any prior
            # retry) is a cycle by definition — the reviewer/generator pair is
            # oscillating and further rounds cannot converge. Stop immediately
            # instead of burning the remaining max_retries, and log it loudly via a
            # stable, always-on line distinct from ordinary exhaustion so a
            # cycle-shipped document stays countable after the fact (mirrors #264).
            current_canonical = _canonical(current_draft)
            if current_canonical in seen_canonicals:
                draft_history.append(current_draft)
                log_review_cycle_detected(chain_id, attempt + 1, max_retries)
                logger.warning(
                    "review_and_refine: chain=%s cycle detected on attempt %d/%d — "
                    "generator retry reproduced an earlier draft in this loop; "
                    "stopping early instead of exhausting retries.",
                    chain_id,
                    attempt + 1,
                    max_retries,
                )
                return _settle(current_draft, path="cycle_detected")
            seen_canonicals.add(current_canonical)

            # #272 Task 3: a fresh draft was produced — track it so an opt-in
            # retain_if can consider it at settle time. No-op cost when
            # retain_if is None (the list is simply never consulted).
            draft_history.append(current_draft)

        # Exhausted all retries — return the last generated draft unreviewed.
        # This is intentional: degraded output is preferable to a broken flow
        # (spec: ADR-021; worst-case call count = 2 * max_retries). #264: this is the
        # "ships silently" case — make it loudly, durably visible.
        log_review_exhausted(chain_id, max_retries, len(last_issues))
        logger.warning(
            "review_and_refine: chain=%s %d retries exhausted. Last known issues: %r",
            chain_id,
            max_retries,
            last_issues,
        )
        return _settle(current_draft, path="exhausted")
    finally:
        # Clear the role/attempt label so a later, unrelated call in this task
        # doesn't inherit a stale review-loop position.
        set_review_call_meta(None, None)

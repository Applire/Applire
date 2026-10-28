# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#659 — the delivered document's redundant bullet pairs reach the CORRECTOR.

## Why this is not a repair pass

ADR-082 decided *detect, never repair* for prose redundancy, and founder ruling 3
(2026-09-05) narrowed that to the DETERMINISTIC layer: a threshold may not delete a
bullet, an LLM corrector reading two sentences may repair one. Ruling W-1 (2026-09-13)
settled the remaining half — on exhaustion the CV ships with the pair REPORTED, and the
pairs enter the corrector's feedback each round as deterministic issues. So this module
computes a pair and writes a sentence; it never touches ``tailored_data``.

## Why a deterministic signal when a reviewer check already exists

`applire-prompt-first` triage, category **C** (asked clearly, model still fails on a real
run) — not B:

* The rule EXISTS. `prompts/review_cv_tailoring.py` carries named blocking check 8,
  REDUNDANCY, on both CV doors since #668 (ADR-082 amendment 2026-09-08), and
  `prompts/review_severity.py` deliberately no longer lists `repetition` among the shared
  minor examples so the check can block.
* It is not reliably acted on AT DELIVERY. The CVs of the 2026-09-10 and 2026-09-11
  delivery runs BOTH shipped with `duplicate-bullets: fail` on their persisted
  `tailored_data`, and both `terminal-review` checks read `exhausted`. Widening or
  re-wording check 8 is the move this project measured and withdrew once already
  (writer rule 7, 2026-08-31: the louder rule reached the model verbatim and was
  violated on its own examples).

What the loop lacks is not a rule — it is the FACT. The reviewer is asked to notice
redundancy by reading; the audit has already computed exactly which pairs are redundant,
one stage later, where nothing in the loop can see it. This is the shape ADR-062 clause 1
licenses: supply the computed fact, leave the judgement (which of the two survives, and
how) to the model. It is the same shape `cv_gap_hints`' under-claim signal and
`limit_grounding`'s ungrounded-limit signal already have.

## The bound

`limit_grounding.limit_signal_issues_fn` states the rule this follows: *an unbounded
deterministic demand is exhaustion fuel*. A document with N redundant bullets has up to
N(N-1)/2 flagged pairs; handing all of them to a corrector that already exhausts would
make the exhaustion worse, not better. :data:`REDUNDANCY_ISSUE_LIMIT` pairs per round,
in render order, recomputed on the CURRENT draft each round — so a pair the corrector has
since resolved stops being demanded, with no state of its own (the bound replaces verdict
memory).
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from applire.prompts.review_severity import SEVERITY_BLOCKING
from applire.services.ats_audit import duplicate_project_pairs, redundant_bullet_pairs
from applire.services.review_issues import ReviewIssue

#: Redundant pairs demanded per corrector round. Two, deliberately: the measured
#: delivered documents carried 1 and 2 flagged pairs, so this is the whole finding on
#: the real population, and it stays a bound rather than a coincidence for the document
#: that carries ten.
REDUNDANCY_ISSUE_LIMIT = 2

#: A bullet is quoted to the corrector at most this long. The corrector's feedback is a
#: bounded budget shared with the reviewer's prose and the other signals, and a pair of
#: full bullets is ~600 characters; the corrector re-reads the draft itself, so the quote
#: only has to IDENTIFY the bullet.
_QUOTE_CHARS = 120


def _quote(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _QUOTE_CHARS else text[: _QUOTE_CHARS - 1] + "…"


def _issue_text(loc_a: str, text_a: str, loc_b: str, text_b: str) -> str:
    """The demand, written for the CORRECTOR's audience (ADR-083 clause 4).

    ``corrector_feedback.render_blocking_issues`` prefixes every line with ``- Fix: ``,
    so this completes an instruction rather than reporting a measurement — the
    2026-08-26 precedent in which a WRITER-audience block reached the corrector with the
    wrong imperative and defeated the reviewer's own feedback.

    Three sentences are load-bearing:

    * it names WHERE both bullets are, because the corrector patches the prose draft and
      one of the two may sit in a composed section it cannot edit (`SF-WRITE.29`);
    * it says *merge or drop the weaker*, never *delete* — deletion of the near-twin is
      exactly what ruling W-1 refused for the deterministic layer, and the corrector is
      the layer that may judge which content is the weaker;
    * it says content may not be LOST, because the failure mode this whole family is
      labelled with (`triage:document-harm`) is a silently missing achievement, and a
      corrector told only "remove the duplicate" has no reason to preserve the detail
      that lives in only one of the two.
    """
    return (
        f"two delivered bullets state the same achievement — [{loc_a}] "
        f'"{_quote(text_a)}" and [{loc_b}] "{_quote(text_b)}". '
        "A reader sees the same accomplishment twice and reads it as padding. "
        "Merge them into the single bullet that carries the most specific evidence, or "
        "drop the weaker wording — but every figure, name and detail that appears in "
        "only ONE of the two must survive in what you keep. If the two genuinely state "
        "different achievements, leave both and say so in your output; never invent a "
        "difference to justify keeping them."
    )


def _demand_answer_texts(
    document: Any, demanded_groups: Sequence[Sequence[str]]
) -> frozenset[str]:
    """786:81 — the delivered bullets that ANSWER an under-claim demand: per demanded
    concept group, the EARLIEST delivered bullet that carries it (render order, the
    writer's own relevance order — the same partition ``bullet_cuts.
    demanded_exempt_indices`` draws for the cap, #666). A demand fires only while no
    bullet states the concept, so its earliest carrier is the bullet the demand
    produced."""
    from applire.services.ats_audit import delivered_bullets
    from applire.services.bullet_cuts import _concepts_carried

    groups = [list(g) for g in demanded_groups if g]
    if not groups:
        return frozenset()
    answers: set[str] = set()
    found: set[int] = set()
    for _where, text in delivered_bullets(document):
        for gi in _concepts_carried(text, groups):
            if gi not in found:
                found.add(gi)
                answers.add(text)
    return frozenset(answers)


def redundancy_signal_issues(
    document: Any | None,
    *,
    limit: int = REDUNDANCY_ISSUE_LIMIT,
    demanded_groups: Sequence[Sequence[str]] = (),
) -> list[ReviewIssue]:
    """The delivered document's redundant bullet pairs as ``ReviewIssue``s.

    ``document`` is the COMPOSED document (the delivered shape, nested projects
    included) — either the typed model or its ``model_dump``. The prose draft alone is
    the wrong subject: #659's six bullets never existed in any writer output, they were
    assembled afterwards by ``_nest_projects``.

    Minted ``blocking`` deliberately, for the same reason the under-claim signal is:
    ``corrector_feedback.render_blocking_issues`` filters to blocking by design, so a
    ``minor`` signal issue would be computed every round and silently dropped — the
    "partial consumption reads as consumption" defect ADR-083 exists to close. The
    severity says *render this*; it forces nothing, because ``review_and_refine``
    evaluates the signal only after it has already decided to run a corrector round.
    """
    if document is None:
        return []
    try:
        pairs = redundant_bullet_pairs(document)
        # 786:81 (E-12 walk, founder 2026-10-22): a bullet added for an ADR-076 cl. 5
        # under-claim demand is exempt from this finding for the rest of the loop. The
        # two signals demanded opposite things about the SAME bullet one round apart
        # (2026-09-17 chain run, recs 18 -> 20: round 1 asked for the Lean bullet,
        # round 2 named it redundant, the corrector deleted it, the CV shipped
        # `narrative-evidence: fail`). The ATS `duplicate-bullets` row still reports
        # the pair — detection is unchanged, only the loop's demand yields.
        answers = _demand_answer_texts(document, demanded_groups) if demanded_groups else frozenset()
        if answers:
            pairs = [p for p in pairs if p[1] not in answers and p[3] not in answers]
    except Exception:  # noqa: BLE001 — a signal may never break the loop
        return []
    issues = [
        ReviewIssue(text=_issue_text(*pair), severity=SEVERITY_BLOCKING)
        for pair in pairs[:limit]
    ]
    # ADR-099 cl. 8 / ADR-094 cl. 3 amended (UAT-2 fix round, WP-B): the same
    # project rendered twice (the ATS `duplicate-project` row) goes the same route
    # — one sentence to the corrector, which decides what to merge; never a
    # deterministic drop (ADR-082 cl. 3). Bounded by one issue per round.
    try:
        projects = duplicate_project_pairs(document)
    except Exception:  # noqa: BLE001
        projects = []
    if projects:
        la, a, lb, b = projects[0]
        issues.append(ReviewIssue(text=_project_issue_text(la, a, lb, b), severity=SEVERITY_BLOCKING))
    return issues


def _project_issue_text(loc_a: str, name_a: str, loc_b: str, name_b: str) -> str:
    """The duplicate-project demand, corrector audience (as :func:`_issue_text`)."""
    return (
        f'the same project is delivered twice — [{loc_a}] "{_quote(name_a)}" and '
        f'[{loc_b}] "{_quote(name_b)}". Keep it in ONE place (under the role it belongs '
        "to) and fold into it every bullet, figure and detail that appears only in the "
        "other copy; nothing that appears in only one copy may be lost."
    )


def redundancy_signal_issues_fn(
    structured_document_fn: Callable[[dict[str, Any]], Any | None],
    *,
    limit: int = REDUNDANCY_ISSUE_LIMIT,
    demanded_groups_fn: Callable[[], Sequence[Sequence[str]]] | None = None,
) -> Callable[[dict[str, Any]], Sequence[ReviewIssue]]:
    """Bind the composition step for ``review_and_refine``'s ``signal_issues_fn``.

    ``structured_document_fn`` maps the round's prose draft to the composed document —
    on the CV terminal chain that is a cache hit on the very composition the reviewer
    was just shown, because the loop evaluates the signal on the same ``current_draft``.
    Recomputed per round, so a pair the corrector has since merged stops being demanded.

    ``demanded_groups_fn`` (786:81) returns the concept groups the under-claim signal
    has demanded so far in this loop (the CV chain's ``demanded_cell``, accumulated
    across rounds — #666); the bullets answering them are exempt from the pairs.
    """

    def fn(draft: dict[str, Any]) -> Sequence[ReviewIssue]:
        try:
            document = structured_document_fn(draft)
        except Exception:  # noqa: BLE001
            return []
        demanded = demanded_groups_fn() if demanded_groups_fn is not None else ()
        return redundancy_signal_issues(document, limit=limit, demanded_groups=demanded)

    return fn

# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#664 / ADR-075 amended 2026-09-11 — is a limit the letter states one the
CANDIDATE stated?

A cover letter may say "I have no direct experience with X". That is a negative
claim about the candidate, delivered in their own name, and it is grounded by
exactly one thing: the candidate having said it. ADR-074 draws the line (a
requirement nobody asked them about is told to the CANDIDATE, not written into
their letter); ADR-075 clause 1 restates it (*"never invent a limit they do not
state"*); reviewer check 1's INVENTED LIMIT bullet is the prose door for it.

The 2026-09-05 delivery run shipped one anyway, and the per-round attribution
(`applire-core/backend/logs/llm/2026-09-05.jsonl`, records 666-686) says why the
prose door was never going to be enough:

* the manufactured limit was **demanded** by the terminal reviewer's own check 5
  in round 2, because `Qualitätsmanagement` reached that round in BOTH the
  VERIFIED COVERAGE CHECK ("claimable — surface it") and the UNADDRESSED HARD
  REQUIREMENTS block ("the candidate does NOT have it — position the gap");
* the next round's check 1 flagged the sentence it had just demanded;
* the corrector repaired it (record 683) — and `LETTER_FINAL_FLOOR`'s page-count
  selection discarded the repair for the condensed composition that still
  carried the false sentence (record 681).

So this module supplies a FACT (ADR-062 clause 1) — *which concepts does this
draft deny, and did the candidate deny them?* — and three consumers use it:
a per-round deterministic issue for the corrector (ADR-076 clause 5), the final
length floor's selection (ADR-076 clause 3 amended), and a settle-time cut of a
sentence no round could ground (ADR-075 amended, clause 2c). Nothing here is an
LLM call and nothing here writes prose: the cut deletes a sentence, whole.

**Why this is not `oracle/extract.py::_is_pure_denial_clause` (ADR-066).**
That predicate was run over THIS population before anything was designed —
242 body sentences from 10 captured letter drafts of the 2026-09-05 chain plus
the 2026-09-10 delivered letter and the pinned `run_2026_08_15` fixture. It
fires 15 times, all true, and **misses the sentence this Bug is about**: its
marker list is Oracle claim-prose register ("habe ich nicht", "keine direkte
Erfahrung") and carries no "beanspruche ich nicht" / "bringe ich nicht mit".
Widening it there would move more clauses into the Oracle's `not_applicable`
bucket — "a false ``not_applicable`` is a hole in the Oracle", its own docstring
— so the two instruments stay separate, the way `oracle/extract.py`'s module
docstring already keeps its own copies of its neighbours' primitives. They serve
different populations with **opposite safe directions**: the Oracle's is *stay
gradeable*, this module's is *never cut an honest sentence*.

**Why the grounding test reads the denied concept's LABEL and never its
statement.** A stated limit names the adjacent STRENGTHS that transfer (ADR-075
clause 3), so testing surface-form presence against the statement TEXT is the
backwards signal ADR-062 deleted as `find_scoped_boundaries`: on the 2026-09-05
run the word `Qualitätssicherung` appears inside a statement as a strength, and
a statement-level test would have "grounded" the manufactured denial with it.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from applire.services.ats_audit import _norm as ats_norm
from applire.services.ats_audit import surface_present

logger = logging.getLogger(__name__)

#: First-person denial markers in LETTER register, DE + EN. A closed literal
#: list — presence of a token, never a proximity or attachment judgement
#: (ADR-062 clause 1; the proximity matcher this codebase deleted in 2026-07-28
#: is named in `cross_document.py`'s module docstring and is not coming back).
#: The DE entries beyond the Oracle's own list are the ones the captured letter
#: population actually uses and the Oracle's misses.
_DENIAL_MARKERS: tuple[str, ...] = (
    # DE
    "habe ich nicht",
    "hatte ich nicht",
    "habe ich bislang nicht",
    "habe ich noch nicht",
    "hatte ich noch nicht",
    "beanspruche ich nicht",
    "bringe ich nicht mit",
    "kann ich nicht vorweisen",
    "verfüge ich nicht",
    "keine direkte erfahrung",
    "keine eigene erfahrung",
    "keine erfahrung",
    "noch nie",
    "noch keine",
    "fehlt mir",
    "mir fehlt",
    "nicht verantwortet",
    "nicht geführt",
    "nicht getragen",
    # EN
    "have not",
    "haven't",
    "has not",
    "hasn't",
    "had not",
    "hadn't",
    "do not have",
    "don't have",
    "does not have",
    "doesn't have",
    "i lack",
    "lacking direct",
    "no direct experience",
    "no experience",
    "never worked",
    "never led",
    "never managed",
    "cannot claim",
    "do not claim",
    # #664 (adversarial): the direct EN calque of "bringe ich nicht mit" — the
    # captured population is DE-only (L measured DE), and this register is a
    # plausible EN rendering of the same construction the DE list already
    # covers; missing it is a false NEGATIVE (an ungrounded limit ships
    # uncaught), the unsafe direction for this module.
    "do not bring",
    "don't bring",
    "does not bring",
    "doesn't bring",
    "not familiar with",
    "unfamiliar with",
    "without experience in",
)

#: Clause boundaries INSIDE one sentence. A denial governs its own clause and
#: never a co-occurring sibling (#207 / #278 / ADR-062). The colon and the
#: spaced dash are here because of a measured false positive: *"Eigenständige
#: Investitionsplanung und Vertriebserfahrung bringe ich nicht mit: SAP PP/MM,
#: Arbeitsplan-Stammdatenbereinigung … übertragen sich auf die Aufgaben."* — the
#: affirming half sits after the colon and yielded SAP / SAP PP / SAP MM as
#: "denied" until the colon split them off.
_SEGMENT_RE = re.compile(
    r"[;,:]\s+"
    r"|\s+[–—-]\s+"
    r"|\s+(?:aber|jedoch|doch|allerdings|dennoch|while|though|but|however)\s+"
)

#: Sentence boundaries, delimiter KEPT so a cut can rejoin what it did not cut.
_SENTENCE_RE = re.compile(r"(?<=[.!?;])(\s+)")


@dataclass(frozen=True)
class UngroundedLimit:
    """One concept a draft denies that the candidate never denied."""

    concept: str
    sentence: str
    paragraph_index: int

    def issue_text(self) -> str:
        return (
            f"Invented limit: the letter states that the candidate lacks "
            f"'{self.concept}', and no stated limit of theirs says so. Remove that "
            f"denial and keep the grounded evidence it throws away. Sentence: "
            f"\"{self.sentence}\""
        )


def split_sentences(paragraph: str) -> list[str]:
    """Sentences of one paragraph, each carrying its own trailing whitespace so
    ``"".join(split_sentences(p)) == p``."""
    if not isinstance(paragraph, str) or not paragraph:
        return []
    parts = _SENTENCE_RE.split(paragraph)
    out: list[str] = []
    # re.split with ONE capturing group yields [text, sep, text, sep, ..., text]
    for i in range(0, len(parts), 2):
        chunk = parts[i]
        sep = parts[i + 1] if i + 1 < len(parts) else ""
        if chunk or sep:
            out.append(chunk + sep)
    return out


def denial_segments(sentence: str) -> list[str]:
    """The segments of ``sentence`` that CARRY a denial marker — never a
    co-occurring sibling clause. Empty when the sentence states no limit."""
    if not isinstance(sentence, str):
        return []
    low = sentence.lower()
    if not any(marker in low for marker in _DENIAL_MARKERS):
        return []
    return [
        seg
        for seg in _SEGMENT_RE.split(sentence)
        if seg and any(marker in seg.lower() for marker in _DENIAL_MARKERS)
    ]


def _ledger_forms(entry: dict[str, Any]) -> list[str]:
    concept = entry.get("concept", "") or ""
    forms = [concept, *(entry.get("surface_forms") or [])]
    seen: set[str] = set()
    out: list[str] = []
    for form in forms:
        if form and form not in seen:
            seen.add(form)
            out.append(form)
    return out


def _denied_labels(
    denied_concepts: list[Any] | None, keyword_ledger: list[dict[str, Any]] | None
) -> list[str]:
    """The GROUNDED set: every concept LABEL the candidate denied — from the
    persisted ``denied_concepts`` records and from ledger rows the denial floor
    already wrote as ``status == "denied"`` (ADR-048 ``_denied_row``).

    Labels only, never ``statement`` text — see the module docstring."""
    labels: list[str] = []
    for denial in denied_concepts or []:
        if isinstance(denial, str):
            label = denial
        elif isinstance(denial, dict):
            label = denial.get("concept", "") or ""
        else:
            label = getattr(denial, "concept", "") or ""
        if label:
            labels.append(label)
    for entry in keyword_ledger or []:
        if isinstance(entry, dict) and entry.get("status") == "denied":
            labels.extend(_ledger_forms(entry))
    return labels


def _concepts_denied_in(segment: str, keyword_ledger: list[dict[str, Any]] | None) -> list[str]:
    """Ledger concepts whose surface form appears in this denial segment.

    ``surface_present`` is a substring predicate by design (US212), so
    ``Fertigung`` is "present" in ``Fertigungsdigitalisierung``. Longest match
    wins: a concept whose matched form is a proper substring of another matched
    form is dropped, because the longer form is what the sentence is about."""
    segment_norm = ats_norm(segment)
    matched: list[tuple[str, str]] = []
    for entry in keyword_ledger or []:
        if not isinstance(entry, dict):
            continue
        concept = entry.get("concept", "") or ""
        if not concept:
            continue
        hits = [f for f in _ledger_forms(entry) if surface_present(f, segment_norm)]
        if hits:
            matched.append((concept, max(hits, key=len)))
    out: list[str] = []
    for concept, form in matched:
        form_norm = ats_norm(form)
        covered = any(
            ats_norm(other) != form_norm and form_norm in ats_norm(other)
            for _, other in matched
        )
        if not covered:
            out.append(concept)
    return list(dict.fromkeys(out))


def ungrounded_limits(
    letter_data: dict[str, Any] | None,
    keyword_ledger: list[dict[str, Any]] | None,
    denied_concepts: list[Any] | None,
) -> list[UngroundedLimit]:
    """Every concept this draft denies that the candidate never denied.

    Pure, deterministic, ``None``/malformed tolerant. Fails in the direction
    that does NOT cut: an unreadable shape, an empty ledger, or a concept whose
    form appears anywhere in a denied LABEL all yield "grounded"."""
    body = (letter_data or {}).get("body") or {}
    paragraphs = body.get("paragraphs") if isinstance(body, dict) else None
    if not isinstance(paragraphs, list):
        return []
    grounded_norm = [ats_norm(label) for label in _denied_labels(denied_concepts, keyword_ledger)]

    findings: list[UngroundedLimit] = []
    for index, paragraph in enumerate(paragraphs):
        if not isinstance(paragraph, str):
            continue
        for sentence in split_sentences(paragraph):
            concepts: list[str] = []
            for segment in denial_segments(sentence):
                concepts.extend(_concepts_denied_in(segment, keyword_ledger))
            for concept in dict.fromkeys(concepts):
                forms = [concept]
                for entry in keyword_ledger or []:
                    if isinstance(entry, dict) and entry.get("concept") == concept:
                        forms = _ledger_forms(entry)
                        break
                if any(
                    surface_present(form, label)
                    for label in grounded_norm
                    for form in forms
                ):
                    continue
                findings.append(
                    UngroundedLimit(
                        concept=concept,
                        sentence=sentence.strip(),
                        paragraph_index=index,
                    )
                )
    return findings


def has_no_ungrounded_limit(
    letter_data: dict[str, Any] | None,
    keyword_ledger: list[dict[str, Any]] | None,
    denied_concepts: list[Any] | None,
) -> bool:
    """Structural predicate over a composed letter — true when it invents no limit."""
    return not ungrounded_limits(letter_data, keyword_ledger, denied_concepts)


def cut_ungrounded_limits(
    letter_data: dict[str, Any],
    keyword_ledger: list[dict[str, Any]] | None,
    denied_concepts: list[Any] | None,
) -> tuple[dict[str, Any], list[UngroundedLimit]]:
    """ADR-075 amended clause 2c — the LAST resort: delete every body sentence
    still carrying an ungrounded limit and return the letter plus what was cut.

    Deletion only; nothing is rewritten or replaced (founder ruling L-1). Fails
    OPEN: when the cut would leave the body with no paragraph at all, the
    original letter is returned untouched and the finding is still reported, so
    the terminal review still names it."""
    findings = ungrounded_limits(letter_data, keyword_ledger, denied_concepts)
    if not findings:
        return letter_data, []

    cut_by_paragraph: dict[int, set[str]] = {}
    for finding in findings:
        cut_by_paragraph.setdefault(finding.paragraph_index, set()).add(finding.sentence)

    body = dict(letter_data.get("body") or {})
    paragraphs: list[str] = list(body.get("paragraphs") or [])
    rebuilt: list[str] = []
    for index, paragraph in enumerate(paragraphs):
        if index not in cut_by_paragraph or not isinstance(paragraph, str):
            rebuilt.append(paragraph)
            continue
        keep = [
            sentence
            for sentence in split_sentences(paragraph)
            if sentence.strip() not in cut_by_paragraph[index]
        ]
        remainder = "".join(keep).strip()
        if remainder:
            rebuilt.append(remainder)

    if not rebuilt:
        logger.warning(
            "limit_grounding: cutting %d ungrounded limit sentence(s) would empty the "
            "letter body — keeping the letter and reporting instead (fail-open)",
            len(findings),
        )
        return letter_data, findings

    out = dict(letter_data)
    body["paragraphs"] = rebuilt
    out["body"] = body
    logger.warning(
        "LETTER_LIMIT_CUT: removed %d sentence(s) carrying an ungrounded limit (%s)",
        len(cut_by_paragraph),
        ", ".join(sorted({f.concept for f in findings})),
    )
    return out, findings


def limit_signal_issues_fn(
    keyword_ledger: list[dict[str, Any]] | None,
    denied_concepts: list[Any] | None,
    compose_fn,
    *,
    limit: int = 2,
):
    """Bind the ledger + denials to ``review_and_refine``'s ``signal_issues_fn``
    (ADR-076 clause 5): each round's own draft is composed and scanned, and every
    ungrounded limit it still carries reaches the corrector as a blocking issue
    through ADR-083 clause 4's single transport.

    Capped at ``limit`` per round for the same reason check 5's demand is capped
    — an unbounded deterministic demand is exhaustion fuel. It can never create
    a round, flip ``approved`` or change the retry count: clause 5's placement in
    ``review_and_refine`` (after the ``approved`` / ``minor_only`` early returns)
    is what guarantees that, structurally."""
    from applire.prompts.review_severity import SEVERITY_BLOCKING
    from applire.services.review_issues import ReviewIssue

    def fn(draft: dict[str, Any]):
        try:
            composed = compose_fn(draft)
        except Exception:  # pragma: no cover - defensive; a signal never breaks the loop
            logger.warning("limit_grounding: compose failed for the signal", exc_info=True)
            return []
        return [
            ReviewIssue(text=f.issue_text(), severity=SEVERITY_BLOCKING)
            for f in ungrounded_limits(composed, keyword_ledger, denied_concepts)[:limit]
        ]

    return fn

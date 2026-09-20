# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""F-5 (#672 line 124) — does the SIGNATURE STORY's measured figure reach the CV?

**The triage, done in this order (`applire-prompt-first`), before a line was written.**

*Step 1 — does the prompt ask?* Yes, verbatim. ``prompts/cv_tailoring.py`` rule 3:
*"The profile's signature_stories are curated for exactly this: EVERY story's measured
outcome figure must appear in the document, in the work entry or nested project that
owns it — a story whose figure is missing is the strongest evidence cut first, the exact
mistake this rule forbids."* So this is category **C**, not a specification gap, and
widening rule 3 is the move measured to fail (2026-08-31: a widened rule reached the
model verbatim and was violated in round 1 on its own examples).

*Step 2 — can the model see the story?* Yes, completely and unconditionally.
``MasterProfileData.signature_stories`` is a profile-ROOT list; ``exclude_unconfirmed``
touches only skills/languages/certifications; ``prompt_view.prompt_profile_view``
allowlists only under ``metadata``; ``prompts/cv_tailoring.build_user_prompt``
json-dumps the whole dict into ``CANDIDATE PROFILE``. There is no role-level whitelist
to drop it — the field was never nested in a role. Not a seam defect.

*Step 2b — who reads what the model does with it?* **Nobody.** Positive set exhausted
2026-09-20: reviewer checks 1–10 in ``prompts/review_cv_tailoring.py`` never demand a
story's figure; the closest, check 9 CLAIM BALANCE, is terminal-round-only and
explicitly *"severity 'minor', NEVER 'blocking'"*. ``cv_budget.condense_to_budget`` can
only protect a figure-bearing bullet that already exists. ``_prefer_measured_outcomes``
only upgrades a bullet the writer already drafted about that same initiative
(``outcome_preference.find_paired_outcome``'s owner scope + token overlap). The
``signature_story`` FactPin demand (``pin_reach``) requires the candidate to have pinned
the quote by hand. **A story the writer's generative pass simply never picks up has no
net anywhere in the pipeline** — which is exactly what the founder UAT of 2026-09-20
found: the story elicited in the same run (three sites, one shared validation strategy,
~80 % less validation effort) was absent, its role got one generic bullet, and both
blind panel reviewers asked for precisely that material.

**What this module therefore does, and does not.** It supplies the reviewer a **FACT**
— the story's measured figures, the entry that owns them, and whether each figure is
literally present in the composed document — and leaves the **JUDGEMENT** (is this
story's evidence actually carried, is the bullet honest) to the reviewer. ADR-062
clause 1 in its intended shape: the deterministic layer computes presence, the model
decides. It writes nothing into any draft and never gates delivery.

**One implementation per capability (ADR-066).** Figure extraction is
``oracle.matchers.figures.extract_figures`` — THE canonical detector the Oracle, the
letter figure-guard and the load-bearing tier already share — reached through
``load_bearing.figures_present``/``_figure_key`` so "is this figure in this text" is the
same question here as everywhere else. Boundary-safe corpus assembly is
``ats_audit.join_corpus_fragments`` (#415: a bare ``"\\n".join`` does not survive
``_norm``'s whitespace collapse, so two unrelated fragments read as one phrase).

**Shape copied deliberately from ``pin_reach.pinned_facts_reviewer_prompt_fn``** (#580):
one wrapper per ``review_and_refine`` invocation = one demand bound, a COMPLETE
statement rather than a prohibition (a block that lists only the absent items had the
reviewer re-deriving the present ones — ``feedback_prohibition_is_not_an_answer``), and
an ``on_demand`` callback so the caller can see what was asked for.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from applire.services.ats_audit import join_corpus_fragments
from applire.services.load_bearing import _figure_key, figures_present

logger = logging.getLogger(__name__)

#: Adversarial finding (Nougat UAT-fixes batch, adv pass, 2026-09-20): the shared
#: ``oracle.matchers.figures.extract_figures``'s ``_PERCENT_RE`` requires a literal
#: ``%`` character, so the idiomatic spelled-out form ("80 Prozent", "80 percent")
#: falls through to ``_NUMBER_RE`` and extracts as a DIFFERENT figure kind
#: (``number`` instead of ``percent``) — falsifying this module's own documented
#: claim that ``80 %`` / ``80%`` / ``80 Prozent`` are the same figure. Normalising
#: the spelled-out form to the symbol form BEFORE ``extract_figures`` runs, scoped
#: to this module only, closes the gap for the story-figure scan without widening
#: the shared extractor (``services/oracle/**`` is nobody's file to touch here —
#: the Oracle keeps its own contract). Word-boundary-guarded so "80 percentage
#: points" or "80 Prozentsatz" is never mistaken for a bare percent figure.
_PERCENT_WORD_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(?:Prozent|percent|per cent)\b", re.IGNORECASE
)


def _normalize_percent_words(text: str) -> str:
    """Rewrite every spelled-out percent in ``text`` to its symbol form.

    ``"um 80 Prozent"`` -> ``"um 80 %"``; ``"80%"``/``"80 %"`` pass through
    unchanged (there is nothing for the pattern to match). Applied to BOTH texts
    this module scans — a signature story's own ``outcome`` (:func:`story_figures`)
    and the composed document (:func:`figures_missing_from`) — so the identity
    check is symmetric: a story recorded from a "Prozent"-phrased outcome and a
    document that restates it with the sign (or vice versa) still match.
    """
    return _PERCENT_WORD_RE.sub(lambda m: f"{m.group(1)} %", text)

#: Figure kinds a signature story's measured outcome may be demanded on. The same
#: narrow set ``load_bearing`` calls load-bearing: a percent or a currency amount is
#: the "a hiring reviewer checks for this number by name" class. A year is a date and
#: a bare number is noise, so demanding either would spend a blocking round on
#: something no reader treats as evidence.
DEMANDABLE_FIGURE_KINDS = frozenset({"percent", "currency"})

#: Upper bound on demands per invocation, whatever the vault holds. Interviews elicit
#: a handful of stories; a vault with fifty would otherwise be able to fill a whole
#: reviewer round with demands and crowd out every other check.
STORY_DEMAND_LIMIT = 3


@dataclass(frozen=True)
class StoryFigure:
    """One signature story's demandable measured figure. A FACT, not a judgement."""

    story_id: str
    title: str
    #: Verbatim substring of the story's ``outcome`` — what the reviewer quotes.
    raw: str
    kind: str
    value: str
    #: Human label of the work/project entry that owns the story, for the reviewer's
    #: feedback ("in which entry should this appear").
    entry_label: str

    @property
    def key(self) -> str:
        return _figure_key(self.kind, self.value)


def _entry_label(profile_json: dict[str, Any], refs: Iterable[str]) -> str:
    """"Company — Role" of the first referenced work entry, or a neutral fallback.

    A story is anchored to its experience by ``experience_refs`` (ADR-044: the
    referenced experience owns time), never nested in it, so the label has to be looked
    up. A ref that resolves to nothing degrades to a neutral phrase rather than raising
    — this module may never become a way for generation to fail.
    """
    ids = {r for r in refs if r}
    for key in ("work_experience", "volunteering"):
        for entry in profile_json.get(key) or []:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("id") or "") in ids:
                company = entry.get("company") or entry.get("organization") or ""
                role = entry.get("role") or entry.get("title") or ""
                label = " — ".join(p for p in (company, role) if p)
                if label:
                    return label
    return "the entry this story belongs to"


def story_figures(profile_json: dict[str, Any]) -> list[StoryFigure]:
    """Every demandable measured figure of every signature story on this profile.

    Reads ``outcome`` only. ``benchmark`` is the comparison the candidate drew, not the
    result they achieved, and ``challenge``/``mechanism`` are setting and method — a
    figure in any of them is not the story's measured outcome and demanding it would
    put a number on the page that the story does not claim.
    """
    out: list[StoryFigure] = []
    seen: set[tuple[str, str]] = set()
    for story in profile_json.get("signature_stories") or []:
        if not isinstance(story, dict):
            continue
        outcome = story.get("outcome")
        if not isinstance(outcome, str) or not outcome.strip():
            continue
        label = _entry_label(profile_json, story.get("experience_refs") or [])
        from applire.services.oracle.matchers.figures import extract_figures

        for figure in extract_figures(_normalize_percent_words(outcome)):
            if figure.kind not in DEMANDABLE_FIGURE_KINDS:
                continue
            dedupe = (str(story.get("id") or ""), _figure_key(figure.kind, figure.value))
            if dedupe in seen:
                continue
            seen.add(dedupe)
            out.append(StoryFigure(
                story_id=str(story.get("id") or ""),
                title=str(story.get("title") or "(untitled story)"),
                raw=figure.raw,
                kind=figure.kind,
                value=figure.value,
                entry_label=label,
            ))
    return out


def _document_text(document: dict[str, Any]) -> str:
    """Every leaf string of a composed document, joined boundary-safely (#415)."""
    parts: list[str] = []

    def _walk(node: Any) -> None:
        if isinstance(node, str):
            parts.append(node)
        elif isinstance(node, dict):
            for value in node.values():
                _walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                _walk(value)

    _walk(document)
    return join_corpus_fragments(parts)


def figures_missing_from(
    figures: Iterable[StoryFigure], document: dict[str, Any]
) -> list[StoryFigure]:
    """Which of these story figures the composed document does NOT carry.

    Presence is figure identity, not string matching: ``80 %`` and ``80%`` are the same
    figure to ``extract_figures`` directly, and a demand keyed on the literal would be
    raised against a document that already carries the number. ``80 Prozent`` /
    ``80 percent`` reach the SAME identity too, but only because THIS module normalises
    the spelled-out unit to the symbol form before the figure scan runs
    (:func:`_normalize_percent_words`, adversarial finding, 2026-09-20) —
    ``extract_figures`` itself still does not unify them; see its own module for that
    contract.
    """
    present = figures_present(_normalize_percent_words(_document_text(document)))
    return [f for f in figures if f.key not in present]


def render_story_figures_check_block(
    *, present: list[StoryFigure], demand: list[StoryFigure], already: list[StoryFigure]
) -> str:
    """The reviewer's deterministic block — ``""`` when there is no story to report.

    A COMPLETE statement: every story figure is accounted for, so the reviewer has its
    answer and nothing to re-derive. English only — reviewer prompts are English.
    """
    if not (present or demand or already):
        return ""
    lines = [
        "SIGNATURE STORY FIGURES (deterministic figure scan over the composed document — "
        "this is ground truth, do not re-derive it). The candidate's curated stories and "
        "whether each story's MEASURED OUTCOME figure reached the page:"
    ]
    if present:
        lines.append(
            "PRESENT — the figure is on the page; no action, and never a check-11 issue:"
        )
        for f in present:
            lines.append(f'  - "{f.title}" ({f.entry_label}): {f.raw}')
    if demand:
        lines.append(
            "MISSING — raise check 11 as BLOCKING, ONE issue per story: the strongest "
            "evidence this candidate has is not on the page. Put the story title and the "
            "figure in the issue text and name in `feedback` which work entry or nested "
            "project it belongs in; the corrector writes ONE bullet for that entry from "
            "the signature story in its source, in the candidate's own terms. It never "
            "invents a number, never restates a figure the document already carries and "
            "never moves a story into an entry that did not own it. PRECEDENCE, as for a "
            "pinned quote: if the figure cannot be placed without breaking check 1, 4, 5 "
            "or 6(b), report that as \"minor\" naming the check that outranks it instead "
            "of demanding the figure."
        )
        for f in demand:
            lines.append(f'  - "{f.title}" ({f.entry_label}): {f.raw}')
    if already:
        lines.append(
            "ALREADY DEMANDED this loop and still absent — a story figure is demanded at "
            "most once; do NOT demand it again (it is reported as unmet):"
        )
        for f in already:
            lines.append(f'  - "{f.title}" ({f.entry_label}): {f.raw}')
    return "\n".join(lines)


def story_figures_reviewer_prompt_fn(
    base_fn: Callable[[str, dict], str],
    profile_json: dict[str, Any],
    *,
    structured_document_fn: Callable[[dict], dict[str, Any]] | None = None,
    on_demand: Callable[[list[StoryFigure]], None] | None = None,
):
    """Wrap a ``reviewer_prompt_fn(source, draft)`` so every round sees the CURRENT
    document's story-figure state (the ``coverage_reviewer_prompt_fn`` shape, #122).

    ``structured_document_fn`` maps the round's draft to the COMPOSED document — the
    same cache hit the coverage and under-claim wrappers use, because a story's bullet
    can be one the deterministic tail assembled (``_nest_projects``) and a prose-only
    scan would demand a figure that is already on the page (#659's class).

    One wrapper = one ``review_and_refine`` invocation = one demand bound per figure,
    at most :data:`STORY_DEMAND_LIMIT` demands per round. Fail-safe throughout: any
    exception leaves the prompt untouched, because a reviewer-prompt decoration may
    never become a new way for generation to fail (ADR-021's standing contract).
    """
    try:
        figures = story_figures(profile_json)
    except Exception:
        logger.exception("story_reach: could not read signature stories; no block appended")
        figures = []
    demanded: set[str] = set()

    def fn(source: str, draft: dict) -> str:
        prompt = base_fn(source, draft)
        if not figures:
            return prompt
        try:
            document = structured_document_fn(draft) if structured_document_fn else draft
            missing = figures_missing_from(figures, document)
            missing_keys = {f.key for f in missing}
            present = [f for f in figures if f.key not in missing_keys]
            demand: list[StoryFigure] = []
            already: list[StoryFigure] = []
            for figure in missing:
                if figure.key in demanded:
                    already.append(figure)
                elif len(demand) < STORY_DEMAND_LIMIT:
                    demanded.add(figure.key)
                    demand.append(figure)
                else:
                    already.append(figure)
            block = render_story_figures_check_block(
                present=present, demand=demand, already=already
            )
            if not block:
                return prompt
            logger.info(
                "SIGNATURE STORY FIGURES: %d present, %d demand(s), %d already demanded "
                "this round: %s",
                len(present), len(demand), len(already), [f.raw for f in demand],
            )
            if demand and on_demand is not None:
                on_demand(demand)
            return f"{prompt}\n\n{block}"
        except Exception:
            logger.exception(
                "story_reach: signature-story figure scan failed this round; shipping the "
                "un-decorated reviewer prompt"
            )
            return prompt

    return fn

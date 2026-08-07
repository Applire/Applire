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

"""#254 — deterministic figure-attribution guard for cover-letter prose.

Live bug (2026-07-24): a generated letter stated "mentoring teams of 5+" — a
headcount the candidate explicitly declined to give. The figure was not
invented from nothing: the ADR-021 review loop's CORRECTOR call, which sees
the WHOLE profile in its prompt, borrowed "5" from an entirely unrelated
vault fact ("Lead a team of five tech leads and system owners", a different
current role) and attached it to the mentoring claim. The writer's own draft
never contained it — only the corrector output does, so ANY guard that runs
once on the initial draft misses the bug. This module is called on the FINAL
settled output of every ``review_and_refine`` pass in
``services/cover_letter.py`` (writer loop AND the condense/refine loop),
never mid-loop, so a corrector-introduced figure can never slip through.

Same defect class as #196 (role-attribution) and #243-adjacent/#248 (letter
figure/claim ownership), now caught deterministically on the GENERATION path
rather than only detected post-hoc by the Oracle (``services/oracle/audit.py``
— belt and suspenders, both wanted). Reuses that package's OWN ownership
machinery end to end and invents no parallel notion of ownership:

* :func:`applire.services.oracle.matchers.build_vault_index` for
  ``EvidenceUnit.owner_ids`` (the US187/#237 nesting-aware ownership model).
* :func:`applire.services.oracle.extract._find_employer_anchor` /
  ``_employer_anchor_candidates`` / ``letter_named_experience_ids`` for the
  SAME per-sentence / whole-letter attribution signals
  ``extract_claims_from_letter`` uses (#237/#248) — a sentence naming exactly
  one employer/project may only be substantiated by that position's own
  evidence (or role-agnostic evidence), and a letter naming exactly one
  position stamps the whole document.

Scope deliberately narrower than a "is this figure real" check: a figure with
NO vault match anywhere is left untouched here — that is the Oracle's
"unbacked" verdict (``services/oracle/matchers/grounding.match_figures``),
not this guard's job, and treating "no literal digits found" as fabricated
would wrongly strip legitimate DERIVED claims a self-hoster never typed as a
literal number anywhere in the vault (e.g. "over 20 years of experience",
computed from date spans, not stored as the digit string "20" — regression-
tested below). This guard only fires on the#254 shape: a figure that DOES
match a vault fact, but every matching fact belongs to a position/story the
surrounding sentence is not about.

Detection floor is DELIBERATELY narrower than the Oracle's own
``oracle.matchers.figures.extract_figures`` (which excludes single digits and
spelled-out numbers by design, US244 — see that module's docstring): the
pinned bug is a bare single-digit headcount ("5+"), so this guard also parses
"N+" growth-qualifier forms, single/multi-digit plain numbers, and spelled-out
EN/DE number words (prior art: ``services/profile/reconcile/stance.py``'s
``_spelled_figures`` — the SAME word tables are reused here, position-aware,
so a vault fact phrased as "five" can be recognised as backing (or NOT
backing) a letter sentence that renders it as "5+"). Years are exempt entirely
(consumed but never emitted as a figure) — same rationale as #196: date spans
and "since 20XX" phrasing are tenure-ambient and legitimately repeat across
positions.

This module has TWO consumers, and the split between them is the whole point
(#299, ADR-062 clause 2 — the disposition recorded in the ADR-062 amendment of
2026-07-28, "move the fact into the prompt").

1. :func:`figure_ownership_reviewer_prompt_fn` — the FACT, handed to the
   ADR-021 letter reviewer every round: *"figure N appears in the vault only
   under owners X, Y, Z."* A data-structure lookup with one correct answer.
   The reviewer sees the prose, the ownership and the reason, and can do what
   deterministic code cannot: re-anchor the claim, drop the borrowed number
   while keeping a real achievement, or remove a claim that was never this
   employer's. No new LLM call — the reviewer prompt already exists and this
   composes onto it like the ledger-coverage, unaddressed-requirement and
   word-floor blocks before it (ADR-058 freeze, amended 2026-07-24).
2. :func:`guard_letter_figures` — the FLOOR, still run on the FINAL settled
   output of every ``review_and_refine`` pass. It cannot be deleted: the #254
   ground truth is that the writer's draft never contained "5+" and only the
   last CORRECTOR call minted it, and ``review_and_refine`` ships that last
   corrector output UNREVIEWED whenever it exhausts, cycles or the reviewer
   call fails — so no amount of reviewer input covers the settle paths. When
   it fires, the WHOLE SENTENCE goes; every drop is logged (house style forbids
   silent truncation) with the figure, its foreign owner(s) and the sentence.

#296 (charter run #7) moved the removal unit from the figure's own character
span to the sentence. Blanking the span shipped grammatical wreckage to a
hiring manager — "deploy time from 45 to 8 minutes" was delivered as "deploy
time from to 8 minutes", "EKS for 12 services" as "EKS for services", "a
99.9% availability target" as "a availability target". A figure is a
noun-phrase argument whose neighbours are load-bearing, so no whitespace
tidying can repair the remainder; the sentence is the smallest unit that
still reads after removal.

#296's OTHER half — which employer an unanchored sentence is about — was first
answered with a paragraph-level running anchor, and that answer failed twice on
real letters. Charter run #8 (2026-07-28) had the carry-forward cleared by the
very sentence that established it (it counted owner NAMES, and
``_employer_anchor_candidates`` lists a nested project under its parent's id,
so one employer counted as two), deleting four grounded achievement figures
from one German paragraph; and it is paragraph-scoped, so #296's own EN case
still lost every Cargonaut figure whenever the writer put those sentences in
their own paragraphs, with the owning employer named one paragraph above.

So #299 applies ADR-062 clause 3 (deletion over repair) to the judgement half
rather than tuning it a third time. The floor now acts ONLY where attribution
is a fact — the sentence names exactly one position, or the letter does — and
leaves every other sentence alone; see :func:`_allowed_owner_ids` for what was
deleted and why. Run #8's other correction, the tenure exemption
(``_TENURE_RE``), was a fact the code got wrong and stands unchanged.

ADR-062 clause 6 classification of what remains:

* FACT (deterministic, kept): which figures a text contains
  (:func:`_extract_letter_figures`), which vault units carry them
  (:func:`_vault_figure_map`), which position owns each unit
  (``EvidenceUnit.owner_ids``), which employer an id belongs to
  (:func:`_employer_of_id`), and whether a name appears in a sentence or in
  the letter (``_find_employer_anchor`` / ``letter_named_experience_ids`` —
  surface-form presence, the same class as ADR-048's ``surface_present``).
* JUDGEMENT (the model's, since #299): which employer a sentence that names
  none is about, and what to do about a misattributed figure.

Standing tension, declared per clause 6 rather than hidden. The floor still
DELETES, so a truthful figure sharing a sentence with a borrowed one is still
collateral, and a dropped sentence can still orphan the next one's anaphor
(both pinned in the tests). Deterministic code has no other remedy; what
changed is that the reviewer now gets the same fact one round earlier and can
prevent the state that reaches the floor. In exchange, a borrowed figure in a
genuinely unanchored sentence of a multi-employer letter is no longer cut
deterministically — it rests on the reviewer acting on the fact, with the
Oracle's post-hoc audit (``services/oracle/audit.py``) and the review screen
behind it. That is the same disposition ``oracle.matchers.attribution.
find_foreign_owner`` already takes (claims without a rendered-position anchor
are never flagged — fail open), and it is why
:func:`render_figure_ownership_block` REQUIRES a rewritten claim to name its
employer: an unanchored survivor would escape this guard (no figure left) and
the Oracle (no anchor) alike (#299's "watch" note).

Prompt-effect evidence (ADR-062 clause 7): whether the reviewer ACTS on the
block is not testable in CI, which mocks the provider. CI pins the wiring, the
facts and the floor; the instruction needs a real-provider charter run ending
in the blind panel — for #296 specifically an EN multi-employer letter
(``it_backend_daniel``), which is that issue's own stated closing condition.
"""
from __future__ import annotations

import copy
import logging
import re
from dataclasses import dataclass
from typing import Any

from applire.services.oracle.extract import (
    _employer_anchor_candidates,
    _find_employer_anchor,
    _profile_get,
    letter_named_experience_ids,
    split_sentences,
)
from applire.services.oracle.matchers import EvidenceUnit, build_vault_index
from applire.services.profile.reconcile.stance import (
    _DE_COMPOUND_UNITS,
    _DE_SCALE_RE,
    _DE_TENS,
    _DE_UND_RE,
    _EN_TENS,
    _EN_UNITS,
    _SCALES,
    _SMALL_WORDS,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LetterFigure:
    kind: str  # "percent" | "number"
    value: str  # canonical numeric string, comparable across digit/spelled forms
    raw: str  # verbatim matched substring (for logging/replacement)
    start: int
    end: int


# ── figure extraction (digits, "N+", percent, years-exempt) ─────────────────
_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")

# A number carrying a years-of-experience UNIT — "14 Jahren", "14-jährige",
# "zehn Jahre", "11 years". Exempt for the same reason ``_YEAR_RE`` is: tenure
# is ambient, spans every position at once, and is DERIVED from date spans
# rather than stored as a literal (module docstring). Group 1 is the number
# itself; the unit is only the evidence that it is a duration.
#
# ADR-062 classification: FACT. "Does the token immediately after this number
# name a unit of years?" is settled by the two tokens alone — no reading for
# meaning, no judgement about the surrounding claim.
#
# Charter run #8 (2026-07-28) is why this exists as well as ``_YEAR_RE``.
# "Years are exempt" was only ever true of CALENDAR years, so "meine
# 14-jährige Expertise in Lean-Methoden" was matched against two unrelated
# vault counts — Rasselstein's "Schicht mit 14 Mitarbeitenden" and Weberit's
# "Rollout auf 14 Spritzgussmaschinen" — declared foreign-owned, and its whole
# sentence removed. That sentence was the letter's closing, so the delivered
# PDF ended on the bare line "Mein Eintrittstermin kann flexibel vereinbart
# werden." A tenure figure collides with an unrelated headcount whenever the
# two happen to share a digit, which is a coincidence, not an attribution.
#
# ``\+?`` (#214, ported from the Oracle's twin in
# ``oracle/matchers/figures._TENURE_RE``, merged in #459): "12+ years of
# experience" is the same duration as "12 years of experience", but the unit no
# longer follows the digits directly, so the growth-quantifier form fell through
# to ``_PLUS_RE`` and was matched against every unrelated vault count containing
# 12. One character, both extractors, same rule.
_TENURE_UNIT = r"(?:jahr(?:e|en|es)?|jährig\w*|years?|yrs?)"
_TENURE_RE = re.compile(
    r"\b(\d+(?:[.,]\d+)?|[A-Za-zÄÖÜäöüß]+)\+?\s*[-–]?\s*" + _TENURE_UNIT + r"\b",
    re.IGNORECASE,
)
_PERCENT_RE = re.compile(r"[~≈]?\s*(\d+(?:[.,]\d+)?)\s*%")
_PLUS_RE = re.compile(r"\b(\d+)\+")
_PLAIN_RE = re.compile(r"\b\d+(?:[.,]\d+)?\b")
_THOUSANDS_RE = re.compile(r"^\d{1,3}(?:[.,]\d{3})+$")
_WORD_RE = re.compile(r"[A-Za-zÄÖÜäöüß]+")

# Unicode punctuation normalized before matching (the U+2019 lesson) — the
# only punctuation that could otherwise sit between a spelled number and a
# following scale word or interfere with word-boundary matching.
_APOSTROPHE_CHARS = "’ʼ‘‛´`"


def _fold_punct(text: str) -> str:
    out = text
    for ch in _APOSTROPHE_CHARS:
        out = out.replace(ch, "'")
    return out


def _canon(raw: str) -> str:
    """Canonical numeric string — mirrors
    ``oracle.matchers.figures._canonical_number``: '2.000'/'2,000' -> '2000';
    '12,5' -> '12.5'."""
    s = raw.strip()
    if _THOUSANDS_RE.match(s):
        return re.sub(r"[.,]", "", s)
    s = s.replace(",", ".")
    if s.endswith(".0"):
        s = s[:-2]
    return s


def _digit_figures(text: str) -> list[LetterFigure]:
    figures: list[LetterFigure] = []
    consumed: list[tuple[int, int]] = []

    def _free(a: int, b: int) -> bool:
        return all(b <= s or a >= e for s, e in consumed)

    # Years are consumed (protected from the plain-number pass below) but
    # never emitted — exempt entirely, same rationale as #196.
    for m in _YEAR_RE.finditer(text):
        consumed.append(m.span())

    for m in _PERCENT_RE.finditer(text):
        if not _free(*m.span()):
            continue
        figures.append(LetterFigure("percent", _canon(m.group(1)), m.group(0), *m.span()))
        consumed.append(m.span())

    for m in _PLUS_RE.finditer(text):
        if not _free(*m.span()):
            continue
        figures.append(LetterFigure("number", _canon(m.group(1)), m.group(0), *m.span()))
        consumed.append(m.span())

    for m in _PLAIN_RE.finditer(text):
        if not _free(*m.span()):
            continue
        figures.append(LetterFigure("number", _canon(m.group(0)), m.group(0), *m.span()))
        consumed.append(m.span())

    return figures


def _spelled_figures(text: str) -> list[LetterFigure]:
    """Position-aware EN/DE spelled-number matches (#207 prior art, made
    span-aware here so a match can be located within its sentence)."""
    matches: list[LetterFigure] = []
    toks = list(_WORD_RE.finditer(text))
    lowered = [m.group(0).lower() for m in toks]
    n = len(toks)
    i = 0
    while i < n:
        tok = lowered[i]
        start, end = toks[i].span()
        nxt = lowered[i + 1] if i + 1 < n else ""
        nxt2 = lowered[i + 2] if i + 2 < n else ""

        small = _SMALL_WORDS.get(tok)
        if small is not None:
            value = small
            end_pos = end
            consumed_to = i
            if tok in _EN_TENS and nxt in _EN_UNITS:
                value = _EN_TENS[tok] + _EN_UNITS[nxt]
                end_pos = toks[i + 1].end()
                consumed_to = i + 1
                if _SCALES.get(nxt2) is not None:
                    value *= _SCALES[nxt2]
                    end_pos = toks[i + 2].end()
                    consumed_to = i + 2
            elif _SCALES.get(nxt) is not None:
                value *= _SCALES[nxt]
                end_pos = toks[i + 1].end()
                consumed_to = i + 1
            matches.append(
                LetterFigure("number", str(value), text[start:end_pos], start, end_pos)
            )
            i = consumed_to + 1
            continue

        if tok in _SCALES:
            matches.append(
                LetterFigure("number", str(_SCALES[tok]), text[start:end], start, end)
            )
            i += 1
            continue

        m = _DE_UND_RE.match(tok)
        if m:  # "fünfundvierzig", "fünfundzwanzigtausend"
            value = _DE_COMPOUND_UNITS[m.group(1)] + _DE_TENS[m.group(2)]
            if m.group(3):
                value *= _SCALES[m.group(3)]
            matches.append(LetterFigure("number", str(value), text[start:end], start, end))
            i += 1
            continue

        m2 = _DE_SCALE_RE.match(tok)
        if m2:  # "zweihundert", "zweitausend"
            value = _DE_COMPOUND_UNITS.get(m2.group(1) or "", 1) * _SCALES[m2.group(2)]
            matches.append(LetterFigure("number", str(value), text[start:end], start, end))
            i += 1
            continue

        i += 1
    return matches


def _extract_letter_figures(text: str) -> list[LetterFigure]:
    """Every droppable figure in ``text``: digits, "N+" growth forms, percent,
    and spelled-out EN/DE numbers.

    Years are exempt and never returned — both CALENDAR years (``_YEAR_RE``,
    consumed inside ``_digit_figures``) and DURATIONS in years (``_TENURE_RE``,
    filtered here so the exemption reaches the spelled-number path too: "zehn
    Jahre ISO-9001-Audit-Praxis" is as tenure-ambient as "10 Jahre").
    """
    folded = _fold_punct(text)
    tenure = [m.span(1) for m in _TENURE_RE.finditer(folded)]
    figures = _digit_figures(folded) + _spelled_figures(folded)
    if not tenure:
        return figures
    return [
        f
        for f in figures
        if not any(f.start < e and f.end > s for s, e in tenure)
    ]


# ── the vault side: an independent (kind, value) -> owners index ────────────
# Deliberately NOT ``VaultIndex.figure_map`` (oracle.matchers.vault) — that
# index is built from ``oracle.matchers.figures.extract_figures``, which
# excludes single digits and spelled numbers by design (US244). The vault fact
# behind #254 ("team of five") is a SPELLED number, so it would never appear
# in the oracle figure_map at all; this guard needs its own symmetric
# extraction (the SAME ``_extract_letter_figures`` above) run over vault unit
# text too, so "five" in the vault and "5+" in the letter canonicalize to the
# same comparable value. Ownership itself (``EvidenceUnit.owner_ids``) is
# still the one shared instrument — only figure-parsing is duplicated, and
# only because the two callers need different recall floors (US244's own
# docstring explains why ITS floor is narrower).
def _vault_figure_map(units: list[EvidenceUnit]) -> dict[tuple[str, str], list[EvidenceUnit]]:
    fmap: dict[tuple[str, str], list[EvidenceUnit]] = {}
    for unit in units:
        for fig in _extract_letter_figures(unit.text):
            fmap.setdefault((fig.kind, fig.value), []).append(unit)
    return fmap


# ── #299 / ADR-062 clause 2: the fact, handed to the reviewer ───────────────


@dataclass(frozen=True)
class FigureOwnership:
    """One figure in the draft, and the vault owners that back it.

    ADR-062 classification: FACT. "Which vault units carry this number, and
    which position owns each of them" is settled by the profile's own structure
    and a numeric comparison — the same two instruments the guard has always
    used (``build_vault_index`` for ownership, ``_extract_letter_figures`` for
    the numbers). Nothing here reads prose for meaning.
    """

    kind: str  # "percent" | "number"
    value: str  # canonical numeric string
    raw: str  # a verbatim form as it appears in the draft ("5+", "99.9%")
    owners: tuple[str, ...]  # employer/project display names, sorted


def _owner_labels(profile: Any) -> dict[str, str]:
    """``owner id -> display name`` — the employer a reader would recognise.

    Work entries win (``_employer_of_id``); an id that belongs to no work entry
    is a standalone project and is labelled with the project's own name. Ids are
    never shown to the model: the reviewer reasons about the letter's prose,
    which names companies, not UUIDs.
    """
    labels = dict(_employer_of_id(profile))
    for name, oid in _employer_anchor_candidates(profile):
        labels.setdefault(oid, name)
    return labels


def figure_ownership_facts(
    letter_data: dict[str, Any] | None, profile: Any
) -> list[FigureOwnership]:
    """Every figure in the draft whose vault backing is owned — with its owners.

    The module's scope rules are applied unchanged, so the reviewer is handed
    exactly the facts the floor itself acts on and no others:

    * a figure with NO vault match anywhere is omitted — that is the Oracle's
      "unbacked" verdict, and telling the reviewer "nobody owns 17" would invite
      it to strip a legitimate derived claim;
    * a figure backed (also) by role-agnostic evidence is omitted — it belongs
      to no position in particular, so there is no attribution question;
    * tenure and calendar years are omitted, because they are never extracted
      as figures at all (``_extract_letter_figures``).

    ADR-062 classification: FACT (see :class:`FigureOwnership`). The judgement —
    which employer the sentence carrying the figure is ABOUT — is deliberately
    NOT computed here; it is what the reviewer is asked to make.
    """
    index = build_vault_index(profile)
    return _facts_from_map(
        letter_data, _vault_figure_map(index.units), _owner_labels(profile)
    )


def render_figure_ownership_block(facts: list[FigureOwnership]) -> str:
    """The reviewer's FIGURE OWNERSHIP block — facts, then one narrow rule.

    ADR-062 clause 2 applied literally: the replacement for a heuristic is the
    underlying facts, verbatim, plus the narrowest instruction that prevents
    them being over-read. The instruction distinguishes the two cases the guard
    provably cannot (#299): a claim that is real at this employer but borrowed
    its NUMBER from another, and a claim that is not this employer's at all.

    The re-anchor requirement is not stylistic. ``oracle.matchers.attribution.
    find_foreign_owner`` never flags a claim without a rendered-position anchor
    (fail open), so a claim that survives a rewrite WITHOUT naming its employer
    escapes the post-loop guard (no figure left) and the Oracle (no anchor)
    alike — a caught problem converted into an uncatchable one.

    Returns "" when there is nothing to state.
    """
    if not facts:
        return ""
    lines = [
        "FIGURE OWNERSHIP (deterministic vault lookup — this is ground truth, do "
        "not re-derive it). Each figure below appears in this draft AND in the "
        "candidate's vault, and EVERY vault fact carrying it belongs to the "
        "position(s) named:",
    ]
    for fact in facts:
        owners = ", ".join(fact.owners)
        lines.append(f'  - "{fact.raw}" — backed only by evidence from: {owners}')
    lines += [
        "",
        "This says nothing about whether the draft attributes them correctly — "
        "that judgement is YOURS, from the draft's own prose. For each figure "
        "above, decide which employer the sentence carrying it is about:",
        "  * an employer in that figure's list — correct; leave it alone;",
        "  * a DIFFERENT employer, but the achievement itself genuinely happened "
        "there and only the number came from elsewhere — set approved=false and "
        "instruct the writer to keep the claim and DROP the number (a grounded "
        "qualifier is fine, an invented one is not);",
        "  * a DIFFERENT employer, and the achievement is that other position's "
        "— set approved=false and instruct the writer to either re-anchor the "
        "claim to the employer that owns it, or remove the claim. A borrowed "
        "claim kept as a vague, unattributed sentence is a worse defect, not a "
        "fix.",
        "Any claim the writer rewrites MUST name, in its own sentence, the "
        "employer it belongs to — an unanchored claim escapes every check that "
        "runs after you. Never ask for a figure to be invented, moved to a "
        "position this list does not name, or added to reach a number.",
    ]
    return "\n".join(lines)


def figure_ownership_reviewer_prompt_fn(base_fn, profile: Any):
    """Wrap a ``reviewer_prompt_fn`` so every ADR-021 review iteration carries
    the vault ownership of the CURRENT draft's figures (#299, ADR-062 clause 2).

    Composes with (never replaces) the existing wrappers — the ledger coverage
    check, the unaddressed-requirements block and the word floor — exactly the
    way they compose with each other: ``review_and_refine`` calls
    ``reviewer_prompt_fn(source, draft)`` fresh each round, so the block is
    recomputed against the latest draft and disappears once every figure sits
    with an employer that owns it. No new LLM call, no new pass, no new loop
    (ADR-058 freeze, amended 2026-07-24: threading existing vault data into an
    existing prompt is bugfix-grade).

    The vault side is computed ONCE, in this closure: the profile cannot change
    inside a review loop, so re-indexing it per round would be pure cost.
    """
    index = build_vault_index(profile)
    vault_fig_map = _vault_figure_map(index.units)
    labels = _owner_labels(profile)

    def fn(source: str, draft: dict[str, Any]) -> str:
        prompt = base_fn(source, draft)
        facts = _facts_from_map(draft, vault_fig_map, labels)
        if not facts:
            return prompt
        logger.info(
            "figure ownership check (#299): %d grounded figure(s) in the draft "
            "carry vault ownership — %s",
            len(facts),
            [(f.raw, f.owners) for f in facts],
        )
        return f"{prompt}\n\n{render_figure_ownership_block(facts)}"

    return fn


def _facts_from_map(
    letter_data: dict[str, Any] | None,
    vault_fig_map: dict[tuple[str, str], list[EvidenceUnit]],
    labels: dict[str, str],
) -> list[FigureOwnership]:
    """:func:`figure_ownership_facts` over an already-built vault side."""
    body = (letter_data or {}).get("body") or {}
    paragraphs = body.get("paragraphs") if isinstance(body, dict) else None
    if not paragraphs:
        return []
    facts: dict[tuple[str, str], FigureOwnership] = {}
    for para in paragraphs:
        if not isinstance(para, str) or not para.strip():
            continue
        for fig in _extract_letter_figures(para):
            key = (fig.kind, fig.value)
            if key in facts:
                continue
            units = vault_fig_map.get(key, [])
            if not units or any(not u.owner_ids for u in units):
                continue
            owners = sorted({labels.get(o, o) for u in units for o in u.owner_ids})
            facts[key] = FigureOwnership(
                kind=fig.kind, value=fig.value, raw=fig.raw.strip(), owners=tuple(owners)
            )
    return list(facts.values())


# ── per-sentence attribution context ───────────────────────────────────────
def _sentence_spans(paragraph: str) -> list[tuple[int, int, str]]:
    """(start, end, sentence) for every sentence ``split_sentences`` finds,
    positioned within the ORIGINAL paragraph so a sentence can be removed
    without disturbing the prose around it."""
    spans: list[tuple[int, int, str]] = []
    cursor = 0
    for sentence in split_sentences(paragraph):
        idx = paragraph.find(sentence, cursor)
        if idx == -1:
            # Cannot locate — fail open on this sentence (never corrupt the
            # paragraph over a text-location mismatch).
            continue
        spans.append((idx, idx + len(sentence), sentence))
        cursor = idx + len(sentence)
    return spans


def _allowed_owner_ids(
    sentence_anchor: str | None,
    letter_named_ids: frozenset[str],
) -> frozenset[str] | None:
    """Owners a figure in THIS sentence may legitimately belong to, or ``None``
    when attribution is not a FACT here and the floor must not act.

    Two — and since #299 only two — fact-grade signals survive, both of them
    plain surface-name presence (the same class as ADR-048's ``surface_present``,
    which ADR-062 explicitly preserves as a fact):

    1. **The sentence names exactly one employer/project.** ``_find_employer_
       anchor``'s exact-name, fail-open-on-ambiguity rule. "At Vector Analytics,
       I mentored teams of 5+" says which position it is about in its own words;
       a headcount the vault holds only under DataCore does not belong in it.
       This is the #254 shape, and it is why the floor still exists.
    2. **The whole letter names exactly one employer/project.** There is then no
       scope question to answer at all: every claim in the document is about that
       one position. (Note this is precisely the escape #296's letters could
       never reach — they name two employers, which is why it was never the
       mechanism that damaged them.)

    Everything else returns ``None`` and the sentence is left alone. Deciding
    which employer an unanchored sentence is about is a judgement about prose
    (ADR-062 clause 1) and the fact now reaches the reviewer instead
    (:func:`figure_ownership_reviewer_prompt_fn`), which sees the same vault
    ownership, the surrounding prose, and can rewrite rather than delete.

    Deleted with #299, per ADR-062 clause 3 (deletion over repair), all three
    for the same reason — each answered "which employer is this sentence about"
    by approximating meaning, and each was measured wrong on real letters:

    * the **paragraph carry-forward**, which read an employer forward from the
      last anchoring sentence. Run #8 had it cleared by the very sentence that
      established it, deleting four grounded achievement figures; and it is
      paragraph-scoped, so #296's own EN case still lost every Cargonaut figure
      the moment the writer put those sentences in their own paragraphs — the
      employer named one paragraph earlier;
    * the **loose per-sentence name match**, which let any owner named anywhere
      in a sentence substantiate a figure in any clause of it;
    * the **clause split**, which re-asked the same question of a fragment.
    """
    if sentence_anchor is not None:
        return frozenset({sentence_anchor})
    if len(letter_named_ids) == 1:
        return letter_named_ids
    return None


def _employer_of_id(profile: Any) -> dict[str, str]:
    """``experience id -> employer name``, built from work experience ONLY.

    The labels the reviewer is given for a figure's vault owners (#299). It
    reads ``work_experience`` directly rather than filtering
    ``_employer_anchor_candidates``, because that list deliberately mixes two
    kinds of name: companies, and PROJECT names mapped onto their parent work id
    by the US187 nesting rule. An id therefore appears in it under as many names
    as the position has projects, and counting names counts projects.

    ADR-062 classification: FACT. Which employer an id belongs to is settled by
    the profile's own structure — one lookup, no prose read.
    """
    out: dict[str, str] = {}
    for w in (_profile_get(profile, "work_experience") or []):
        wid = _profile_get(w, "id")
        company = _profile_get(w, "company")
        if isinstance(wid, str) and wid.strip() and isinstance(company, str) and company.strip():
            out[wid.strip()] = company.strip()
    return out


def _collapse_whitespace(text: str) -> str:
    cleaned = re.sub(r"[ \t]{2,}", " ", text)
    cleaned = re.sub(r" +([,.;:!?])", r"\1", cleaned)
    return cleaned


def _unattributable_figures(
    text: str,
    allowed_ids: frozenset[str],
    vault_fig_map: dict[tuple[str, str], list[EvidenceUnit]],
) -> list[dict[str, Any]]:
    """Every figure in ``text`` whose vault backing is EXCLUSIVELY foreign to
    ``allowed_ids`` (mirrors ``oracle.matchers.attribution.find_foreign_owner``'s
    "any role-agnostic or allowed unit clears it" rule, generalized to a set of
    allowed owners). A figure with no vault match anywhere is left untouched —
    not this guard's job (module docstring).

    Detection only (#296). This used to excise the figure's own character span,
    which is what produced run #7's mutilated prose — "deploy time from 45 to 8
    minutes" shipped as "deploy time from to 8 minutes", "EKS for 12 services"
    as "EKS for services", "a 99.9% availability target" as "a availability
    target". A figure is a noun-phrase argument, not a removable modifier: the
    surrounding words are grammatically load-bearing, so no amount of whitespace
    tidying makes the remainder a sentence. The caller now removes the whole
    sentence instead — the smallest unit that stands on its own.
    """
    dropped: list[dict[str, Any]] = []
    for fig in _extract_letter_figures(text):
        units = vault_fig_map.get((fig.kind, fig.value), [])
        if not units:
            continue
        if any((not u.owner_ids) or (u.owner_ids & allowed_ids) for u in units):
            continue
        dropped.append(
            {
                "raw": fig.raw,
                "kind": fig.kind,
                "sentence": text.strip(),
                "foreign_owners": sorted({o for u in units for o in u.owner_ids}),
            }
        )
    return dropped


def _guard_paragraph(
    paragraph: str,
    candidates: list[tuple[str, str]],
    letter_named_ids: frozenset[str],
    vault_fig_map: dict[tuple[str, str], list[EvidenceUnit]],
) -> tuple[str, list[dict[str, Any]]]:
    """Drop every SENTENCE that FACTUALLY misattributes a figure.

    The removal unit is the **sentence**, not the figure's character span
    (#296): excising the span left grammatical wreckage in the delivered PDF
    ("from to 8 minutes"), because a figure is a noun-phrase argument whose
    neighbours are load-bearing.

    The firing rule is :func:`_allowed_owner_ids` — an explicit name in the
    sentence, or a letter that names exactly one position, and otherwise
    nothing. A sentence whose employer is not a fact is left ALONE (#299): the
    reviewer has been given the same ownership facts and can rewrite, which is
    the remedy deterministic code cannot offer.
    """
    dropped: list[dict[str, Any]] = []
    pieces: list[str] = []
    cursor = 0
    for start, end, sentence in _sentence_spans(paragraph):
        allowed = _allowed_owner_ids(
            _find_employer_anchor(sentence, candidates), letter_named_ids
        )
        sentence_dropped: list[dict[str, Any]] = (
            []
            if allowed is None
            else _unattributable_figures(sentence, allowed, vault_fig_map)
        )

        if sentence_dropped:
            dropped.extend(sentence_dropped)
            cursor = end  # skip the sentence AND its leading separator
            continue
        pieces.append(paragraph[cursor:end])
        cursor = end
    pieces.append(paragraph[cursor:])
    return _collapse_whitespace("".join(pieces)).strip(), dropped


def guard_letter_figures(letter_data: dict[str, Any], profile: Any) -> dict[str, Any]:
    """The #254 prevention guard — run on the FINAL settled output of every
    generation attempt (writer draft AND every corrector/condense pass),
    never mid-loop.

    Returns ``letter_data`` unchanged (same object) when nothing was dropped;
    otherwise a deep copy with the offending sentence(s) removed from
    ``body.paragraphs`` and each drop logged (never silent).
    """
    body = (letter_data or {}).get("body") or {}
    paragraphs = body.get("paragraphs") if isinstance(body, dict) else None
    if not paragraphs:
        return letter_data

    index = build_vault_index(profile)
    vault_fig_map = _vault_figure_map(index.units)
    letter_named_ids = letter_named_experience_ids(letter_data, profile)
    candidates = _employer_anchor_candidates(profile)

    new_paragraphs: list[Any] = []
    all_dropped: list[dict[str, Any]] = []
    changed = False
    for pi, para in enumerate(paragraphs):
        if not isinstance(para, str) or not para.strip():
            new_paragraphs.append(para)
            continue
        new_para, para_dropped = _guard_paragraph(
            para, candidates, letter_named_ids, vault_fig_map
        )
        if para_dropped:
            changed = True
            for d in para_dropped:
                d["paragraph_index"] = pi
            all_dropped.extend(para_dropped)
        if not new_para.strip():
            # Every sentence went. An empty string would render as a blank gap
            # between paragraphs, so the paragraph goes with them (#296).
            continue
        new_paragraphs.append(new_para)

    if not changed:
        return letter_data

    for d in all_dropped:
        logger.warning(
            "letter_figure_guard (#254/#296): removed the sentence carrying "
            "figure %r from cover-letter paragraph %d — backed only by evidence "
            "owned by %s, which this sentence does not name (%r). The "
            "WHOLE sentence goes: excising the figure alone left ungrammatical "
            "prose in the delivered PDF.",
            d["raw"], d["paragraph_index"], d["foreign_owners"], d["sentence"],
        )

    result = copy.deepcopy(letter_data)
    result.setdefault("body", {})["paragraphs"] = new_paragraphs
    return result

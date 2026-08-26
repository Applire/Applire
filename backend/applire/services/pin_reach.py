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

"""ADR-077 clauses 3 + 4 — how a fact pin reaches generation.

Three responsibilities, all deterministic:

* the **PINNED FACTS input block** for the writer prompts and the letter
  condense-regenerate (the ADR-076 INPUT-BLOCK fate — the
  `render_scope_positioning_block` pattern: pure function, empty string when
  silent, candidate-side vault text only);
* **presence as a measurement** — the *fact* of normalized-quote containment
  (`_norm_quote`, ADR-062 clause 1), scoped to the pinned entry's tailored
  twin resolved by id — NEVER document-wide, which would let a generic short
  quote ("Python") immunize or satisfy unrelated content (SF-PIN.3);
* the **carrier partition input** for `bullet_cuts.rank_cuts(pinned=...)` —
  which bullet indices carry a pin and therefore never enter the removable
  set (clause 4). Only bullet-scoped pin types (work / project / volunteer /
  signature_story) can mark a bullet; the scalar types (skill, certification,
  education, language, publication) live in their own sections and never
  partition bullets.

Whether a *rephrased* bullet is the same fact as the pin is a judgement and
stays with the reviewer; this module computes containment only. A miss is
ship-and-report (clause 5), never a gate.
"""

from __future__ import annotations

import logging
from typing import Iterable

from applire.schemas.application import FactPin
from applire.schemas.cover_letter import LetterData
from applire.schemas.cv import TailoredCVData
from applire.schemas.profile import MasterProfileData
from applire.services.fact_pins import CV_UNRENDERABLE_PIN_TYPES, _find_entry
from applire.services.signal_disposition import (
    ExhaustionDisposition,
    register_signal_disposition,
)
from applire.services.scope_requirements import _norm_quote

logger = logging.getLogger(__name__)

#: Pin types whose quotes live in role/project bullet lists — the only types
#: that may partition bullets out of the removable set (clause 4).
BULLET_SCOPED_TYPES = frozenset(
    {"work", "project", "volunteer", "signature_story"}
)


def active_pins(pins: Iterable[FactPin], target: str) -> list[FactPin]:
    """Pins that reach generation for one document: on-target and not stale."""
    return [p for p in pins if not p.stale and target in p.targets]


# ── The PINNED FACTS input block (clause 3) ──────────────────────────────────

# Two instruction variants (2026-08-25 adversarial real-LLM finding): the CV
# demands VERBATIM reproduction — "woven naturally" invited paraphrase, and a
# paraphrased pin escapes BOTH the containment-based cut immunity and the
# presence measurement (it reads as an honest miss). The letter keeps the
# natural-weave instruction: letters are prose, and the reviewer enforces the
# pinned_facts positioning key there (check 4).
_BLOCK_HEADER_CV = {
    "en": (
        "PINNED FACTS (user-selected, REQUIRED):\n"
        "The candidate pinned these facts. Reproduce each quote below "
        "WORD-FOR-WORD as its own bullet in its matching entry (or verbatim "
        "in its section for skills/certifications/education) — never "
        "paraphrase, shorten, or extend it. A reworded pin counts as "
        "missing."
    ),
    "de": (
        "PINNED FACTS (vom Kandidaten angeheftet, VERPFLICHTEND):\n"
        "Der Kandidat hat diese Fakten angeheftet. Gib jedes Zitat unten "
        "WORTWÖRTLICH als eigenen Bullet im passenden Eintrag wieder (bzw. "
        "verbatim in seiner Sektion bei Kenntnissen/Zertifizierungen/"
        "Ausbildung) — niemals umformulieren, kürzen oder erweitern. Ein "
        "umformulierter Pin gilt als fehlend."
    ),
}
_BLOCK_HEADER_LETTER = {
    "en": (
        "PINNED FACTS (user-selected, REQUIRED):\n"
        "The candidate pinned these facts — each MUST appear in the letter, "
        "woven naturally, quoting the fact's own wording as closely as the "
        "sentence allows. State only what the quote states; never extend or "
        "inflate it."
    ),
    "de": (
        "PINNED FACTS (vom Kandidaten angeheftet, VERPFLICHTEND):\n"
        "Der Kandidat hat diese Fakten angeheftet — jeder MUSS im Anschreiben "
        "erscheinen, natürlich eingewoben, so nah am Wortlaut des Zitats wie "
        "der Satz erlaubt. Nur wiedergeben, was das Zitat aussagt; niemals "
        "erweitern oder aufblähen."
    ),
}


def _entry_label(pin: FactPin, profile: MasterProfileData) -> str:
    entry = _find_entry(profile, pin.entry_type, pin.entry_id)
    if entry is None:
        return pin.entry_type
    for attrs in (("role", "company"), ("role", "organization"), ("name",),
                  ("title",), ("institution", "degree"), ("language",)):
        parts = [getattr(entry, a, "") or "" for a in attrs]
        label = ", ".join(p for p in parts if p)
        if label:
            return f"{pin.entry_type}: {label}"
    return pin.entry_type


# #580 (ADR-077 amended 2026-08-26): the CORRECTOR's variant, folded into the CV
# review loop's `source`. The real-provider replay of 2026-08-26 showed the corrector
# obeying the WRITER header ("reproduce WORD-FOR-WORD") over the reviewer's feedback
# ("do not insert the conflicted pin") — re-inserting a ledger-conflicted quote the
# next round's check 6(b) would strike again. The corrector's block is a reference:
# insert only what the feedback names, never what it marks conflicted/overridden.
_BLOCK_HEADER_CV_CORRECTOR = (
    "PINNED FACTS (reference for corrections):\n"
    "The candidate pinned these vault quotes. Insert a quote — word-for-word, as its "
    "own bullet in its entry (a skill pin verbatim into `skills`) — ONLY when the REVIEW "
    "FEEDBACK names it as missing. Keep every pinned quote already in the draft intact. "
    "Never insert a quote the feedback marks as conflicted or overridden by a truth "
    "finding, and never insert one the feedback does not name."
)


def render_pinned_facts_block(
    pins: Iterable[FactPin],
    profile: MasterProfileData,
    *,
    target: str,
    language: str | None,
    audience: str = "writer",
) -> str:
    """The deterministic PINNED FACTS block — "" when nothing to say.

    ``audience="corrector"`` (CV only) renders the reference variant for the
    review loop's ``source``; the writer keeps the REQUIRED/word-for-word header."""
    selected = active_pins(pins, target)
    if not selected:
        return ""
    lang = "de" if (language or "").startswith("de") else "en"
    if audience == "corrector":
        lines = [_BLOCK_HEADER_CV_CORRECTOR]
    else:
        header = _BLOCK_HEADER_CV if target == "cv" else _BLOCK_HEADER_LETTER
        lines = [header[lang]]
    for pin in selected:
        lines.append(f'- "{pin.quote}" ({_entry_label(pin, profile)})')
    return "\n".join(lines)


# ── Presence measurement (clause 3) ──────────────────────────────────────────


def _work_twin(tailored: TailoredCVData, entry_id: str):
    for w in tailored.work_history:
        if w.id and w.id == entry_id:
            return w
    return None


def _twin_scope_texts(pin: FactPin, tailored: TailoredCVData) -> list[str] | None:
    """The texts of the pinned entry's tailored twin — None = no twin scope.

    Section-scoped for the scalar types (the quote IS the name, and the
    section is the twin); id-resolved for work/volunteer; name+bullets for
    projects (the twin carries no id — the nesting pass's own matching
    limitation); summary+bullets for signature stories (woven prose has no
    twin section; the long distinctive quote bounds the false-positive risk).
    Publications have no CV section: no scope, honestly unmet.
    """
    if pin.entry_type in ("work", "volunteer"):
        twin = _work_twin(tailored, pin.entry_id)
        if twin is None:
            return None
        texts = [twin.role, *twin.bullets]
        for proj in twin.projects:
            texts.append(proj.name)
            texts.extend(proj.bullets)
        return texts
    if pin.entry_type == "project":
        texts: list[str] = []
        for w in tailored.work_history:
            for proj in w.projects:
                texts.append(proj.name)
                texts.extend(proj.bullets)
        for proj in tailored.projects:
            texts.append(proj.name)
            texts.extend(proj.bullets)
        return texts
    if pin.entry_type == "skill":
        return list(tailored.skills)
    if pin.entry_type == "certification":
        return [c.name for c in tailored.certifications]
    if pin.entry_type == "education":
        return [
            " ".join(filter(None, (e.institution, e.degree, e.field)))
            for e in tailored.education
        ]
    if pin.entry_type == "language":
        return [
            " ".join(filter(None, (l.language, l.level)))
            for l in tailored.languages
        ]
    if pin.entry_type == "signature_story":
        texts = [tailored.summary]
        for w in tailored.work_history:
            texts.extend(w.bullets)
        return texts
    return None  # publication: no CV section


def pin_present_in_cv(pin: FactPin, tailored: TailoredCVData) -> bool:
    quote_norm = _norm_quote(pin.quote)
    if not quote_norm:
        return False
    scope = _twin_scope_texts(pin, tailored)
    if not scope:
        return False
    return any(
        isinstance(t, str) and quote_norm in _norm_quote(t) for t in scope
    )


def pin_present_in_letter(pin: FactPin, letter: LetterData) -> bool:
    """Letter presence — containment over the paragraphs.

    The letter has no entry structure, so the twin-scoping discipline cannot
    apply; the body is the narrowest honest scope. Measurement only.
    """
    quote_norm = _norm_quote(pin.quote)
    if not quote_norm:
        return False
    body = " ".join(letter.body.paragraphs)
    return quote_norm in _norm_quote(body)


def letter_pin_present_in_dict(pin: FactPin, letter_data: dict) -> bool:
    """Dict-shaped twin of :func:`pin_present_in_letter` for the audit path
    (the persisted ``letter_data`` JSONB, overrides already applied)."""
    quote_norm = _norm_quote(pin.quote)
    if not quote_norm:
        return False
    body = (letter_data or {}).get("body") or {}
    paragraphs = body.get("paragraphs") if isinstance(body, dict) else None
    text = " ".join(p for p in (paragraphs or []) if isinstance(p, str))
    return quote_norm in _norm_quote(text)


def pinned_facts_positioning_entry(pins: Iterable[FactPin]) -> dict | None:
    """ADR-077 clause 3 — the ``positioning_requested["pinned_facts"]`` entry.

    The reviewer requires it (check 4 names the key), the corrector preserves
    it. None when no active letter pin exists, so the key is absent rather
    than empty (the scope_positioning gating precedent)."""
    selected = active_pins(pins, "letter")
    if not selected:
        return None
    quotes = "; ".join(f'"{p.quote}"' for p in selected)
    return {
        "required": True,
        "instruction": (
            "The candidate PINNED these facts — each must appear in the "
            f"letter, woven naturally, never extended: {quotes}"
        ),
    }


# ── Carrier partition input (clause 4) ───────────────────────────────────────


def bullet_pin_carrier_indices(
    texts: Iterable[str],
    *,
    entry_id: str,
    pins: Iterable[FactPin],
) -> set[int]:
    """Indices of ``texts`` that carry an active bullet-scoped CV pin.

    ``entry_id`` is the tailored entry the texts belong to: work/volunteer
    pins mark bullets only inside their own entry (id-resolved); project and
    signature_story pins have no per-bullet id and match by containment
    wherever their (long, distinctive) quote appears. Scalar pins never mark
    bullets — that is SF-PIN.3's over-protection hole, closed by scoping.
    """
    relevant: list[str] = []
    for pin in pins:
        if pin.stale or "cv" not in pin.targets:
            continue
        if pin.entry_type not in BULLET_SCOPED_TYPES:
            continue
        if pin.entry_type in ("work", "volunteer") and pin.entry_id != entry_id:
            continue
        norm = _norm_quote(pin.quote)
        if norm:
            relevant.append(norm)
    if not relevant:
        return set()
    return {
        i
        for i, text in enumerate(texts)
        if isinstance(text, str)
        and any(norm in _norm_quote(text) for norm in relevant)
    }


# ── #580 / ADR-077 amended 2026-08-26: the reviewer-side pin surface ─────────
#
# Per round, the CV reviewer gets a deterministic PINNED FACTS CHECK block — the
# US213 VERIFIED COVERAGE CHECK pattern (`keyword_ledger.coverage_reviewer_prompt_fn`)
# applied to pins. Measurement uses the ONE presence instrument above; the
# reviewer turns a DEMAND into a blocking issue (check 7); the corrector re-reads
# the verbatim quote from the PINNED FACTS block folded into the loop's `source`.
#
# Three structural guards, each from the refuter pass that preceded the amendment:
#   * DEMAND is limited to the pin types the writer AUTHORS in the prose shape —
#     a demand the corrector cannot satisfy is a control that fires falsely,
#     every round, for the life of the pin;
#   * every pin is demanded at most ONCE per `review_and_refine` invocation —
#     the deterministic layer has no memory of verdicts, so a bound replaces
#     memory: a demand a truth check overrides cannot re-enter, and pins can
#     never supply more than one corrector round each (the #525 exhaustion shape);
#   * a quote that carries a Keyword-Ledger DO-NOT-CLAIM term as a whole token is
#     a LEDGER CONFLICT and is never demanded — truth outranks the pin, and a
#     demand must not contradict check 6(b) inside one round. That is a string
#     FACT about the quote (ADR-062 clause 1), never a claim about why the pin
#     is absent; the report carries it as `ledger_conflict`.

PINNED_FACT_SIGNAL_ID = "pinned_fact"

#: Pin types the WRITER authors in the prose shape — the only ones a corrector
#: round can satisfy. Certifications/education/languages are joined verbatim by
#: code after the writer; publications and volunteering have no CV section
#: (`CV_UNRENDERABLE_PIN_TYPES`, refused at pin time); projects are excluded
#: because `_nest_projects` dedups by name and a corrector stub could suppress
#: the vault project. Everything else stays measurement-and-report (clause 3).
DEMANDABLE_PIN_TYPES = frozenset({"work", "skill", "signature_story"})


def ensure_pinned_fact_signal_registered() -> None:
    """ADR-076 clause 2 — the signal's exhaustion disposition, declared at the
    migration site. Idempotent (re-registration overwrites the same record), so
    the CV loops call it right before passing ``signal_ids`` and a registry
    reset in a test process cannot turn the declaration into an
    ``UndeclaredSignalDispositionError`` at settle time."""
    register_signal_disposition(
        PINNED_FACT_SIGNAL_ID,
        ExhaustionDisposition.SHIP_AND_REPORT,
        rationale=(
            "ADR-077 clause 3 / amendment 2026-08-26 (#580): an unmet pin never "
            "blocks delivery — it ships with a machine-readable report entry "
            "(`ATSReport.pinned_facts`) on every door. No fallback writer: clause 3 "
            "refused to build a deterministic inserter and the amendment keeps "
            "that; presence stays compliance-contingent, bounded to one demand "
            "per pin per loop."
        ),
    )


ensure_pinned_fact_signal_registered()


def pin_ledger_conflicts(quote: str, keyword_ledger) -> list[str]:
    """DO-NOT-CLAIM concepts whose concept or surface form the quote carries as a
    whole token — `review_compliance.term_present` (token-boundary, clause-4
    normalisation), deliberately NOT `ats_audit.surface_present` (a substring
    match: 'REST' inside 'restructuring', 'agile' inside 'fragile').

    A fact about the quote, in ledger order, de-duplicated; ``[]`` without a
    ledger or without forbidden entries. Whether the quote *claims* the concept
    is the reviewer's judgement (check 6(b)) and is never computed here."""
    if not quote or not keyword_ledger:
        return []
    from applire.services.keyword_ledger import split_ledger_for_prompt
    from applire.services.review_compliance import term_present

    _, forbidden = split_ledger_for_prompt(keyword_ledger)
    if not forbidden:
        return []
    forms_by_concept: dict[str, list[str]] = {}
    for entry in keyword_ledger:
        concept = entry.get("concept") if isinstance(entry, dict) else None
        if concept:
            forms_by_concept.setdefault(concept, []).extend(
                f for f in (entry.get("surface_forms") or []) if f
            )
    out: list[str] = []
    for concept in forbidden:
        if concept in out:
            continue
        if any(term_present(f, quote) for f in [concept, *forms_by_concept.get(concept, [])]):
            out.append(concept)
    return out


def measure_pins_in_draft(
    pins: Iterable[FactPin],
    draft: dict,
    profile_json: dict,
    *,
    composed: bool = False,
) -> dict[str, bool] | None:
    """Presence per active CV pin against ONE draft, with the one instrument.

    ``composed=False``: the loop's prose draft (``summary``/``work``/``skills``)
    is assembled through ``assemble_tailored_cv`` first — the same join the
    attribution round measures on (lazy import: ``services/cv.py`` imports this
    module). ``composed=True``: the terminal subject is already
    ``TailoredCVData``-shaped. Fail-safe: any assembly/validation failure → ``None``
    (unmeasured — never a demand, never a crash; the report floor still sees the
    delivered document)."""
    try:
        if composed:
            tailored = TailoredCVData.model_validate(draft)
        else:
            from applire.services.cv import assemble_tailored_cv  # lazy — cv.py imports us

            tailored = TailoredCVData.model_validate(assemble_tailored_cv(draft, profile_json))
    except Exception:
        logger.warning(
            "fact pins: the round's draft is not measurable — no demand this round "
            "(fail-safe; the report floor measures the delivered document)",
            exc_info=True,
        )
        return None
    return {p.pin_id: pin_present_in_cv(p, tailored) for p in active_pins(pins, "cv")}


def render_pinned_facts_check_block(
    *,
    demand: list[tuple[FactPin, str]],
    conflicted: list[tuple[FactPin, str, list[str]]],
    present: list[tuple[FactPin, str]] = (),
    already_demanded: list[tuple[FactPin, str]] = (),
    report_only: list[tuple[FactPin, str]] = (),
) -> str:
    """The reviewer's deterministic block — ``""`` when there is nothing to say.

    A COMPLETE statement, not a prohibition: the real-provider replay of
    2026-08-26 had the reviewer re-derive "pinned skill missing" for a pin the
    scan had found present, because the block only listed the absent ones. Every
    active CV pin is now accounted for — present, demanded, already demanded
    this loop, report-only (a type the corrector cannot author), or in ledger
    conflict — so check 7 has its answer and nothing to re-derive
    (feedback_prohibition_is_not_an_answer).

    English only: reviewer prompts are English; the WRITER block is bilingual
    because the output language governs it, the reviewer's input does not."""
    if not (demand or conflicted or present or already_demanded or report_only):
        return ""
    lines = [
        "PINNED FACTS CHECK (deterministic word-for-word scan — this is ground truth, do "
        "not re-derive it, and raise check 7 ONLY for the DEMAND entries below). Status "
        "of every pinned vault quote for this CV against the current draft:"
    ]
    if present:
        lines.append("PRESENT — verified word-for-word in the draft; no action, never an issue:")
        for pin, label in present:
            lines.append(f'  - entry id {pin.entry_id} ({label}): "{pin.quote}"')
    if demand:
        lines.append(
            "DEMAND — check 7, blocking: name the entry id and the quote's first words in "
            "your feedback; the corrector inserts the quote verbatim from the PINNED FACTS "
            "block in its source."
        )
        for pin, label in demand:
            lines.append(f'  - entry id {pin.entry_id} ({label}): "{pin.quote}"')
    if already_demanded:
        lines.append(
            "ALREADY DEMANDED this loop and still absent — a pin is demanded at most once; "
            "do NOT demand it again (it is reported as unmet):"
        )
        for pin, label in already_demanded:
            lines.append(f'  - entry id {pin.entry_id} ({label}): "{pin.quote}"')
    if report_only:
        lines.append(
            "REPORT ONLY — absent, but this entry type is joined by code after the writer "
            "or has no CV section; the corrector cannot author it: do NOT demand it:"
        )
        for pin, label in report_only:
            lines.append(f'  - entry id {pin.entry_id} ({label}): "{pin.quote}"')
    if conflicted:
        lines.append(
            "LEDGER CONFLICT — the quote carries a DO-NOT-CLAIM term of the Keyword Ledger; "
            "truth outranks the pin: do NOT demand it (it is reported as unmet, naming the term):"
        )
        for pin, label, terms in conflicted:
            lines.append(
                f'  - entry id {pin.entry_id} ({label}): "{pin.quote}" — term(s): {", ".join(terms)}'
            )
    return "\n".join(lines)


def pinned_facts_reviewer_prompt_fn(
    base_fn,
    pins: Iterable[FactPin],
    profile_json: dict,
    keyword_ledger,
    *,
    composed: bool = False,
):
    """Wrap a ``reviewer_prompt_fn(source, draft)`` so every round sees the CURRENT
    draft's pin state (the ``coverage_reviewer_prompt_fn`` shape, #122).

    One wrapper = one ``review_and_refine`` invocation = one demand bound: the
    prose loop and the terminal loop build their own (at most two demands per pin
    per generation). ``pins`` may carry any target/staleness — only active CV
    pins are measured."""
    selected = active_pins(pins, "cv")
    demanded: set[str] = set()
    try:
        profile = MasterProfileData.model_validate(profile_json)
    except Exception:  # a slim/legacy profile: labels degrade to the entry type
        profile = None
    conflicts = {p.pin_id: pin_ledger_conflicts(p.quote, keyword_ledger) for p in selected}

    def _label(pin: FactPin) -> str:
        return _entry_label(pin, profile) if profile is not None else pin.entry_type

    def fn(source: str, draft: dict) -> str:
        prompt = base_fn(source, draft)
        if not selected:
            return prompt
        presence = measure_pins_in_draft(selected, draft, profile_json, composed=composed)
        if presence is None:
            return prompt
        demand: list[tuple[FactPin, str]] = []
        conflicted: list[tuple[FactPin, str, list[str]]] = []
        present: list[tuple[FactPin, str]] = []
        already: list[tuple[FactPin, str]] = []
        report_only: list[tuple[FactPin, str]] = []
        for pin in selected:
            if presence.get(pin.pin_id, True):
                present.append((pin, _label(pin)))
                continue
            if pin.entry_type not in DEMANDABLE_PIN_TYPES:
                report_only.append((pin, _label(pin)))  # the corrector cannot author it
                continue
            terms = conflicts.get(pin.pin_id) or []
            if terms:
                conflicted.append((pin, _label(pin), terms))
                continue
            if pin.pin_id in demanded:
                already.append((pin, _label(pin)))  # the bound: one demand per pin per loop
                continue
            demanded.add(pin.pin_id)
            demand.append((pin, _label(pin)))
        block = render_pinned_facts_check_block(
            demand=demand, conflicted=conflicted, present=present,
            already_demanded=already, report_only=report_only,
        )
        if not block:
            return prompt
        logger.info(
            "PINNED FACTS CHECK: %d demand(s), %d ledger conflict(s) this round: %s",
            len(demand),
            len(conflicted),
            [p.pin_id for p, _ in demand] + [p.pin_id for p, _, _ in conflicted],
        )
        return f"{prompt}\n\n{block}"

    return fn


def pinned_skill_quote_norms(pins: Iterable[FactPin]) -> frozenset[str]:
    """Normalised quotes of the active CV skill pins — the immunity set for the
    skills-list passes (`_dedup_skills`, `_drop_ungrounded_jd_echo_skills`,
    ADR-077 amended 2026-08-26 / clause 4 correction)."""
    return frozenset(
        _norm_quote(p.quote) for p in active_pins(pins, "cv") if p.entry_type == "skill"
    )


def skill_tag_is_pinned(tag: str, pinned_norms: frozenset[str]) -> bool:
    """Is this rendered skill tag one of the pinned quotes (same fold as presence)?"""
    if not pinned_norms or not isinstance(tag, str):
        return False
    return _norm_quote(tag) in pinned_norms

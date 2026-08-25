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

from typing import Iterable

from applire.schemas.application import FactPin
from applire.schemas.cover_letter import LetterData
from applire.schemas.cv import TailoredCVData
from applire.schemas.profile import MasterProfileData
from applire.services.fact_pins import _find_entry
from applire.services.scope_requirements import _norm_quote

#: Pin types whose quotes live in role/project bullet lists — the only types
#: that may partition bullets out of the removable set (clause 4).
BULLET_SCOPED_TYPES = frozenset(
    {"work", "project", "volunteer", "signature_story"}
)


def active_pins(pins: Iterable[FactPin], target: str) -> list[FactPin]:
    """Pins that reach generation for one document: on-target and not stale."""
    return [p for p in pins if not p.stale and target in p.targets]


# ── The PINNED FACTS input block (clause 3) ──────────────────────────────────

_BLOCK_HEADER = {
    "en": (
        "PINNED FACTS (user-selected, REQUIRED):\n"
        "The candidate pinned these facts — each MUST appear in the document, "
        "woven naturally where it fits best. State only what the quote "
        "states; never extend or inflate it."
    ),
    "de": (
        "PINNED FACTS (vom Kandidaten angeheftet, VERPFLICHTEND):\n"
        "Der Kandidat hat diese Fakten angeheftet — jeder MUSS im Dokument "
        "erscheinen, natürlich eingewoben. Nur wiedergeben, was das Zitat "
        "aussagt; niemals erweitern oder aufblähen."
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


def render_pinned_facts_block(
    pins: Iterable[FactPin],
    profile: MasterProfileData,
    *,
    target: str,
    language: str | None,
) -> str:
    """The deterministic PINNED FACTS block — "" when nothing to say."""
    selected = active_pins(pins, target)
    if not selected:
        return ""
    lang = "de" if (language or "").startswith("de") else "en"
    lines = [_BLOCK_HEADER[lang]]
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

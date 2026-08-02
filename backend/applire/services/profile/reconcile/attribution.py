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

"""Deterministic employer-attribution guard for the ADR-046 reconciler (#243).

Ground truth (live-reproduced 2026-07-24, founder charter re-run, main @
53ffa85 — see ``tests/unit/test_reconcile_attribution.py``): a single
multi-employer interview answer named BOTH NordPharm and Applire in one turn.
The reconciler LLM correctly split the answer into several ``add_bullets``
ops, but two of them carried an Applire-only clause's text while TARGETING a
NordPharm entity (a WorkEntry and a nested ProjectEntry). This is a
model-emitted wrong-target op (prompt-side): the deterministic applier
(``apply.py``) faithfully applies whatever ``target`` the op names — by
design (ADR-046: "the applier never re-decides whether two entities are the
same"). This module is the belt-and-braces deterministic backstop the
applier layer was missing.

The pattern mirrors ``services.oracle.extract``'s letter-path employer
anchoring (``_find_employer_anchor`` / ``_employer_anchor_candidates``,
#237/#196) — kept as an independent copy here rather than a cross-package
import: ``oracle`` depends on the profile/reconcile write path's OUTPUT
(rendered documents), not the other way around, and ``apply.py``'s own
docstring commits to staying a pure, self-contained module. Importing
``oracle`` here would invert that dependency for a few dozen lines of
regex.

Design (belt AND braces, per #243):

* Scope: free-text ``add_bullets`` content (``responsibilities`` /
  ``achievements`` — NOT ``technologies``, see ``_GUARDED_FIELDS``) merging
  into an EXISTING work/project entity. New-entity creation and bulk
  ``cv_upload``/``manual`` sources are out of scope (see ``_grounding_corpus``
  reuse from ``stance.py`` — the same interview-turn-only restriction #127
  already established).
* Anchoring is at COMPANY-NAME granularity, not per-entity-id: a candidate
  who held several roles at the same employer (the live profile has three
  NordPharm stints) must not make "NordPharm" read as an ambiguous anchor
  merely because it maps to multiple entity ids — every WorkEntry sharing a
  company name collapses to ONE candidate (see ``_company_candidates``).
* A bullet's own text rarely repeats the employer name (the live bug's
  bullets name NEITHER company) — the anchor comes from the ANSWER SENTENCE
  the bullet was drawn from, located via token-overlap coverage (reusing
  ``ats_audit.skill_tokens``, the shared tokenizer — never a second one),
  not a literal substring match (the live bullets are lightly paraphrased
  from the source sentence, not verbatim quotes).
* A bullet whose owning sentence cannot be found (paraphrased beyond
  recognition), or whose owning sentence names NO employer, or the SAME
  employer as the op's target, keeps today's behaviour unchanged — over-drop
  discipline: legitimate enrichment answers rarely name any employer at all
  and must not be blocked.
* A sentence naming TWO OR MORE distinct employers is ambiguous and fails
  open (documented #243 design choice) rather than guessing.
* A genuine mismatch does NOT silently apply and does NOT silently drop the
  content — it is rerouted into a ``request_confirmation`` op (the existing
  pending-confirmation channel every other reconcile guard already uses).
"""
from __future__ import annotations

import re
from typing import Any

from applire.schemas.profile import MasterProfileData, ProjectEntry, WorkEntry
from applire.services.ats_audit import skill_tokens
from applire.services.profile.reconcile.ops import (
    AddBullets,
    ReconcileOp,
    RequestConfirmation,
)
from applire.services.profile.reconcile.stance import _grounding_corpus

# ── punctuation / sentence-splitting (independent copy — see module docstring) ──

_APOSTROPHE_CHARS = "’ʼ‘‛´`"
_DASH_CHARS = "‒–—―−"


def _normalize_punct(text: str) -> str:
    out = text
    for ch in _APOSTROPHE_CHARS:
        out = out.replace(ch, "'")
    for ch in _DASH_CHARS:
        out = out.replace(ch, "-")
    return out


# Academic degree abbreviations added #416 (same gap as the oracle package's
# independent copy in services.oracle.extract — see that module's
# _ABBREVIATIONS comment for the charter-run-13 ground truth). This copy
# deliberately does NOT carry oracle's "Mio."/"Mrd."/"Tsd."/"Mr."/"Mrs."/"Ms."
# additions from #398 — the two lists are allowed to diverge; only the
# degree family + the ordering fix below are shared.
#
# Ordering constraint: protection is applied by SEQUENTIAL literal
# ``str.replace`` (below), so a shorter member that is a prefix/substring of
# a longer one must not run first, or it partially sentinel-fies the longer
# match and destroys it — e.g. "Dr." vs. "Dr. rer. pol." / "Dr.-Ing.", or
# "B.A." vs. "M.B.A.". The tuple is sorted longest-first once at import time
# so the human-readable grouping above can stay unordered.
_ABBREVIATIONS = tuple(
    sorted(
        (
            "z.B.", "z. B.", "d.h.", "d. h.", "u.a.", "u. a.", "bzw.", "ggf.",
            "inkl.", "ca.", "vs.", "e.g.", "i.e.", "etc.", "approx.",
            "Dr.", "Prof.", "Nr.", "No.",
            "M.Sc.", "B.Sc.", "M.A.", "B.A.", "M.Eng.", "B.Eng.",
            "Dipl.-Ing.", "Dipl.-Kfm.", "Dipl.-Betriebsw.",
            "Dr. rer. nat.", "Dr. rer. pol.", "Dr.-Ing.",
            "LL.M.", "M.B.A.", "Ph.D.",
        ),
        key=len,
        reverse=True,
    )
)
_SENTINEL = "\x00"
_DECIMAL_DOT_RE = re.compile(r"(?<=\d)\.(?=\d)")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _split_sentences(text: str) -> list[str]:
    """Deterministic sentence split with abbreviation and decimal guards.

    Independent copy of ``services.oracle.extract.split_sentences`` (see
    module docstring for why this isn't a cross-package import).
    """
    t = (text or "").strip()
    if not t:
        return []
    protected = t
    for abbrev in _ABBREVIATIONS:
        protected = protected.replace(abbrev, abbrev.replace(".", _SENTINEL))
    protected = _DECIMAL_DOT_RE.sub(_SENTINEL, protected)
    sentences = []
    for part in _SENTENCE_SPLIT_RE.split(protected):
        restored = part.replace(_SENTINEL, ".").strip()
        if restored:
            sentences.append(restored)
    return sentences


# ── company-name anchoring ───────────────────────────────────────────────────

# Common DE/EN legal-form suffixes, stripped so "NordPharm SE" and a spoken
# "NordPharm" (or "bei NordPharm") anchor to the SAME candidate (#243 test:
# legal-form variants).
_LEGAL_FORM_RE = re.compile(
    r"\s+(?:SE|AG|GmbH(?:\s*&\s*Co\.?\s*KG)?|gGmbH|mbH|KG|OHG|GbR|"
    r"e\.\s?V\.?|Inc\.?|LLC|Ltd\.?|Co\.?|Corp\.?|Corporation|PLC|LLP)\.?\s*$",
    re.IGNORECASE,
)


def _core_company_name(name: str) -> str:
    """Legal-form-stripped company name for anchor matching."""
    stripped = _LEGAL_FORM_RE.sub("", name.strip())
    return stripped.strip() or name.strip()


def _company_candidates(profile: MasterProfileData) -> dict[str, str]:
    """core name -> display name, deduped so multiple roles at the SAME
    employer (the live profile has three NordPharm stints) collapse to ONE
    anchor candidate rather than reading as an ambiguous multi-entity match.
    """
    candidates: dict[str, str] = {}
    for w in profile.work_experience:
        company = (w.company or "").strip()
        if not company:
            continue
        core = _core_company_name(company)
        if core and core not in candidates:
            candidates[core] = company
    return candidates


def _anchor_company(text: str, candidates: dict[str, str]) -> tuple[str, str] | None:
    """The (core, display) company name ``text`` names, iff EXACTLY ONE
    (fail open on zero or ambiguous — #243 documented design choice)."""
    if not text or not candidates:
        return None
    normalized = _normalize_punct(text)
    found: set[str] = set()
    for core in candidates:
        pattern = re.compile(r"\b" + re.escape(core) + r"\b", re.IGNORECASE)
        if pattern.search(normalized):
            found.add(core)
    if len(found) == 1:
        core = next(iter(found))
        return core, candidates[core]
    return None


# ── owning-sentence lookup (token-overlap coverage, not literal substring) ──

_COVERAGE_MIN = 0.7


def _owning_sentence(bullet: str, sentences: list[str]) -> str | None:
    """The sentence ``bullet`` was most likely drawn from, or ``None``.

    The reconciler paraphrases lightly ("I built a deterministic verification
    layer, the Truthfulness Oracle, that audits" -> "Built deterministic
    verification layer (Truthfulness Oracle) auditing") — a literal substring
    check misses this, so we measure how much of the bullet's own content-token
    set is covered by each sentence (reusing ``ats_audit.skill_tokens``, the
    shared tokenizer) and take the best match. Below ``_COVERAGE_MIN`` the
    bullet is treated as unlocatable — fail open (see module docstring).
    """
    bullet_tokens = skill_tokens(bullet)
    if not bullet_tokens:
        return None
    best_sentence: str | None = None
    best_coverage = 0.0
    for sentence in sentences:
        sentence_tokens = skill_tokens(sentence)
        if not sentence_tokens:
            continue
        hits = len(bullet_tokens & sentence_tokens)
        coverage = hits / len(bullet_tokens)
        if coverage > best_coverage:
            best_coverage = coverage
            best_sentence = sentence
    if best_coverage >= _COVERAGE_MIN:
        return best_sentence
    return None


# ── entity <-> employer resolution ───────────────────────────────────────────


def _resolve_existing(target: str | None, profile: MasterProfileData) -> Any | None:
    """An EXISTING work/project entity by id (never a same-batch local ref —
    this guard runs before ``apply_ops`` creates anything, so an unresolvable
    target is either a not-yet-created local ref or unknown; either way,
    fail open)."""
    if not target:
        return None
    for entry in (*profile.work_experience, *profile.projects):
        if getattr(entry, "id", None) == target:
            return entry
    return None


def _entity_employer_core(entity: Any, profile: MasterProfileData) -> str | None:
    """The core company name ``entity`` belongs to, or ``None`` when there is
    no employer context to guard against (a standalone project)."""
    if isinstance(entity, WorkEntry):
        return _core_company_name(entity.company) if entity.company else None
    if isinstance(entity, ProjectEntry):
        assoc = entity.associated_experience
        if assoc:
            parent = next(
                (w for w in profile.work_experience if w.id == assoc), None
            )
            if parent is not None and parent.company:
                return _core_company_name(parent.company)
        return None
    return None


# ── the guard itself ─────────────────────────────────────────────────────────

# Technologies are short generic nouns ("Databricks", "LangGraph") — the
# owning-sentence coverage check is unreliable at that granularity (a
# one-token bullet trivially "covers" many unrelated sentences), so they stay
# out of scope; the live incident's misattributions were both free-text
# achievement/responsibility bullets.
_GUARDED_FIELDS = ("responsibilities", "achievements")


def _build_confirmation(
    op: AddBullets,
    entity: Any,
    flagged: list[tuple[str, str, str]],
    target_display: str,
) -> RequestConfirmation:
    anchor_displays = sorted({display for _, _, display in flagged})
    anchor_text = " / ".join(anchor_displays)
    sample = flagged[0][1]
    section = "work_experience" if isinstance(entity, WorkEntry) else "projects"
    question = (
        f"'{sample}' reads like it belongs to {anchor_text}, not "
        f"{target_display} — the answer named {anchor_text} for this part, "
        f"but this entry is under {target_display}. Where should it go?"
    )
    return RequestConfirmation(
        question=question,
        options=[
            f"Move to {anchor_text}",
            f"Keep on {target_display}",
            "Discard it",
        ],
        context={
            "section": section,
            "target": op.target,
            "target_employer": target_display,
            "anchor_employer": anchor_text,
            "flagged": [{"field": field, "text": text} for field, text, _ in flagged],
        },
    )


def enforce_attribution(
    ops: list[ReconcileOp],
    *,
    profile: MasterProfileData,
    new_info: Any,
    source: str,
) -> list[ReconcileOp]:
    """Deterministic belt on top of the reconciler's own entity-matching (#243).

    For every ``add_bullets`` op targeting an EXISTING work/project entity,
    each incoming responsibility/achievement bullet is checked against the
    ONE employer its owning answer-sentence names (if any). A bullet that
    anchors to a DIFFERENT employer than its target entity's own employer is
    pulled out of the op and rerouted into a ``request_confirmation`` — never
    silently applied, never silently dropped. Everything else (no anchor,
    same-employer anchor, ambiguous anchor, non-interview sources) is
    returned unchanged — over-drop discipline: legitimate enrichment must
    keep working.
    """
    corpus = _grounding_corpus(new_info, source)
    if corpus is None:
        return ops
    corpus = _normalize_punct(corpus)
    sentences = _split_sentences(corpus)
    if not sentences:
        return ops
    candidates = _company_candidates(profile)
    if not candidates:
        return ops

    result: list[ReconcileOp] = []
    for op in ops:
        if not isinstance(op, AddBullets):
            result.append(op)
            continue

        entity = _resolve_existing(op.target, profile)
        target_core = _entity_employer_core(entity, profile) if entity is not None else None
        if target_core is None:
            result.append(op)
            continue
        target_display = candidates.get(target_core, target_core)

        kept: dict[str, list[str]] = {}
        flagged: list[tuple[str, str, str]] = []
        for field in _GUARDED_FIELDS:
            kept_bullets: list[str] = []
            for bullet in getattr(op, field):
                sentence = _owning_sentence(bullet, sentences)
                anchor = _anchor_company(sentence, candidates) if sentence else None
                if anchor is not None and anchor[0] != target_core:
                    flagged.append((field, bullet, anchor[1]))
                else:
                    kept_bullets.append(bullet)
            kept[field] = kept_bullets

        if not flagged:
            result.append(op)
            continue

        if any(kept.values()):
            result.append(op.model_copy(update=kept))
        result.append(_build_confirmation(op, entity, flagged, target_display))

    return result

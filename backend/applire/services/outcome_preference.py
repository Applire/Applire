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

"""#261 — prefer MEASURED OUTCOMES over TARGETS for the same initiative.

Ground truth (run-4 blind hiring-panel finding, 2026-07-24): the generated CV
kept "targeting a 70% reduction" (a projection) sitting right next to a
properly quantified measured win for the same initiative. The blind hiring
manager read the unqualified projection as "intentionally blurring aspiration
and outcome" — one of two named reasons for the invite-"no". This module is a
purely deterministic SELECTION-layer rule, invoked by ``services.cv`` and (for
the letter's evidence selection path) ``services.letter_outcome_guard``:
prompts and the writer LLM are untouched.

Two independent pieces:

1. ``is_target_phrase`` — deterministic EN/DE marker detection for
   target/aspirational phrasing (own marker list, NOT a reuse of the Oracle's
   ``oracle.stance.classify_stance`` — see below for why). Reuses
   ``oracle.stance.normalize_stance_text``, the SHARED unicode-fold +
   casefold normalizer (the U+2019 lesson), so a real-model curly apostrophe
   near a marker can never defeat detection — never a second normalizer.

2. ``find_paired_outcome`` — deterministic "same initiative" pairing, reusing
   the #196/#244 attribution machinery UNCHANGED: ``EvidenceUnit.owner_ids``
   from ``oracle.matchers.build_vault_index`` is the ONLY notion of ownership
   used here (no parallel ownership concept invented). Owner-scoping is a
   necessary but not sufficient condition for "same initiative" — a work
   entry can describe several initiatives — so pairing ALSO requires a
   minimum shared-content-token overlap (``ats_audit.skill_tokens``, the
   shared tokenizer — never a second one) between the target sentence and the
   candidate outcome text. Both gates must clear; when either is uncertain,
   pairing fails closed (returns ``None``) — a wrong pairing would fabricate
   a result the candidate never reported for that initiative, so "no
   pairing" is always the safe default (mirrors
   ``oracle.matchers.attribution.find_foreign_owner``'s own fail-open/fail-
   closed posture and ``profile.reconcile.attribution``'s "ambiguous ->
   change nothing" design choice).

Why NOT reuse ``oracle.stance.classify_stance`` wholesale for (1): tested
against the real dev-DB "Alpha Systems GmbH" case (2026-07-25) this issue was
filed against, it (a) does not recognise the gerund "targeting" at all (the
live bug's own bullet), and (b) misclassifies the MEASURED achievement bullet
as "aspirational" — it merely REFERENCES the word "target" while reporting
that the target was conservative ("...confirming the 60% reduction target is
conservative"), and classify_stance's own EN marker list includes the bare
substring "target is". Reusing it here would have inverted this exact bug
(flagging the outcome as the projection). ``oracle/*`` is also out of scope
for this issue (siblings own/froze it) — its marker JSON files are not
touched. The unicode-normalization helper is still reused, per the module
docstring above.
"""
from __future__ import annotations

import re

from applire.services.ats_audit import _norm, skill_tokens
from applire.services.oracle.matchers import EvidenceUnit
from applire.services.oracle.stance import normalize_stance_text
from applire.templates.labels import outcome_frame_label

# ── target-phrase marker lists (own list — see module docstring) ───────────
# Deliberately narrower than classify_stance's own aspirational list: no bare
# "target"/"target is" (the live false-positive trap above), no "planned"
# alone (too generic a past-participle to safely flag prose retroactively).

_EN_TARGET_PHRASES = (
    "targeting",
    "aiming for",
    "aim to",
    "aims to",
    "with the aim of",
    "goal of",
    "with the goal of",
    "on track to",
    "projected to",
    "set a target of",
    "target of",
)

# DE: "Ziel von" / "angestrebt" fire standalone (issue's literal examples).
# "soll ... reduzieren" is a two-part pattern (modal verb + infinitive
# reduction verb, not necessarily adjacent) — checked separately below so a
# bare, unrelated "soll" sentence ("Er soll das Meeting beginnen") does not
# misfire (over-drop discipline, mirrors the reconcile-attribution module's
# same "ambiguous -> fail open" posture).
_DE_TARGET_PHRASES = (
    "ziel von",
    "ziel ist",
    # Adjective/participle inflection forms of "angestrebt" (DE declines by
    # case/gender/number: "die angestrebte Reduktion", "den angestrebten
    # Wert") — spelled out explicitly rather than a stem-prefix regex, to
    # keep matching literal and auditable.
    "angestrebt",
    "angestrebte",
    "angestrebten",
    "angestrebtes",
    "angestrebter",
    "strebt an",
    "anstreben",
    "avisiert",
)

_DE_MODAL_RE = re.compile(r"(?<!\w)(?:soll|sollen|sollte)(?!\w)")
_DE_REDUCTION_INFINITIVE_RE = re.compile(
    r"(?<!\w)(?:reduzieren|senken|verringern|kürzen|verkürzen)(?!\w)"
)


def _boundary_pattern(phrases: tuple[str, ...]) -> re.Pattern[str]:
    alternatives = "|".join(re.escape(p) for p in sorted(phrases, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)")


_TARGET_PHRASE_RE = _boundary_pattern(_EN_TARGET_PHRASES + _DE_TARGET_PHRASES)


def is_target_phrase(text: str) -> bool:
    """Deterministic target/aspirational-phrasing detection (EN/DE).

    Matching is on ``normalize_stance_text`` output — NFKC, typographic
    apostrophes folded to ASCII, casefolded, whitespace-collapsed (the SHARED
    normalizer, the U+2019 lesson) — so real-model punctuation variance can
    never defeat a marker match.
    """
    norm = normalize_stance_text(text or "")
    if not norm:
        return False
    if _TARGET_PHRASE_RE.search(norm):
        return True
    return bool(_DE_MODAL_RE.search(norm) and _DE_REDUCTION_INFINITIVE_RE.search(norm))


# ── owner-scoped "same initiative" pairing ──────────────────────────────────

# Only the vault fields that carry a MEASURABLE RESULT are outcome candidates:
# WorkEntry/ProjectEntry achievements, and a signature story's ``outcome``
# field (ADR-055: "the measurable result — figures live here"). Title/
# challenge/mechanism/benchmark are narrative framing, not the result itself.
_OUTCOME_PATH_MARKERS = (".achievements[",)
_OUTCOME_PATH_SUFFIX = ".outcome"

# Pairing thresholds: a wrong pairing is fabrication-class (attaching a result
# the candidate never reported to this initiative), so both gates are
# deliberately conservative. Calibrated against the real dev-DB "Alpha
# Systems GmbH" case (2026-07-25): the true-positive pairing there shares 4
# content tokens at ~0.24 coverage of the target's token set — comfortably
# above both floors; an unrelated-initiative same-owner pair in the same
# vault shares 0 tokens.
_MIN_SHARED_TOKENS = 3
_MIN_COVERAGE = 0.15


def _is_outcome_candidate_path(path: str) -> bool:
    return any(m in path for m in _OUTCOME_PATH_MARKERS) or path.endswith(_OUTCOME_PATH_SUFFIX)


def find_paired_outcome(
    target_text: str,
    owner_ids: frozenset[str],
    units: list[EvidenceUnit],
) -> EvidenceUnit | None:
    """The measured-outcome unit describing the SAME initiative as
    ``target_text``, or ``None`` when no safe pairing exists.

    Fails closed (returns ``None``) when: there is no owner to scope by (no
    rendered-position anchor — mirrors the #196 attribution matcher's own
    "claims without an anchor are never flagged" rule); no candidate shares
    an owner; the best candidate's content-token overlap with the target
    falls under the conservative floor; or the best candidate is ITSELF
    phrased as a target (not actually a measured result).
    """
    if not owner_ids or not units:
        return None
    target_tokens = skill_tokens(target_text)
    if not target_tokens:
        return None

    best: EvidenceUnit | None = None
    best_coverage = 0.0
    for unit in units:
        if not (unit.owner_ids & owner_ids):
            continue
        if not _is_outcome_candidate_path(unit.path):
            continue
        if is_target_phrase(unit.text):
            continue
        candidate_tokens = skill_tokens(unit.text)
        if not candidate_tokens:
            continue
        shared = target_tokens & candidate_tokens
        if len(shared) < _MIN_SHARED_TOKENS:
            continue
        coverage = len(shared) / len(target_tokens)
        if coverage < _MIN_COVERAGE:
            continue
        if coverage > best_coverage:
            best_coverage = coverage
            best = unit
    return best


# ── bullet-list transform (CV work-entry granularity) ───────────────────────

# The framing dash is language-neutral punctuation; only the word ("measured"
# / "gemessen") follows the document's output language (ADR-038 — this text
# is written INTO generated CV/letter content, not UI chrome, but the
# language-follows-output invariant is identical, see
# ``templates.labels.outcome_frame_label``).
_FRAME_DASH = " — "
_FRAME_WORD_RE = re.compile(
    r" — (?:" + "|".join(re.escape(w) for w in ("measured", "gemessen")) + r"): "
)


def is_already_framed(text: str) -> bool:
    """Idempotency check independent of output language — recognises the
    frame marker regardless of which language wrote it, so a second pass over
    an already-processed document (in EITHER language) is a no-op."""
    return bool(_FRAME_WORD_RE.search(text or ""))


def reframe_with_outcome(target_bullet: str, outcome_text: str, lang: str = "de") -> str:
    """Fold the measured outcome into the target bullet as trailing context —
    "target X — measured: Y" / "Ziel X — gemessen: Y" (issue's own
    prescribed format, output-language-aware). Both halves are verbatim
    vault/tailored text; nothing is invented."""
    word = outcome_frame_label(lang)
    return f"{target_bullet.rstrip()}{_FRAME_DASH}{word}: {outcome_text.strip()}"


def prefer_measured_outcomes_for_owner(
    bullets: list[str],
    owner_id: str,
    units: list[EvidenceUnit],
    lang: str = "de",
) -> list[str]:
    """Pure bullet-list transform for ONE owner (a work/project entry id).

    For every bullet that reads as a target AND has a safely-paired measured
    outcome (:func:`find_paired_outcome`): if that outcome already appears
    elsewhere in ``bullets`` as its own entry (the exact "naked target next
    to an unqualified outcome" shape the run-4 panel flagged), the bare
    target bullet is DROPPED — the outcome already stands on its own, so
    keeping both would still read as "blurring aspiration and outcome".
    Otherwise the target bullet is reframed in place (:func:`reframe_with_outcome`)
    so the outcome surfaces and the target is demoted to explicit context.

    A bullet with no safe pairing, or a bullet already reframed (idempotency:
    :func:`is_already_framed`), is left untouched. Returns ``bullets``
    unchanged (same list object) when nothing needed to change.
    """
    if not owner_id or not bullets:
        return bullets

    owner_ids = frozenset({owner_id})
    norm_bullets = [_norm(b) for b in bullets]

    result: list[str] = []
    changed = False
    for i, bullet in enumerate(bullets):
        if is_already_framed(bullet) or not is_target_phrase(bullet):
            result.append(bullet)
            continue
        paired = find_paired_outcome(bullet, owner_ids, units)
        if paired is None:
            result.append(bullet)
            continue

        changed = True
        outcome_norm = _norm(paired.text)
        already_present = any(
            j != i and (outcome_norm in norm_bullets[j] or norm_bullets[j] in outcome_norm)
            for j in range(len(bullets))
            if norm_bullets[j]
        )
        if already_present:
            continue  # drop the naked target; the outcome already stands alone
        result.append(reframe_with_outcome(bullet, paired.text, lang))

    return result if changed else bullets

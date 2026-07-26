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

"""#271 Task 2/3 — strongest-vault-evidence digest for the letter.

Ground truth (charter run #5): the letter's CANDIDATE PROFILE block is built
from ``cv_data`` (the TAILORED CV's ``tailored_data``), condensed to
``work_history[:6]`` x ``bullets[:6]`` (``prompts.cover_letter.
build_cover_letter_prompt``). The run-5 BioNTech work entry survived tailoring
with only 3 bullets, so everything the CV's own condensation compressed away
was invisible to the letter — including the exact sentence both run-4 blind
panel reviewers named as the invite-flipping fact ("Human-authored documents
usually need two to three review rounds, while the right LLMs pass the first
round", ``work_experience[0].achievements[3]`` in the vault) and the
mentoring/distributed-team leadership material the JD's own 60/40 weighting
called for. Neither fact reached the CV or the letter — the CV's
condensation, not the letter's own selection step, silently dropped them
first.

This module selects the vault's STRONGEST JD-relevant material independently
of what the tailored CV kept, so a fact present in the vault but absent from
``tailored_data`` can still reach the letter (Task 3). It is purely
deterministic (ADR-058 exception (a) — no LLM, no new chain): it selects and
quotes vault text VERBATIM with its source path; it never summarises,
rewrites, combines, or infers a connection between separate vault entries.

Three selection channels, all owner/concept-scoped so nothing is a blind
vault dump:

1. CLAIMABLE-CONCEPT ANCHOR (rule 1) — for each ``claimable`` Keyword Ledger
   concept, the single best-matching vault evidence unit (longest text,
   deterministic path tie-break) whose text contains one of the concept's
   surface forms (:func:`applire.services.ats_audit.surface_present` — THE
   shared presence predicate, never a second one).

2. MEASURED-OVER-TARGET PREFERENCE (rule 2, #261 extended to selection) —
   when an anchor unit itself reads as a target/aspirational phrase
   (:func:`applire.services.outcome_preference.is_target_phrase`), it is
   swapped for its safely-paired measured outcome
   (:func:`applire.services.outcome_preference.find_paired_outcome`,
   owner-scoped) when one exists — never BOTH kept, so a swapped anchor never
   reintroduces the "naked target next to its own outcome" shape #261 fixed
   on the CV side. Reused, not reimplemented, per the issue brief.

   SAME-INITIATIVE EXTENSION: a concept anchor only proves ONE sentence of
   an initiative is JD-relevant; the initiative's OTHER quantified evidence —
   still owned by the SAME work/project entry
   (``oracle.matchers.vault.EvidenceUnit.owner_ids``, the #196/#244
   attribution machinery, unchanged) and carrying a concrete
   ``EvidenceUnit.figures`` entry — is "vault evidence that supports" the
   claimable concept in the broader sense rule 1 asks for, even when its own
   text does not literally contain the concept's surface form (this is
   exactly how ``achievements[3]`` above reaches the digest: it shares an
   owner and an initiative with the "RAG pipelines"/"retrieval systems"/"AI
   evaluation" ledger concepts' own anchors, but its own sentence never says
   any of those words). The same-initiative scan is ALSO filtered through
   ``is_target_phrase`` — a bare target-phrased sentence must never surface
   via this extension either; if it has a genuine measured pair, that pair
   independently qualifies as its own figure-bearing, non-target candidate
   in the very same scan.

3. LEADERSHIP ELIGIBILITY (rule 3) — gated on the JD excerpt itself stating a
   leadership weighting or leadership responsibilities (a fixed marker-word
   check against the SAME de-chromed excerpt the writer/reviewer already
   see, :mod:`applire.services.jd_excerpt` — never a hardcoded assumption
   that every JD wants leadership evidence). Only then does vault text
   carrying the SAME marker vocabulary become eligible.

Bounded to ``cap`` items (default 8); every dropped candidate is logged at
info level (never a silent truncation) with a fixed, documented priority
order: claimable-concept anchors first (ledger order), then same-initiative
extensions, then leadership evidence — each internally ordered by vault path
for determinism.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from applire.services.ats_audit import surface_present
from applire.services.oracle.matchers.vault import EvidenceUnit, build_vault_index
from applire.services.outcome_preference import find_paired_outcome, is_target_phrase

logger = logging.getLogger(__name__)

DEFAULT_DIGEST_CAP = 10
# Per-anchor / per-trigger sub-caps — bound how much ONE channel can
# contribute before the overall `cap` truncation runs, so a single
# initiative or a single JD trigger can never crowd out every other
# concept's evidence.
_MAX_SAME_INITIATIVE_PER_ANCHOR = 3
_MAX_LEADERSHIP_ITEMS = 4

# Evidence-unit path fragments that carry a genuine work/initiative claim
# (as opposed to identity/dates/technologies-list noise) — reused shape from
# outcome_preference's own outcome-candidate scoping, applied slightly wider
# here (responsibilities included, not just achievements/story-outcome)
# because the run-5 leadership material (the mentoring arc, the distributed-
# team line) lives under ``responsibilities``, not ``achievements``.
_INITIATIVE_PATH_MARKERS = (".achievements[", ".responsibilities[")

# Deterministic, deliberately generic people-leadership vocabulary — shared
# between the JD-trigger check and the vault-evidence check so a JD asking
# for "leadership"/"managing"/"mentoring" and a vault line reading "Led a
# team..."/"Leads a distributed team..."/"...now both project leads..." are
# recognised as the same concept without inventing a second word list.
_LEADERSHIP_MARKERS = (
    "leadership",
    "managing",
    "management",
    "mentoring",
    "mentor",
    "mentored",
    "led a team",
    "leads a team",
    "leads a",
    "leading a team",
    "team lead",
    "project lead",
    "line manager",
    "people management",
    "morale",
)


def _contains_any_marker(text_norm: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text_norm for marker in markers)


def jd_signals_leadership(jd_excerpt: str) -> bool:
    """True iff the (already de-chromed, :mod:`applire.services.jd_excerpt`)
    JD excerpt itself states a leadership weighting or leadership
    responsibility — the deterministic, JD-grounded trigger for rule 3.
    Never fires on a JD that never mentions leadership at all."""
    if not jd_excerpt:
        return False
    return _contains_any_marker(jd_excerpt.lower(), _LEADERSHIP_MARKERS)


def _is_leadership_evidence(unit: EvidenceUnit) -> bool:
    return _contains_any_marker(unit.text_norm, _LEADERSHIP_MARKERS)


def _ledger_forms(entry: dict[str, Any]) -> list[str]:
    concept = entry.get("concept", "") or ""
    forms = entry.get("surface_forms") or ([concept] if concept else [])
    seen: set[str] = set()
    out: list[str] = []
    for f in forms:
        if f and f not in seen:
            seen.add(f)
            out.append(f)
    return out


def _claimable_entries(keyword_ledger: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [e for e in (keyword_ledger or []) if isinstance(e, dict) and e.get("claimable")]


@dataclass(frozen=True)
class EvidenceDigestItem:
    """One verbatim vault evidence unit selected for the letter's digest.

    ``concept`` is the human-readable reason this item was selected (a
    ledger concept name, or a fixed channel label) — never invented prose,
    purely for the writer's/reviewer's/log's context. ``text``/``path`` are
    the vault's own :class:`~applire.services.oracle.matchers.vault.
    EvidenceUnit` fields, unmodified.
    """

    concept: str
    reason: str
    path: str
    text: str


def _anchor_for_concept(
    entry: dict[str, Any], units: list[EvidenceUnit]
) -> EvidenceUnit | None:
    forms = _ledger_forms(entry)
    if not forms:
        return None
    candidates = [
        u for u in units if any(surface_present(f, u.text_norm) for f in forms if f)
    ]
    if not candidates:
        return None
    # Deterministic: longest text (most specific/substantive), path as a
    # stable tie-break so equal-length candidates never depend on dict/list
    # iteration order.
    return max(candidates, key=lambda u: (len(u.text), u.path))


def select_letter_evidence(
    keyword_ledger: list[dict[str, Any]] | None,
    jd_excerpt: str,
    profile_json: dict[str, Any] | Any,
    *,
    cap: int = DEFAULT_DIGEST_CAP,
) -> list[EvidenceDigestItem]:
    """Select the vault's strongest JD-relevant evidence for the letter
    (#271 Tasks 2/3). Pure, deterministic; ``None``/empty tolerant.

    See the module docstring for the three selection channels and their
    priority order under the ``cap`` bound.
    """
    index = build_vault_index(profile_json or {})
    selected: list[EvidenceDigestItem] = []
    selected_paths: set[str] = set()
    # A coarser union used ONLY to RANK (never to select) leadership
    # evidence in channel 3 below — "does this candidate belong to a
    # position the ledger already proved JD-relevant" is a legitimate
    # priority signal even though it is looser than channel 2's own
    # subset-scoped "same initiative" test.
    jd_relevant_owners: set[str] = set()
    anchor_owner_sets: list[frozenset[str]] = []

    # ── Channel 1 + 2: claimable-concept anchors, measured-over-target ─────
    for entry in _claimable_entries(keyword_ledger):
        anchor = _anchor_for_concept(entry, index.units)
        if anchor is None:
            continue
        concept = entry.get("concept", "") or ""
        reason = "claimable-concept"
        if anchor.owner_ids and is_target_phrase(anchor.text):
            paired = find_paired_outcome(anchor.text, anchor.owner_ids, index.units)
            if paired is not None:
                anchor = paired
                reason = "measured-outcome-preferred"
        if anchor.owner_ids:
            jd_relevant_owners |= anchor.owner_ids
            anchor_owner_sets.append(anchor.owner_ids)
        if anchor.path in selected_paths:
            continue
        selected.append(EvidenceDigestItem(concept=concept, reason=reason, path=anchor.path, text=anchor.text))
        selected_paths.add(anchor.path)

    # ── Channel 2 (extension): same-initiative quantified evidence ─────────
    # A concept anchor proves one sentence of an initiative is JD-relevant;
    # other FIGURE-bearing evidence belonging to the SAME work/project entry
    # is "vault evidence that supports" that concept in the broader sense
    # rule 1 asks for, even when its own text never says the concept's
    # surface form (this is how achievements[3] — the review-rounds
    # sentence — reaches the digest: same owner, same initiative, no
    # literal keyword match of its own).
    #
    # Scoped by SUBSET, not mere overlap: a candidate only counts as "the
    # same initiative" when its OWN owner set is entirely contained in the
    # anchor's owner set. A plain work-entry achievement carries owner_ids=
    # {work_id} — a subset of an anchor scoped to {work_id} alone OR to
    # {work_id, some_project_id} — so it always qualifies once ANY anchor
    # touches that work entry. A SIBLING project merely sharing the same
    # parent employer (owner_ids={other_project_id, work_id}) is NOT a
    # subset of {work_id} or of {work_id, a_different_project_id}, and is
    # correctly excluded — mere overlap-on-work_id would otherwise sweep in
    # every unrelated project ever associated with that employer.
    for anchor_owners in anchor_owner_sets:
        same_initiative = [
            u
            for u in index.units
            if u.owner_ids
            and u.owner_ids <= anchor_owners
            and any(m in u.path for m in _INITIATIVE_PATH_MARKERS)
            and u.figures
            and u.path not in selected_paths
            # Rule 2 applies here too, not only to the literal anchor: a
            # bare target-phrased sentence must never surface even via this
            # extension channel — if it has a genuine measured pair, that
            # pair independently qualifies as ITS OWN figure-bearing,
            # non-target candidate in this same scan.
            and not is_target_phrase(u.text)
        ]
        same_initiative.sort(key=lambda u: u.path)
        keep, drop = (
            same_initiative[:_MAX_SAME_INITIATIVE_PER_ANCHOR],
            same_initiative[_MAX_SAME_INITIATIVE_PER_ANCHOR:],
        )
        for u in keep:
            selected.append(
                EvidenceDigestItem(
                    concept="same-initiative measured evidence",
                    reason="same-initiative-evidence",
                    path=u.path,
                    text=u.text,
                )
            )
            selected_paths.add(u.path)
        if drop:
            logger.info(
                "select_letter_evidence: dropped %d same-initiative candidate(s) for "
                "owner set %s beyond the per-anchor cap: %s",
                len(drop), sorted(anchor_owners), [u.path for u in drop],
            )

    # ── Channel 3: leadership eligibility ────────────────────────────────────
    if jd_signals_leadership(jd_excerpt):
        leadership_candidates = [
            u for u in index.units if u.path not in selected_paths and _is_leadership_evidence(u)
        ]
        # Rank same-initiative (JD-proven-relevant position) evidence ahead
        # of role-agnostic matches (summary/certifications/other
        # positions) — a plain path sort would otherwise favour whichever
        # prefix happens to come first alphabetically, unrelated to
        # relevance. Within that, prefer the position's OWN
        # work_experience entries over an associated project's — a
        # person's own responsibilities are the most direct evidence of
        # how they lead day-to-day; an associated project's description is
        # about that one initiative, not their leadership generally.
        leadership_candidates.sort(
            key=lambda u: (
                0 if (u.owner_ids & jd_relevant_owners) else 1,
                0 if u.path.startswith("work_experience") else 1,
                u.path,
            )
        )
        keep, drop = (
            leadership_candidates[:_MAX_LEADERSHIP_ITEMS],
            leadership_candidates[_MAX_LEADERSHIP_ITEMS:],
        )
        for u in keep:
            selected.append(
                EvidenceDigestItem(
                    concept="leadership (JD states a leadership weighting/responsibility)",
                    reason="leadership-eligible",
                    path=u.path,
                    text=u.text,
                )
            )
            selected_paths.add(u.path)
        if drop:
            logger.info(
                "select_letter_evidence: dropped %d leadership candidate(s) beyond the "
                "cap: %s",
                len(drop), [u.path for u in drop],
            )

    # ── Global bound ─────────────────────────────────────────────────────────
    if len(selected) > cap:
        kept, dropped = selected[:cap], selected[cap:]
        logger.info(
            "select_letter_evidence: capped digest at %d, dropped %d lower-priority "
            "item(s): %s",
            cap, len(dropped), [d.path for d in dropped],
        )
        selected = kept

    return selected


_BLOCK_INSTRUCTION = (
    "The items below are the candidate's OWN strongest JD-relevant material, "
    "selected deterministically from their vault (not the tailored CV, which "
    "may have compressed some of it away). Surface an item where it "
    "genuinely fits the letter's flow. Each item is grounded and claimable — "
    "quote or closely paraphrase it, never invent a connection between two "
    "items that is not stated verbatim in either. This is ADDITIONAL "
    "evidence to choose from, not content that must all appear in the "
    "letter — selectivity is expected. It never overrides the GROUNDING "
    "CONTRACT above: an item is only ever used to support a claim, never as "
    "license to state something beyond what it says."
)


def render_letter_evidence_block(items: list[EvidenceDigestItem]) -> str:
    """Render the digest for the WRITER prompt (threaded via
    ``build_cover_letter_prompt``'s new ``vault_evidence_block`` kwarg).

    Returns ``""`` when ``items`` is empty so a JD with no claimable
    concepts / no leadership trigger adds nothing.
    """
    if not items:
        return ""
    lines = ["=== STRONGEST VAULT EVIDENCE (deterministic — #271) ===", _BLOCK_INSTRUCTION]
    for item in items:
        lines.append(f"  - [{item.concept}] {item.text} (source: {item.path})")
    return "\n".join(lines)

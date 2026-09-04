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

"""#271 Task 2/3 — strongest-vault-evidence digest, shared by BOTH document
chains (renamed from ``letter_evidence`` for #303, 2026-08-07).

**Why it is no longer letter-only (ADR-066 — one logical operation, one
implementation).** "Select the vault's strongest JD-relevant evidence and hand
it to the writer verbatim, with the vault entry that owns it" is one logical
operation. #271 built it and wired it to the cover-letter writer only; #271's
own closure note recorded the CV half as still open, and #303 is that half.
The CV writer does receive the whole profile JSON, so this block adds no NEW
data there — it adds the two things the CV writer never had: the MAPPING from
each claimable ledger concept to the specific vault sentence that answers it,
and that sentence's OWNER path. Until now the CV writer's only concept→evidence
pointer was the ledger's ``evidence`` field, which is the gap classifier's
free-text ``reason`` (``services.gap.ledger_input_from_classification``) — a
paraphrase of why the classifier graded the row, carrying no vault quote, no
owner and no figure. That is why a `direct`/claimable concept could reach the
delivered CV as a bare skills-list keyword while the letter, which HAS this
block, named the vault's own sentence: charter runs #7 (`Budgetverantwortung`
/ `6 Mio. €`), 13 (#415, `Jahresabschluss`), 17 and 18 (#452, and run 18's
surviving `~90 MA` residual) all show that same CV-vs-letter asymmetry, and
every blind panel read it as `aufgeblasen`.

The digest OFFERS evidence to a writer; it never decides what the delivered
document contains, never gates, and never deletes. It is therefore not the
keyword-proxy STRENGTH ranking ADR-067 clause 4 retired (that clause governs
deterministic code choosing which of the writer's own bullets survives a cap —
``bullet_cuts``/``condense_to_budget``, whose cut order is a figure fact).

Ground truth (charter run #5): the letter's CANDIDATE PROFILE block is built
from ``cv_data`` (the TAILORED CV's ``tailored_data``), condensed to
``work_history[:6]`` x ``bullets[:6]`` (``prompts.cover_letter.
build_cover_letter_prompt``). The run-5 NordPharm work entry survived tailoring
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

   MEASURED-OUTCOME QUALIFIER (rule 2, run-6 follow-up): an anchor need not
   itself read as a target to have its claim MADE CREDIBLE by a same-owner
   measured result — a "pioneered X" responsibility is not aspirational
   language, but the work entry can separately carry a bare headline figure
   AND that figure's own measured justification. When the swap above does
   NOT fire (the anchor is not itself a target phrase),
   ``find_paired_outcome`` is tried anyway against the anchor's own text; a
   genuine pairing is added immediately after the anchor, in the SAME
   ledger-order slot, rather than left to the same-initiative scan below —
   ground truth (run-6, verified against the real dev-DB vault/log,
   2026-07-26): left to the same-initiative scan alone, this fact only
   competes in path-sort order against every other figure-bearing sibling
   and every other concept's own anchor, so it — not the bare headline
   figure it explains — was the one silently lost to the shared cap. Both
   run-4 blind panel reviewers named exactly this fact as the invite-
   flipping evidence.

   SAME-INITIATIVE EXTENSION: a concept anchor only proves ONE sentence of
   an initiative is JD-relevant; the initiative's OTHER quantified evidence —
   still owned by the SAME work/project entry
   (``oracle.matchers.vault.EvidenceUnit.owner_ids``, the #196/#244
   attribution machinery, unchanged) and carrying a concrete
   ``EvidenceUnit.figures`` entry — is "vault evidence that supports" the
   claimable concept in the broader sense rule 1 asks for, even when its own
   text does not literally contain the concept's surface form. The
   same-initiative scan is ALSO filtered through ``is_target_phrase`` — a
   bare target-phrased sentence must never surface via this extension
   either; if it has a genuine measured pair, that pair independently
   qualifies as its own figure-bearing, non-target candidate in the very
   same scan (or, when it is itself the pairing for a NON-target anchor, it
   is already selected by the qualifier rule above and skipped here as a
   duplicate).

3. LEADERSHIP ELIGIBILITY (rule 3) — gated on the posting's OWN stated
   leadership-vs-hands-on weighting, ``JobAnalysis.leadership_emphasis``
   (#271, migration 0056): a facet the job-analysis model extracts in its
   existing call, carrying the posting's verbatim sentence as its identity
   and one of ``leadership_led``/``balanced``/``hands_on_led``. That value
   is read as DATA here — it both triggers the channel and sets its sub-cap
   (:data:`_LEADERSHIP_ITEMS_BY_EMPHASIS`), so a posting that is mostly
   hands-on with a mentoring line no longer admits as much leadership
   evidence as one that is 60% leadership. Until #271 this was a fixed
   marker-word check over the JD excerpt, which could answer only "is the
   word there" — a flat cap of 4 whether the posting said 10% or 90%, the
   defect both charter-run-#5 blind reviewers named. The excerpt check
   survives ONLY as the use-time fallback for rows written before migration
   0056 (:func:`resolve_leadership_emphasis`), where it reproduces the
   pre-#271 behaviour exactly. Only once triggered does vault text carrying
   the leadership marker vocabulary become eligible; a posting that names no
   people-leadership still selects none.

Bounded to ``cap`` items (default 8); every dropped candidate is logged at
info level (never a silent truncation) with a fixed, documented priority
order: claimable-concept anchors first (ledger order), then same-initiative
extensions, then leadership evidence — each internally ordered by vault path
for determinism.
"""
from __future__ import annotations

import copy
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

# #271 — the leadership sub-cap, read off the posting's OWN stated weighting
# (``JobAnalysis.leadership_emphasis.emphasis``, model-extracted, floored in
# services/job.py). Mapping a stored value to a bound is a fact; the judgement
# that produced the value is the model's (ADR-062 clause 1). Before this, the
# cap was a flat 4 whether the posting said "10% leadership" or "90%" — the
# defect #271's 2026-07-30 restatement names. The numbers straddle the old flat
# value in both directions so no emphasis silently becomes the old behaviour,
# and all three stay under DEFAULT_DIGEST_CAP: channel 3 runs last and the
# global cap truncates from the tail, so raising this bound can never starve a
# claimable-concept anchor — it only claims room the other channels left.
_LEADERSHIP_ITEMS_BY_EMPHASIS = {
    "leadership_led": 6,
    "balanced": 4,
    "hands_on_led": 2,
}

# Evidence-unit path fragments that carry a genuine work/initiative claim
# (as opposed to identity/dates/technologies-list noise) — reused shape from
# outcome_preference's own outcome-candidate scoping, applied slightly wider
# here (responsibilities included, not just achievements/story-outcome)
# because the run-5 leadership material (the mentoring arc, the distributed-
# team line) lives under ``responsibilities``, not ``achievements``.
_INITIATIVE_PATH_MARKERS = (".achievements[", ".responsibilities[")

# Deterministic, deliberately generic people-leadership vocabulary. ADR-062's
# clause-1 survey names this list as a known violation site because it answered
# TWO questions, one of which is a judgement about the posting.
#
# #271 retires the JD-side half: "does this posting want leadership, and how
# much" is now answered by ``JobAnalysis.leadership_emphasis``, extracted by the
# job-analysis model. :func:`jd_signals_leadership` survives ONLY as the
# use-time resolution for rows that hold no facet — every ``job_analyses`` row
# written before migration 0056 — so those keep the behaviour they have today
# instead of silently losing channel 3. It is not consulted when a facet exists.
#
# The VAULT-side question ("does this candidate sentence show leadership") is
# still answered here and is still the clause-1 violation the survey describes.
# Retiring it is a separate change with a separate cost: it needs a per-sentence
# judgement the vault has no channel for today, and #271 does not widen into it.
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
    """LEGACY use-time fallback for a JD row that carries no
    ``leadership_emphasis`` facet (#271) — every ``job_analyses`` row written
    before migration 0056, which is not back-filled.

    True iff the (already de-chromed, :mod:`applire.services.jd_excerpt`) JD
    excerpt mentions leadership at all. It answers presence and nothing else:
    it cannot tell a posting that is 90% leadership from one that mentions
    mentoring in a footnote, which is precisely why it is no longer the primary
    trigger. Never fires on a JD that never mentions leadership.
    """
    if not jd_excerpt:
        return False
    return _contains_any_marker(jd_excerpt.lower(), _LEADERSHIP_MARKERS)


def resolve_leadership_emphasis(
    leadership_emphasis: dict[str, Any] | None, jd_excerpt: str
) -> str | None:
    """The rule-3 trigger, resolved at use time (#271).

    Returns the emphasis the posting states, ``"balanced"`` when only the legacy
    marker fallback fired (presence known, weighting unknown — the honest value,
    and the one that reproduces today's flat cap of 4), or ``None`` when the
    posting asks for no people-leadership at all.

    Tolerant of a facet the floor did not produce (hand-written fixtures, an
    older caller, a hand-edited row): an unrecognised ``emphasis`` is treated as
    absent rather than trusted, so a malformed value can never widen selection.
    """
    if isinstance(leadership_emphasis, dict):
        emphasis = leadership_emphasis.get("emphasis")
        if emphasis in _LEADERSHIP_ITEMS_BY_EMPHASIS:
            return emphasis
        logger.warning(
            "select_vault_evidence: ignoring unrecognised leadership_emphasis %r "
            "— falling back to the legacy JD marker check (#271).",
            leadership_emphasis,
        )
    return "balanced" if jd_signals_leadership(jd_excerpt) else None


def _leadership_concept_label(leadership_emphasis: dict[str, Any] | None) -> str:
    """What the writer prompts are told this evidence answers.

    With a facet, the label carries the posting's OWN sentence verbatim, so the
    writer positions against "~60% technical leadership / 40% hands-on" instead
    of against a boolean — the input-threading half of #271. Without one (legacy
    row) or with one the trigger rejected, the pre-#271 wording is kept
    unchanged: a facet whose emphasis did not resolve contributes no quote
    either, so a rejected facet is rejected whole.
    """
    if isinstance(leadership_emphasis, dict) and (
        leadership_emphasis.get("emphasis") in _LEADERSHIP_ITEMS_BY_EMPHASIS
    ):
        quote = leadership_emphasis.get("quote")
        if isinstance(quote, str) and quote.strip():
            # ADR-084 embedding point 23a: this is the one place a VERBATIM
            # posting sentence travels into a writer prompt as a concept LABEL.
            # Neutralised rather than fenced: the label is an item in the
            # Form-B-marked block below, and fencing a value that also serves as
            # an identity string risks changing what downstream comparisons see.
            # The residual is named in ADR-084's Consequences.
            from applire.services.untrusted_text import neutralise

            return f'leadership (the posting states: "{neutralise(quote.strip())}")'
    return "leadership (JD states a leadership weighting/responsibility)"


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
    # ADR-070 clause 5: scope entries out by predicate. Before this guard the
    # non-match was accidental — the bar's synthesised label (which embeds the
    # JD's own figure) simply never occurred verbatim in vault text, so
    # _anchor_for_concept found nothing. Scope evidence reaches the letter via
    # render_scope_positioning_block, never through this digest.
    from applire.services.keyword_ledger import is_scope_entry

    return [
        e
        for e in (keyword_ledger or [])
        if isinstance(e, dict) and e.get("claimable") and not is_scope_entry(e)
    ]


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
    # #303: the vault entry id(s) that OWN this unit
    # (:attr:`~applire.services.oracle.matchers.vault.EvidenceUnit.owner_ids`,
    # the #196/#244 attribution machinery). Carried so the segmented CV path
    # can hand each work-section prompt ONLY its own entry's evidence — a
    # whole-vault digest inside a per-entry prompt is an invitation to the
    # ADR-071 misattribution class. Defaulted so nothing that constructs an
    # item positionally has to change.
    owner_ids: frozenset[str] = frozenset()


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


def select_vault_evidence(
    keyword_ledger: list[dict[str, Any]] | None,
    jd_excerpt: str,
    profile_json: dict[str, Any] | Any,
    *,
    cap: int = DEFAULT_DIGEST_CAP,
    leadership_emphasis: dict[str, Any] | None = None,
) -> list[EvidenceDigestItem]:
    """Select the vault's strongest JD-relevant evidence for the letter
    (#271 Tasks 2/3). Pure, deterministic; ``None``/empty tolerant.

    See the module docstring for the three selection channels and their
    priority order under the ``cap`` bound.

    ``leadership_emphasis`` is the posting's own stated leadership-vs-hands-on
    weighting (``JobAnalysis.leadership_emphasis``, #271) — it drives channel 3's
    trigger, its sub-cap and its concept label. ``None`` (a pre-migration row, or
    a posting that names no people-leadership) falls back to the legacy JD marker
    check, which reproduces the pre-#271 behaviour exactly.
    """
    # "Pure" has to be true, not aspirational (#303). ``build_vault_index``
    # coerces a dict through ``MasterProfileData.model_validate``, and that
    # model's ``mode="before"`` ``_migrate_legacy_fields`` validator rewrites
    # its input IN PLACE (``data.pop("work_history")``,
    # ``data["skills"] = [...]``, ``data.pop("contact")``). The CV chain hands
    # this function the very ``profile_json`` it then serialises as the
    # ADR-021 reviewer's source of truth, so a legacy-shaped vault would have
    # its reviewer grounded against a normalised rewrite it never stored.
    # Copied here rather than at each call site: the mutation belongs to this
    # function's own dependency, and a caller cannot be expected to know.
    # (The in-place validator is a defect in its own right; it is left alone
    # deliberately — every other caller's behaviour is unchanged by this.)
    if isinstance(profile_json, dict):
        profile_json = copy.deepcopy(profile_json)
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
        selected.append(EvidenceDigestItem(concept=concept, reason=reason, path=anchor.path,
                                          text=anchor.text, owner_ids=anchor.owner_ids))
        selected_paths.add(anchor.path)

        # #271 run-6 follow-up — MEASURED-OUTCOME QUALIFIER, placed
        # immediately after its own anchor (never deferred to the separate
        # same-initiative pass below): an anchor that does NOT itself read
        # as a target (the swap above did not fire, reason is still
        # "claimable-concept") can still have its own claim MADE CREDIBLE by
        # a same-owner measured result elsewhere in the vault — exactly the
        # run-6 shape (ground truth, verified against the real dev-DB vault/
        # log 2026-07-26): a "pioneered X" responsibility whose work entry
        # separately carries a bare headline figure AND that figure's
        # measured justification. The same-initiative extension (below)
        # WOULD eventually find this same unit too, but only in path-sort
        # order alongside every other figure-bearing sibling and behind
        # every other claimable concept's own anchor — under the shared
        # global cap, that is what let the bare headline outlive the fact
        # that justifies it. Anchoring the qualifier to ITS OWN concept's
        # ledger-order slot (like the anchor itself) gives it the same
        # priority protection the anchor already has.
        #
        # ``find_paired_outcome`` reused verbatim (#261's own pairing
        # function — owner-scoped, token-overlap-gated, fails closed to
        # ``None``) is not guaranteed to exclude the anchor's OWN unit from
        # its candidate search when the anchor itself lives at an
        # ``achievements[]``/``.outcome`` path (a legitimate "outcome
        # candidate" path) — a self-match there would return the anchor
        # unchanged. The explicit path check below is required, not
        # decorative: self-matching anchors are exactly the case the
        # existing same-initiative extension already covers unaided, so
        # skipping them here causes no loss of coverage.
        if reason == "claimable-concept" and anchor.owner_ids:
            qualifier = find_paired_outcome(anchor.text, anchor.owner_ids, index.units)
            if (
                qualifier is not None
                and qualifier.path != anchor.path
                and qualifier.path not in selected_paths
                and not is_target_phrase(qualifier.text)
            ):
                selected.append(
                    EvidenceDigestItem(
                        concept=concept,
                        reason="measured-outcome-qualifier",
                        path=qualifier.path,
                        text=qualifier.text,
                        owner_ids=qualifier.owner_ids,
                    )
                )
                selected_paths.add(qualifier.path)

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
                    owner_ids=u.owner_ids,
                )
            )
            selected_paths.add(u.path)
        if drop:
            logger.info(
                "select_vault_evidence: dropped %d same-initiative candidate(s) for "
                "owner set %s beyond the per-anchor cap: %s",
                len(drop), sorted(anchor_owners), [u.path for u in drop],
            )

    # ── Channel 3: leadership eligibility ────────────────────────────────────
    # #271: the trigger AND the bound come from the posting's own stated
    # weighting, not from a substring check that cannot tell 10% from 90%.
    resolved_emphasis = resolve_leadership_emphasis(leadership_emphasis, jd_excerpt)
    if resolved_emphasis is not None:
        max_leadership_items = _LEADERSHIP_ITEMS_BY_EMPHASIS[resolved_emphasis]
        leadership_concept = _leadership_concept_label(leadership_emphasis)
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
            leadership_candidates[:max_leadership_items],
            leadership_candidates[max_leadership_items:],
        )
        for u in keep:
            selected.append(
                EvidenceDigestItem(
                    concept=leadership_concept,
                    reason="leadership-eligible",
                    path=u.path,
                    text=u.text,
                    owner_ids=u.owner_ids,
                )
            )
            selected_paths.add(u.path)
        if drop:
            logger.info(
                "select_vault_evidence: dropped %d leadership candidate(s) beyond the "
                "%r sub-cap of %d: %s",
                len(drop), resolved_emphasis, max_leadership_items,
                [u.path for u in drop],
            )

    # ── Global bound ─────────────────────────────────────────────────────────
    if len(selected) > cap:
        kept, dropped = selected[:cap], selected[cap:]
        logger.info(
            "select_vault_evidence: capped digest at %d, dropped %d lower-priority "
            "item(s): %s",
            cap, len(dropped), [d.path for d in dropped],
        )
        selected = kept

    return selected


_LETTER_BLOCK_INSTRUCTION = (
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

# #303. Deliberately NOT the letter wording: that text names "the letter's
# flow" and contrasts the digest with "the tailored CV", neither of which
# means anything to the writer of the CV itself.
#
# Two clauses carry the whole point of the block on this chain:
#   * the SOURCE path. Grounding rules 1 and 2 of the CV writer prompt are
#     per-ENTRY ("a bullet under a work entry must trace to THAT ENTRY'S OWN
#     …"), and an evidence item without its owner is exactly the input that
#     produces the ADR-071 misattribution class.
#   * "selectivity is expected". The same prompt carries a hard ROLE BULLET
#     BUDGETS ceiling; a block that demanded every item appear would be a
#     second instruction contradicting the first about the same entry, which
#     is what ADR-062 clause 4 forbids and what drove the reverted 2026-07-30
#     #303 fix to full review-loop exhaustion.
_CV_BLOCK_INSTRUCTION = (
    "The items below are the candidate's OWN strongest JD-relevant material, "
    "selected deterministically from their vault: for each claimable "
    "Keyword-Ledger concept, the vault's own sentence that actually answers "
    "it. Each item's (source: …) names the vault entry that OWNS it — a "
    "bullet drawn from an item belongs under THAT work entry and nowhere "
    "else. Quote or closely paraphrase; never fuse two items into one claim "
    "and never invent a connection that is not stated verbatim in either. "
    "This is evidence to choose from within the ROLE BULLET BUDGETS ceiling, "
    "not content that must all appear — selectivity is expected, and an item "
    "whose substance another bullet already carries needs no second one. "
    "These lines are EVIDENCE. They are never work-entry ids: the only ids "
    "you may key a response to are the ones in ROLE BULLET BUDGETS."
)

_CHAIN_INSTRUCTIONS = {"cv": _CV_BLOCK_INSTRUCTION, "letter": _LETTER_BLOCK_INSTRUCTION}


def filter_vault_evidence_for_owner(
    items: list[EvidenceDigestItem], owner_id: str | None
) -> list[EvidenceDigestItem]:
    """The subset of ``items`` owned by one vault entry (#303, segmented path).

    The segmented CV path builds one prompt per work entry, so it must be
    handed that entry's evidence only — a whole-vault digest inside a
    per-entry prompt is an invitation to write another employer's achievement
    under this one (ADR-071 clause 1/3). Order is preserved. An item with no
    ``owner_ids`` (a summary/certification-level unit) belongs to no entry and
    is never emitted here. Empty/unknown ``owner_id`` → ``[]``, so the caller
    adds nothing rather than adding everything.
    """
    if not owner_id:
        return []
    return [i for i in items if owner_id in i.owner_ids]


def render_vault_evidence_block(
    items: list[EvidenceDigestItem], *, chain: str = "letter"
) -> str:
    """Render the digest for a WRITER prompt.

    ``chain`` selects the instruction wording: ``"letter"`` (threaded via
    ``build_cover_letter_prompt``'s ``vault_evidence_block`` kwarg, #271) or
    ``"cv"`` (``prompts.cv_tailoring.build_user_prompt`` /
    ``prompts.cv_segmented.build_work_section_prompt``, #303). The selected
    ITEMS are identical on both chains — one selector, one implementation
    (ADR-066); only the sentence telling the writer what to do with them
    differs, because the two writers are producing different documents.

    Returns ``""`` when ``items`` is empty so a JD with no claimable
    concepts / no leadership trigger adds nothing.
    """
    if not items:
        return ""
    try:
        instruction = _CHAIN_INSTRUCTIONS[chain]
    except KeyError:  # pragma: no cover - programmer error, fail loudly
        raise ValueError(
            f"unknown chain {chain!r}; expected one of {sorted(_CHAIN_INSTRUCTIONS)}"
        ) from None
    lines = ["=== STRONGEST VAULT EVIDENCE (deterministic — #271) ===", instruction]
    for item in items:
        if chain == "cv":
            # Deliberately NOT the letter's "  - [<label>] …" shape (PR #473).
            # ``cv_budget.render_budget_table`` owns that shape on this chain —
            # "  - [<work_entry_id>] <company> — <role>: max N" is the
            # ADR-067 clause 3 id channel, and the system prompt tells the
            # writer to key its response to the id it finds there. Rendering a
            # concept label in the same shape a few lines above it hands the
            # writer two things that look like ids; the integration stack's
            # mock writer duly returned prose keyed to the concept "Python",
            # and a real model reading the same two blocks can conflate them
            # for the same reason. The letter prompt has no id channel, so its
            # rendering is left byte-identical (charter-verified, run 18).
            lines.append(
                f"  EVIDENCE (concept: {item.concept} | source: {item.path})\n"
                f"      \u201c{item.text}\u201d"
            )
        else:
            lines.append(f"  - [{item.concept}] {item.text} (source: {item.path})")
    return "\n".join(lines)

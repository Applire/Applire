# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""ADR-072 — THE selection instrument of the ledger restore, and the plan that
carries a restoration across the ADR-038 language pass (#724).

Until 2026-09-18 the selection ("which claimable concept is verifiably missing,
and which vault bullet of the owning work entry carries it") lived inline in
``services/cv.py::_restore_ledger_bullets``, which runs inside
``_compose_document`` — i.e. strictly AFTER ``_review_cv_language``, the pass
that makes the document one language. The consequence was structural rather than
occasional: on every EN-vault → DE-document generation in which the restore
fired, the delivered CV was bilingual by construction (#724,
``triage:document-harm``, founder's edge UAT 2026-09-18 — 4 of 15 delivered work
bullets in English, both blind reviewers reading the document as unedited).

ADR-072 amended 2026-09-18: **"verbatim" is verbatim INTO the draft.** The
selection runs BEFORE the language pass, over the same provisionally composed
document the tail would have seen, so the selected set is unchanged; the chosen
vault bullets ride into the PROSE draft and that pass translates them like any
other bullet; the tail keeps its ordering, its `#315` load-bearing placement and
its whole ceiling enforcement, and may never re-add a vault bullet the preseed
already placed.

This module owns the selection (ADR-066: one implementation — the preseed and
the tail call the SAME function, there is no second definition of what gets
restored) and the plan object that carries the result across the language pass.
It computes only facts (a ledger concept's verifiable absence, a vault bullet's
surface forms) and it never deletes, never re-words and never calls a model.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RestoreCandidate:
    """One vault bullet the restore would place into one work entry."""

    entry_id: str
    vault_text: str
    concept: str
    load_bearing: bool


@dataclass
class PreseededBullet:
    """A candidate that was injected into the prose draft before the language
    pass, together with the text that pass settled on.

    ``translated_text`` equals ``vault_text`` when the language pass did not run,
    did not change the bullet, or lost it (in which case the vault text was
    re-appended verbatim — the pre-#724 behaviour, never a lost concept).
    """

    entry_id: str
    vault_text: str
    translated_text: str
    concept: str
    load_bearing: bool


@dataclass
class PreseedPlan:
    """What the preseed placed, keyed by work-entry id.

    ``by_entry`` is populated at injection time and its ``translated_text``
    values are settled after the language pass. ``industry_context`` and
    ``projects`` carry the two other vault-verbatim classes that used to reach
    the delivered document past the last language pass (ADR-062 clause 1 and
    ADR-067, both amended by the same 2026-09-18 ruling).
    """

    by_entry: dict[str, list[PreseededBullet]] = field(default_factory=dict)
    #: work-entry id -> the language pass's rendering of that role's industry line
    industry_context: dict[str, str] = field(default_factory=dict)
    #: vault skill name -> the language pass's rendering of it (#672 line 102).
    #: Keys are the VAULT's own spelling; `_tailor_skills_to_jd` reads the keys to
    #: know it must not place that spelling on the page a second time.
    skills: dict[str, str] = field(default_factory=dict)
    #: vault skill names (#192 tier-0/required) the preseed found ALREADY on the
    #: page before the language pass ran — the writer's own echo of the vault's
    #: spelling, not something this preseed placed. Tracked separately from
    #: ``skills`` because `_tailor_skills_to_jd`'s end-of-tail recompute reads the
    #: page AFTER translation and `skills_page_dupe` is blind to a cross-language
    #: pair (ADR-066/067 — a same-script instrument, not a language-aware one):
    #: without this record, a concept the writer already had — and the language
    #: pass then correctly translated — looks "missing" again once translated,
    #: and the tail re-adds the vault spelling next to its own translation
    #: (#672 L102 residual, delivery-run probe 2026-09-19 — "Contract testing"
    #: next to "Vertragstests").
    skills_already_covered: frozenset[str] = field(default_factory=frozenset)
    #: claimable ledger CONCEPTS the preseed found already covered by the
    #: PROVISIONAL draft (the writer's own narration, before the language
    #: pass ran) — a bullet half of the same class ``skills_already_covered``
    #: closes for chips (#724 residual, delivery-run probe re-run 2026-09-19,
    #: M-27: "Integrated a Stripe-based checkout for a subscription billing
    #: feature." delivered verbatim beside the writer's own, differently-
    #: phrased German bullet covering the same concept). ``select_restore_
    #: candidates``'s coverage predicate (``ats_audit.surface_present``,
    #: ADR-066) is a same-script instrument: a SECOND derivation, run at
    #: compose time against the document AFTER ``_review_cv_language`` has
    #: translated it, can no longer see that this concept was already
    #: narrated — it reads as newly "missing" and the tail would restore the
    #: vault's own English text a second time, verbatim, past the language
    #: pass. Recorded once, here, while the draft is still in whatever script
    #: the ledger's surface forms were built to match; consulted by
    #: ``_restore_ledger_bullets`` (``services/cv.py``) to tell a concept
    #: that was ALREADY covered (silent) from one that GENUINELY went missing
    #: since (logged, and still never papered over with a verbatim bullet —
    #: on a cross-language document the tail may not inject vault text at
    #: all, since anything it selects at that point is past the last chance
    #: to translate it).
    bullets_already_covered: frozenset[str] = field(default_factory=frozenset)
    #: bullet counts per entry at injection time, used to verify the settle
    _pre_lengths: dict[str, int] = field(default_factory=dict)
    #: skills-list length at injection time, same purpose
    _pre_skills_len: int = 0

    def excluded_skill_names(self) -> frozenset[str]:
        """Every vault skill name ``_tailor_skills_to_jd`` may not place a SECOND
        time: what this preseed itself placed (``skills`` keys, the vault's own
        spelling) UNION what it found already covering the page before the
        language pass ran (``skills_already_covered``). ONE set — the tail's
        recompute must never re-derive "missing" from the post-translation page
        alone, because that page is exactly where a translated concept becomes
        invisible to a same-script dupe check.
        """
        return frozenset(self.skills) | self.skills_already_covered

    def excluded_vault_norms(self) -> dict[str, frozenset[str]]:
        """Per entry, the normalised vault bullets the tail may not restore again.

        ADR-072 amendment 2026-09-18 clause 4: without this, a concept whose
        surface form does not survive translation would deliver the German
        translation AND the English original — strictly worse than the defect.
        """
        from applire.services.ats_audit import _norm

        return {
            eid: frozenset(_norm(p.vault_text) for p in placed)
            for eid, placed in self.by_entry.items()
            if placed
        }

    def settled_by_norm(self, entry_id: str) -> dict[str, PreseededBullet]:
        """The settled texts of this entry's restorations, keyed by ``_norm``.

        Identity, not position: the language refiner is told not to reorder, but
        matching on the settled text survives a reorder, and a rewording (a later
        terminal-review round) simply drops the bullet back to ordinary status —
        it is still never deleted-and-re-added.
        """
        from applire.services.ats_audit import _norm

        return {_norm(p.translated_text): p for p in self.by_entry.get(entry_id, ())}

    def is_empty(self) -> bool:
        return (
            not any(self.by_entry.values())
            and not self.industry_context
            and not self.skills
            and not self.skills_already_covered
            and not self.bullets_already_covered
        )


def select_restore_candidates(
    draft_json: dict,
    profile_json: dict,
    keyword_ledger: Sequence[dict] | None,
    budget,
    *,
    exclude: Mapping[str, frozenset[str]] | None = None,
) -> dict[str, list[RestoreCandidate]]:
    """THE ledger-restore selection (ADR-072 clause 1, ADR-066).

    Uses the shared presence predicate (``ats_audit.surface_present`` via
    ``keyword_ledger.verified_missing_claimable`` — #122: "the loop that grades is
    the loop that heals") to find claimable concepts verifiably ABSENT from the
    whole document, then picks, per work entry in DOCUMENT order, the vault
    responsibility/achievement bullets of that entry which carry one. A concept is
    claimed at most once, by the first entry whose vault text carries it — the
    reverse-chronological preference #234 established.

    ``exclude`` names, per entry id, normalised vault-bullet texts that may not be
    selected again (ADR-072 amendment 2026-09-18 clause 4 — the preseed already
    placed them, possibly translated).

    Pure: no LLM, no I/O, nothing mutated.
    """
    from applire.services.ats_audit import _norm, surface_present
    from applire.services.keyword_ledger import (
        is_load_bearing,
        verified_missing_claimable,
        verified_missing_load_bearing,
    )

    out: dict[str, list[RestoreCandidate]] = {}
    if not keyword_ledger:
        return out

    missing = verified_missing_claimable(draft_json, keyword_ledger)
    # #315: a LOAD-BEARING concept (a `direct`+`claimable` figure a hiring reviewer
    # checks for by name) is missing its evidence even when a bare keyword mention
    # elsewhere (skills list, summary) already satisfies the whole-document check
    # above. Union in, deduped by concept.
    already = {e.get("concept") for e in missing}
    for entry in verified_missing_load_bearing(draft_json, keyword_ledger):
        if entry.get("concept") not in already:
            missing.append(entry)
            already.add(entry.get("concept"))
    if not missing:
        return out

    vault_by_id: dict[str, dict] = {}
    for w in profile_json.get("work_experience") or []:
        wid = str(w.get("id") or "")
        if wid:
            vault_by_id[wid] = w

    def _entry_forms(entry: dict) -> list[str]:
        forms = list(entry.get("surface_forms") or [])
        if entry.get("concept"):
            forms.append(entry["concept"])
        return forms

    remaining = list(missing)
    for w in draft_json.get("work_history") or []:
        if not remaining:
            break
        eid = str((w or {}).get("id") or "")
        vault_entry = vault_by_id.get(eid)
        if vault_entry is None:
            continue
        existing_norms = {
            _norm(b) for b in (w.get("bullets") or []) if isinstance(b, str)
        }
        blocked = (exclude or {}).get(eid) or frozenset()
        vault_bullets = [
            b
            for key in ("responsibilities", "achievements")
            for b in (vault_entry.get(key) or [])
            if isinstance(b, str) and b.strip()
        ]
        for vb in vault_bullets:
            vb_norm = _norm(vb)
            if not vb_norm or vb_norm in existing_norms or vb_norm in blocked:
                continue
            hit_idx = next(
                (
                    i
                    for i, m in enumerate(remaining)
                    if any(surface_present(f, vb_norm) for f in _entry_forms(m))
                ),
                None,
            )
            if hit_idx is None:
                continue
            matched_entry = remaining.pop(hit_idx)
            existing_norms.add(vb_norm)
            out.setdefault(eid, []).append(
                RestoreCandidate(
                    entry_id=eid,
                    vault_text=vb,
                    concept=str(matched_entry.get("concept") or ""),
                    load_bearing=bool(is_load_bearing(matched_entry)),
                )
            )
    return out

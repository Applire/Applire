# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#415 — ADR-072 amended 2026-09-17: the cut ranking reads the corpus the delivered
`narrative-evidence` check grades, and clause 4's exemption gains its SECOND provenance.

Founder rulings W-1 / W-1b (Nougat close-out). Two halves, measured separately:

* **clause 1, the sole-carrier TIER's corpus.** 2026-09-11 delivery run: the cap removed
  the role's only ISO-9001 bullet at `sole_carrier=False` — false because the *skills
  list* carried the tag — and the delivered report then read
  `narrative-evidence: fail … ISO 9001 (claimed but not evidenced)`. Two instruments, one
  question, two populations of the same document. The tier now reads work-entry + project
  bullets ∪ the vault-joined `languages`/`certifications`/`education` sections, i.e.
  byte-for-byte what `verified_narrative_underclaim(..., structured_document=…)` treats as
  covered. It stays a TIER: the per-role ceiling always holds (a partition on a property
  measured 7 of 8 bullets exempt in #666 — the cap switched off, not amended).
* **clause 4, the second provenance.** The 2026-09-05 ruling says *"a bullet introduced in
  this round in response to a COVERAGE **or** under-claim signal"*; #666 built the
  under-claim half. The ADR-048/US213 verified-coverage demand now reports through
  `coverage_reviewer_prompt_fn(on_demand=…)`, with the WHOLE-document corpus **its own**
  producer measures absence in — an exemption may never be wider than its demand.

Delivery-tier before/after on the captured 2026-09-11 state (zero provider calls, driver
and raw output in `Documents/Runs/Nougat/close-out/w/`): the BEFORE arm reproduces the
production log line byte-for-byte (`sole_carrier=False tier=(False, -3)`, same removed
text, both rounds) and its composed bullets equal the delivered `tailored_data`; the AFTER
arm keeps the ISO-9001 bullet, holds the ceiling at 5 of 5 and the bullet total at 8, and
the delivered check reads `pass`. Flagged-after-deletion: 2 → 0.

CI pins the mechanism. Every guard below is mutation-killed by name in the report.
"""
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.bullet_cuts import rank_cuts  # noqa: E402

# ── The 2026-09-11 shape, reduced to its load-bearing parts ──────────────────────────
#: The bullet the cap took. Figure-less, listed late, and the ONLY bullet carrying
#: ISO 9001 — while the skills list carries the tag.
ISO = "Jährliche ISO-9001-Audits als Bereichsverantwortlicher begleitet."
FIG_LTIF = "Unfallquote (LTIF) über fünf Jahre von 8,2 auf 3,1 gesenkt."
FIG_BUDGET = "Operative Budgetverantwortung für rund 6 Mio. EUR getragen."
LEAD = "Führung von zwei Fertigungsbereichen mit 38 Mitarbeitenden."
#: The W-1b fixture: a bullet whose concept the LANGUAGES section already delivers.
LANG = "Deutsch als Muttersprache."

#: Order matters and is the run's own: the ISO bullet was listed LAST of the
#: figure-less ones, which is half of why it ranked last (#423's shape).
BULLETS = [LEAD, FIG_BUDGET, LANG, FIG_LTIF, ISO]
#: `(bullet_carries_figure, -index)` — `_cap_bullets`' own key.
TIERS = [(True, -0), (True, -1), (False, -2), (True, -3), (False, -4)]
GROUPS = (("ISO 9001",), ("Budgetverantwortung",), ("Führungserfahrung", "Führung"),
          ("Sehr gutes Deutsch", "Deutsch"))

#: Everything the cap cannot cut, as the whole document: the skills list names ISO 9001,
#: the LANGUAGES block names Deutsch.
DOC_EXTERNAL = (
    "skills: ISO 9001, Lean Management, Budgetverantwortung, Führung. "
    "languages: Deutsch — Muttersprache."
)
#: Narrative only — another role's bullets. Neither concept appears.
NARRATIVE_EXTERNAL = "Als Fertigungsmeister eine Schicht mit 22 Mitarbeitenden gesteuert."
#: The EVIDENCE corpus: narrative ∪ the vault-joined structured sections. Deutsch is in
#: it (LANGUAGES is evidence a recruiter reads, RULING W1-3); ISO 9001 is NOT (a skills
#: tag is a tag, which is ADR-076 clause 5's founding rule).
EVIDENCE_EXTERNAL = NARRATIVE_EXTERNAL + "\nDeutsch\nMuttersprache"


def _cuts(keep, **kw):
    return [c.text for c in rank_cuts(BULLETS, TIERS, keep=keep, concept_groups=GROUPS,
                                      external_text=DOC_EXTERNAL, **kw)]


def _cut_objs(keep, **kw):
    return rank_cuts(BULLETS, TIERS, keep=keep, concept_groups=GROUPS,
                     external_text=DOC_EXTERNAL, **kw)


# ── clause 1: the tier's corpus ──────────────────────────────────────────────────────


def test_the_pre_amendment_ranking_deletes_the_sole_narrative_carrier():
    """The BEFORE arm, asserted rather than assumed (a guard test is only a guard when
    the baseline provably fails without it). The skills tag makes ISO 9001 read as
    covered, so the figure-less ISO bullet ranks last and is cut."""
    cuts = _cut_objs(keep=4)
    assert [c.text for c in cuts] == [ISO]
    assert cuts[0].sole_carrier is False, (
        "the whole-document corpus is exactly why production logged sole_carrier=False"
    )


def test_the_evidence_corpus_keeps_the_bullet_a_skills_tag_only_appeared_to_cover():
    """RULING W-1's own guard fixture — the 2026-09-11 ISO-9001 case. Same inputs, same
    ceiling; only the corpus the tier reads changes."""
    cuts = _cut_objs(keep=4, evidence_external_text=EVIDENCE_EXTERNAL)
    assert ISO not in [c.text for c in cuts]
    assert [c.text for c in cuts] == [LANG], (
        "the cap takes the bullet whose concept the LANGUAGES block already delivers"
    )


def test_a_structured_section_still_counts_as_coverage():
    """RULING W-1b. The corpus is the delivered check's, not 'bullets only': a concept a
    vault-joined section delivers is covered, so the bullet restating it is NOT protected.
    Reading it as narrative-only would re-create RULING W1-3's defect one pass later —
    protecting 'Deutsch als Muttersprache.' at a quantified bullet's expense."""
    narrative_only = [c.text for c in rank_cuts(
        BULLETS, TIERS, keep=4, concept_groups=GROUPS, external_text=DOC_EXTERNAL,
        evidence_external_text=NARRATIVE_EXTERNAL,
    )]
    assert narrative_only == [FIG_LTIF], (
        "narrative-only protects BOTH ISO and Deutsch and spends a figure on it"
    )
    assert _cuts(keep=4, evidence_external_text=EVIDENCE_EXTERNAL) == [LANG]


def test_the_ceiling_always_holds_even_when_every_bullet_is_a_sole_carrier():
    """The tier is a TIER, not a partition — the difference ruling W-1 named explicitly.
    With nothing covered anywhere, all five bullets are sole carriers and the cap still
    cuts down to the ceiling, by the caller's own key."""
    cuts = _cut_objs(keep=2, evidence_external_text="")
    assert len(cuts) == 3, "the per-role ceiling is not negotiable on this path"
    assert cuts[-1].sole_carrier is True, (
        "once the unprotected bullets are gone the cap takes a protected one — and says so"
    )


def test_the_conflict_is_logged_naming_both_producers(caplog):
    """ADR-072 clause 4's own standard: a constraint conflict is diagnosable from the log
    alone. A sole-carrier cut now names the ceiling's producer AND the coverage grader."""
    import logging

    from applire.services.bullet_cuts import log_cuts

    cuts = _cut_objs(keep=2, evidence_external_text="")
    with caplog.at_level(logging.INFO, logger="applire.services.bullet_cuts"):
        log_cuts("_cap_bullets", cuts, ceiling=2)
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "a sole-carrier cut must not be logged at INFO"
    assert "BUDGET_VS_COVERAGE" in warnings[0]
    assert "cv_budget.RoleBudget" in warnings[0]
    assert "cv_gap_hints.verified_narrative_underclaim" in warnings[0]
    assert "ats_audit._narrative_evidence_check" in warnings[0]
    assert "TAIL_DELETE (ADR-072 clause 4)" in warnings[0], "the line's shape is unchanged"


def test_omitting_the_evidence_corpus_reproduces_the_pre_amendment_ranking():
    """`None` is the legacy contract, byte-for-byte — which is what makes the seam tests
    below meaningful: a production call site that forgets the argument gets the OLD
    behaviour silently, so each call site is pinned separately."""
    assert _cuts(keep=4) == _cuts(keep=4, evidence_external_text=None) == [ISO]


# ── clause 4: the second provenance ──────────────────────────────────────────────────

COVERAGE_DEMANDED = (("Budgetverantwortung",),)


def test_a_coverage_demanded_bullet_is_exempt_from_the_cap():
    """The half the 2026-09-05 ruling named and #666 did not build. Without the
    provenance the budget bullet is cut at a tight ceiling; with it, the cap takes a
    non-demanded bullet instead."""
    tight = [c.text for c in rank_cuts(
        BULLETS, TIERS, keep=1, concept_groups=GROUPS, external_text=NARRATIVE_EXTERNAL,
    )]
    assert FIG_BUDGET in tight

    exempted = [c.text for c in rank_cuts(
        BULLETS, TIERS, keep=1, concept_groups=GROUPS, external_text=NARRATIVE_EXTERNAL,
        coverage_demanded_groups=COVERAGE_DEMANDED,
    )]
    assert FIG_BUDGET not in exempted
    assert len(exempted) == 4, "the demanded bullet occupies the single remaining slot"


def test_the_coverage_exemption_uses_ITS_OWN_corpus_not_the_narrative_one():
    """An exemption may never be wider than its demand. `verified_missing_claimable`
    scans the WHOLE document, so a concept the skills list already carries was never
    demanded and its bullet is not exempt. Giving this provenance the narrative corpus is
    the 7-of-8 direction #666 measured and refused."""
    cuts = [c.text for c in rank_cuts(
        BULLETS, TIERS, keep=4, concept_groups=GROUPS, external_text=DOC_EXTERNAL,
        # ISO 9001 IS in the skills list, so the whole-document test says "covered".
        coverage_demanded_groups=(("ISO 9001",),),
    )]
    assert cuts == [ISO], "a concept the document already carries buys no exemption"


def test_a_bullet_answering_both_demands_occupies_one_slot():
    """Union, not sum — the same rule #666 wrote for pins."""
    cuts = rank_cuts(
        BULLETS, TIERS, keep=3, concept_groups=GROUPS, external_text=NARRATIVE_EXTERNAL,
        demanded_groups=(("Budgetverantwortung",),),
        narrative_external_text=NARRATIVE_EXTERNAL,
        coverage_demanded_groups=COVERAGE_DEMANDED,
    )
    assert len(cuts) == 2, "one exempt bullet, one budget slot"
    assert FIG_BUDGET not in [c.text for c in cuts]

# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#415 — one seam test per CEILING ENFORCER, plus the demand-report seam.

ADR-072 clause 1 has been stated against a single named function twice and been wrong
both times (#377's scope note; the 2026-08-02 amendment 2). There are THREE enforcers and
each calls `bullet_cuts.rank_cuts` itself, so the amendment of 2026-09-17 is live only
where the two new arguments are actually passed:

| # | enforcer | reached from |
|---|---|---|
| 1 | `cv._cap_bullets` | `_restore_ledger_bullets`, the branch where nothing was restored |
| 2 | `_restore_ledger_bullets`' own ranked branch | a restoration fired AND the entry is over its ceiling |
| 3 | `cv_budget.condense_to_budget` | the page-overrun path, iterations 1 and 2 |

Plus the producer seam: `keyword_ledger.coverage_reviewer_prompt_fn(on_demand=…)`, which
is where the second demand PROVENANCE is recorded.

Each test below drives the real service function and asserts on the artefact it produced.
Revert any ONE call site (drop `evidence_external_text=` / `coverage_demanded_groups=`
there) and exactly the test named for that seam fails — the mutation matrix is in
`Documents/Runs/Nougat/close-out/w/report.md`.
"""
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.schemas.cv import TailoredCVData  # noqa: E402
from applire.services.cv import _restore_ledger_bullets  # noqa: E402
from applire.services.cv_budget import (  # noqa: E402
    BudgetResult, BulletTier, RoleBudget, condense_to_budget,
)

CONCEPT = "ISO 9001"
#: The 2026-09-11 shape: the ONLY bullet carrying the concept, figure-less, listed last.
SOLE = "Jährliche ISO-9001-Audits als Bereichsverantwortlicher begleitet."
FIGS = [
    "Ausschussquote von 4,1 % auf 2,3 % gesenkt.",
    "Termintreue von 87 % auf 96 % verbessert.",
    "Unfallquote von 8,2 auf 3,1 gesenkt.",
    "Durchlaufzeit um 18 % reduziert.",
]
BULLETS = FIGS + [SOLE]


def _ledger():
    return [{
        "concept": CONCEPT, "surface_forms": [CONCEPT], "claimable": True,
        "status": "direct", "sources": ["required"], "fit_weight": 1.0,
        "evidence": "ISO-9001-Audits als Bereichsverantwortlicher begleitet",
    }]


def _budget(max_bullets=4, **kw):
    return BudgetResult(
        roles={"w1": RoleBudget(work_entry_id="w1", tier="top", max_bullets=max_bullets)},
        tiers={"top": BulletTier("top", max_bullets, max_bullets - 1)},
        target_pages=2, region="DACH",
        claimable_forms=(CONCEPT,), claimable_concepts=((CONCEPT,),), **kw,
    )


def _doc(bullets, *, skills=(CONCEPT,), projects=(), languages=()):
    return TailoredCVData.model_validate({
        "contact": {"name": "Stefan Brandt"},
        "summary": "Produktionsleiter.",
        "skills": list(skills),
        "languages": list(languages),
        "work_history": [{
            "id": "w1", "company": "Weberit", "role": "Produktionsleiter",
            "start_date": "2017-04", "end_date": None,
            "bullets": list(bullets), "projects": list(projects),
        }],
    })


def _profile(responsibilities):
    return {
        "work_experience": [{
            "id": "w1", "company": "Weberit", "role": "Produktionsleiter",
            "start_date": "2017-04", "end_date": None, "is_current": True,
            "responsibilities": list(responsibilities), "achievements": [],
        }],
        "projects": [],
    }


# ── seam 1: `_cap_bullets` via the nothing-restored branch ───────────────────────────


def test_seam_1_cap_bullets_reads_the_evidence_corpus():
    """The skills list carries the tag, so before the amendment this pass cut the only
    bullet that evidenced the concept — the 2026-09-11 production deletion. The vault
    carries every bullet already, so nothing is restored and this is the `_cap_bullets`
    branch (the run logged `pass=_cap_bullets`)."""
    out = _restore_ledger_bullets(
        _doc(BULLETS), _profile(BULLETS), _ledger(), _budget(max_bullets=4)
    )
    kept = out.work_history[0].bullets
    assert len(kept) == 4, "the per-role ceiling still holds"
    assert SOLE in kept, (
        "the sole narrative carrier survived; a skills tag is not evidence"
    )


def test_seam_1_a_structured_section_still_counts_as_coverage():
    """RULING W-1b's own fixture. `Deutsch` lives in the vault-joined LANGUAGES block,
    which the delivered check reads as evidence — so the bullet restating it is NOT
    protected and yields before a quantified bullet."""
    lang_bullet = "Deutsch als Muttersprache."
    bullets = FIGS + [lang_bullet]
    ledger = [{
        "concept": "Sehr gutes Deutsch", "surface_forms": ["Deutsch"], "claimable": True,
        "status": "direct", "sources": ["required"], "fit_weight": 1.0,
        "evidence": "Deutsch als Muttersprache",
    }]
    budget = BudgetResult(
        roles={"w1": RoleBudget(work_entry_id="w1", tier="top", max_bullets=4)},
        tiers={"top": BulletTier("top", 4, 3)}, target_pages=2, region="DACH",
        claimable_forms=("Deutsch",), claimable_concepts=(("Deutsch", "Sehr gutes Deutsch"),),
    )
    out = _restore_ledger_bullets(
        _doc(bullets, skills=(), languages=[{"language": "Deutsch", "level": "Muttersprache"}]),
        _profile(bullets), ledger, budget,
    )
    kept = out.work_history[0].bullets
    assert lang_bullet not in kept, (
        "protecting it here would re-create RULING W1-3's defect one pass later"
    )
    assert all(f in kept for f in FIGS)


# ── seam 2: `_restore_ledger_bullets`' own ranked branch ─────────────────────────────

Y1 = "Termintreue von 87 % auf 96 % verbessert durch neue Feinplanung."
Y2 = "Rüstzeiten um 35 % gesenkt; Feinplanung im Dreischichtbetrieb neu aufgesetzt."
LB_VAULT = "Produktionsbudget von 6 Mio. EUR über fünf Jahre verantwortet."


def _two_concept_ledger():
    return [
        {"concept": CONCEPT, "surface_forms": [CONCEPT], "claimable": True,
         "status": "direct", "sources": ["required"], "fit_weight": 1.0,
         "evidence": "ISO-9001-Audits begleitet"},
        {"concept": "Feinplanung", "surface_forms": ["Feinplanung"], "claimable": True,
         "status": "direct", "sources": ["required"], "fit_weight": 1.0,
         "evidence": "Feinplanung neu aufgesetzt"},
        {"concept": "Budgetverantwortung", "surface_forms": ["Produktionsbudget"],
         "claimable": True, "status": "direct", "sources": ["required"],
         "fit_weight": 1.0, "evidence": "Produktionsbudget von 6 Mio. EUR verantwortet"},
    ]


def _three_concept_budget(max_bullets):
    return BudgetResult(
        roles={"w1": RoleBudget(work_entry_id="w1", tier="top", max_bullets=max_bullets)},
        tiers={"top": BulletTier("top", max_bullets, max_bullets - 1)},
        target_pages=2, region="DACH",
        claimable_forms=(CONCEPT, "Feinplanung", "Produktionsbudget"),
        claimable_concepts=((CONCEPT,), ("Feinplanung",), ("Produktionsbudget",)),
    )


def test_seam_2_the_restore_branch_reads_the_evidence_corpus():
    """A restoration fires, so the entry goes over its ceiling and THIS function ranks the
    cut itself instead of delegating to `_cap_bullets` — the second implementation the
    2026-08-02 amendment 2 was written about.

    The fixture is built so only the corpus can decide: every competing bullet is a ledger
    hit (`_is_hit` is True for all of them, so that tier is flat), `Feinplanung` is carried
    by TWO of them, and `ISO 9001` by one bullet plus the skills tag. Whole-document: the
    tag covers it, the sole-carrier bullet ranks last and is cut. Evidence corpus: the tag
    is not evidence, the bullet is protected, and a redundant `Feinplanung` bullet yields.
    """
    draft = [Y1, Y2, SOLE]
    out = _restore_ledger_bullets(
        _doc(draft), _profile(draft + [LB_VAULT]), _two_concept_ledger(),
        _three_concept_budget(3),
    )
    kept = out.work_history[0].bullets
    assert LB_VAULT in kept, "the restoration fired — this is the ranked branch"
    assert len(kept) == 3, "the ceiling still holds"
    assert SOLE in kept, "the sole narrative carrier survived the restore path's own cut"
    assert (Y1 in kept) != (Y2 in kept), "one of the two Feinplanung bullets yielded"


# ── the second provenance, through each enforcer ─────────────────────────────────────
#
# The exemption is a PARTITION and the sole-carrier rule is a TIER, and the difference
# only shows when the ceiling is tighter than the protected set — a tier is silently
# defeated there, which is the whole reason #666 chose a partition. So every fixture
# below is deliberately over-subscribed.

FIG_X = "Ausschussquote von 4,1 % auf 2,3 % gesenkt."
FIG_Y = "Durchlaufzeit um 18 % reduziert."
DEMANDED = "Jährliche ISO-9001-Audits als Bereichsverantwortlicher begleitet."
FILLER = "Allgemeine Aufgaben in der Fertigung wahrgenommen."
_TIGHT = [FIG_X, FIG_Y, DEMANDED, FILLER]


def _tight_ledger():
    return [
        {"concept": CONCEPT, "surface_forms": [CONCEPT], "claimable": True,
         "status": "direct", "sources": ["required"], "fit_weight": 1.0,
         "evidence": "ISO-9001-Audits begleitet"},
        {"concept": "Ausschussquote", "surface_forms": ["Ausschussquote"],
         "claimable": True, "status": "direct", "sources": ["required"],
         "fit_weight": 1.0, "evidence": "Ausschussquote gesenkt"},
        {"concept": "Durchlaufzeit", "surface_forms": ["Durchlaufzeit"],
         "claimable": True, "status": "direct", "sources": ["required"],
         "fit_weight": 1.0, "evidence": "Durchlaufzeit reduziert"},
    ]


def _tight_budget(coverage_demanded=()):
    return BudgetResult(
        roles={"w1": RoleBudget(work_entry_id="w1", tier="top", max_bullets=2)},
        tiers={"top": BulletTier("top", 2, 1)}, target_pages=2, region="DACH",
        claimable_forms=(CONCEPT, "Ausschussquote", "Durchlaufzeit"),
        claimable_concepts=((CONCEPT,), ("Ausschussquote",), ("Durchlaufzeit",)),
        coverage_demanded_concepts=coverage_demanded,
    )


def test_seam_1b_cap_bullets_honours_the_coverage_provenance():
    """Enforcer 1. Without the provenance the ceiling defeats the tier and the demanded
    bullet goes; with it, the cap takes a figure bullet instead. This is the half the
    2026-09-05 ruling named ("a COVERAGE or under-claim signal") and #666 did not build."""
    def kept(budget):
        out = _restore_ledger_bullets(
            _doc(_TIGHT, skills=()), _profile(_TIGHT), _tight_ledger(), budget
        )
        return out.work_history[0].bullets

    assert DEMANDED not in kept(_tight_budget()), (
        "baseline: a tight ceiling defeats the sole-carrier tier — that is why the "
        "exemption is a partition"
    )
    with_demand = kept(_tight_budget(coverage_demanded=((CONCEPT,),)))
    assert DEMANDED in with_demand
    assert len(with_demand) == 2, "the ceiling still binds; the cap cuts elsewhere"


def test_seam_2b_the_restore_branch_honours_the_coverage_provenance(caplog):
    """Enforcer 2, same question. The restore path must not silently undo what the cap
    path protects.

    The branch is identified from the audit trail rather than asserted by inference: the
    cut is logged with `pass=_restore_ledger_bullets`, which only this branch emits
    (`_cap_bullets` logs its own name)."""
    import logging

    def kept(budget):
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="applire.services.bullet_cuts"):
            out = _restore_ledger_bullets(
                _doc(_TIGHT, skills=()), _profile(_TIGHT + [LB_VAULT]),
                _tight_ledger() + [{
                    "concept": "Budgetverantwortung", "surface_forms": ["Produktionsbudget"],
                    "claimable": True, "status": "direct", "sources": ["required"],
                    "fit_weight": 1.0, "evidence": "Produktionsbudget verantwortet",
                }], budget,
            )
        assert any("pass=_restore_ledger_bullets" in r.getMessage() for r in caplog.records), (
            "this fixture must exercise the RANKED branch, not the _cap_bullets one"
        )
        return out.work_history[0].bullets

    assert DEMANDED not in kept(_tight_budget())
    assert DEMANDED in kept(_tight_budget(coverage_demanded=((CONCEPT,),)))


def test_seam_3b_condense_to_budget_honours_the_coverage_provenance():
    """Enforcer 3. #377's scope note a third time."""
    def kept(budget):
        data = _doc(_TIGHT, skills=()).model_dump(mode="json")
        out, _ = condense_to_budget(data, budget, iteration=1)
        return out["work_history"][0]["bullets"]

    assert DEMANDED not in kept(_tight_budget())
    assert DEMANDED in kept(_tight_budget(coverage_demanded=((CONCEPT,),)))


# ── seam 3: `condense_to_budget`, the page-overrun path ──────────────────────────────


def test_seam_3_condense_to_budget_reads_the_evidence_corpus():
    """#377's scope note for the third time: a fix that stops at `_cap_bullets` lets this
    pass re-delete the bullet the other one protected, two passes later."""
    data = _doc(BULLETS).model_dump(mode="json")
    out, changed = condense_to_budget(data, _budget(max_bullets=4), iteration=1)
    assert changed
    kept = out["work_history"][0]["bullets"]
    assert len(kept) == 4
    assert SOLE in kept


def test_seam_3_iteration_2_lowers_the_ceiling_and_still_reads_it():
    """Iteration 2 lowers every ceiling by one (ADR-051 §6). The corpus question does not
    change with the ceiling."""
    data = _doc(BULLETS).model_dump(mode="json")
    out, changed = condense_to_budget(data, _budget(max_bullets=4), iteration=2)
    assert changed
    kept = out["work_history"][0]["bullets"]
    assert len(kept) == 3, "iteration 2 = ceiling - 1"
    assert SOLE in kept


# ── the producer seam: the coverage demand's own report ──────────────────────────────


def test_seam_4_the_coverage_demand_reports_what_it_demanded():
    """ADR-048 cross-reference (2026-09-17). The report carries the terms the block really
    carries — after the rank gate and after the per-round bound — and it fires on every
    evaluation, including the empty one, so a caller can SEE that a round demanded
    nothing rather than having to infer it (`on_settle`'s contract)."""
    from applire.services.keyword_ledger import coverage_reviewer_prompt_fn

    seen = []
    fn = coverage_reviewer_prompt_fn(
        lambda source, draft: "BASE", _ledger(), on_demand=seen.append
    )

    absent = {"summary": "Produktionsleiter.", "skills": [], "work": []}
    prompt = fn("src", absent)
    assert len(seen) == 1 and [e["concept"] for e in seen[0]] == [CONCEPT]
    assert "VERIFIED COVERAGE" in prompt.upper() or CONCEPT in prompt

    present = {"summary": "Produktionsleiter.", "skills": [CONCEPT], "work": []}
    fn("src", present)
    assert len(seen) == 2 and seen[1] == [], (
        "an empty round must be reported, not inferred from silence"
    )


def test_seam_5_the_terminal_review_threads_both_provenances_into_the_budget():
    """The wiring `_terminal_review` owns: BOTH demand cells reach BOTH consumers through
    ONE `dataclasses.replace` on the budget, which is how the #540 cap-vs-condense seam was
    created the first time (two signatures growing a parameter)."""
    import inspect

    from applire.services import cv as cv_mod

    src = inspect.getsource(cv_mod._terminal_review)
    assert "coverage_demanded_concepts=coverage_demanded_cell" in src
    assert "demanded_concepts=demanded_cell" in src
    assert "on_demand=_record_coverage_demand" in src
    # the derivation must be the SAME one that built `claimable_concepts`, or the
    # exemption matches nothing and silently does not exist
    assert "_group_claimable_forms" in src

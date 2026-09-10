# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#666 — ADR-072 clause 4 amended: the bullet cap yields to a demanded bullet.

Founder ruling 1 (Stracciatella RC walk-through, 2026-09-05): *the cap yields to the
signal.* Two accepted decisions were setting the same number — the ADR-076 clause-5
under-claim signal asks the corrector for a bullet, and `_cap_bullets` (ADR-072
clause 4) deletes it again inside the same round, so no retry budget can help.

Delivery-tier evidence, on the captured RC production state and with ZERO provider
calls (`Documents/Runs/Nougat/build-1/w1/report.md`; script
`wt-w1-writer/tmp/replay_666.py`): the real `_compose_document` over the captured
terminal corrector's own output, with the demand provenance read from the captured
corrector prompts (round 1 = Produktionsverantwortung + ISO 9001, round 2 = Deutsch).
**Implementation fraction 2 of 4 → 3 of 4**, and the role stays at exactly its
5-bullet ceiling in both arms — the exemption did not blow the budget. Production's
own delivered number on that run was 1 of 4.

Two designs were measured and one was discarded, which is why the shipped one is
provenance and not a property:

* PROPERTY ("exempt the sole narrative carrier of any concept at REQUIRED weight"):
  19 required concepts over 8 bullets made **7 of 8** exempt against a ceiling of 5.
  That is not an amendment to the cap, it is the cap switched off.
* PROVENANCE ("exempt the earliest carrier of a concept THIS terminal review's signal
  actually raised"): 3 groups on the same document, ceiling intact.

CI pins the mechanism. Whether a model then keeps the bullet is not CI's to prove.
"""
import sys
from pathlib import Path

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.bullet_cuts import demanded_exempt_indices, rank_cuts  # noqa: E402
from applire.services.cv_budget import BudgetResult  # noqa: E402

# One role's bullets. `TAG_ONLY` is the shape the whole amendment is about: the concept
# it carries also sits in the skills list, so the pre-amendment sole-carrier tier reads
# it as "covered elsewhere" and cuts it — while the clause-5 demand that produced it
# says in its own words that a skills-list entry does not satisfy the requirement.
FIG_A = "Ausschussquote von 4,1 % auf 2,3 % gesenkt."
FIG_B = "Termintreue von 87 % auf 96 % verbessert."
FIG_C = "Produktionsbudget von 6 Mio. EUR verantwortet."
DEMANDED_BULLET = "Seit 2017 jährliche ISO-9001-Audits als Bereichsverantwortlicher begleitet."
FILLER = "Allgemeine Aufgaben in der Fertigung wahrgenommen."

BULLETS = [FIG_A, FIG_B, FIG_C, DEMANDED_BULLET, FILLER]
TIERS = [(True, -0), (True, -1), (True, -2), (False, -3), (False, -4)]
GROUPS = (("ISO 9001",), ("Ausschussquote",), ("Termintreue",), ("Produktionsbudget",))
DEMANDED_GROUPS = (("ISO 9001",),)
#: The skills list and the summary — everything the cap cannot cut. It NAMES the
#: demanded concept, which is exactly why the old ranking cut the bullet.
DOC_EXTERNAL = "skills: ISO 9001, Lean Management. summary: Produktionsleiter."
#: The same slice restricted to work-entry and nested-project bullets: no ISO 9001.
NARRATIVE_EXTERNAL = "Als Schichtleiter 14 Mitarbeitende geführt."


def _cut_texts(**kw):
    return [c.text for c in rank_cuts(BULLETS, TIERS, keep=4, concept_groups=GROUPS, **kw)]


# ---------------------------------------------------------------------------
# The exemption itself
# ---------------------------------------------------------------------------


def test_without_provenance_the_cap_deletes_the_demanded_bullet():
    """The pre-amendment behaviour, asserted rather than assumed: with no demand
    recorded, the figure-less demanded bullet is cut before the filler, because the
    skills list makes its concept read as covered."""
    assert _cut_texts(external_text=DOC_EXTERNAL) == [FILLER]
    # tighter ceiling: the demanded bullet goes, and nothing protects it
    cuts = [c.text for c in rank_cuts(
        BULLETS, TIERS, keep=3, concept_groups=GROUPS, external_text=DOC_EXTERNAL,
    )]
    assert DEMANDED_BULLET in cuts


def test_the_demanded_bullet_survives_a_ceiling_that_would_have_cut_it():
    """The amendment. Same inputs, same ceiling, plus the provenance."""
    cuts = [c.text for c in rank_cuts(
        BULLETS, TIERS, keep=3, concept_groups=GROUPS, external_text=DOC_EXTERNAL,
        demanded_groups=DEMANDED_GROUPS, narrative_external_text=NARRATIVE_EXTERNAL,
    )]
    assert DEMANDED_BULLET not in cuts
    assert FILLER in cuts, "the cap takes a NON-demanded bullet instead"
    assert len(cuts) == 2, "the ceiling still binds — the cap cuts, it just cuts elsewhere"


def test_a_skills_tag_does_not_count_as_coverage_for_a_demanded_concept():
    """The half that makes the exemption bite. `external_text` names ISO 9001 (the
    skills list); `narrative_external_text` does not. If the exemption read the
    document-wide set it would find the concept covered and protect nothing —
    SF-WRITE.30's cause ("a bare tag ends the demand") one pass later."""
    exempt = demanded_exempt_indices(
        BULLETS, concept_groups=GROUPS, demanded_groups=DEMANDED_GROUPS,
        narrative_external_text=NARRATIVE_EXTERNAL,
    )
    assert exempt == {BULLETS.index(DEMANDED_BULLET)}
    covered_in_narrative = demanded_exempt_indices(
        BULLETS, concept_groups=GROUPS, demanded_groups=DEMANDED_GROUPS,
        narrative_external_text="Andere Rolle: ISO 9001 Audits geführt.",
    )
    assert covered_in_narrative == set(), (
        "another ROLE's bullet already carries the concept — the demand will not "
        "re-fire, so nothing needs protecting"
    )


def test_only_the_earliest_carrier_is_exempt():
    """One bullet per concept is what the demand asks for; the writer's own order is
    its relevance judgement (this module's standing tie-break). Protecting every
    carrier would let one concept occupy a whole role's budget."""
    texts = ["frueh: ISO 9001 Audit", "mitte: nichts", "spaet: ISO 9001 erneut"]
    exempt = demanded_exempt_indices(
        texts, concept_groups=(("ISO 9001",),), demanded_groups=(("ISO 9001",),),
        narrative_external_text="",
    )
    assert exempt == {0}


def test_the_exemption_is_inert_without_a_recorded_demand():
    """Every non-terminal path (the drafting loop, the section-editor re-audit, every
    caller that never wires the signal) must behave EXACTLY as it did before #666."""
    for demanded in ((), None):
        kwargs = {} if demanded is None else {"demanded_groups": demanded}
        assert _cut_texts(external_text=DOC_EXTERNAL, **kwargs) == [FILLER]


def test_pinned_and_demanded_occupy_one_slot_not_two():
    """A bullet that is both a fact-pin carrier (ADR-077 clause 4) and the answer to a
    demand must not be double-counted against the ceiling — the two partitions are
    unioned, not summed."""
    idx = BULLETS.index(DEMANDED_BULLET)
    cuts = rank_cuts(
        BULLETS, TIERS, keep=4, concept_groups=GROUPS, external_text=DOC_EXTERNAL,
        pinned={idx}, demanded_groups=DEMANDED_GROUPS,
        narrative_external_text=NARRATIVE_EXTERNAL,
    )
    assert len(cuts) == 1, "one protected bullet, one slot"


def test_a_ceiling_tighter_than_the_demand_set_yields_and_says_so(caplog):
    """ADR-077 clause 4's partition precedent, which the ruling names: the ceiling
    yields rather than silently defeating the exemption — and the WARNING names BOTH
    producers, so the conflict is diagnosable from the log alone (ADR-072 clause 4's
    own standard)."""
    import logging

    caplog.set_level(logging.WARNING, logger="applire.services.bullet_cuts")
    cuts = rank_cuts(
        BULLETS, TIERS, keep=1, concept_groups=GROUPS,
        external_text=DOC_EXTERNAL,
        demanded_groups=(("ISO 9001",), ("Ausschussquote",), ("Termintreue",)),
        narrative_external_text=NARRATIVE_EXTERNAL,
    )
    survivors = set(BULLETS) - {c.text for c in cuts}
    assert {FIG_A, FIG_B, DEMANDED_BULLET} <= survivors
    line = "\n".join(r.getMessage() for r in caplog.records)
    assert "BUDGET_VS_SIGNAL_CONFLICT" in line
    assert "cv_budget.RoleBudget" in line and "verified_narrative_underclaim" in line


# ---------------------------------------------------------------------------
# The provenance actually reaches the two passes that cut
# ---------------------------------------------------------------------------


def test_the_budget_carries_the_demand_and_compute_never_populates_it():
    """`demanded_concepts` is provenance recorded by the terminal loop, never a
    property computed from the ledger. The measured reason is in the module docstring:
    the property form exempted 7 of 8 bullets."""
    from applire.services.cv_budget import compute_bullet_budgets

    ledger = [
        {"concept": "ISO 9001", "surface_forms": ["ISO 9001"], "claimable": True,
         "status": "direct", "fit_weight": 1.0, "evidence": "x"},
    ]
    budget = compute_bullet_budgets(
        [{"id": "w1", "company": "A", "role": "R", "start_date": "2022-01",
          "is_current": True, "responsibilities": ["ISO 9001 Audits"]}],
        keyword_ledger=ledger, target_pages=2,
    )
    assert budget.claimable_concepts == (("ISO 9001",),)
    assert budget.demanded_concepts == (), (
        "compute_bullet_budgets must not derive the exemption — see the docstring"
    )
    assert isinstance(budget, BudgetResult)


def test_condense_to_budget_honours_the_same_exemption():
    """#377's scope note, applied again: the epic named only `cv._cap_bullets`, and a
    fix that stops there lets the page-overrun path re-delete the protected bullet two
    passes later (the #540 cap-vs-condense seam)."""
    import dataclasses

    from applire.services.cv_budget import compute_bullet_budgets, condense_to_budget

    ledger = [
        {"concept": "ISO 9001", "surface_forms": ["ISO 9001"], "claimable": True,
         "status": "direct", "fit_weight": 1.0, "evidence": "x"},
    ]
    work = [{"id": "w1", "company": "A", "role": "R", "start_date": "2022-01",
             "is_current": True,
             "responsibilities": [FIG_A, FIG_B, FIG_C, DEMANDED_BULLET, FILLER]}]
    budgets = compute_bullet_budgets(work, keyword_ledger=ledger, target_pages=2)
    doc = {
        "work_history": [{"id": "w1", "company": "A", "role": "R",
                          "bullets": BULLETS, "projects": []}],
        "skills": ["ISO 9001"],
        "summary": "Produktionsleiter.",
    }
    tight = dataclasses.replace(
        budgets,
        roles={"w1": dataclasses.replace(budgets.roles["w1"], max_bullets=3)},
    )
    plain, _ = condense_to_budget(doc, tight, iteration=1)
    assert DEMANDED_BULLET not in plain["work_history"][0]["bullets"]

    with_demand = dataclasses.replace(tight, demanded_concepts=(("ISO 9001",),))
    protected, _ = condense_to_budget(doc, with_demand, iteration=1)
    assert DEMANDED_BULLET in protected["work_history"][0]["bullets"]
    assert len(protected["work_history"][0]["bullets"]) == 3


def test_the_signal_wrapper_reports_the_concepts_it_demanded():
    """The recorder is a REPORT, exactly like `review_and_refine`'s `on_settle`: it
    cannot change which issues are raised, and it fires on every evaluation."""
    from applire.services.cv_gap_hints import underclaim_signal_issues_fn

    ledger = [
        {"concept": "ISO 9001", "surface_forms": ["ISO 9001"], "claimable": True,
         "status": "direct", "fit_weight": 1.0, "evidence": "Audits seit 2017"},
    ]
    seen: list[tuple[str, ...]] = []
    fn = underclaim_signal_issues_fn(
        ledger, on_demand=lambda cs: seen.append(tuple(c.concept for c in cs))
    )
    issues = fn({"work": [{"id": "w1", "bullets": ["Nichts einschlägiges."]}]})
    assert [i.text for i in issues] and "ISO 9001" in issues[0].text
    assert seen == [("ISO 9001",)]

    # a draft that already carries it narratively: no demand, and the report says so
    fn({"work": [{"id": "w1", "bullets": ["ISO 9001 Audits begleitet."]}]})
    assert seen[-1] == ()


def test_the_signal_wrapper_without_a_recorder_is_unchanged():
    """`on_demand` defaults to None; the wrapper's issue output is byte-identical to
    what `underclaim_signal_issues` produces."""
    from applire.services.cv_gap_hints import (
        underclaim_signal_issues,
        underclaim_signal_issues_fn,
    )

    ledger = [
        {"concept": "ISO 9001", "surface_forms": ["ISO 9001"], "claimable": True,
         "status": "direct", "fit_weight": 1.0, "evidence": "Audits seit 2017"},
    ]
    draft = {"work": [{"id": "w1", "bullets": ["Nichts einschlägiges."]}]}
    assert [i.text for i in underclaim_signal_issues_fn(ledger)(draft)] == [
        i.text for i in underclaim_signal_issues(draft, ledger)
    ]


def test_the_demand_universe_is_the_signals_own(monkeypatch):
    """ADR-066 / the standing rule that a limit is reconciled with its PRODUCER: the
    cap's notion of "may this concept be demanded" is the signal's own selector,
    re-exported, not a second copy of the filter."""
    from applire.services import cv_gap_hints
    from applire.services.keyword_ledger import underclaim_candidate_entries

    ledger = [
        {"concept": "A", "surface_forms": ["A"], "claimable": True, "status": "direct",
         "fit_weight": 1.0, "evidence": "x"},
        {"concept": "B", "surface_forms": ["B"], "claimable": False, "status": "gap",
         "fit_weight": 1.0, "evidence": ""},
        {"concept": "C", "surface_forms": ["C"], "claimable": True, "status": "partial",
         "fit_weight": 1.0, "evidence": "x", "adjacent_evidence": "arc42"},
        {"concept": "D ~120 MA", "surface_forms": ["D"], "claimable": True,
         "status": "direct", "fit_weight": 1.0, "evidence": "x", "bar": "120"},
    ]
    assert [e["concept"] for e in underclaim_candidate_entries(ledger)] == ["A"]
    assert cv_gap_hints._underclaim_candidates(ledger) == underclaim_candidate_entries(ledger)


@pytest.mark.parametrize("groups", [(), (("Nicht vorhanden",),)])
def test_a_demand_nothing_carries_protects_nothing(groups):
    assert demanded_exempt_indices(
        BULLETS, concept_groups=GROUPS, demanded_groups=groups,
        narrative_external_text=NARRATIVE_EXTERNAL,
    ) == set()


# ---------------------------------------------------------------------------
# The demand's own scope: a structured section is DELIVERY, a tag is not
# ---------------------------------------------------------------------------


def _prose(*bullets: str) -> dict:
    return {"work": [{"id": "w1", "bullets": list(bullets)}], "skills": ["Deutsch"]}


_LANG_LEDGER = [
    {"concept": "Deutsch", "surface_forms": ["Deutsch"], "claimable": True,
     "status": "direct", "fit_weight": 1.0, "evidence": "Deutsch als Muttersprache."},
]


def test_a_concept_the_languages_section_delivers_is_not_under_claimed():
    """Founder ruling on W1-3 (2026-09-08): measured on the captured RC state, honouring
    the `Deutsch` demand under the new cap exemption bought the bullet "Deutsch als
    Muttersprache." at the price of the LTIF 8,2 -> 3,1 safety bullet AND the 6 Mio. EUR
    budget bullet — on a document whose LANGUAGES section already stated it. The demand,
    not the cap, was wrong on that shape."""
    from applire.services.cv_gap_hints import verified_narrative_underclaim

    draft = _prose("Ausschussquote von 4,1 % auf 2,3 % gesenkt.")
    assert [c.concept for c in verified_narrative_underclaim(draft, _LANG_LEDGER)] == ["Deutsch"]
    composed = {"languages": [{"name": "Deutsch", "level": "Muttersprache"}]}
    assert verified_narrative_underclaim(
        draft, _LANG_LEDGER, structured_document=composed
    ) == []


def test_a_bilingual_language_name_is_still_recognised_as_delivered():
    """Adversarial pass (Nougat build-1, `wt-adv-writer`) — a DACH candidate's vault
    carries the LANGUAGES section verbatim in ITS OWN language (ADR-067 clause 3), while
    the clause-5 ledger's surface forms come from the JD's own language. An English-
    language posting requiring "English" against a vault section stating "Englisch" is
    the realistic instance: before the fix, `_structured_norm` never found "english" as a
    substring of "englisch", so W1-3's own suppression did not fire and the concept stayed
    demanded — reproducing the exact cost W1-3 exists to prevent (a demand bought at the
    price of another bullet) for a fact the document already carries. Fixed by reusing
    `cv._LANGUAGE_NAME_CANON`, the already-established ADR-062 clause-1 fact table for
    exactly this ("a finite lookup, not a judgement")."""
    from applire.services.cv_gap_hints import verified_narrative_underclaim

    ledger = [
        {"concept": "English", "surface_forms": ["English"], "claimable": True,
         "status": "direct", "fit_weight": 1.0, "evidence": "Englisch: C1"},
    ]
    draft = _prose("Ausschussquote von 4,1 % auf 2,3 % gesenkt.")
    assert [c.concept for c in verified_narrative_underclaim(draft, ledger)] == ["English"]

    composed_german_name = {"languages": [{"language": "Englisch", "level": "C1"}]}
    assert verified_narrative_underclaim(
        draft, ledger, structured_document=composed_german_name
    ) == [], "the vault's OWN-language name for the concept must still count as delivered"

    # The direct-match case must keep working too — the fix only ADDS a translated
    # reading, it must never remove the literal one.
    composed_english_name = {"languages": [{"language": "English", "level": "C1"}]}
    assert verified_narrative_underclaim(
        draft, ledger, structured_document=composed_english_name
    ) == []


def test_the_skills_list_is_deliberately_not_a_structured_section():
    """The signal's founding rule — "a skills-list entry does NOT satisfy this" — is
    exactly what must NOT be relaxed here. The draft above already carries `Deutsch` in
    `skills`, and the demand fires anyway."""
    from applire.services.cv_gap_hints import _STRUCTURED_SECTIONS, verified_narrative_underclaim

    assert "skills" not in _STRUCTURED_SECTIONS
    assert "summary" not in _STRUCTURED_SECTIONS
    composed = {"skills": ["Deutsch"], "summary": "Deutsch als Muttersprache."}
    assert [c.concept for c in verified_narrative_underclaim(
        _prose("Ausschussquote gesenkt."), _LANG_LEDGER, structured_document=composed
    )] == ["Deutsch"]


def test_certifications_and_education_count_the_same_way():
    from applire.services.cv_gap_hints import verified_narrative_underclaim

    ledger = [
        {"concept": "ISO 9001 Lead Auditor", "surface_forms": ["ISO 9001 Lead Auditor"],
         "claimable": True, "status": "direct", "fit_weight": 1.0, "evidence": "x"},
    ]
    draft = {"work": [{"id": "w1", "bullets": ["Nichts einschlägiges."]}], "skills": []}
    assert [c.concept for c in verified_narrative_underclaim(draft, ledger)]
    for section in ("certifications", "education"):
        composed = {section: [{"name": "ISO 9001 Lead Auditor", "issuer": "TÜV"}]}
        assert verified_narrative_underclaim(
            draft, ledger, structured_document=composed
        ) == [], section


def test_without_a_composed_document_the_demand_is_unchanged():
    """Every non-terminal caller passes None — the prose shape has no structured
    sections to read, and the demand must behave exactly as before."""
    from applire.services.cv_gap_hints import verified_narrative_underclaim

    draft = _prose("Ausschussquote gesenkt.")
    assert verified_narrative_underclaim(draft, _LANG_LEDGER) == \
        verified_narrative_underclaim(draft, _LANG_LEDGER, structured_document=None)

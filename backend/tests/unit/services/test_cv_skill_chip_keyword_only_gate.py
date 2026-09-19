# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""RULING E-5b (blind Kaile probe, 2026-09-19) — the skills-list gap guard may
not re-add a name whose only authority is a JD-KEYWORD-ONLY ledger row.

Ground truth (dev DB ``generated_cvs`` 1b581b82…, synthetic persona Katrin
Hoffmann): the delivered skills section ended with "Gruppe", "Vertrieb",
"Produktion" — three bare nouns from the job ad's prose, none of them a vault
skill. A blind hiring manager flagged the section as "ATS-Keyword-Stuffing
ohne Beleg".

**The control that did not fire.** ``_drop_ungrounded_jd_echo_skills`` (#250)
already embodies the decision that a JD echo with no vault tie does not reach
the page, and all three chips ARE JD echoes with no vault tie. It ran — and
then ``_restore_narrative_named_skills``, LAST in the skills pipeline, put
all three back: they are claimable ledger surface forms, they occur in the
tailored narrative, and the Oracle's grounding accepted them. The chip order
of all three probe CVs is the append order of this pass.

The fix narrows this pass's candidate pool by the ledger row's own JD
provenance (``keyword_ledger.is_jd_keyword_only``): a row carrying only
``sources: ["keyword"]`` records a noun scraped from the ad, not something
the ad asks for. The pass only ever ADDS names, so narrowing it can never
remove a chip the writer chose; and the vault-skill half of the pool is
untouched, so a keyword-only concept that is ALSO a vault skill is still
restored.
"""
from __future__ import annotations

from applire.schemas.cv import TailoredCVData
from applire.services.cv import _restore_narrative_named_skills

PROFILE = {
    "personal_info": {"name": "Katrin Hoffmann"},
    "skills": [
        {"name": "Werkscontrolling", "status": "confirmed"},
        {"name": "Konsolidierung", "status": "confirmed"},
    ],
    "work_experience": [
        {
            "id": "w-schwarzwald",
            "company": "Schwarzwald Präzision GmbH",
            "role": "Senior Controllerin",
            "responsibilities": [
                "Verantwortung für Monatsabschluss und Reporting nach HGB.",
                "Acht Jahre Werkscontrolling an zwei Produktionsstandorten.",
                "Einführung von LucaNet in einer Gruppe aus GmbH und "
                "Schweizer Vertriebsgesellschaft begleitet.",
            ],
        }
    ],
}

# The tailored document repeats all of it in its bullets — every candidate
# below is literally narrated, which is what made the old guard add them.
TAILORED = {
    "contact": {"name": "Katrin Hoffmann"},
    "skills": ["Werkscontrolling", "Konsolidierung"],
    "work_history": [
        {
            "company": "Schwarzwald Präzision GmbH",
            "role": "Senior Controllerin",
            "bullets": [
                "Verantwortung für Monatsabschluss und Reporting nach HGB.",
                "Werkscontrolling für zwei Produktionsstandorte mit "
                "Herstellkosten.",
                "Bei der LucaNet-Einführung in einer Gruppe aus GmbH und "
                "Schweizer Vertriebsgesellschaft mitgearbeitet.",
            ],
        }
    ],
}

# Two REQUIRED rows (stated expectations of the posting) and three
# keyword-only rows (nouns lifted from its prose) — the exact mix the probe's
# job carried.
LEDGER = [
    {
        "concept": "Monatsabschluss-Reporting",
        "status": "direct",
        "claimable": True,
        "sources": ["required"],
        "fit_weight": 1.0,
        "evidence": "Verantwortung für Monatsabschluss und Reporting (HGB).",
        "surface_forms": ["Monatsabschluss-Reporting", "Monatsabschluss", "Reporting"],
    },
    {
        "concept": "Produktionscontrolling",
        "status": "direct",
        "claimable": True,
        "sources": ["required"],
        "fit_weight": 1.0,
        "evidence": "Acht Jahre Werkscontrolling an zwei Produktionsstandorten.",
        "surface_forms": ["Produktionscontrolling", "Werkscontrolling"],
    },
    {
        "concept": "Unternehmensgruppe",
        "status": "direct",
        "claimable": True,
        "sources": ["keyword"],
        "fit_weight": 0.0,
        "evidence": "Einführung von LucaNet in einer Gruppe aus GmbH und "
        "Schweizer Vertriebsgesellschaft.",
        "surface_forms": ["Unternehmensgruppe", "Gruppe", "Konzern"],
    },
    {
        "concept": "Vertrieb",
        "status": "direct",
        "claimable": True,
        "sources": ["keyword"],
        "fit_weight": 0.0,
        "evidence": "Vertriebsreporting in Excel aufgebaut.",
        "surface_forms": ["Vertrieb", "Vertriebsgesellschaft", "Sales"],
    },
    {
        "concept": "Produktion",
        "status": "direct",
        "claimable": True,
        "sources": ["keyword"],
        "fit_weight": 0.0,
        "evidence": "Werkscontrolling für zwei Produktionsstandorte.",
        "surface_forms": ["Produktion", "Produktionsstandorte"],
    },
]


def _restored() -> list[str]:
    out = _restore_narrative_named_skills(
        TailoredCVData.model_validate(TAILORED), PROFILE, LEDGER
    )
    return list(out.skills or [])


def test_jd_keyword_only_nouns_are_not_re_added_to_the_page():
    skills = _restored()
    for noise in ("Gruppe", "Unternehmensgruppe", "Konzern", "Vertrieb", "Produktion"):
        assert noise not in skills, f"{noise!r} must not reach the delivered chips"


def test_the_gruppe_shape_is_the_mutation_guard():
    """THE guard. "Gruppe" is whole-token present in the vault AND in the
    tailored narrative — the vault genuinely says "in einer Gruppe aus GmbH
    …" — so no amount of whole-token tightening can hold it back. Only the
    ledger row's JD provenance can. Invert the ``exclude_keyword_only``
    argument in ``_restore_narrative_named_skills`` and THIS test fails while
    the keep-tests below stay green.
    """
    from applire.services.ats_audit import surface_present_whole_token
    from applire.services.keyword_ledger import profile_literal_corpus
    from applire.services.oracle.matchers.grounding import ground_skill_claim
    from applire.services.oracle.matchers.vault import build_vault_index

    assert surface_present_whole_token("Gruppe", profile_literal_corpus(PROFILE))
    assert ground_skill_claim("Gruppe", build_vault_index(PROFILE)) is not None
    assert "Gruppe" not in _restored()


def test_required_row_competences_are_still_restored():
    """Over-drop guard: the bullet-evidenced competences the same document
    legitimately earns keep being restored."""
    skills = _restored()
    assert "Monatsabschluss" in skills or "Monatsabschluss-Reporting" in skills
    assert len([s for s in skills if s in ("Monatsabschluss", "Reporting")]) <= 1


def test_writer_chosen_chips_are_never_removed():
    """The pass only ADDS. Narrowing its pool cannot take a chip off a page."""
    skills = _restored()
    assert skills[:2] == ["Werkscontrolling", "Konsolidierung"]


def test_a_keyword_only_concept_that_is_also_a_vault_skill_still_lands():
    """Containment: the vault-skill half of the candidate pool is untouched,
    so a JD-keyword-only concept the candidate genuinely holds as a vault
    skill is still restored — through the vault, not through the ledger."""
    profile = {
        **PROFILE,
        "skills": PROFILE["skills"] + [{"name": "Vertriebsgesellschaft", "status": "confirmed"}],
    }
    out = _restore_narrative_named_skills(
        TailoredCVData.model_validate(TAILORED), profile, LEDGER
    )
    assert "Vertriebsgesellschaft" in (out.skills or [])

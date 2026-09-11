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

"""SF-GAP.12 (#231/ADR-059 5th seam, Nougat build-2 delivery-run 2026-09-11) —
a ledger REBUILD can silently lose the surface form that releases a
containment-only denial.

**The finding.** SF-GAP.10/E-4 (2026-09-11) fixed the containment-release
floor at all four persist/read seams that call ``containment_release_form``
directly (``_enforce_denial_stance``, ``upgrade_ledger_for_concepts``,
``reevaluate_gap_ledger_against_vault``, ``_claimable_backing_violation``) —
confirmed by the WP-P2 adversarial pass (``Documents/Runs/Nougat/build-2/
adversarial/vault.md``, Surface 1). The 2026-09-11 delivery run
(``2026-09-11-operations-marcus-de.md``, outcome 3) still reproduced the OLD
flip: immediately after interview completion, ``POST /gaps/refresh`` released
``Produktion`` correctly (surface_forms ``["Produktion", "Produktionsleiter",
"Fertigung"]`` — the vault's own ``work_experience[].role`` "Produktionsleiter"
independently affirms it outside the denied compound "direkte Produktion für
Lebensmittelkunden"). A LATER recompute in the SAME run — a brand new
``analyze_gaps()`` LLM classification, triggered by
``flow.advance("interview", ...)`` — reclassified the identical concept with
DIFFERENT surface_forms (``["Produktion", "Fertigungsbereiche", "Fertigung"]``,
``20b-gaps-final.json``): the one form that was independently attested in the
vault ("Produktionsleiter") is simply absent from this classification call's
own output, so the SAME (correct) containment-release logic, given THIS
entry's forms, legitimately finds nothing to release and forces the concept
back to ``category_c``/``critical_gaps``.

**The fifth path, pinned.** Not a missing seam call — the call path is
``services/gap.py::_run_analysis`` -> ``build_keyword_ledger`` (its internal
``_enforce_denial_stance`` call, keyword_ledger.py ~3429), and that call
correctly probes ``containment_release_form`` at the entry's full width. The
gap is that ``build_keyword_ledger`` has no memory of a PRIOR ledger build for
the same (job, profile-lineage) concept, so a fresh classification's own
surface_forms silently REPLACE — never merge with — whatever the previous
build already established. The fix stays on the matching-rule side (a
deterministic set union over two already-computed lists), never a prompt
change: ``build_keyword_ledger`` gains an optional ``previous_ledger`` kwarg
and unions in any surface form a matching-concept row of it already carried,
BEFORE the denial floor runs.

These two cases are reconstructed directly from the delivery run's own
persisted artefacts (denied_concepts verbatim from ``28-profile-after-
interview.json``, the vault's attested ``Produktionsleiter`` role, and both
the correct (``20-gaps-refresh.json``) and broken (``20b-gaps-final.json``)
ledger rows' own surface_forms) — not invented shapes.
"""

from applire.services.keyword_ledger import build_keyword_ledger

# ── real data, reconstructed from the 2026-09-11 delivery-run artefacts ────

_DENIAL_STATEMENT = (
    "Nein, mit IFS oder BRC habe ich keine Erfahrung – das ist eine ehrliche "
    "Lücke, und ich habe nie direkt für Lebensmittelkunden produziert."
)

_DENIED_CONCEPTS = [
    {
        "concept": "direkte Produktion für Lebensmittelkunden",
        "statement": _DENIAL_STATEMENT,
        "source": "interview",
        "date": "2026-09-11",
        "denial_level": "direct",
    },
    {
        "concept": "IFS",
        "statement": _DENIAL_STATEMENT,
        "source": "interview",
        "date": "2026-09-11",
        "denial_level": "direct",
    },
    {
        "concept": "BRC",
        "statement": _DENIAL_STATEMENT,
        "source": "interview",
        "date": "2026-09-11",
        "denial_level": "direct",
    },
]


def _profile_json() -> dict:
    """The vault's own attestation: ``Produktionsleiter`` is a real
    ``work_experience[].role`` — the sole affirmation ``denial_release_corpus``
    reads for this scenario (real profile shape, trimmed to what the corpus
    consumes)."""
    return {
        "work_experience": [
            {
                "company": "Weberit Kunststofftechnik GmbH",
                "role": "Produktionsleiter",
                "start_date": "2017-01",
                "technologies": ["SAP PP", "MES"],
            }
        ],
        "skills": [{"name": "Lean Management", "category": "technical"}],
        "certifications": [],
        "languages": [],
        "metadata": {"denied_concepts": _DENIED_CONCEPTS},
    }


def _classification(surface_forms: list[str]) -> dict:
    return {
        "concept": "Produktion",
        "status": "direct",
        "surface_forms": surface_forms,
        "evidence": "14 years of experience in discrete manufacturing, "
        "including the current role as Produktionsleiter.",
    }


# The FIRST build's own classification — matches 20-gaps-refresh.json's row
# verbatim. Releases on its own: "Produktionsleiter" is one of ITS OWN forms.
_GOOD_FORMS = ["Produktion", "Produktionsleiter", "Fertigung"]

# The SECOND build's classification — matches 20b-gaps-final.json's row
# verbatim. Never mentions "Produktionsleiter" at all.
_BAD_FORMS = ["Produktion", "Fertigungsbereiche", "Fertigung"]


def _by_concept(ledger):
    return {e["concept"]: e for e in ledger}


def _build(surface_forms: list[str], *, previous_ledger=None) -> dict:
    ledger = build_keyword_ledger(
        classifications=[_classification(surface_forms)],
        required_skills=["Produktion"],
        nice_to_have_skills=[],
        keywords=[],
        denied_concepts=_DENIED_CONCEPTS,
        profile_json=_profile_json(),
        previous_ledger=previous_ledger,
    )
    return _by_concept(ledger)["Produktion"]


# ── sanity: the first build already behaves as SF-GAP.10 documents ─────────


def test_the_first_build_releases_on_its_own_surface_forms():
    """Baseline, no history to carry forward — matches 20-gaps-refresh.json."""
    entry = _build(_GOOD_FORMS)
    assert entry["claimable"] is True
    assert entry["status"] == "direct"


def test_a_rebuild_with_no_history_still_floors_when_its_own_forms_miss():
    """Baseline RED shape, unrelated to the fix: with nothing to carry forward
    (first-ever build, or a caller that never passes ``previous_ledger``), a
    classification call whose own surface_forms miss the attested compound
    still floors — matches 20b-gaps-final.json in isolation. The fix targets
    REBUILD resilience, not clairvoyance on a build with no prior state."""
    entry = _build(_BAD_FORMS, previous_ledger=None)
    assert entry["claimable"] is False
    assert entry["status"] == "gap"


# ── the fix: a rebuild carries forward a prior build's releasing form ──────


def test_a_rebuild_carries_forward_the_prior_builds_releasing_surface_form():
    """THE regression pin, red before the fix. The SECOND build's own
    classification (``_BAD_FORMS``) reproduces the delivery run's flip when
    it has no memory of the first build. Handed the FIRST build's ledger as
    ``previous_ledger`` (exactly what ``services/gap.py::_run_analysis``
    already has on hand as ``previous.keyword_ledger``), the union must
    recover "Produktionsleiter" and the concept must stay claimable —
    'Produktion' may not become a critical gap on the SAME denied_concepts,
    the SAME vault, and a classification call that simply forgot one alias.
    """
    previous_ledger = [
        {
            "concept": "Produktion",
            "surface_forms": _GOOD_FORMS,
            "sources": ["required"],
            "fit_weight": 1.0,
            "status": "direct",
            "evidence": "14 years of experience in discrete manufacturing, "
            "including the current role as Produktionsleiter.",
            "claimable": True,
        }
    ]

    entry = _build(_BAD_FORMS, previous_ledger=previous_ledger)

    assert entry["claimable"] is True, (
        "a ledger rebuild must not silently drop a surface form an earlier "
        "build for the SAME concept already established"
    )
    assert entry["status"] == "direct"
    assert "Produktionsleiter" in entry["surface_forms"]
    # The classifier's own (fresh, still-valid) forms are kept too — a union,
    # never a replacement.
    assert "Fertigungsbereiche" in entry["surface_forms"]


def test_carry_forward_is_scoped_to_the_same_concept():
    """A previous ledger entry for a DIFFERENT concept must never leak its
    surface forms onto this one — the union key is the concept, not "any
    prior row"."""
    previous_ledger = [
        {
            "concept": "Investitionsplanung",
            "surface_forms": ["Investitionsplanung", "Produktionsleiter"],
            "sources": ["required"],
            "fit_weight": 1.0,
            "status": "direct",
            "evidence": "unrelated",
            "claimable": True,
        }
    ]
    entry = _build(_BAD_FORMS, previous_ledger=previous_ledger)
    assert entry["claimable"] is False
    assert "Produktionsleiter" not in entry["surface_forms"]


def test_carry_forward_does_not_manufacture_a_release_the_vault_never_attested():
    """The carried-forward form is still subject to the SAME corpus check —
    carrying forward a form that is not (or no longer) independently attested
    changes nothing. Never a bypass of the release predicate, only a wider
    probe list fed into it."""
    previous_ledger = [
        {
            "concept": "Produktion",
            "surface_forms": ["Produktion", "Fabrikleitung", "Fertigung"],
            "sources": ["required"],
            "fit_weight": 1.0,
            "status": "direct",
            "evidence": "an earlier, differently-worded classification",
            "claimable": True,
        }
    ]
    entry = _build(_BAD_FORMS, previous_ledger=previous_ledger)
    assert entry["claimable"] is False
    assert "Fabrikleitung" in entry["surface_forms"]  # carried, but unattested

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

"""#260 — pre-generation keyword-liability check (deterministic, ADR-058 freeze).

Both run-4 blind hiring reviewers discounted the application because JD
hard-requirement keywords (RAG, evaluation) appeared with zero narrative
substantiation ("RAG appears once, as a skills-list keyword"). A keyword can
be `claimable` (a bare skills-list/adjacency match) while the vault carries
NO narrative (bullet/achievement/signature-story) for it anywhere — that
combination is a LIABILITY: it will be echoed by the generator (claimable)
but reads as unsubstantiated to a human reviewer.

This is the inverse of #250 (which drops a JD-echo skill TAG that has no
vault tie at all). Here the concept genuinely IS vault-tied (it clears the
ledger's own claimable classification, possibly literally) — the gap is
specifically narrative depth, not vault presence.
"""

from applire.services.keyword_ledger import build_keyword_ledger, keyword_liabilities


def _cls(concept, status, surface_forms=None, evidence=""):
    item = {"concept": concept, "status": status, "evidence": evidence}
    if surface_forms is not None:
        item["surface_forms"] = surface_forms
    return item


def _by_concept(ledger):
    return {e["concept"]: e for e in ledger}


# ---------------------------------------------------------------------------
# narrative_backed annotation
# ---------------------------------------------------------------------------


def test_bare_skill_entry_with_no_narrative_anywhere_is_not_narrative_backed():
    """RAG classified direct off the skills list alone; nothing in
    work_experience/projects/signature_stories mentions it — the exact
    run-4 shape ("RAG appears once, as a skills-list keyword")."""
    profile_json = {
        "skills": [{"name": "RAG"}],
        "work_experience": [
            {
                "role": "ML Engineer",
                "responsibilities": ["Built backend services."],
                "achievements": ["Shipped a new onboarding flow."],
            }
        ],
    }
    ledger = build_keyword_ledger(
        classifications=[_cls("RAG", "direct", ["RAG"], evidence="listed under Skills")],
        required_skills=["RAG"],
        nice_to_have_skills=[],
        keywords=[],
        profile_json=profile_json,
    )
    rag = _by_concept(ledger)["RAG"]
    assert rag["claimable"] is True
    assert rag["narrative_backed"] is False


def test_concept_covered_by_a_signature_story_is_narrative_backed():
    """Same concept, but a signature story's mechanism/outcome mentions it —
    must NOT be flagged."""
    profile_json = {
        "skills": [{"name": "RAG"}],
        "signature_stories": [
            {
                "title": "Cut review time with LLM classification",
                "challenge": "Manual document triage was slow.",
                "mechanism": "Built a RAG-backed classifier over the support corpus.",
                "outcome": "Review rounds dropped by 30%.",
            }
        ],
    }
    ledger = build_keyword_ledger(
        classifications=[_cls("RAG", "direct", ["RAG"], evidence="listed under Skills")],
        required_skills=["RAG"],
        nice_to_have_skills=[],
        keywords=[],
        profile_json=profile_json,
    )
    rag = _by_concept(ledger)["RAG"]
    assert rag["narrative_backed"] is True


def test_concept_covered_by_a_work_achievement_is_narrative_backed():
    profile_json = {
        "skills": [{"name": "RAG"}],
        "work_experience": [
            {
                "role": "ML Engineer",
                "achievements": ["Deployed a RAG pipeline that cut lookup latency by half."],
            }
        ],
    }
    ledger = build_keyword_ledger(
        classifications=[_cls("RAG", "direct", ["RAG"], evidence="listed under Skills")],
        required_skills=["RAG"],
        nice_to_have_skills=[],
        keywords=[],
        profile_json=profile_json,
    )
    rag = _by_concept(ledger)["RAG"]
    assert rag["narrative_backed"] is True


def test_concept_covered_by_a_project_responsibility_is_narrative_backed():
    profile_json = {
        "skills": [{"name": "Evaluation"}],
        "projects": [
            {
                "name": "Internal search",
                "responsibilities": ["Ran offline evaluation harnesses against golden sets."],
            }
        ],
    }
    ledger = build_keyword_ledger(
        classifications=[_cls("Evaluation", "direct", ["Evaluation"], evidence="skills list")],
        required_skills=["Evaluation"],
        nice_to_have_skills=[],
        keywords=[],
        profile_json=profile_json,
    )
    e = _by_concept(ledger)["Evaluation"]
    assert e["narrative_backed"] is True


def test_no_profile_json_defaults_narrative_backed_true_back_compat():
    """Omitting profile_json (legacy callers) must reproduce the pre-#260
    behaviour exactly — never a false liability signal without data to check."""
    ledger = build_keyword_ledger(
        classifications=[_cls("RAG", "direct", ["RAG"], evidence="listed under Skills")],
        required_skills=["RAG"],
        nice_to_have_skills=[],
        keywords=[],
    )
    rag = _by_concept(ledger)["RAG"]
    assert rag["narrative_backed"] is True


def test_empty_profile_json_with_no_narrative_fields_is_not_backed():
    """A profile_json that IS given but genuinely carries no narrative text
    anywhere is honestly narrative_backed False — distinct from the omitted-
    argument back-compat case above."""
    ledger = build_keyword_ledger(
        classifications=[_cls("RAG", "direct", ["RAG"], evidence="listed under Skills")],
        required_skills=["RAG"],
        nice_to_have_skills=[],
        keywords=[],
        profile_json={"skills": [{"name": "RAG"}]},
    )
    rag = _by_concept(ledger)["RAG"]
    assert rag["narrative_backed"] is False


# ---------------------------------------------------------------------------
# keyword_liabilities() — the filter hard-requirement + claimable + unbacked
# ---------------------------------------------------------------------------


def test_keyword_liabilities_flags_required_claimable_narrative_less_concept():
    profile_json = {"skills": [{"name": "RAG"}]}
    ledger = build_keyword_ledger(
        classifications=[_cls("RAG", "direct", ["RAG"], evidence="listed under Skills")],
        required_skills=["RAG"],
        nice_to_have_skills=[],
        keywords=[],
        profile_json=profile_json,
    )
    liabilities = keyword_liabilities(ledger)
    assert [e["concept"] for e in liabilities] == ["RAG"]


def test_keyword_liabilities_excludes_narrative_backed_concept():
    """Direction 2 of the acceptance criteria: once a story covers it, it
    drops off the liability list."""
    profile_json = {
        "skills": [{"name": "RAG"}],
        "signature_stories": [
            {
                "title": "x",
                "challenge": "x",
                "mechanism": "Built a RAG pipeline end to end.",
                "outcome": "x",
            }
        ],
    }
    ledger = build_keyword_ledger(
        classifications=[_cls("RAG", "direct", ["RAG"], evidence="listed under Skills")],
        required_skills=["RAG"],
        nice_to_have_skills=[],
        keywords=[],
        profile_json=profile_json,
    )
    assert keyword_liabilities(ledger) == []


def test_nice_to_have_without_narrative_is_not_a_liability():
    """Hard requirements only — a nice-to-have bare skill is never flagged,
    however thin its narrative backing."""
    profile_json = {"skills": [{"name": "Terraform"}]}
    ledger = build_keyword_ledger(
        classifications=[_cls("Terraform", "direct", ["Terraform"], evidence="listed under Skills")],
        required_skills=[],
        nice_to_have_skills=["Terraform"],
        keywords=[],
        profile_json=profile_json,
    )
    e = _by_concept(ledger)["Terraform"]
    assert e["narrative_backed"] is False  # honestly unbacked...
    assert keyword_liabilities(ledger) == []  # ...but not a liability (not required)


def test_honest_gap_concept_is_never_a_liability():
    """A non-claimable (honest gap) concept is never surfaced as a liability
    — it isn't going to be echoed as a strength in the first place."""
    ledger = build_keyword_ledger(
        classifications=[_cls("Kubernetes", "gap", ["Kubernetes"])],
        required_skills=["Kubernetes"],
        nice_to_have_skills=[],
        keywords=[],
        profile_json={"skills": []},
    )
    assert keyword_liabilities(ledger) == []


def test_keyword_liabilities_tolerates_none_and_empty():
    assert keyword_liabilities(None) == []
    assert keyword_liabilities([]) == []

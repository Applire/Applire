# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""US244 — table-driven tests for the deterministic Oracle matchers."""
import pytest

from applire.services.oracle.matchers import (
    build_vault_index,
    extract_figures,
    ground_skill_claim,
    ground_text_claim,
    match_figures,
)


# ── figures ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "text,expected",
    [
        ("Reduced effort by 70%.", [("percent", "70")]),
        ("targets a ~70% reduction", [("percent", "70")]),
        ("cut costs by 12,5 %", [("percent", "12.5")]),
        ("saved €1.2M annually", [("currency", "1.2m")]),
        ("budget of 500k €", [("currency", "500k")]),
        ("managed a $2 Mio budget", [("currency", "2m")]),
        ("from 2019 to 2023", [("year", "2019"), ("year", "2023")]),
        ("migrated 200 users", [("number", "200")]),
        ("supported 1.000 clients", [("number", "1000")]),
        ("supported 1,000 clients", [("number", "1000")]),
        ("led a team of 12", [("number", "12")]),
        # single digits sit below the signal floor (no false red flags)
        ("led a team of 5", []),
        ("no figures here", []),
        # a percent span is consumed once, not double-counted as a number
        ("improved by 40% overall", [("percent", "40")]),
    ],
)
def test_extract_figures(text, expected):
    got = [(f.kind, f.value) for f in extract_figures(text)]
    assert got == expected


# ── #374 — standard/regulation identifiers are not figures (recon-verified
# 2026-08-01, edge probe 2026-07-29): "unter ISO 15189, DSGVO, IVDR und dem
# EU AI Act" was extracting "15189" as Figure(number, "15189"), producing a
# false claim-side "No vault evidence for figure(s): 15189" AND, on the vault
# side, a certification like "ISO 9001 Lead Auditor" polluted figure_map with
# ("number", "9001") so an unrelated "9001" elsewhere would falsely GROUND.
# ADR-062 clause 1: this is a fact-level exclusion (a standard number is not
# a quantified claim), never a heuristic about meaning — narrow, literal
# prefix list, uppercase-only to avoid German lowercase word collisions
# ("en" is common German prose; "EN" the DIN/EN standard prefix is not).
@pytest.mark.parametrize(
    "text,expected",
    [
        ("unter ISO 15189, DSGVO, IVDR und dem EU AI Act", []),
        ("ISO 9001 und ISO 27001", []),
        ("ISO/IEC 27001", []),
        ("EN 9100-zertifiziert", []),
        ("en 9100 lowercase stays a number", [("number", "9100")]),
        ("21 CFR Part 11", []),
        ("GAMP 5", []),
        # control cases — the guard must not overreach.
        ("Budget von 15189 EUR", [("currency", "15189")]),
        ("seit 2015", [("year", "2015")]),
        ("ISO 2015", []),  # year-kind match must be excluded too
        ("S/4HANA rollout", []),  # SAP module name, not a digit-run identifier
    ],
)
def test_extract_figures_identifier_exclusion(text, expected):
    got = [(f.kind, f.value) for f in extract_figures(text)]
    assert got == expected


# ── #377 (ADR-067 clause 4) — single-digit decimals are quantified substance ──
#
# n=10 real-provider trials: the lost bullet was "Unfallquote (LTIF) von 8,2
# auf 3,1 gesenkt" -- neither "8,2" nor "3,1" matched _NUMBER_RE (single
# leading digit), so the bullet looked figure-free to the deterministic cap
# even though it carries a real, quantified safety-ratio improvement.
@pytest.mark.parametrize(
    "text,expected",
    [
        # single-digit decimals now match as plain numbers.
        ("Unfallquote (LTIF) von 8,2 auf 3,1 gesenkt", [("number", "8.2"), ("number", "3.1")]),
        ("8,2", [("number", "8.2")]),
        ("3.1", [("number", "3.1")]),
        # the bare single-digit exclusion is NOT relaxed — a lone digit with
        # no decimal part still sits below the signal floor.
        ("led a team of 5", []),
        # unchanged: a decimal immediately followed by "%" is still a percent
        # figure, not a number — percent has priority and consumes the span.
        ("cut costs by 12,5 %", [("percent", "12.5")]),
        # German date-ordinal false-positive risk: "1.5" (day.month, "1.5.")
        # is syntactically identical to a single-digit decimal number. DECISION
        # (documented, not silently accepted): matching "1.5" as a number is
        # acceptable — this extractor has no calendar awareness anywhere else
        # either (see _YEAR_RE, which is equally blind to date context), and
        # scoping a date exclusion here would need lookahead this module
        # deliberately doesn't carry. A false-positive "number" figure is a
        # weaker failure mode than the false NEGATIVE this fix closes (#377).
        ("Beginn am 1.5. im Werk", [("number", "1.5")]),
    ],
)
def test_extract_figures_single_digit_decimal(text, expected):
    got = [(f.kind, f.value) for f in extract_figures(text)]
    assert got == expected


# ── vault index ───────────────────────────────────────────────────────────────

PROFILE = {
    "personal_info": {"name": "Anna Bauer"},
    "professional_summary": {"en": "IT leader focused on compliance automation."},
    "work_experience": [
        {
            "id": "w1",
            "company": "Acme GmbH",
            "role": "Head of IT",
            "start_date": "2019-03",
            "end_date": "2023-05",
            "responsibilities": ["Led a team of 12 engineers."],
            "achievements": [
                "Rollout of a compliance workflow that targets a ~70% reduction in manual effort.",
                "Reduced deployment time by 40% through CI automation.",
            ],
            "technologies": ["Python", "Kubernetes"],
        }
    ],
    "skills": [{"name": "Python"}, {"name": "Kubernetes"}],
    "metadata": {
        "enrichment_history": [
            {
                "id": "rec-1",
                "timestamp": "2026-07-01T10:00:00Z",
                "source": "interview",
                "changes": [
                    {
                        "section": "work_experience",
                        "field": "achievements",
                        "action": "added",
                        "new_value": (
                            "Rollout of a compliance workflow that targets a ~70% "
                            "reduction in manual effort."
                        ),
                    }
                ],
            }
        ]
    },
}


def test_vault_index_units_and_figure_map():
    index = build_vault_index(PROFILE)
    paths = {u.path for u in index.units}
    assert "work_experience[0].achievements[0]" in paths
    assert "work_experience[0].dates" in paths
    assert "skills[0]" in paths
    assert ("percent", "70") in index.figure_map
    assert ("percent", "40") in index.figure_map
    assert ("year", "2019") in index.figure_map
    assert ("number", "12") in index.figure_map


# ── #374 — vault-side identifier pollution (recon-verified 2026-08-01) ──────
ISO_CERT_PROFILE = {
    "personal_info": {"name": "Anna Bauer"},
    "certifications": [
        {"name": "ISO 9001 Lead Auditor", "issuing_organization": "TÜV"},
    ],
}


def test_vault_index_certification_prefix_not_indexed_as_figure():
    index = build_vault_index(ISO_CERT_PROFILE)
    assert ("number", "9001") not in index.figure_map


def test_vault_index_attaches_adr046_receipts():
    index = build_vault_index(PROFILE)
    target_unit = next(
        u for u in index.units if u.path == "work_experience[0].achievements[0]"
    )
    assert target_unit.receipt_ids == ["rec-1"]
    other = next(u for u in index.units if u.path == "work_experience[0].achievements[1]")
    assert other.receipt_ids == []


# ── figure matching ───────────────────────────────────────────────────────────

def test_match_figures_unmatched_is_red_flag_material():
    index = build_vault_index(PROFILE)
    result = match_figures(extract_figures("Improved satisfaction by 55%."), index)
    assert [f.value for f in result.unmatched] == ["55"]
    assert result.matched == []

    result = match_figures(extract_figures("Reduced effort by 70%."), index)
    assert result.unmatched == []
    assert result.matched[0][1][0].path == "work_experience[0].achievements[0]"


# ── grounding (shared predicate) ─────────────────────────────────────────────

def test_ground_skill_claim_surface_and_near_dupe():
    index = build_vault_index(PROFILE)
    assert ground_skill_claim("Python", index) is not None
    assert ground_skill_claim("kubernetes", index) is not None  # case-folded
    assert ground_skill_claim("React Native", index) is None


# ── #244 — CV skill-audit vs keyword ledger contradiction (live 2026-07-24
# founder acceptance run, generated_cvs ed9234fe-1502-...) ───────────────────
#
# Ground truth pinned against the dev DB profile 63cc8964-8100-4ae7-9b3b-
# 508ebea9414f: vault skill "Team Leadership and Mentorship" plus a NordPharm
# responsibility "Provided strategic guidance and mentorship to the
# architecture team..." back the CV skill "Mentoring", but the panel flagged
# it unbacked because neither the literal-substring nor the strict
# skills_near_dupe/containment check folds the gerund "Mentoring" to the
# vault's "...Mentorship" noun form (different single tokens by construction
# -- not a plural, not a multi-token containment, Jaccard 0). "Strategic
# Planning" has NO such deterministic tie to "Digital Strategy" (different
# stems entirely: strategic/strategy, planning/direction) -- its ledger
# "evidence" is LLM semantic adjacency, not a literal/near-dupe vault match,
# so it is correctly left unbacked by the Oracle's deterministic contract.
BUG244_PROFILE = {
    "personal_info": {"name": "Anna Bauer"},
    "professional_summary": {
        "en": (
            "Clinical-adjacent leadership includes GCLP team management, "
            "establishing the clinical front end for specialty biologics "
            "production."
        )
    },
    "work_experience": [
        {
            "id": "w-nordpharm",
            "company": "NordPharm SE",
            "role": "Associate Director",
            "responsibilities": [
                "Set strategic direction, roadmap and release planning for "
                "supply-chain systems in alignment with global process "
                "owners and domain architects",
                "Provided strategic guidance and mentorship to the "
                "architecture team, cultivating a culture of innovation "
                "and collaboration",
            ],
        }
    ],
    "skills": [{"name": "Digital Strategy"}, {"name": "Team Leadership and Mentorship"}],
}


def test_ground_skill_claim_mentoring_folds_to_mentorship_vault_skill():
    """#244: 'Mentoring' must ground -- vault carries 'Mentorship' evidence
    (the skill AND a matching responsibility), a same-stem derivational form
    the near-dupe/containment instruments cannot see (single differing
    tokens), reusing the shared surface_present verb-form fallback instead."""
    index = build_vault_index(BUG244_PROFILE)
    unit = ground_skill_claim("Mentoring", index)
    assert unit is not None
    assert "mentorship" in unit.text_norm


def test_ground_skill_claim_strategic_planning_stays_unbacked():
    """#244: 'Strategic Planning' has NO deterministic vault tie -- the
    ledger's claimable verdict rests on LLM semantic adjacency ('Digital
    Strategy' skill + a roadmap/planning responsibility), not a literal or
    near-dupe match, so the Oracle correctly leaves it unbacked."""
    index = build_vault_index(BUG244_PROFILE)
    assert ground_skill_claim("Strategic Planning", index) is None


def test_ground_skill_claim_team_management_already_grounds_via_literal_phrase():
    """#244 regression lock: 'Team Management' already grounds today via the
    literal substring 'team management' in the professional summary -- this
    pair was never actually broken, unlike 'Mentoring'."""
    index = build_vault_index(BUG244_PROFILE)
    unit = ground_skill_claim("Team Management", index)
    assert unit is not None
    assert "team management" in unit.text_norm


def test_ground_skill_claim_agrees_with_ledger_claimable_skill_evidence():
    """Ledger-vs-audit agreement (#122 lesson): a skill the Keyword Ledger
    marks claimable with evidence naming a real vault skill must not audit
    unbacked -- the two surfaces must never disagree on the SAME skill."""
    ledger_entry = {
        "concept": "Mentoring",
        "claimable": True,
        "evidence": (
            "Explicit skill (Team Leadership and Mentorship) and "
            "demonstrated in responsibilities."
        ),
    }
    index = build_vault_index(BUG244_PROFILE)
    assert ledger_entry["claimable"] is True
    assert ground_skill_claim(ledger_entry["concept"], index) is not None


def test_ground_text_claim_coverage():
    index = build_vault_index(PROFILE)
    close = ground_text_claim("Cut deployment time through CI automation.", index)
    assert close.best_unit is not None
    assert close.best_unit.path == "work_experience[0].achievements[1]"
    assert close.best_coverage >= 0.6

    far = ground_text_claim("Owned vendor negotiations across three continents.", index)
    assert far.best_coverage < 0.6


# ── #378/run-12 (sibling of #355) — story owner_ids miss project→parent
# resolution ──────────────────────────────────────────────────────────────
#
# Ground truth (run 12, controlling_emma_de): the story "Management-Reporting
# auf Power BI umstellen" carries experience_refs=[<the PROJECT's id>]; the
# project's own units already resolve to the parent work id via
# ``_project_owners`` (US187 nesting), but the story owner construction
# trusted ``experience_refs`` verbatim, so the project's own units cleared a
# claim rendered under the parent work position while the story's units
# (same fact, same claim) flagged it "misattributed" — a false positive
# (byte-identical detail text reproduced by recon 2026-08-01).
from applire.services.oracle.matchers import find_foreign_owner

RUN12_PROFILE = {
    "personal_info": {"name": "Anna Bauer"},
    "work_experience": [
        {
            "id": "w1",
            "company": "Controlling GmbH",
            "role": "Head of Controlling",
            "start_date": "2020-01",
            "end_date": None,
        },
    ],
    "projects": [
        {
            "id": "p1",
            "name": "Power BI Rollout",
            "role": "Lead",
            "associated_experience": "w1",
        },
    ],
    "signature_stories": [
        {
            "title": "Management-Reporting auf Power BI umstellen",
            "challenge": "Manual Excel reporting was slow and error-prone.",
            "mechanism": "Rolled out a Power BI-based reporting stack.",
            "outcome": "Reduced monthly close time by 40%.",
            "experience_refs": ["p1"],
        },
    ],
}


def test_story_owner_ids_resolve_project_ref_to_parent_work_id():
    index = build_vault_index(RUN12_PROFILE)
    story_unit = next(
        u for u in index.units if u.path == "signature_stories[0].outcome"
    )
    assert "p1" in story_unit.owner_ids
    assert "w1" in story_unit.owner_ids


def test_story_backed_claim_not_misattributed_when_ref_is_a_project():
    index = build_vault_index(RUN12_PROFILE)
    story_unit = next(
        u for u in index.units if u.path == "signature_stories[0].outcome"
    )
    assert find_foreign_owner("w1", [story_unit], index.same_employer_ids.get("w1", frozenset())) is None


def test_story_owner_ids_ref_naming_work_id_directly_unchanged():
    profile = {
        "personal_info": {"name": "Anna Bauer"},
        "work_experience": [{"id": "w1", "company": "Acme", "role": "Lead"}],
        "signature_stories": [
            {
                "title": "Direct work ref",
                "challenge": "c",
                "mechanism": "m",
                "outcome": "o",
                "experience_refs": ["w1"],
            }
        ],
    }
    index = build_vault_index(profile)
    story_unit = next(u for u in index.units if u.path == "signature_stories[0].outcome")
    assert story_unit.owner_ids == frozenset({"w1"})


def test_story_owner_ids_dangling_ref_kept_verbatim():
    """#355: a ref matching NO entity stays in owner_ids as-is (fail-open is
    out of scope for this fix — removing it would change that behavior)."""
    profile = {
        "personal_info": {"name": "Anna Bauer"},
        "signature_stories": [
            {
                "title": "Dangling ref",
                "challenge": "c",
                "mechanism": "m",
                "outcome": "o",
                "experience_refs": ["ghost-id"],
            }
        ],
    }
    index = build_vault_index(profile)
    story_unit = next(u for u in index.units if u.path == "signature_stories[0].outcome")
    assert story_unit.owner_ids == frozenset({"ghost-id"})

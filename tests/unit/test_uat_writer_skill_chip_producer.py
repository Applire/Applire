# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#672 F-9 (founder UAT 2026-09-20) — WHICH string the skills-list gap guard puts
on the page.

Ground truth, measured on 11 real-provider runs (`Documents/Runs/Nougat/
founder-uat-fixes/d/f5-f9-replay-per-round.md`, openrouter/gpt-5.6-luna): the
delivered CV's skills list ended with the chips ``roadmap`` and ``AI automation use
case``. Neither is a competence; both are in the delivered list of **11 of 11** runs
and in the WRITER's own drafted list of **0 of 11**. The producer is
``_restore_narrative_named_skills`` (#376) — deterministic code that runs inside
``_compose_document``, i.e. BEFORE the terminal review and AGAIN after every terminal
correction, which is why the reviewer's check-12 finding can never be corrected away
by the loop (`tests/unit/test_uat_writer_skill_shape.py` owns the detector).

The producer defect was an asymmetry, not a missing filter: the Oracle audits a
delivered chip with ``ground_skill_claim(name, index, ledger_forms)``
(`services/oracle/audit.py:1240`) and this pass called the same function WITHOUT the
ledger arm, so a ledger row's own concept name could never ground here and the only
groundable candidate left was whichever JD surface form the prose happened to echo.
"""
from __future__ import annotations

from applire.schemas.cv import TailoredCVData
from applire.services.cv import _restore_narrative_named_skills

# The F-9 shape, synthetic: the candidate's own bullet says "roadmap" in a sentence,
# the vault has NO "roadmap" skill row, and the job's ledger row names the competence
# properly and lists the JD's bare noun as a surface form.
PROFILE = {
    "personal_info": {"name": "Mara Lindqvist"},
    "skills": [{"name": "Computerised System Validation", "status": "confirmed"}],
    "work_experience": [
        {
            "id": "w-1",
            "company": "Aventra Diagnostics Group",
            "role": "Head of Quality Systems",
            "responsibilities": [
                "Owned the platform roadmap for three regulated sites and drove "
                "annual budget estimation for the shared investment plan.",
            ],
        }
    ],
}

TAILORED = {
    "contact": {"name": "Mara Lindqvist"},
    "skills": ["Computerised System Validation"],
    "work_history": [
        {
            "company": "Aventra Diagnostics Group",
            "role": "Head of Quality Systems",
            "bullets": [
                "Owned the platform roadmap for three regulated sites and drove "
                "annual budget estimation for the shared investment plan.",
            ],
        }
    ],
}

LEDGER = [
    {
        "concept": "Roadmap & Budget Ownership",
        "surface_forms": ["roadmap", "budget estimation"],
        "claimable": True,
        "status": "direct",
        "sources": ["required"],
        "fit_weight": 1.0,
        "evidence": "Owned the platform roadmap … annual budget estimation.",
    }
]


def _restored(profile=None, tailored=None, ledger=None) -> list[str]:
    out = _restore_narrative_named_skills(
        TailoredCVData.model_validate(tailored or TAILORED),
        profile or PROFILE,
        ledger if ledger is not None else LEDGER,
    )
    return list(out.skills or [])


def test_the_row_concept_name_lands_and_the_jd_surface_form_does_not():
    """THE F-9 test. Two mutations fail it and nothing else in the suite:

    * drop ``_ledger_form_groups`` from ``_oracle_backed``'s ``ground_skill_claim``
      call — the concept then has no vault backing of its own and the fallback puts
      ``roadmap`` on the page, exactly what the founder received;
    * restore the pre-fix candidate order (``for f in narrated`` instead of
      ``for f in group``) — the concept is not narrated, so the same thing happens.
    """
    skills = _restored()
    assert "Roadmap & Budget Ownership" in skills
    assert "roadmap" not in skills, "the JD's bare noun must not be the chip"
    assert "budget estimation" not in skills, "#386: one page entry per competence"


def test_the_chip_this_pass_adds_is_one_the_oracle_grounds():
    """Containment (ADR-066 clause 2): the generator may not put a name on the page
    that the document's own truthfulness report then marks ``unbacked``. Asserted
    with the AUDIT's own call — same function, same ledger argument
    (`services/oracle/audit.py:1240` / :1683).
    """
    from applire.services.keyword_ledger import claimable_surface_form_groups
    from applire.services.oracle.matchers.grounding import ground_skill_claim
    from applire.services.oracle.matchers.vault import build_vault_index
    from applire.services.profile.reconcile.stance import exclude_unconfirmed

    added = [s for s in _restored() if s not in TAILORED["skills"]]
    assert added, "fixture must exercise the add path"
    index = build_vault_index(exclude_unconfirmed(PROFILE))
    forms = claimable_surface_form_groups(LEDGER, exclude_keyword_only=True)
    for chip in added:
        assert ground_skill_claim(chip, index, forms) is not None, chip


def test_narration_is_still_required_for_the_whole_group():
    """The ledger arm may not become a free pass. A row whose forms the document
    never narrates adds nothing, however well the vault backs it — #376 is about a
    competence the DOCUMENT names, not about every competence the candidate has.
    """
    unnarrated = {
        **TAILORED,
        "work_history": [
            {
                "company": "Aventra Diagnostics Group",
                "role": "Head of Quality Systems",
                "bullets": ["Ran the validation programme for three regulated sites."],
            }
        ],
    }
    assert _restored(tailored=unnarrated) == ["Computerised System Validation"]

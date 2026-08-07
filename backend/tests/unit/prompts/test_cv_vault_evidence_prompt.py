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

"""#303 — the STRONGEST VAULT EVIDENCE digest must reach the CV writer's
prompts, not only the cover letter's. String-level assertions only; no LLM.

What these tests do NOT do, deliberately: nothing here asserts that a ledger
surface form appears in generated prose. That predicate was the reverted
2026-07-30 #303 fix — unsatisfiable by truthful prose, because ledger surface
forms are abstract compound nouns honest testimony never uses verbatim
(ADR-060 amended 2026-07-30, #377). These tests pin PROMPT INPUT only: which
facts the writer is given. What the writer then does with them is evidenced by
a charter run (ADR-062 clause 7), never by CI.
"""

from applire.prompts.cv_segmented import build_work_section_prompt
from applire.prompts.cv_tailoring import build_user_prompt
from applire.services.vault_evidence import (
    render_vault_evidence_block,
    select_vault_evidence,
)

_JOB = {"role_title": "Werkleiter", "required_skills": [], "keywords": []}
_PROFILE_EMPTY: dict = {"work_experience": []}
_DIRECTIVE = {"summary_angle": "operations", "skills_focus": []}

# Invented fixture (never real personal data), shaped like the run-#7 defect:
# the ledger row's `evidence` is the gap classifier's own free-text `reason`
# (services.gap.ledger_input_from_classification copies `reason` onto
# `evidence`), which paraphrases WHY the row was graded and never quotes the
# vault. The vault sentence carrying the figure a hiring reviewer looks for
# lives only in the profile.
_VAULT_SENTENCE = "Budgetverantwortung ca. 6 Mio. EUR fuer Personal und Instandhaltung."
_CLASSIFIER_REASON = "Listed as a skill and visible in the work history."

_LEDGER = [
    {
        "concept": "Budgetverantwortung",
        "surface_forms": ["Budgetverantwortung"],
        "claimable": True,
        "status": "direct",
        "fit_weight": 1.0,
        "evidence": _CLASSIFIER_REASON,
    }
]
_PROFILE = {
    "work_experience": [
        {
            "id": "w1",
            "company": "Weberit",
            "role": "Produktionsleiter",
            "start_date": "2015-01",
            "end_date": None,
            "is_current": True,
            "responsibilities": [_VAULT_SENTENCE],
            "achievements": [],
        }
    ],
    "projects": [],
    "skills": [],
}

_ITEMS = select_vault_evidence(_LEDGER, "", _PROFILE)
_CV_BLOCK = render_vault_evidence_block(_ITEMS, chain="cv")


def test_fixture_actually_selects_the_vault_sentence():
    """Pins the fixture — if selection ever goes empty every test below is vacuous."""
    assert _ITEMS, "fixture must yield at least one digest item"
    assert any(_VAULT_SENTENCE in i.text for i in _ITEMS)
    assert _CV_BLOCK


def test_the_block_carries_the_vault_sentence_the_ledger_evidence_field_does_not():
    """The pinned #303 mechanism: the CV writer's only concept->evidence pointer
    was the ledger's classifier paraphrase. The digest supplies the vault's own
    sentence and the entry that owns it."""
    assert _VAULT_SENTENCE in _CV_BLOCK
    assert _CLASSIFIER_REASON not in _CV_BLOCK
    assert "work_experience[0]" in _CV_BLOCK


def test_build_user_prompt_carries_the_vault_evidence_block():
    prompt = build_user_prompt(_JOB, _PROFILE_EMPTY, [], [], vault_evidence_block=_CV_BLOCK)
    assert "STRONGEST VAULT EVIDENCE" in prompt
    assert _VAULT_SENTENCE in prompt


def test_build_user_prompt_without_vault_evidence_is_byte_identical_to_baseline():
    baseline = build_user_prompt(_JOB, _PROFILE_EMPTY, [], [])
    with_none = build_user_prompt(_JOB, _PROFILE_EMPTY, [], [], vault_evidence_block=None)
    with_empty = build_user_prompt(_JOB, _PROFILE_EMPTY, [], [], vault_evidence_block="")
    assert with_none == baseline
    assert with_empty == baseline
    assert "STRONGEST VAULT EVIDENCE" not in baseline


def test_build_work_section_prompt_carries_the_vault_evidence_block():
    """ADR-067's own opening complaint is that the single-call and segmented
    paths diverged. The digest must reach both."""
    entry = _PROFILE["work_experience"][0]
    prompt = build_work_section_prompt(
        entry, _DIRECTIVE, _JOB, [], "de", vault_evidence_block=_CV_BLOCK
    )
    assert "STRONGEST VAULT EVIDENCE" in prompt
    assert _VAULT_SENTENCE in prompt


def test_build_work_section_prompt_without_vault_evidence_is_byte_identical():
    entry = _PROFILE["work_experience"][0]
    baseline = build_work_section_prompt(entry, _DIRECTIVE, _JOB, [], "de")
    with_none = build_work_section_prompt(
        entry, _DIRECTIVE, _JOB, [], "de", vault_evidence_block=None
    )
    assert with_none == baseline
    assert "STRONGEST VAULT EVIDENCE" not in baseline


def test_the_cv_rendering_is_not_written_for_the_letter():
    """The shipped #271 instruction is letter-specific prose ("the letter's
    flow", "appear in the letter"). Handing it to the CV writer verbatim would
    instruct it about a document it is not writing."""
    low = _CV_BLOCK.lower()
    assert "letter" not in low


def test_the_cv_rendering_names_the_owning_entry_rule():
    """Rule 1/2 of the CV writer prompt are per-ENTRY. The digest's whole added
    value over the raw profile dump is the owner path, so the instruction must
    say what to do with it — otherwise it invites the ADR-071 misattribution
    class it should be preventing."""
    low = _CV_BLOCK.lower()
    assert "source" in low
    assert "owns" in low


def test_the_cv_rendering_never_becomes_a_must_appear_demand():
    """ADR-062 clause 4: the prompt already carries a hard ROLE BULLET BUDGETS
    ceiling. A block demanding that every item appear would contradict it and
    drive the review loop to exhaustion — the failure mode of the reverted fix."""
    low = _CV_BLOCK.lower()
    assert "selectivity is expected" in low
    assert "not content that must all appear" in low


def test_the_letter_rendering_is_unchanged():
    """The letter chain was charter-verified with this exact wording (run 18);
    the CV variant must not perturb it."""
    letter_block = render_vault_evidence_block(_ITEMS, chain="letter")
    assert "the letter's flow" in letter_block
    assert _VAULT_SENTENCE in letter_block

# Copyright (C) 2026 Tobias Rosenbaum
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify it under the
# terms of the GNU Affero General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU Affero General Public License for more
# details.
#
# You should have received a copy of the GNU Affero General Public License along
# with this program. If not, see <https://www.gnu.org/licenses/>.
"""#229 regression guard — BOTH extraction prompts must split role bullets.

Same shape, same trap, same pair of prompts as the #190 certifications guard
next door (``test_extraction_prompts_certifications.py``):

  * ``cv_extraction``      — the browser ``/upload`` and import-job path.
  * ``profile_extraction`` — the ``import_from_text`` path, i.e. the MCP/agent
    ``import_cv`` tool, LinkedIn text/zip/pdf import, and paste-text import.

Before this fix ``profile_extraction``'s ``work_history`` schema offered a single
flat ``"bullets"`` list. A model cannot emit a field it was never given, so on
that entire door ``achievements`` and ``technologies`` were not *misclassified* —
they were structurally unreachable, and everything collapsed into
``responsibilities`` via the ``bullets`` -> ``responsibilities`` migration in
``MasterProfileData._migrate_legacy_fields``. Thirteen real flat-door calls in
``backend/logs/llm/2026-07-2*.jsonl`` returned entries whose only bullet key was
``bullets``, across six days — never once ``achievements``.

These are PROMPT-CONTENT assertions on purpose. ``MockLLMProvider`` returns a
fixed profile shape regardless of what the prompt actually asked for, so an
import-through-mock test goes green against a prompt with no ``achievements``
field at all. Only reading the prompt itself catches the real defect.
"""

from applire.prompts import cv_extraction, profile_extraction

_SPLIT_FIELDS = ('"responsibilities"', '"achievements"', '"technologies"')
_CLASSIFY_MARKER = "RESPONSIBILITIES vs ACHIEVEMENTS"
_TECH_MARKER = "TECHNOLOGIES vs PRACTICES"


def test_profile_extraction_prompt_declares_split_work_fields():
    """The MCP / LinkedIn / paste door — the door #229 was reported on."""
    for field in _SPLIT_FIELDS:
        assert field in profile_extraction.SYSTEM_PROMPT, (
            f"{field} missing from profile_extraction.SYSTEM_PROMPT — the model "
            f"cannot emit a field it was never given"
        )


def test_profile_extraction_prompt_has_no_flat_bullets_field():
    """The flat field must be gone, not merely accompanied by the split ones.

    Leaving ``"bullets"`` in the schema hands the model two competing places to
    put the same content, which is how the split silently stayed empty.
    """
    assert '"bullets"' not in profile_extraction.SYSTEM_PROMPT


def test_profile_extraction_prompt_carries_classification_rules():
    assert _CLASSIFY_MARKER in profile_extraction.SYSTEM_PROMPT
    assert _TECH_MARKER in profile_extraction.SYSTEM_PROMPT


def test_profile_extraction_rule_forbids_empty_achievements_as_default():
    """The rule must say an empty achievements list is not the safe default.

    Without this the model reads "only extract what is stated" (rule 2) as a
    licence to leave the harder list empty — which is exactly what it did.
    """
    assert "not the safe default" in profile_extraction.SYSTEM_PROMPT


def test_profile_extraction_user_prompt_reminds_about_the_split():
    reminder = profile_extraction.build_user_prompt("SOME CV TEXT")
    assert "achievements" in reminder
    assert "technologies" in reminder
    assert "SOME CV TEXT" in reminder


def test_cv_extraction_prompts_declare_split_work_fields():
    """The browser /upload door — guard the working behaviour from regressing."""
    for prompt in (
        cv_extraction.GENERIC_CV_EXTRACTION_PROMPT,
        cv_extraction.JD_AWARE_CV_EXTRACTION_PROMPT,
    ):
        for field in _SPLIT_FIELDS:
            assert field in prompt
        assert _TECH_MARKER in prompt


def test_both_doors_agree_on_the_technologies_hygiene_rule():
    """#327 established technologies is tools-only BY INSTRUCTION.

    That instruction is what makes an empty ``technologies`` list defensible
    rather than a bug — so both doors have to carry it, or the two doors mean
    different things by the same field name.
    """
    assert _TECH_MARKER in profile_extraction.SYSTEM_PROMPT
    assert _TECH_MARKER in cv_extraction.GENERIC_CV_EXTRACTION_PROMPT

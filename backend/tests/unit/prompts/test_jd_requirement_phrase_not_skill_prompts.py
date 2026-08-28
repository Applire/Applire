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

"""Bug #391 category B (applire-prompt-first): the writer's rule 7 said a skill with no
BASIS in the profile is omitted, but never named the SHAPE that fails even when grounded
-- a JD-requirement phrase (a duration, an industry/sector name, a degree requirement) is
not a skill at all. Charter runs 11-13 (2026-07-31...08-01) showed the writer minting
"5 Jahre Controlling-Erfahrung" and "Verpackungsindustrie" as skills-list entries; Emma's
case had user-visible cost -- the CV's "5 Jahre" contradicted the letter's truthful "neun
Jahren", and the blind HR reviewer flagged the mismatch as the application's top risk
signal. PO ruling 2026-08-15 (ADR-076 amendment 4, issue #391): the predicate-side fix
(tightening the deterministic vault-tie threshold) is clause-4-illegal and rides the
A3-A7 unit migration; this prompt-side half ships now, per the prompt-first ordering the
same ruling states.

Two independent builders emit the CV's `skills` field, and each states its own grounding
rule for what belongs there -- a rule written into only one of them is no rule at the
other (applire-prompt-first: "a rule written against ONE named function"):
  - the single-call writer's rule 7 (cv_tailoring.SYSTEM_PROMPT, paired with
    build_user_prompt -- services/cv.py:470-483, ``system=SYSTEM_PROMPT``)
  - the ADR-047 segmented writer's dedicated skills section
    (cv_segmented.SKILLS_SECTION_SYSTEM_PROMPT, paired with build_skills_prompt --
    services/cv.py:394-401, ``system=SKILLS_SECTION_SYSTEM_PROMPT``)

String-level assertions only; no LLM (mirrors test_cv_budget_prompts.py /
test_skill_verbatim_prompts.py in this directory). Each test calls the real builder
function with a minimal job/profile fixture and asserts the rule sentence is present in
the SYSTEM prompt that is actually paired with that builder's output at the real call
site cited above -- i.e. the prompt as built, not a hand-copied string.
"""

from applire.prompts.cv_segmented import SKILLS_SECTION_SYSTEM_PROMPT, build_skills_prompt
from applire.prompts.cv_tailoring import SYSTEM_PROMPT, build_user_prompt

_JOB = {"role_title": "Engineer", "required_skills": [], "keywords": []}
_PROFILE = {"work_experience": []}

# The exact clause both sites must carry -- same words, so a future edit to one site
# without the other is caught by test_both_builder_sites_state_the_rule_identically.
# Both cv_tailoring.SYSTEM_PROMPT (one long line per rule) and cv_segmented.py
# (hard-wraps prose within the triple-quoted string -- a PRE-EXISTING convention, e.g.
# "...skills to lead with those\nthe job requires..." above the edited sentence) may
# embed a literal newline mid-sentence; a soft-wrap is not semantically significant to
# the model, so containment is checked on whitespace-normalized text, not the raw
# string, to stay correct regardless of exactly where either file wraps a line.
_RULE_CLAUSE = (
    "is not a skill: a skill is a named competence, tool or method the candidate "
    "holds in the vault"
)


def _normalize(text: str) -> str:
    return " ".join(text.split())


def test_single_call_writer_prompt_states_a_requirement_phrase_is_not_a_skill():
    """cv_tailoring.build_user_prompt is the user-prompt half of the single-call writer
    call; services/cv.py:480 pairs it with ``system=SYSTEM_PROMPT``, so SYSTEM_PROMPT is
    the system half of the prompt this builder's output is actually sent alongside."""
    user_prompt = build_user_prompt(_JOB, _PROFILE, [], "en")
    assert user_prompt  # the builder call itself must succeed against the fixture
    built_prompt = _normalize(SYSTEM_PROMPT + "\n\n" + user_prompt)
    assert _RULE_CLAUSE in built_prompt
    assert "duration" in built_prompt and "degree requirement" in built_prompt


def test_segmented_skills_writer_prompt_states_a_requirement_phrase_is_not_a_skill():
    """cv_segmented.build_skills_prompt is the user-prompt half of the ADR-047 segmented
    skills-section call; services/cv.py:400 pairs it with
    ``system=SKILLS_SECTION_SYSTEM_PROMPT``."""
    user_prompt = build_skills_prompt({}, _JOB, _PROFILE, [], "en")
    assert user_prompt
    built_prompt = _normalize(SKILLS_SECTION_SYSTEM_PROMPT + "\n\n" + user_prompt)
    assert _RULE_CLAUSE in built_prompt
    assert "duration" in built_prompt and "degree requirement" in built_prompt


def test_both_builder_sites_state_the_rule_identically():
    """A rule written into only one of two skills-emitting builders is no rule at the
    other (applire-prompt-first). Pins the SAME wording at both sites so a future edit
    to one cannot silently drift from or drop at the other."""
    sites = {
        "cv_tailoring.SYSTEM_PROMPT": SYSTEM_PROMPT,
        "cv_segmented.SKILLS_SECTION_SYSTEM_PROMPT": SKILLS_SECTION_SYSTEM_PROMPT,
    }
    for name, prompt in sites.items():
        assert _RULE_CLAUSE in _normalize(prompt), (
            f"{name} is missing the requirement-phrase rule"
        )

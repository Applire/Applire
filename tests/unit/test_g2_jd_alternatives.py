# Copyright (C) 2026 Tobias Rosenbaum
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.
"""G-2 = C / G-2c (founder, 2026-10-24) — job analysis v13: a choice between two
NAMED tools is one requirement; "or comparable" and a preferred qualifier are not
a choice. Pins the rule AND its contrast pairs, so a later edit cannot keep the
rule and lose the boundary the measurement shipped on (the untightened variant
produced "Apache Kafka / comparable event streaming" and "SAP PP / SAP MM")."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from applire.prompts.job_analysis import SYSTEM_PROMPT  # noqa: E402


def _alternatives_block() -> str:
    start = SYSTEM_PROMPT.index("ALTERNATIVES (one requirement")
    end = SYSTEM_PROMPT.index("SCOPE REQUIREMENTS", start)
    return SYSTEM_PROMPT[start:end]


def test_a_choice_between_named_tools_is_one_requirement_with_both_keywords():
    block = _alternatives_block()
    assert '"FastAPI / Django"' in block
    assert 'keywords: "FastAPI",\n"Django"' in block or '"FastAPI", "Django"' in block.replace("\n", " ")
    assert "either one" in block


def test_or_comparable_is_not_a_choice():
    block = _alternatives_block()
    assert "or comparable" in block and "is NOT a choice" in block
    assert '("Apache Kafka")' in block


def test_a_preferred_qualifier_is_never_merged():
    block = _alternatives_block()
    for word in ("idealerweise", "wünschenswert", "preferably"):
        assert word in block
    assert '"SAP PP" and\n"SAP MM", never one merged entry' in block or (
        '"SAP PP" and "SAP MM", never one merged entry' in block.replace("\n", " ")
    )


def test_example_lists_and_enumerations_stay_outside_the_rule():
    block = _alternatives_block()
    assert "This is about a CHOICE only" in block
    assert "z. B. LucaNet" in block

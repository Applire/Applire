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

"""E042 / US236 — region norm registry + target-page resolution (ADR-051 §1).

No component may hard-code a page number; everything reads REGION_NORMS via
resolve_target_pages(). Precedence: per-generation override > user setting >
REGION_NORMS[region].cv_standard_pages. No Docker, no LLM.
"""

import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.norms import DEFAULT_REGION, REGION_NORMS, resolve_target_pages


def test_region_norms_has_dach_row():
    assert "DACH" in REGION_NORMS
    dach = REGION_NORMS["DACH"]
    assert dach.cv_standard_pages == 2
    assert dach.cv_max_pages == 3
    assert dach.letter_pages == 1


def test_default_region_is_dach():
    assert DEFAULT_REGION == "DACH"


def test_resolve_target_pages_override_wins():
    assert resolve_target_pages(override=3, setting=1) == 3


def test_resolve_target_pages_setting_wins_without_override():
    assert resolve_target_pages(override=None, setting=1) == 1


def test_resolve_target_pages_falls_back_to_region_standard():
    assert resolve_target_pages(override=None, setting=None) == 2


def test_resolve_target_pages_explicit_region():
    assert resolve_target_pages(override=None, setting=None, region="DACH") == 2


def test_resolve_target_pages_override_beats_setting_even_when_both_present():
    assert resolve_target_pages(override=5, setting=2) == 5

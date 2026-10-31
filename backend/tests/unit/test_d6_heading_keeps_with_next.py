# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
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

"""D-6 residual (Hazelnut build 5, delivery run 4): page 1 of the CV ended on
the nested "PROJECTS" sub-label; its first project started page 2. Every
heading a CV template prints directly above a block — the section heading and
the nested projects sub-label — keeps with that block in print
(``break-after: avoid``), which the preview's pagination mirrors
(``frontend/components/document/previewPages.ts`` ``headingToMove``).

Measured before the fix on the seven templates (84 synthetic CVs, Chromium
print + pdftotext): 15 pages ended on the sub-label; after it, 0."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from applire.services.cv import _TEMPLATE_FILES

TEMPLATES = Path(__file__).resolve().parents[2] / "applire" / "templates"

#: the class each template prints its section heading with
_HEADING = {
    "lebenslauf.html.j2": "section-title", "executive.html.j2": "section-title",
    "academic.html.j2": "section-title", "compact_pro.html.j2": "section-title",
    "creative_sidebar.html.j2": "section-title", "modern_swiss.html.j2": "section-header",
    "tech_developer.html.j2": "section-label",
}


def _rules(css: str, cls: str) -> list[str]:
    return re.findall(r"(?m)^\s*\." + re.escape(cls) + r"\s*\{([^}]*)\}", css)


@pytest.mark.parametrize("template", sorted(set(_TEMPLATE_FILES.values())))
@pytest.mark.parametrize("which", ["section", "entry-projects-title"])
def test_a_heading_keeps_with_its_first_block_in_print(template, which):
    css = (TEMPLATES / template).read_text(encoding="utf-8")
    cls = _HEADING[template] if which == "section" else which
    rules = _rules(css, cls)
    assert rules, f"{template}: no .{cls} rule"
    assert any(re.search(r"(?<![-\w])break-after:\s*avoid", r) for r in rules), f"{template}: .{cls} may end a page alone"

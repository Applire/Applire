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

"""D-35 (Hazelnut build 5, delivery run 8): a role with ≤ 3
own bullets was ``entry-atomic`` although its NESTED projects made it long — one
unbreakable block; rep a's page 1 stayed ~40 % empty and the CV ran to 4 pages.
And when a role did break, its "PROJECTS" block could open the next page without
the role's heading (rep c: read as belonging to the role above).

The rule, in all seven templates:
- an entry is atomic only when ALL of it is short (own + nested project bullets ≤ 3);
- otherwise head + first own bullets keep together (``entry-lead``), the rest break;
- a project with more than one bullet keeps its name + first bullet together
  (``project-lead``) and may break after it;
- the nested projects never open a page on their own (``.entry-projects``
  ``break-before: avoid``; a long role's tail is opened so only its LAST bullet
  travels with them).

Measured on the stored run-8 CVs (Chromium print): rep a v1 4 → 3 pages (page-1
foot gap 43 % → 23 %), v2 4 → 3 (43 % → 5 %), rep c's projects no longer open a page."""
from __future__ import annotations

import re

import pytest

from applire.schemas.cv import TailoredContact, TailoredCVData, TailoredProjectEntry, TailoredWorkEntry
from applire.services.color_detection import _make_color_context
from applire.services.cv import _TEMPLATE_FILES, _jinja_env
from applire.templates.labels import cv_labels

B = "Steuerte die Einführung eines neuen Prüfprozesses für {n} Linien und senkte die Reklamationsquote"


def _render(template: str, own: int, project_bullets: list[int]) -> str:
    projects = [TailoredProjectEntry(name=f"Vorhaben {i}", bullets=[B.format(n=100 + 10 * i + j) for j in range(n)])
                for i, n in enumerate(project_bullets)]
    cv = TailoredCVData(
        contact=TailoredContact(name="Erika Muster", location="Köln"), summary="Qualitätsingenieurin.",
        skills=["SPC"], show_photo=False,
        work_history=[TailoredWorkEntry(id="w0", company="Firma GmbH", role="Schichtleiterin", start_date="2019-05",
                                        end_date="2023-11", bullets=[B.format(n=j) for j in range(own)], projects=projects)],
    )
    return _jinja_env.get_template(_TEMPLATE_FILES[template]).render(
        cv=cv, color=_make_color_context("#2b5fa8"), lang="de", labels=cv_labels("de"))


def _work_entry(html: str) -> str:
    """The <div class="entry…"> that holds the role, up to the next role / section."""
    body = html[html.index("<body"):]
    i = body.index('class="entry-title">Schichtleiterin<')
    start = body.rfind('<div class="entry', 0, i)
    while 'class="entry-' in body[start:start + 20]:  # skip entry-lead / entry-header
        start = body.rfind('<div class="entry', 0, start)
    return body[start:]


def _classes(html: str, prefix: str) -> list[str]:
    return re.findall(r'class="(' + prefix + r'[^"]*)"', html)


TEMPLATES = sorted(_TEMPLATE_FILES)


@pytest.mark.parametrize("template", TEMPLATES)
def test_a_two_bullet_role_with_a_five_bullet_project_can_break(template):
    entry = _work_entry(_render(template, own=2, project_bullets=[5]))
    head = entry[:entry.index(">")]
    assert "entry-atomic" not in head, f"{template}: the whole role is one unbreakable block"
    assert 'class="entry-lead"' in entry, f"{template}: head + first bullets keep together"
    item = entry[entry.index('class="project-item'):]
    assert item.startswith('class="project-item"'), f"{template}: a 5-bullet project is not atomic"
    lead = item[item.index('class="project-lead"'):]
    lead = lead[:lead.index("</div>", lead.index("</ul>"))]
    assert "Vorhaben 0" in lead and lead.count("<li>") == 1, f"{template}: project name + first bullet keep together"
    assert item.count("<li>") >= 5


@pytest.mark.parametrize("template", TEMPLATES)
def test_a_short_role_with_a_short_project_stays_one_unit(template):
    """The DACH convention holds where it is cheap: ≤ 3 bullets in all → one unit."""
    entry = _work_entry(_render(template, own=2, project_bullets=[1]))
    assert "entry-atomic" in entry[:entry.index(">")]
    assert 'class="project-item project-atomic"' in entry


@pytest.mark.parametrize("template", TEMPLATES)
def test_a_long_roles_tail_opens_when_projects_follow(template):
    entry = _work_entry(_render(template, own=5, project_bullets=[2]))
    assert "entry-tail entry-tail-open" in entry
    no_projects = _work_entry(_render(template, own=5, project_bullets=[]))
    assert "entry-tail-open" not in no_projects


@pytest.mark.parametrize("template", TEMPLATES)
def test_the_nested_projects_never_open_a_page_alone_in_print(template):
    html = _render(template, own=2, project_bullets=[5])
    css = html[:html.index("</style>")]
    rule = re.search(r"\.entry-projects \{([^}]*)\}", re.sub(r"\.entry-projects \{ margin-top:[^}]*\}", "", css))
    assert rule and re.search(r"(?<![-\w])break-before:\s*avoid", rule.group(1)), template
    keep = re.search(r"\.project-atomic,\s*\.project-lead \{([^}]*)\}", css)
    assert keep and "break-inside: avoid" in keep.group(1), template
    assert re.search(r"\.entry-tail\.entry-tail-open \{[^}]*break-inside: auto", css), template

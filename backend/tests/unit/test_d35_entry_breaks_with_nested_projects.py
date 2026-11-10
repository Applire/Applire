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
foot gap 43 % → 23 %), v2 4 → 3 (43 % → 5 %), rep c's projects no longer open a page.

RU-3 / ruling P-4 (Hazelnut build 8) amends two clauses. With 50–78-word bullets
(the founder's re-UAT CV) every keep-together unit was ~10 lines tall, and the
Kreativ template left ~0.8 of a page blank over 5 pages:
- "short" counts text as well: atomic only at ≤ 3 bullets AND ≤ ``atomic_chars``;
- a role that is not short keeps its head + FIRST own bullet together;
- a lead/tail/project lead holding long bullets (``lead-long``) is no longer one
  unit, a bullet of 6+ lines may break with ≥ 3 lines per page (orphans/widows 3);
- D-35 clause 4 stays whole (ruling P-4): the nested projects never open a page
  alone; the project name keeps with its first lines.
The real-render proof is ``tests/ats/test_page_fill_ru3.py``."""
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
    """D-35 clause 4, kept whole by ruling P-4: the projects never open a page alone;
    the head parts (incl. the project name) keep with their first lines (RU-3)."""
    html = _render(template, own=2, project_bullets=[5])
    css = html[:html.index("</style>")]
    rule = re.search(r"\.entry-projects \{([^}]*)\}", re.sub(r"\.entry-projects \{ margin-top:[^}]*\}", "", css))
    assert rule and re.search(r"(?<![-\w])break-before:\s*avoid", rule.group(1)), template
    keep = re.search(r"\.project-atomic,\s*\.project-lead \{([^}]*)\}", css)
    assert keep and "break-inside: avoid" in keep.group(1), template
    assert re.search(r"\.entry-tail\.entry-tail-open \{[^}]*break-inside: auto", css), template
    heads = re.search(r"\.entry-header,\s*\.entry-title,\s*\.entry-subtitle,\s*\.entry-facts,\s*\.project-name \{([^}]*)\}", css)
    assert heads and re.search(r"(?<![-\w])break-after:\s*avoid", heads.group(1)), template


@pytest.mark.parametrize("template", TEMPLATES)
def test_a_long_bullet_may_break_with_three_lines_each_side(template):
    css = _render(template, own=1, project_bullets=[])
    css = css[:css.index("</style>")]
    rule = re.search(r"\.section \.entry-bullets li \{([^}]*)\}", css)
    assert rule, template
    body = rule.group(1)
    assert "break-inside: auto" in body and "orphans: 3" in body and "widows: 3" in body, template
    lead = re.search(r"\.entry-lead\.lead-long,\s*\.entry-tail\.lead-long,\s*\.project-lead\.lead-long \{([^}]*)\}", css)
    assert lead and "break-inside: auto" in lead.group(1), template


L = ("Verantwortete Planung, Steuerung und Abnahme eines bereichsübergreifenden Vorhabens mit "
     "messbarem Ergebnis für Fachbereiche, Betrieb und externe Partner {n}")  # ~150 chars


def _render_long(template: str, own: int, project_bullets: list[int], text: str = L) -> str:
    projects = [TailoredProjectEntry(name=f"Vorhaben {i}", bullets=[text.format(n=100 + 10 * i + j) for j in range(n)])
                for i, n in enumerate(project_bullets)]
    cv = TailoredCVData(
        contact=TailoredContact(name="Erika Muster", location="Köln"), summary="Qualitätsingenieurin.",
        skills=["SPC"], show_photo=False,
        work_history=[TailoredWorkEntry(id="w0", company="Firma GmbH", role="Schichtleiterin", start_date="2019-05",
                                        end_date="2023-11", bullets=[text.format(n=j) for j in range(own)], projects=projects)],
    )
    return _jinja_env.get_template(_TEMPLATE_FILES[template]).render(
        cv=cv, color=_make_color_context("#2b5fa8"), lang="de", labels=cv_labels("de"))


@pytest.mark.parametrize("template", TEMPLATES)
def test_a_three_bullet_role_with_long_text_is_not_one_unit(template):
    """RU-3: three ~150-char bullets (450 chars) exceed atomic_chars — the role breaks
    after its head + first bullet; three short bullets stay one unit (#622)."""
    entry = _work_entry(_render_long(template, own=3, project_bullets=[]))
    assert "entry-atomic" not in entry[:entry.index(">")], template
    lead = entry[entry.index('class="entry-lead'):]
    lead = lead[:lead.index("</ul>")]
    assert lead.count("<li") == 1, f"{template}: the lead holds the head + FIRST bullet only"
    short = _work_entry(_render(template, own=3, project_bullets=[]))
    assert "entry-atomic" in short[:short.index(">")], template


@pytest.mark.parametrize("template", TEMPLATES)
def test_a_lead_or_project_with_a_long_bullet_is_marked_long(template):
    very = L + " " + L  # ~300+ chars: one bullet of ~6 lines in a narrow column
    entry = _work_entry(_render_long(template, own=1, project_bullets=[1], text=very))
    assert 'class="entry-lead lead-long"' in entry, template
    assert "project-atomic" not in entry, f"{template}: a one-bullet project with a long bullet may break"
    plain = _work_entry(_render(template, own=2, project_bullets=[5]))
    assert "lead-long" not in plain, f"{template}: short bullets keep their units"

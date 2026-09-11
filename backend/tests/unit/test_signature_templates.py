# Copyright (C) 2026 Tobias Rosenbaum
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

"""#F (frontend/signature), Nougat build 2 — pins the signature-image seam
rendered into all 14 Jinja document templates (7 CV, 7 cover letter).

``services/cv.py::get_cv_html`` and ``services/cover_letter.py::get_cover_letter_html``
now pass ``signature_image`` (a ``data:image/png;base64,...`` URI or ``None``) to
every render, and ``signature_image``/``signature_place_date`` to every CV render.
This suite is pure Jinja: no Chromium, no Playwright, no Docker — it renders each
template directly with a minimal context and asserts on the resulting string.

Every environment goes through ``applire.templates.filters.build_template_env`` —
the one factory for an Applire Jinja environment (guarded tree-wide by
``tests/unit/test_dach_conventions.py::test_the_factory_is_the_only_way_an_environment_is_built``,
which this file does not duplicate).
"""
from pathlib import Path

import pytest

from applire.templates.filters import build_template_env
from applire.templates.labels import cover_letter_labels, cv_labels

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "applire" / "templates"

# Explicit tuples, not a glob — a 15th template that never wires the signature
# seam must fail the count/parity assertion below instead of silently
# dropping out of the parametrised sweep.
CV_TEMPLATES = (
    "lebenslauf.html.j2",
    "modern_swiss.html.j2",
    "executive.html.j2",
    "tech_developer.html.j2",
    "creative_sidebar.html.j2",
    "academic.html.j2",
    "compact_pro.html.j2",
)
LETTER_TEMPLATES = (
    "lebenslauf_letter.html.j2",
    "modern_swiss_letter.html.j2",
    "executive_letter.html.j2",
    "tech_developer_letter.html.j2",
    "creative_sidebar_letter.html.j2",
    "academic_letter.html.j2",
    "compact_pro_letter.html.j2",
)
ALL_TEMPLATES = CV_TEMPLATES + LETTER_TEMPLATES

SENTINEL_URI = "data:image/png;base64,iVBORTESTSENTINELxxxxxxxxxxxxxxxxxxxxxxxxxx=="


def test_fourteen_templates_are_all_on_disk_and_enumerated():
    """7 CV + 7 letter = 14. If an 8th template joins either family without
    being added to these tuples, this catches the drift instead of the sweep
    below silently covering only 13 of 14."""
    assert len(CV_TEMPLATES) == 7
    assert len(LETTER_TEMPLATES) == 7
    assert len(ALL_TEMPLATES) == 14
    on_disk = {p.name for p in TEMPLATES_DIR.glob("*.html.j2")}
    assert set(ALL_TEMPLATES) == on_disk, (
        "template directory drifted from the pinned tuples — "
        f"only on disk: {on_disk - set(ALL_TEMPLATES)}; "
        f"only in tuples: {set(ALL_TEMPLATES) - on_disk}"
    )


@pytest.fixture(scope="module")
def env():
    return build_template_env(TEMPLATES_DIR)


MINIMAL_CV = {
    "contact": {
        "name": "Erika Musterfrau",
        "location": "Berlin",
        "phone": "+49 30 1234567",
        "email": "erika@example.com",
        "linkedin": None,
        "photo_url": None,
    },
    "show_photo": False,
    "summary": "Erfahrene Softwareentwicklerin mit Schwerpunkt Backend.",
    "work_history": [
        {
            "role": "Senior Developerin",
            "company": "Beispiel GmbH",
            "start_date": "2020-01",
            "end_date": None,
            "team_size": None,
            "budget_managed": None,
            "industry_context": None,
            "bullets": ["Leitete das Backend-Team.", "Baute die Zahlungs-API."],
            "projects": [],
        }
    ],
    "projects": [],
    "education": [
        {
            "degree": "M.Sc. Informatik",
            "field": None,
            "institution": "TU Berlin",
            "start_date": "2015-09",
            "end_date": "2019-07",
        }
    ],
    "certifications": [],
    "skills": ["Python", "SQL", "Docker"],
    "languages": [{"language": "Deutsch", "level": "Muttersprache"}],
}

MINIMAL_COLOR = {
    "primary": "#12233E",
    "primary_tint": "#e8ecf3",
    "surface": "#12233E",
    "surface_text": "#ffffff",
    "secondary": "#12233E",
    "accent": "#12233E",
    "tint": "#e8ecf3",
}

MINIMAL_LETTER = {
    "header": {
        "name": "Erika Musterfrau",
        "address": "Musterstraße 1, 10115 Berlin",
        "phone": "+49 30 1234567",
        "email": "erika@example.com",
    },
    "recipient": {
        "name": "Herr Fenrich",
        "title": "Personalleiter",
        "company": "Zielfirma GmbH",
        "address": "Zielstraße 2, 10117 Berlin",
        "date": "11. September 2026",
    },
    "body": {
        "paragraphs": [
            "Mit großem Interesse bewerbe ich mich auf die ausgeschriebene Stelle.",
            "Meine Erfahrung als Senior Developerin passt gut zu Ihren Anforderungen.",
        ]
    },
    "signature": {
        "closing": "Mit freundlichen Grüßen",
        "name": "Erika Musterfrau",
    },
}


def render_cv(env, name, *, signature_image="__unset__", signature_place_date="__unset__"):
    """``"__unset__"`` (the default) means "key absent from the context
    entirely" — the old-call-site case. Pass ``None`` explicitly to test the
    toggle-off case."""
    tmpl = env.get_template(name)
    ctx = dict(cv=MINIMAL_CV, color=MINIMAL_COLOR, lang="de", labels=cv_labels("de"))
    if signature_image != "__unset__":
        ctx["signature_image"] = signature_image
    if signature_place_date != "__unset__":
        ctx["signature_place_date"] = signature_place_date
    return tmpl.render(**ctx)


def render_letter(env, name, *, signature_image="__unset__"):
    tmpl = env.get_template(name)
    ctx = dict(
        letter=MINIMAL_LETTER,
        color=MINIMAL_COLOR,
        lang="de",
        labels=cover_letter_labels("de"),
        subject="Bewerbung als Senior Developerin",
    )
    if signature_image != "__unset__":
        ctx["signature_image"] = signature_image
    return tmpl.render(**ctx)


# ── 1. the sentinel URI appears exactly once when signature_image is set ───


@pytest.mark.parametrize("name", CV_TEMPLATES)
def test_cv_signature_image_renders_once(env, name):
    out = render_cv(
        env, name, signature_image=SENTINEL_URI, signature_place_date="Berlin, 11. September 2026"
    )
    assert out.count(SENTINEL_URI) == 1
    assert out.count("Berlin, 11. September 2026") == 1


@pytest.mark.parametrize("name", LETTER_TEMPLATES)
def test_letter_signature_image_renders_once(env, name):
    out = render_letter(env, name, signature_image=SENTINEL_URI)
    assert out.count(SENTINEL_URI) == 1


# ── 2. None behaves EXACTLY like "the variable is absent from the context" ─
#
# NOTE: the bare substrings "signature-image" / "signature-block" /
# "has-signature" also occur unconditionally in each template's <style>
# block (the CSS *selectors*, e.g. ".signature-image { ... }" and
# ".signature.has-signature .closing { ... }") — those exist regardless of
# whether an image is ever rendered. The markers below match the actual
# RENDERED markup only: an opening tag / a space-separated class value,
# neither of which appears in the CSS.

IMG_TAG_MARKER = '<img class="signature-image"'
CV_BLOCK_MARKER = '<div class="signature-block">'
HAS_SIGNATURE_CLASS_MARKER = 'signature has-signature"'


@pytest.mark.parametrize("name", CV_TEMPLATES)
def test_cv_signature_none_is_byte_identical_to_absent(env, name):
    rendered_none = render_cv(env, name, signature_image=None, signature_place_date=None)
    rendered_absent = render_cv(env, name)  # neither key in the context at all
    assert IMG_TAG_MARKER not in rendered_none
    assert CV_BLOCK_MARKER not in rendered_none
    assert rendered_none == rendered_absent


@pytest.mark.parametrize("name", LETTER_TEMPLATES)
def test_letter_signature_none_is_byte_identical_to_absent(env, name):
    rendered_none = render_letter(env, name, signature_image=None)
    rendered_absent = render_letter(env, name)  # key absent from the context at all
    assert IMG_TAG_MARKER not in rendered_none
    assert rendered_none == rendered_absent


# ── 3. letters only: the image sits BETWEEN the closing and the name ───────


@pytest.mark.parametrize("name", LETTER_TEMPLATES)
def test_letter_signature_image_between_closing_and_name(env, name):
    out = render_letter(env, name, signature_image=SENTINEL_URI)
    closing_idx = out.index(MINIMAL_LETTER["signature"]["closing"])
    image_idx = out.index(SENTINEL_URI)
    # search for the printed name AFTER the image — the same string also
    # appears earlier, in the letter header.
    name_idx = out.index(MINIMAL_LETTER["signature"]["name"], image_idx)
    assert closing_idx < image_idx < name_idx


# ── 4. has-signature appears on the wrapper iff an image was rendered ──────


@pytest.mark.parametrize("name", LETTER_TEMPLATES)
def test_letter_has_signature_class_only_with_image(env, name):
    with_image = render_letter(env, name, signature_image=SENTINEL_URI)
    without_image = render_letter(env, name, signature_image=None)
    assert HAS_SIGNATURE_CLASS_MARKER in with_image
    assert HAS_SIGNATURE_CLASS_MARKER not in without_image

"""
Parametrized smoke test: every registered template must render without
Jinja2 errors given a minimal TailoredCVData fixture.

Run: pytest tests/unit/test_template_render.py -v
"""
import sys
from pathlib import Path

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


@pytest.fixture(scope="module")
def minimal_cv():
    from applire.schemas.cv import (
        TailoredCVData, TailoredContact, TailoredWorkEntry,
        TailoredEducationEntry, TailoredLanguage,
    )
    return TailoredCVData(
        contact=TailoredContact(
            name="Anna Musterfrau",
            email="anna@example.com",
            phone="+49 89 123456",
            location="München",
            linkedin="linkedin.com/in/anna",
            photo_url=None,
        ),
        summary="Erfahrene Managerin mit Fokus auf digitale Transformation.",
        work_history=[
            TailoredWorkEntry(
                company="Beispiel GmbH",
                role="Head of Product",
                start_date="2020",
                end_date=None,
                bullets=["Aufbau des Produktteams", "Einführung agiler Methoden"],
            )
        ],
        skills=["Python", "Agile", "Stakeholder Management"],
        education=[
            TailoredEducationEntry(
                institution="LMU München",
                degree="MBA",
                field="Betriebswirtschaft",
                start_date="2014",
                end_date="2016",
            )
        ],
        languages=[TailoredLanguage(language="Deutsch", level="Muttersprache")],
        show_photo=False,
    )


@pytest.fixture(scope="module")
def minimal_color():
    from applire.services.color_detection import _make_color_context
    return _make_color_context("#2b5fa8")


@pytest.fixture(scope="module")
def jinja_env():
    from applire.templates.filters import build_template_env
    templates_dir = _backend / "applire" / "templates"
    # #307: the ONE factory — a hand-rolled Environment misses shared filters.
    return build_template_env(templates_dir)


ALL_TEMPLATES = [
    ("classic_german", "lebenslauf.html.j2"),
    ("modern_swiss", "modern_swiss.html.j2"),
    ("executive", "executive.html.j2"),
    ("tech_developer", "tech_developer.html.j2"),
    ("creative_sidebar", "creative_sidebar.html.j2"),
    ("academic", "academic.html.j2"),
    ("compact_pro", "compact_pro.html.j2"),
]


@pytest.mark.parametrize("template_key,template_file", ALL_TEMPLATES)
def test_template_renders_without_error(
    template_key, template_file, jinja_env, minimal_cv, minimal_color
):
    """Each template must render to a non-empty HTML string with no Jinja2 errors."""
    from applire.templates.labels import cv_labels
    template = jinja_env.get_template(template_file)
    html = template.render(cv=minimal_cv, color=minimal_color, lang="de", labels=cv_labels("de"))
    assert html, f"{template_key}: rendered HTML is empty"
    assert "Anna Musterfrau" in html, f"{template_key}: contact name missing from output"
    assert "Beispiel GmbH" in html, f"{template_key}: work history missing from output"


@pytest.mark.parametrize("template_key,template_file", ALL_TEMPLATES)
def test_template_uses_color_variables(template_key, template_file, jinja_env, minimal_color):
    """Rendered HTML must contain the primary colour hex value."""
    template = jinja_env.get_template(template_file)
    from applire.schemas.cv import TailoredCVData, TailoredContact
    from applire.templates.labels import cv_labels
    cv = TailoredCVData(contact=TailoredContact(name="Test", location="Berlin"), show_photo=False)
    html = template.render(cv=cv, color=minimal_color, lang="de", labels=cv_labels("de"))
    assert "#2b5fa8" in html, f"{template_key}: primary colour not found in rendered HTML"


# ---------------------------------------------------------------------------
# #634 — autoescape was off on every template
#
# ``select_autoescape(["html"])`` matches on the template filename's suffix; the
# shipped templates are all named ``*.html.j2``, so the guard never fired and
# free text containing angle brackets was emitted into the HTML verbatim.
# Chromium then swallowed it as an unknown tag and the phrase was missing from
# the delivered PDF — while the ADR-039 audit passed, because it never reads
# bullet or summary text.
#
# ``tests/ats/test_autoescape_634.py`` pins the delivered artefact, but that
# suite skips itself when Chromium is unavailable. These three run in every job
# and pin the cause.
# ---------------------------------------------------------------------------

BRACKET_TEXT = "Koordination mit <Projekt Phoenix> und R&D-Teams beim Rollout."


@pytest.fixture(scope="module")
def bracket_cv():
    from applire.schemas.cv import (
        TailoredCVData, TailoredContact, TailoredWorkEntry,
    )
    return TailoredCVData(
        contact=TailoredContact(name="Anna Musterfrau", email="anna@example.com"),
        summary="Head of Product mit Schwerpunkt <Digitale Fertigung>.",
        work_history=[
            TailoredWorkEntry(
                company="Beispiel GmbH",
                role="Head of Product",
                start_date="2020",
                end_date=None,
                bullets=[BRACKET_TEXT],
            )
        ],
        show_photo=False,
    )


def test_autoescape_does_not_depend_on_the_template_filename(jinja_env):
    """#634's class: a suffix-matching default is silently off under our naming.

    Asserting the *shape* rather than the outcome for one name — a callable here
    means escaping is decided per filename again, which is exactly what let
    ``*.html.j2`` slip through.
    """
    assert jinja_env.autoescape is True, (
        "template autoescape must be unconditional — a filename-dependent "
        "policy is what #634 was"
    )


@pytest.mark.parametrize("template_key,template_file", ALL_TEMPLATES)
def test_angle_brackets_in_free_text_are_escaped(
    template_key, template_file, jinja_env, bracket_cv, minimal_color
):
    """The reported loss: the phrase must reach the HTML as text, not as a tag."""
    from applire.templates.labels import cv_labels
    html = jinja_env.get_template(template_file).render(
        cv=bracket_cv, color=minimal_color, lang="de", labels=cv_labels("de")
    )
    assert "&lt;Projekt Phoenix&gt;" in html, f"{template_key}: bullet not escaped"
    assert "&lt;Digitale Fertigung&gt;" in html, f"{template_key}: summary not escaped"
    assert "<Projekt Phoenix>" not in html, f"{template_key}: raw tag still emitted"


@pytest.mark.parametrize("template_key,template_file", ALL_TEMPLATES)
def test_template_markup_is_not_escaped_into_visible_text(
    template_key, template_file, jinja_env, bracket_cv, minimal_color
):
    """The other direction. ``{% set head %}`` captures rendered markup into a
    variable; under autoescape Jinja marks that capture as ``Markup``, so
    ``{{ head }}`` must still emit real tags. If it were escaped instead, the
    candidate's CV would print its own ``<div>`` scaffolding."""
    from applire.templates.labels import cv_labels
    html = jinja_env.get_template(template_file).render(
        cv=bracket_cv, color=minimal_color, lang="de", labels=cv_labels("de")
    )
    assert "&lt;div" not in html, f"{template_key}: template markup was escaped"
    assert "&amp;lt;" not in html, f"{template_key}: text was escaped twice"

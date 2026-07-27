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

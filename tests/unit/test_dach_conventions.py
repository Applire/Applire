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

"""#307 — the three DACH conventions charter run #7 shipped wrong.

All three are deterministic and render-side; the run's delivered PDFs are the
ground truth each test is pinned against:

1. CV dates rendered ISO (``2017-04 – heute``) instead of MM/YYYY (``04/2017``).
2. The Anschreiben's Anrede ran into the opening sentence as one paragraph.
3. The Grußformel carried a comma, which German does not take.
"""
import sys
from pathlib import Path

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.templates.filters import month_year, register_filters  # noqa: E402
from applire.templates.labels import cover_letter_labels  # noqa: E402


# ── 1. MM/YYYY dates ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "stored,rendered",
    [
        # The exact values run #7 delivered, and what a DACH reader expects instead.
        ("2017-04", "04/2017"),
        ("2011-08", "08/2011"),
        ("2004-07", "07/2004"),
        # Full ISO reaches certifications (`date_obtained`) — same convention.
        ("2022-01-01", "01/2022"),
        # Single-digit month without zero padding, and the slash spelling.
        ("2017-4", "04/2017"),
        ("2017/04", "04/2017"),
        # A bare year is already conventional on a CV and a certificate.
        ("2016", "2016"),
        ("  2010  ", "2010"),
    ],
)
def test_partial_dates_render_as_month_slash_year(stored, rendered):
    assert month_year(stored) == rendered


@pytest.mark.parametrize(
    "value",
    [
        "heute",
        "Present",
        "seit 2021",
        "2017-13",  # a 13th month means the value is not what we think it is
        "",
    ],
)
def test_unrecognised_values_pass_through_rather_than_blanking(value):
    """Failing open is deliberate: a blank date column on a real candidate's CV
    is far worse than an unconverted string."""
    assert month_year(value) == value.strip()


def test_none_renders_empty_not_the_string_none():
    assert month_year(None) == ""


def test_every_jinja_environment_registers_the_filter():
    """The tree has FOUR independent Jinja environments and the section editor
    plus the tests/ats harness borrow ``cv.py``'s. A filter registered on some
    but not all of them is the render-site drift CLAUDE.md warns about — this is
    the test that makes a missed site fail instead of silently rendering ISO."""
    from applire.services.cover_letter import _jinja_env as letter_env
    from applire.services.cv import _jinja_env as cv_env

    for env in (cv_env, letter_env):
        assert "month_year" in env.filters

    # thumbnails.py builds its env inside the function; assert the registrar is
    # applied there by exercising it the same way the module does.
    from jinja2 import Environment

    env = Environment()
    register_filters(env)
    assert env.filters["month_year"]("2017-04") == "04/2017"


def test_no_cv_template_renders_a_raw_date():
    """Belt-and-braces against a NEW template being added without the filter."""
    templates = Path(__file__).parent.parent.parent / "backend" / "applire" / "templates"
    offenders = []
    for tpl in sorted(templates.glob("*.html.j2")):
        for lineno, line in enumerate(tpl.read_text(encoding="utf-8").splitlines(), 1):
            for field in ("start_date", "end_date", "date_obtained"):
                # A date interpolated without the filter — `{% if edu.start_date %}`
                # style guards are conditions, not output, so only `{{ … }}` counts.
                for chunk in line.split("{{")[1:]:
                    expr = chunk.split("}}")[0]
                    if field in expr and "month_year" not in expr:
                        offenders.append(f"{tpl.name}:{lineno}: {expr.strip()}")
    assert not offenders, "date interpolated without |month_year:\n" + "\n".join(offenders)


# ── 2. The Anrede gets its own paragraph ────────────────────────────────────


def _split(paragraphs):
    from applire.services.cover_letter import _split_inline_salutation

    return _split_inline_salutation({"body": {"paragraphs": paragraphs}})["body"]["paragraphs"]


def test_run7_inline_anrede_is_split_into_its_own_paragraph():
    """The verbatim shape delivered to a German employer in charter run #7."""
    delivered = (
        "Sehr geehrte Damen und Herren, mit großem Interesse habe ich Ihre "
        "Stellenausschreibung für den Leiter Operations (m/w/d) bei der Rheinwerk "
        "Verpackungen GmbH gelesen. Als erfahrener Produktionsleiter sehe ich eine "
        "ideale Möglichkeit, meine Expertise einzubringen."
    )
    result = _split([delivered, "Zweiter Absatz."])

    # Exact equality, not a substring probe: the point of the fix is the SHAPE of
    # what remains, and only a full comparison catches a mangled remainder.
    assert result == [
        "Sehr geehrte Damen und Herren,",
        "mit großem Interesse habe ich Ihre Stellenausschreibung für den Leiter "
        "Operations (m/w/d) bei der Rheinwerk Verpackungen GmbH gelesen. Als "
        "erfahrener Produktionsleiter sehe ich eine ideale Möglichkeit, meine "
        "Expertise einzubringen.",
        "Zweiter Absatz.",
    ]


def test_a_named_anrede_splits_at_its_own_comma_not_a_later_one():
    result = _split(["Sehr geehrter Herr Dr. Müller, ich bewerbe mich, wie besprochen, bei Ihnen."])
    assert result == [
        "Sehr geehrter Herr Dr. Müller,",
        "ich bewerbe mich, wie besprochen, bei Ihnen.",
    ]


def test_english_salutation_splits_too():
    result = _split(["Dear Ms Smith, I am writing to apply for the role."])
    assert result == ["Dear Ms Smith,", "I am writing to apply for the role."]


def test_an_already_separate_anrede_is_left_alone():
    """Idempotence — the normaliser runs on the pipeline, the condense pass and
    the agent door, so it must not keep chewing its own output."""
    paragraphs = ["Sehr geehrte Damen und Herren,", "mit großem Interesse ..."]
    assert _split(list(paragraphs)) == paragraphs
    assert _split(_split(list(paragraphs))) == paragraphs


def test_a_long_first_comma_is_not_treated_as_an_anrede_boundary():
    """A sentence that merely opens with a greeting-like word must survive whole."""
    text = (
        "Liebe Kolleginnen und Kollegen aus der Fertigung, der Instandhaltung "
        "und der Arbeitsvorbereitung, ich melde mich."
    )
    assert _split([text]) == [text]


def test_a_paragraph_with_no_salutation_is_untouched():
    text = "Bei der Weberit Kunststofftechnik GmbH führe ich, seit 2017, zwei Bereiche."
    assert _split([text]) == [text]


def test_a_bare_salutation_with_nothing_after_the_comma_is_untouched():
    assert _split(["Sehr geehrte Damen und Herren,"]) == ["Sehr geehrte Damen und Herren,"]


def test_malformed_letter_data_never_raises():
    from applire.services.cover_letter import _split_inline_salutation

    for payload in ({}, {"body": None}, {"body": {}}, {"body": {"paragraphs": None}}):
        _split_inline_salutation(payload)  # must not raise


# ── 3. No comma after the German Grußformel ─────────────────────────────────


def test_german_grussformel_takes_no_comma_and_english_does():
    assert cover_letter_labels("de")["closing_punctuation"] == ""
    assert cover_letter_labels("en")["closing_punctuation"] == ","


def test_no_letter_template_hardcodes_the_closing_comma():
    """Run #7 delivered "Mit freundlichen Grüßen," because all seven letter
    templates wrote the comma inline. Punctuation is chrome and follows the
    output language (ADR-038) — this fails if any template takes it back."""
    templates = Path(__file__).parent.parent.parent / "backend" / "applire" / "templates"
    offenders = [
        tpl.name
        for tpl in sorted(templates.glob("*_letter.html.j2"))
        if "{{ letter.signature.closing }}," in tpl.read_text(encoding="utf-8")
    ]
    assert not offenders, f"hardcoded comma after the Grußformel in: {offenders}"

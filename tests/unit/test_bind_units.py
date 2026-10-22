# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#786 (Hazelnut build 3, WP-L): a figure and its unit never break across a line.

Marcus's letter broke "96 %" between "96" and "%" in justified text (UX audit
2026-10-20). The binding is one rule (``templates.filters.bind_units``) applied
at the two document sinks: every ``{{ … }}`` of the Jinja environment (PDF/HTML,
CV and letter) and the DOCX text sink (``office_export._common.xml_safe``).
"""
from pathlib import Path

from markupsafe import Markup

from applire.services.office_export._common import xml_safe
from applire.templates.filters import bind_units, build_template_env

NBSP = " "


def test_bind_units_binds_figure_and_unit_only():
    assert bind_units("von 87 % auf 96 %") == f"von 87{NBSP}% auf 96{NBSP}%"
    assert bind_units("ca. 6 Mio. € Budget") == f"ca. 6{NBSP}Mio.{NBSP}€ Budget"
    assert bind_units("12 €") == f"12{NBSP}€"
    # Not a unit: ordinary words and counts keep their breakable space.
    assert bind_units("2 Seiten, 100 Mitarbeitende") == "2 Seiten, 100 Mitarbeitende"
    assert bind_units("5 Mio.x") == "5 Mio.x"
    assert bind_units(42) == 42


def test_bind_units_keeps_markup_safe():
    out = bind_units(Markup("<b>9 %</b>"))
    assert isinstance(out, Markup) and out == Markup(f"<b>9{NBSP}%</b>")


def test_template_env_binds_every_output(tmp_path: Path):
    """Seam 1: the environment every CV and letter template is rendered with."""
    (tmp_path / "t.html.j2").write_text("<p>{{ text }}</p>", encoding="utf-8")
    env = build_template_env(tmp_path)
    html = env.get_template("t.html.j2").render(text="Termintreue von 87 % auf 96 %")
    assert f"87{NBSP}%" in html and f"96{NBSP}%" in html and "87 %" not in html


def test_production_letter_template_binds_the_unit():
    """Seam 1 on a real letter template (no Chromium: HTML only)."""
    from applire.services.cover_letter import _TEMPLATES_DIR

    env = build_template_env(_TEMPLATES_DIR)
    out = env.from_string("{{ p }}").render(p="Ausschussquote von 4,1 % auf 2,3 % gesenkt")
    assert f"4,1{NBSP}%" in out and f"2,3{NBSP}%" in out


def test_docx_text_sink_binds_the_unit():
    """Seam 2: the DOCX text sink every paragraph/run goes through."""
    assert xml_safe("Termintreue 96 %") == f"Termintreue 96{NBSP}%"
    assert xml_safe(None) is None

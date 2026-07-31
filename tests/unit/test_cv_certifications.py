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

"""PQ F7 — certifications must appear in the tailored CV.

Blind PQ found: a profile with certifications (incl. a DIRECT JD match) produced a
tailored CV with no certifications section at all — certifications were never part
of the tailored-CV data model (spec-to-code gap from E036).

Design decision (binding, ADR-040 truthfulness): certifications are FACTUAL data,
like contact info — they are copied verbatim from the master profile into
``tailored_data.certifications`` by deterministic code AFTER the LLM step(s), never
routed through an LLM JSON schema. These tests pin the schema extension, the
deterministic code-side passthrough step, and the template rendering.
"""
import sys
from pathlib import Path

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


# ---------------------------------------------------------------------------
# Schema: TailoredCVData must carry a certifications list.
# ---------------------------------------------------------------------------


def test_tailored_certification_carries_name_and_issuer_and_dates():
    from applire.schemas.cv import TailoredCertification

    cert = TailoredCertification(
        name="Lead Auditor ISO 9001",
        issuing_organization="TÜV Süd",
        date_obtained="2021-05",
        expiry_date="2027-05",
    )
    assert cert.name == "Lead Auditor ISO 9001"
    assert cert.issuing_organization == "TÜV Süd"
    assert cert.date_obtained == "2021-05"
    assert cert.expiry_date == "2027-05"


def test_tailored_cv_data_has_certifications_field():
    from applire.schemas.cv import TailoredCVData, TailoredContact, TailoredCertification

    cv = TailoredCVData(
        contact=TailoredContact(name="X"),
        certifications=[TailoredCertification(name="Lead Auditor ISO 9001")],
    )
    assert cv.certifications[0].name == "Lead Auditor ISO 9001"


def test_tailored_cv_data_defaults_no_certifications():
    """Back-compat: legacy tailored_data without a certifications field still
    validates, with an empty certifications list."""
    from applire.schemas.cv import TailoredCVData

    cv = TailoredCVData.model_validate(
        {
            "contact": {"name": "Legacy"},
            "work_history": [
                {"company": "Old Co", "role": "Dev", "start_date": "2015"}
            ],
        }
    )
    assert cv.certifications == []


# ---------------------------------------------------------------------------
# Deterministic passthrough: _apply_certifications copies the master profile's
# certifications verbatim into tailored_data.certifications, no LLM involvement.
# ---------------------------------------------------------------------------


def _tailored_minimal():
    from applire.schemas.cv import TailoredCVData, TailoredContact

    return TailoredCVData(contact=TailoredContact(name="Anna"))


def test_apply_certifications_copies_all_profile_certifications_verbatim():
    from applire.services.cv import _apply_certifications

    profile_json = {
        "certifications": [
            {
                "name": "Lead Auditor ISO 9001",
                "issuing_organization": "TÜV Süd",
                "date_obtained": "2021-05-01",
                "expiry_date": "2027-05-01",
            },
            {
                "name": "PMP",
                "issuing_organization": "PMI",
                "date_obtained": "2019-01-01",
            },
            {"name": "Scrum Master I"},
        ]
    }
    tailored = _apply_certifications(_tailored_minimal(), profile_json)

    assert [c.name for c in tailored.certifications] == [
        "Lead Auditor ISO 9001",
        "PMP",
        "Scrum Master I",
    ]
    assert tailored.certifications[0].issuing_organization == "TÜV Süd"


def test_apply_certifications_noop_when_profile_has_none():
    from applire.services.cv import _apply_certifications

    tailored = _apply_certifications(_tailored_minimal(), {"certifications": []})
    assert tailored.certifications == []


def test_apply_certifications_noop_when_key_absent():
    """A legacy profile with no `certifications` key at all must not raise."""
    from applire.services.cv import _apply_certifications

    tailored = _apply_certifications(_tailored_minimal(), {})
    assert tailored.certifications == []


def test_apply_certifications_does_not_mutate_input():
    from applire.services.cv import _apply_certifications

    original = _tailored_minimal()
    profile_json = {"certifications": [{"name": "Lead Auditor ISO 9001"}]}
    result = _apply_certifications(original, profile_json)

    assert original.certifications == []
    assert result.certifications[0].name == "Lead Auditor ISO 9001"


# ---------------------------------------------------------------------------
# E049/ADR-067: assemble_segmented_cv is DELETED — assembly is now the ONE
# assemble_tailored_cv both generation paths share (ADR-066). Certifications
# are NOT joined there either (see assemble_tailored_cv's own docstring):
# _apply_certifications, called by the caller after assembly (mirroring
# _nest_projects), remains their one writer, so the assembled dict validates
# cleanly with certifications defaulting empty (no LLM section writer is ever
# asked to produce them, on either generation path).
# ---------------------------------------------------------------------------


def test_assembled_cv_validates_with_empty_certifications():
    from applire.schemas.cv import TailoredCVData
    from applire.services.cv import assemble_tailored_cv

    prose = {"summary": "", "work": [], "skills": []}
    profile_json = {"personal_info": {"name": "Anna"}}
    cv = TailoredCVData.model_validate(assemble_tailored_cv(prose, profile_json))
    assert cv.certifications == []


# ---------------------------------------------------------------------------
# Templates: every CV template renders certifications (name + issuing org),
# adjacent to education, and OMITS the section entirely when there are none.
# ---------------------------------------------------------------------------

ALL_TEMPLATES = [
    "lebenslauf.html.j2",
    "modern_swiss.html.j2",
    "executive.html.j2",
    "tech_developer.html.j2",
    "creative_sidebar.html.j2",
    "academic.html.j2",
    "compact_pro.html.j2",
]


@pytest.fixture(scope="module")
def jinja_env():
    from applire.templates.filters import build_template_env

    templates_dir = _backend / "applire" / "templates"
    # #307: the ONE factory — a hand-rolled Environment misses shared filters.
    return build_template_env(templates_dir)


@pytest.fixture(scope="module")
def color_ctx():
    from applire.services.color_detection import _make_color_context

    return _make_color_context("#2b5fa8")


def _cv_with_certifications():
    from applire.schemas.cv import TailoredCVData, TailoredContact, TailoredCertification

    return TailoredCVData(
        contact=TailoredContact(name="Anna Bauer", location="Berlin"),
        summary="Quality engineer.",
        skills=["Python"],
        certifications=[
            TailoredCertification(
                name="Lead Auditor ISO 9001",
                issuing_organization="TÜV Süd",
                date_obtained="2021-05",
            ),
        ],
        show_photo=False,
    )


def _cv_without_certifications():
    from applire.schemas.cv import TailoredCVData, TailoredContact

    return TailoredCVData(
        contact=TailoredContact(name="Anna Bauer", location="Berlin"),
        summary="Quality engineer.",
        skills=["Python"],
        show_photo=False,
    )


@pytest.mark.parametrize("template_file", ALL_TEMPLATES)
def test_template_renders_certification_name_and_issuer(template_file, jinja_env, color_ctx):
    from applire.templates.labels import cv_labels

    cv = _cv_with_certifications()
    html = jinja_env.get_template(template_file).render(
        cv=cv, color=color_ctx, lang="de", labels=cv_labels("de")
    )
    assert "Lead Auditor ISO 9001" in html, f"{template_file}: certification name missing"
    assert "TÜV Süd" in html, f"{template_file}: issuing organization missing"


@pytest.mark.parametrize("template_file", ALL_TEMPLATES)
def test_template_omits_certifications_section_when_none(template_file, jinja_env, color_ctx):
    from applire.templates.labels import cv_labels

    cv = _cv_without_certifications()
    html = jinja_env.get_template(template_file).render(
        cv=cv, color=color_ctx, lang="de", labels=cv_labels("de")
    )
    assert "Lead Auditor ISO 9001" not in html


@pytest.mark.parametrize("template_file", ALL_TEMPLATES)
def test_template_certifications_label_localised(template_file, jinja_env, color_ctx):
    from applire.templates.labels import cv_labels

    cv = _cv_with_certifications()
    html_de = jinja_env.get_template(template_file).render(
        cv=cv, color=color_ctx, lang="de", labels=cv_labels("de")
    )
    html_en = jinja_env.get_template(template_file).render(
        cv=cv, color=color_ctx, lang="en", labels=cv_labels("en")
    )
    # Case-insensitive: tech_developer lowercases all section labels by design.
    assert "zertifizierungen" in html_de.lower(), f"{template_file}: German certifications label missing"
    assert "certifications" in html_en.lower(), f"{template_file}: English certifications label missing"

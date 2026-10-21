# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""783:L65 — a certificate dated "2010" in the source rendered as "01/2010".

Delivery run 3 (Hazelnut build 1, record 5): the extraction model emitted
``"date_obtained": "2010"``; the schema padded it to 2010-01-01 and the CV's
``month_year`` filter printed the invented month. ADR-013 amended 2026-10-20:
certificate and publication dates keep the precision the source states.
"""
from applire.schemas.cv import TailoredCVData
from applire.schemas.profile import MasterProfileData
from applire.services.cv import _apply_certifications
from applire.templates.filters import month_year


def _vault(date_obtained):
    return MasterProfileData.model_validate(
        {"certifications": [{"name": "Industriemeister Metall (IHK)", "date_obtained": date_obtained}]}
    ).model_dump(mode="json")


def test_a_year_only_certificate_renders_as_the_year_on_the_cv():
    vault = _vault("2010")
    assert vault["certifications"][0]["date_obtained"] == "2010"
    tailored = _apply_certifications(TailoredCVData(contact={"name": "Marcus Schmidt"}), vault)
    assert month_year(tailored.certifications[0].date_obtained) == "2010"


def test_a_month_precise_certificate_keeps_its_month():
    tailored = _apply_certifications(TailoredCVData(contact={"name": "Marcus Schmidt"}), _vault("03.2014"))
    assert month_year(tailored.certifications[0].date_obtained) == "03/2014"


def test_a_row_stored_before_the_amendment_loads_unchanged():
    # Residual (named in the ADR delta): an old padded row cannot be told from a
    # real 1 January and is not rewritten.
    assert _vault("2010-01-01")["certifications"][0]["date_obtained"] == "2010-01-01"

# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""ADR-052 amended 2026-10-22 (WP-T, tier C; #790 line "Oracle explanation is
engineer-speak, partly in another language"): the red-flag details the review card
shows carry a stable ``detail_key`` + ``detail_params``, so the surface says them in
the UI language. ``detail`` stays the English text for every other reader."""
import importlib.util
from pathlib import Path

import pytest

from applire.schemas.oracle import Claim
from applire.services.oracle.audit import verify_claim

_spec = importlib.util.spec_from_file_location("_e2", Path(__file__).parent / "test_e2_credential_check.py")
_e2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_e2)
VAULT = _e2._VAULT


@pytest.mark.asyncio
async def test_skill_without_evidence_has_a_key():
    v = await verify_claim(Claim(text="Kubernetes", location="skills[0]", kind="skill"), VAULT, None, document_language="de")
    assert v.verdict == "unbacked"
    assert v.detail_key == "skillNoEvidence" and v.detail_params == {"skill": "Kubernetes"}


@pytest.mark.asyncio
async def test_figure_without_evidence_has_a_key():
    v = await verify_claim(
        Claim(text="Monatsabschlüsse für 12 Gesellschaften nach HGB vorbereitet", location="work_history[0].bullets[0]",
              kind="bullet", source_experience_id="11111111-1111-1111-1111-111111111111"),
        VAULT, None, document_language="de",
    )
    assert v.verdict == "unbacked" and v.checker == "numbers"
    assert v.detail_key == "figuresNoEvidence" and v.detail_params["figures"] == "12"


@pytest.mark.asyncio
async def test_career_tenure_beyond_the_vault_has_a_key():
    v = await verify_claim(
        Claim(text="Finanzbuchhalterin mit 25 Jahren Erfahrung im Monatsabschluss.", location="summary[0]"),
        VAULT, None, document_language="de",
    )
    assert v.verdict == "unbacked"
    assert v.detail_key in {"tenureExceedsVault", "subjectTenureExceeds"}
    assert v.detail_params["claimed"]

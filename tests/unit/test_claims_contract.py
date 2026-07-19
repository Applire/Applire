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

"""E045 (US253) — the `claims/1` testimony contract (ADR-054)."""
import pytest
from pydantic import ValidationError

from applire.schemas.claims import (
    CLAIMS_SCHEMA_VERSION,
    ClaimItem,
    ClaimsSubmission,
)


def test_schema_version():
    assert CLAIMS_SCHEMA_VERSION == "claims/1"


def test_minimal_claim_is_statement_only():
    item = ClaimItem(statement="I led the Kubernetes migration at Acme in 2024.")
    assert item.question is None
    assert item.gap is None


def test_full_claim():
    item = ClaimItem(
        statement="Yes, I hold a CKA certification since 2023.",
        question="Do you hold any Kubernetes certifications?",
        gap="Kubernetes",
    )
    assert item.gap == "Kubernetes"


@pytest.mark.parametrize("statement", ["", "x" * 2001])
def test_statement_length_bounds(statement):
    with pytest.raises(ValidationError):
        ClaimItem(statement=statement)


def test_statement_max_length_ok():
    ClaimItem(statement="x" * 2000)


def test_question_and_gap_length_bounds():
    with pytest.raises(ValidationError):
        ClaimItem(statement="ok", question="q" * 501)
    with pytest.raises(ValidationError):
        ClaimItem(statement="ok", gap="g" * 201)


def test_unknown_field_forbidden_with_dotted_path():
    with pytest.raises(ValidationError) as exc:
        ClaimsSubmission.model_validate(
            {"claims": [{"statement": "ok", "operation": "upsert_skill"}]}
        )
    assert "operation" in str(exc.value)


def test_submission_bounds():
    with pytest.raises(ValidationError):
        ClaimsSubmission(claims=[])
    with pytest.raises(ValidationError):
        ClaimsSubmission(claims=[ClaimItem(statement="ok")] * 21)
    ClaimsSubmission(claims=[ClaimItem(statement="ok")] * 20)


@pytest.mark.asyncio
async def test_schema_resource_payload():
    """`schema://claims` returns {schema_version, json_schema} as JSON string
    (E044 static-resource pattern)."""
    import json

    from applire.mcp.server import resource_schema_claims

    payload = json.loads(await resource_schema_claims())
    assert payload["schema_version"] == CLAIMS_SCHEMA_VERSION
    assert payload["json_schema"] == ClaimsSubmission.model_json_schema()


def test_gap_documented_as_exact_ledger_concept():
    """The schema description must tell agents where a valid gap comes from
    (trap 4 — exact concept string from analyze_gaps output)."""
    schema = ClaimsSubmission.model_json_schema()
    gap_desc = schema["$defs"]["ClaimItem"]["properties"]["gap"]["description"]
    assert "keyword ledger" in gap_desc
    assert "analyze_gaps" in gap_desc

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

import pytest
from applire.providers.llm.mock import MockLLMProvider
from applire.schemas.profile import Conflict, MasterProfileData
from applire.services.profile.reconcile.interview_bridge import (
    _to_summary,
    reconcile_interview_turn,
)


def test_to_summary_formats_list_value_as_clean_text():
    # Bug 2 regression — a list/dict value must NOT leak a raw Python repr into
    # the user-facing ConflictSummary (no `['Yes', 'No']`).
    conflict = Conflict(
        section="",
        field="role",
        existing_value=None,
        incoming_value=["Yes, same role", "No, separate roles"],
        source="cv_upload",
    )
    summary = _to_summary(conflict)
    assert summary.new_value == "Yes, same role, No, separate roles"
    assert "[" not in summary.new_value and "'" not in summary.new_value
    # None side renders as empty, not the string "None"
    assert summary.old_value == ""


@pytest.mark.asyncio
async def test_bridge_returns_updated_dict_changes_and_progress():
    profile = {"personal_info": {"full_name": "Test User"}}
    out = await reconcile_interview_turn(
        profile_dict=profile, gap="skills", question="What do you use?",
        answer="Python daily", provider=MockLLMProvider(), session_id="s1",
    )
    # Mock emits one upsert_skill -> a mutation -> "addressed"
    assert any(s.get("name") == "Python" for s in out.profile_dict.get("skills", []))
    assert out.addressed is True
    assert out.changes  # FieldChange list, non-empty
    assert out.conflict_summaries == []
    assert out.pending_confirmations == []


@pytest.mark.asyncio
async def test_bridge_empty_ops_is_not_addressed():
    class _Empty:
        async def aparse_json(self, prompt, **kw):
            return {"ops": [], "ambiguities": []}
    out = await reconcile_interview_turn(
        profile_dict={}, gap="g", question="q", answer="a",
        provider=_Empty(), session_id="s1",
    )
    assert out.addressed is False
    assert out.changes == []


@pytest.mark.asyncio
async def test_bridge_preserves_existing_enrichment_history():
    """The dict->MasterProfileData->dict round-trip must NOT drop a pre-existing
    enrichment_history; a new record is appended to it (the one real data-loss
    vector flagged in the US182a final review)."""
    prior = {
        "timestamp": "2026-06-01T00:00:00+00:00",
        "source": "cv_upload",
        "source_session_id": "earlier",
        "changes": [],
    }
    profile = {
        "personal_info": {"full_name": "Test User"},
        "metadata": {"enrichment_history": [prior]},
    }
    out = await reconcile_interview_turn(
        profile_dict=profile, gap="skills", question="What do you use?",
        answer="Python daily", provider=MockLLMProvider(), session_id="s2",
    )
    history = out.profile_dict["metadata"]["enrichment_history"]
    # The prior record survives the round-trip AND the new interview record is appended.
    assert len(history) == 2
    assert history[0]["source"] == "cv_upload"
    assert history[0]["source_session_id"] == "earlier"
    assert history[1]["source"] == "interview"
    assert history[1]["source_session_id"] == "s2"


@pytest.mark.asyncio
async def test_denial_only_turn_is_recorded_not_dropped_as_no_op():
    """#231 — "no direct LegalTech experience, that's an honest gap" must not
    vanish as a plain no-op: the denial persists to
    metadata.denied_concepts WITH a receipt, and `denial_recorded` is True —
    but `addressed` stays False (a denial must never read as "resolved this
    gap"; F8's ledger-upgrade/gap-advance gate stays keyed on real changes)."""

    class _Denier:
        async def aparse_json(self, prompt, **kw):
            return {"ops": [], "ambiguities": [], "denials": ["LegalTech"]}

    answer = "No direct LegalTech experience, that's an honest gap."
    out = await reconcile_interview_turn(
        profile_dict={}, gap="LegalTech experience",
        question="Do you have LegalTech experience?",
        answer=answer, provider=_Denier(), session_id="s3",
    )
    assert out.addressed is False
    assert out.denial_recorded is True
    denied = out.profile_dict["metadata"]["denied_concepts"]
    assert len(denied) == 1
    assert denied[0]["concept"] == "LegalTech"
    assert denied[0]["statement"] == answer
    assert denied[0]["source"] == "interview"
    history = out.profile_dict["metadata"]["enrichment_history"]
    assert history, "a denial-only turn must still leave a receipt"
    assert history[-1]["source"] == "interview"
    assert any(c["field"] == "denied_concepts" for c in history[-1]["changes"])


@pytest.mark.asyncio
async def test_no_denials_key_in_payload_is_still_a_clean_no_op():
    """Back-compat: a provider payload omitting `denials` entirely (the shape
    _Empty already used) must not crash record_denials and stays no_change/
    not-addressed/not-denial_recorded."""

    class _Empty:
        async def aparse_json(self, prompt, **kw):
            return {"ops": [], "ambiguities": []}

    out = await reconcile_interview_turn(
        profile_dict={}, gap="g", question="q", answer="a",
        provider=_Empty(), session_id="s1",
    )
    assert out.addressed is False
    assert out.denial_recorded is False
    assert out.profile_dict.get("metadata", {}).get("denied_concepts", []) == []


@pytest.mark.asyncio
async def test_current_position_answer_resolves_end_date_gap():
    """#155 — "this is my current position" must converge: the reconciler emits
    set_field is_current=true, end_date stays null, and re-detection no longer
    reports the end_date gap for that entry."""
    from applire.services.profile.completeness import field_gaps

    profile = MasterProfileData.model_validate(
        {
            "work_experience": [
                {
                    "company": "Acme",
                    "role": "Dev",
                    "start_date": "2020-01",
                    "end_date": None,
                    "achievements": ["Shipped X"],
                    "expected_fields": [],
                }
            ]
        }
    ).model_dump(mode="json")
    work_id = profile["work_experience"][0]["id"]
    gap = "end_date: Dev @ Acme"
    assert gap in field_gaps(profile)

    class _CurrentMarker:
        async def aparse_json(self, prompt, **kwargs):
            return {
                "ops": [
                    {"op": "set_field", "target": work_id, "field": "is_current", "value": True}
                ],
                "ambiguities": [],
                "denials": [],
            }

    out = await reconcile_interview_turn(
        profile_dict=profile,
        gap=gap,
        question="When did you leave this role, or is it your current position?",
        answer="This is my current position.",
        provider=_CurrentMarker(),
        session_id="s1",
    )
    assert out.addressed is True
    entry = out.profile_dict["work_experience"][0]
    assert entry["is_current"] is True
    assert entry["end_date"] is None
    assert gap not in field_gaps(out.profile_dict)

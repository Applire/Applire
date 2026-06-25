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
from applire.schemas.profile import MasterProfileData
from applire.services.profile.reconcile.interview_bridge import reconcile_interview_turn


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

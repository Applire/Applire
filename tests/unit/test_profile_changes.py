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

"""
US145 — GET /api/profile/changes read contract (the "what changed & why" surface data).

Returns the decision trail (enrichment_history) + pending_conflicts, sourced from the
Master Profile only — never the uploads (ADR-040 §2, ADR-005). The retention-independence
test below proves the trail survives when no upload exists.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.models.profile import MasterProfile  # noqa: E402
from applire.schemas.profile import (  # noqa: E402
    Conflict,
    EnrichmentRecord,
    FieldChange,
    MasterProfileData,
    ProfileMetadata,
)

from tests.support.profile_factory import make_master_profile  # noqa: E402


def _record_with_trail() -> MasterProfile:
    data = MasterProfileData(
        metadata=ProfileMetadata(
            enrichment_history=[
                EnrichmentRecord(
                    timestamp=datetime.now(timezone.utc),
                    source="cv_upload",
                    changes=[FieldChange(
                        section="work_experience", field="work_experience", action="merged",
                        new_value="Senior Dev @ Acme", rationale="Combined as the same position.",
                    )],
                )
            ],
            pending_conflicts=[Conflict(
                section="work_experience", field="start_date",
                existing_value="2020-01", incoming_value="2019-01", source="cv_upload",
            )],
        )
    )
    return make_master_profile(
        id="00000000-0000-0000-0000-000000000001",
        profile_json=data.model_dump(mode="json"),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


class TestGetProfileChanges:
    @pytest.mark.asyncio
    async def test_returns_history_and_conflicts(self):
        from applire.services.profile import get_profile_changes

        with patch("applire.services.profile._get_latest", new=AsyncMock(return_value=_record_with_trail())):
            result = await get_profile_changes(db=AsyncMock())
        assert len(result.enrichment_history) == 1
        assert result.enrichment_history[0].changes[0].rationale
        assert len(result.pending_conflicts) == 1

    @pytest.mark.asyncio
    async def test_empty_when_no_profile(self):
        from applire.services.profile import get_profile_changes

        with patch("applire.services.profile._get_latest", new=AsyncMock(return_value=None)):
            result = await get_profile_changes(db=AsyncMock())
        assert result.enrichment_history == []
        assert result.pending_conflicts == []

    @pytest.mark.asyncio
    async def test_trail_is_retention_independent(self):
        """ADR-040 §2 / ADR-005: the trail lives in the profile, so it is fully
        available even though uploads hard-delete after 7 days. We assert the data
        source is the profile record, never an UploadRecord lookup."""
        from applire.services.profile import get_profile_changes

        db = AsyncMock()
        with patch("applire.services.profile._get_latest", new=AsyncMock(return_value=_record_with_trail())):
            result = await get_profile_changes(db=db)
        # The trail came back without the service ever querying uploads.
        assert result.enrichment_history
        db.get.assert_not_called()

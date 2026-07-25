# Copyright (C) 2024-2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""EnrichmentRecord.source accepts the new manual_role_add value."""
from datetime import datetime, timezone

from applire.schemas.profile import EnrichmentRecord


def test_source_accepts_agent_interview():
    """E045: submit_claims receipts carry agent-interview provenance (ADR-054;
    the ADR spells it `agent-interview`, the Literal-safe identifier is
    `agent_interview` — mapping recorded in the ADR-054 amendment)."""
    rec = EnrichmentRecord(
        timestamp=datetime.now(timezone.utc),
        source="agent_interview",
    )
    assert rec.source == "agent_interview"


def test_source_accepts_manual_role_add():
    rec = EnrichmentRecord(
        timestamp=datetime.now(timezone.utc),
        source="manual_role_add",
    )
    assert rec.source == "manual_role_add"


def test_source_accepts_testimony():
    """#258: submit_testimony receipts (UI paste box + MCP submit_testimony
    tool) carry the `testimony` provenance marker, distinct from `interview`/
    `agent_interview`."""
    rec = EnrichmentRecord(
        timestamp=datetime.now(timezone.utc),
        source="testimony",
    )
    assert rec.source == "testimony"

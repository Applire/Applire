# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Content logs are written only for a person who consented (ADR-091/096
amended 2026-11-18). A test that reads the content or LLM call log acts for
the harness owner WITH a consent, the way an auth door would set it::

    from tests.support.log_consent import consented_owner  # noqa: F401
    pytestmark = pytest.mark.usefixtures("consented_owner")
"""

import pytest

from applire.ownership import current_owner
from applire.services import allowance


def consent_facts_for_current_owner():
    """Set consenting payer facts for the current owner; returns the reset token."""
    owner = current_owner()
    if owner is None or owner.user_id is None:
        return None
    from applire.services import log_consent_live

    # What the door does when it reads a consent: the facts and the live state.
    log_consent_live.note_door(owner.user_id, True)
    return allowance._facts.set(
        allowance.PayerFacts(owner_id=owner.user_id, mode="instance", log_consent_ok=True)
    )


@pytest.fixture
def consented_owner(harness_owner_context):
    token = consent_facts_for_current_owner()
    try:
        yield harness_owner_context
    finally:
        if token is not None:
            allowance._facts.reset(token)

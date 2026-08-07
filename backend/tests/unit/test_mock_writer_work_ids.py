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

"""The mock CV writer resolves work-entry ids from the ROLE BULLET BUDGETS
block, not from any line in the prompt that happens to look like one.

Why this exists: PR #473's integration stack failed with
``UnknownWorkEntryIdError: CV writer returned prose for unknown work-entry id
'Python'``. ``_prompt_work_ids`` scanned the WHOLE prompt with
``^\\s*-\\s*\\[([^\\]]+)\\]``, so a new prompt block that happened to lead its
items with ``- [label]`` was read as the id channel. The block's rendering is
fixed separately (a real model could conflate the two the same way); this pins
the parser so the next block cannot silently poison it either.

These are unit tests of the MOCK provider — the component the whole
integration tier's determinism rests on. A fragile mock reports failures the
production code does not have, and passes ones it does.
"""

from applire.providers.llm.mock import _prompt_work_ids

_BUDGET_BLOCK = (
    "ROLE BULLET BUDGETS (ADR-051 §3) — this document targets 2 page(s) (DACH region norm).\n"
    "  - [8bae58e9-1111-4444-8888-000000000001] Acme — Engineer: max 5 bullet(s) (tier: top)\n"
    "  - [e84d091a-2222-4444-8888-000000000002] Globex — Dev: max 3 bullet(s) (tier: mid)"
)
_VAULT_IDS = [
    "8bae58e9-1111-4444-8888-000000000001",
    "e84d091a-2222-4444-8888-000000000002",
]

# A bullet-list block that is NOT the id channel. Deliberately the shape the
# #303 digest originally used, so this test keeps failing if it comes back.
_DECOY_BLOCK = (
    "=== STRONGEST VAULT EVIDENCE (deterministic — #271) ===\n"
    "Some instruction sentence.\n"
    "  - [Python] Built the ingestion service in Python. (source: work_experience[0])\n"
    "  - [Kubernetes] Ran the fleet on Kubernetes. (source: work_experience[0])"
)


def test_work_ids_come_from_the_budget_block_when_another_list_precedes_it():
    prompt = f"Tailor the CV.\n\n{_DECOY_BLOCK}\n\n{_BUDGET_BLOCK}\n\nReturn JSON."
    assert _prompt_work_ids(prompt) == _VAULT_IDS


def test_a_decoy_list_alone_yields_no_ids_rather_than_wrong_ones():
    """Failing closed matters more than guessing: with no budget block and no
    profile JSON, an empty list makes the assembled CV keep every vault entry
    with empty bullets, while a wrong id raises UnknownWorkEntryIdError."""
    assert _prompt_work_ids(f"Tailor the CV.\n\n{_DECOY_BLOCK}\n\nReturn JSON.") == []


def test_the_budget_block_alone_still_works():
    assert _prompt_work_ids(f"Tailor the CV.\n\n{_BUDGET_BLOCK}") == _VAULT_IDS


def test_profile_json_remains_the_fallback_when_no_budget_block_exists():
    """Legacy/degraded callers pass no budget; the CANDIDATE PROFILE block is
    still authoritative and must not be displaced by a decoy list."""
    profile_block = (
        'CANDIDATE PROFILE:\n'
        '{\n  "work_experience": [\n    {"id": "w1", "company": "Acme"}\n  ]\n}\n\n'
        "KEYWORD GAPS:\n[]"
    )
    prompt = f"Tailor the CV.\n\n{_DECOY_BLOCK}\n\n{profile_block}"
    assert _prompt_work_ids(prompt) == ["w1"]


def test_prose_naming_the_block_mid_sentence_does_not_start_it():
    """The #303 evidence digest's own instruction says "within the ROLE BULLET
    BUDGETS ceiling". A substring search would start the id block at that
    mention and sweep in every bulleted line beneath it — which is how the
    first attempt at this fix still returned ['Budgetverantwortung', 'Python'].
    The header is matched at LINE START, where render_budget_table writes it."""
    prompt = (
        "Tailor the CV.\n\n"
        "=== STRONGEST VAULT EVIDENCE (deterministic — #271) ===\n"
        "This is evidence to choose from within the ROLE BULLET BUDGETS ceiling, "
        "not content that must all appear.\n"
        "  - [Python] Built the ingestion service in Python. (source: work_experience[0])\n"
        "\n"
        f"{_BUDGET_BLOCK}\n\nReturn JSON."
    )
    assert _prompt_work_ids(prompt) == _VAULT_IDS

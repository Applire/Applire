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

"""#615 — the reconcile prompt's NEW INFORMATION block renders the incoming
DOCUMENT's content, not the extraction's bookkeeping (ADR-078 amended
2026-08-28, second face).

``build_reconcile_prompt`` used to render a ``BaseModel`` ``new_info`` via
``str()`` (a Python repr carrying every entry's extraction-minted ``id``,
``status``, ``experience_refs``, ``expected_fields``, ``source``) — and rule 1
of the system prompt reads an ``id`` as an EXISTING entity, so the model
either emitted no upsert for an id-bearing incoming entry, or gap-fill ops
against the incoming's own phantom ids that ``apply_ops`` cannot resolve.
Real-provider replay (``scratchpad/w1/replay_evidence.md``, 15 calls): with
ids present, 10 of 11 merges lost whole sections; with the identity/bookkeeping
keys stripped, 0 of 5 — five byte-identical 22-op batches.

The fixture (``tests/files/615_import_witness/fixture.json``) is the captured
``backend[0]`` extraction (3 work / 8 skills / 2 languages / 2 education) that
produced the loss; ``_reference_strip`` below is an INDEPENDENT re-
implementation of the validated ``replay_record6_noid.py`` recipe (not a call
to the production function) — the pin in
``test_prompt_incoming_view_matches_the_validated_replay_recipe`` proves
``prompt_incoming_view`` reproduces the exact rendering that measured 0/5
lossy replays, not merely that it agrees with itself.
"""
import json
from pathlib import Path

import pytest

from applire.exceptions import LLMTruncatedError
from applire.prompts.reconcile import build_reconcile_prompt
from applire.schemas.profile import MasterProfileData
from applire.services.prompt_view import prompt_incoming_view, prompt_profile_view
from applire.services.profile.reconcile.import_bridge import (
    _reconcile_import_batched,
    reconcile_import,
)

_ROOT = Path(__file__).resolve().parents[3]
FIXTURE = json.loads(
    (_ROOT / "tests" / "files" / "615_import_witness" / "fixture.json").read_text()
)

# The six identity/bookkeeping keys ADR-078 amended names — kept as a literal
# tuple here (not imported) so this pin does not silently track a future
# change to the production constant.
_STRIP_KEYS = {"id", "status", "experience_refs", "expected_fields", "source", "evidence_refs"}


def _reference_strip(value):
    """Independent copy of ``replay_record6_noid.py``'s validated ``strip()`` —
    empties are ``(None, [], "")`` ONLY (no bare ``{}``, e.g.
    ``role_fact_projections: {}`` on every incoming WorkEntry survives this
    recipe, exactly as it did in the five replayed, byte-identical prompts)."""
    if isinstance(value, dict):
        return {
            k: _reference_strip(v)
            for k, v in value.items()
            if k not in _STRIP_KEYS and v not in (None, [], "")
        }
    if isinstance(value, list):
        return [_reference_strip(v) for v in value]
    return value


class _RecordingProvider:
    """Stub LLMProvider — records every prompt, and can simulate a truncated
    first call to force the segmented fallback."""

    def __init__(self, responses=None, *, truncate_first=False):
        self.prompts: list[str] = []
        self._responses = list(responses or [])
        self._truncate_first = truncate_first
        self._calls = 0

    async def aparse_json(self, prompt, **kwargs):
        self.prompts.append(prompt)
        self._calls += 1
        if self._truncate_first and self._calls == 1:
            raise LLMTruncatedError("simulated output cap")
        if self._responses:
            return self._responses.pop(0)
        return {"ops": [], "ambiguities": [], "denials": []}


def _incoming() -> MasterProfileData:
    return MasterProfileData.model_validate(FIXTURE["incoming"])


def _existing() -> MasterProfileData:
    return MasterProfileData.model_validate(FIXTURE["existing_empty"])


# ---------------------------------------------------------------------------
# 1. The view itself
# ---------------------------------------------------------------------------


def test_prompt_incoming_view_strips_identity_and_bookkeeping_keys_recursively():
    dump = {
        "work_experience": [
            {
                "id": "w-1",
                "status": "confirmed",
                "experience_refs": ["w-1"],
                "expected_fields": ["industry_context"],
                "source": "cv_upload",
                "evidence_refs": ["w-1"],
                "company": "Acme GmbH",
                "role": "Engineer",
            }
        ],
        "skills": [{"id": "s-1", "name": "Python", "status": "confirmed"}],
    }
    view = prompt_incoming_view(dump)
    entry = view["work_experience"][0]
    assert set(entry.keys()) == {"company", "role"}
    assert view["skills"] == [{"name": "Python"}]


def test_prompt_incoming_view_drops_empty_values_but_keeps_empty_dicts():
    dump = {
        "work_experience": [
            {
                "id": "w-1",
                "company": "Acme GmbH",
                "role": "Engineer",
                "end_date": None,
                "role_aliases": [],
                "location": "",
                # An empty dict is NOT in the validated recipe's empty set —
                # it survives (see module docstring / _reference_strip).
                "role_fact_projections": {},
            }
        ],
    }
    view = prompt_incoming_view(dump)
    entry = view["work_experience"][0]
    assert "end_date" not in entry
    assert "role_aliases" not in entry
    assert "location" not in entry
    assert entry["role_fact_projections"] == {}


def test_prompt_incoming_view_matches_the_validated_replay_recipe_on_the_captured_extraction():
    dumped = _incoming().model_dump(mode="json")
    reference = _reference_strip(prompt_profile_view(dumped))
    actual = prompt_incoming_view(dumped)
    assert actual == reference
    rendered = json.dumps(actual, ensure_ascii=False, indent=2)
    assert '"id"' not in rendered
    assert '"status"' not in rendered
    assert '"experience_refs"' not in rendered
    assert '"expected_fields"' not in rendered
    assert '"source"' not in rendered
    assert '"evidence_refs"' not in rendered


# ---------------------------------------------------------------------------
# 2. build_reconcile_prompt: BaseModel goes through the incoming view; dict /
#    list / str callers are untouched (interview, testimony, agent bridges).
# ---------------------------------------------------------------------------


def test_build_reconcile_prompt_renders_a_basemodel_with_no_ids():
    prompt = build_reconcile_prompt(_existing(), _incoming(), "cv_upload")
    new_info_block = prompt.split("NEW INFORMATION")[-1]
    assert '"id"' not in new_info_block
    assert "WorkEntry(" not in new_info_block
    assert "id=" not in new_info_block
    # The content itself must still be there — this is a rendering fix, not a
    # data drop: the model still needs to SEE the declared skill names.
    assert "SAP CO/FI" in new_info_block


def test_build_reconcile_prompt_dict_new_info_is_byte_identical_to_before():
    """The testimony bridge's `{"answer": text}` shape is untouched."""
    new_info = {"answer": "Ich habe 5 Jahre Erfahrung mit Python."}
    prompt = build_reconcile_prompt(_existing(), new_info, "cv_upload")
    new_info_block = prompt.split("NEW INFORMATION to reconcile into the profile:\n")[-1]
    expected = json.dumps(new_info, ensure_ascii=False, indent=2)
    assert new_info_block.split("\n\nEmit the JSON op batch")[0] == expected


def test_build_reconcile_prompt_str_new_info_is_byte_identical_to_before():
    prompt = build_reconcile_prompt(_existing(), "plain testimony text", "testimony")
    assert "plain testimony text" in prompt
    assert "id" not in prompt.split("NEW INFORMATION")[-1].split("plain testimony text")[0]


# ---------------------------------------------------------------------------
# 3. Seam tests — one per import call site (fast path + segmented fallback) —
#    driving the REAL bridge with a stub provider that records the prompt.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_import_fast_path_prompt_carries_no_ids():
    provider = _RecordingProvider(responses=[{"ops": [], "ambiguities": [], "denials": []}])
    await reconcile_import(_existing(), _incoming(), "cv_upload", provider)
    assert len(provider.prompts) == 1
    new_info_block = provider.prompts[0].split("NEW INFORMATION")[-1]
    assert '"id"' not in new_info_block
    assert "WorkEntry(" not in new_info_block


@pytest.mark.asyncio
async def test_segmented_reconcile_import_prompts_carry_no_ids():
    """Force the ADR-047 segmented fallback (first call truncates) and prove
    EVERY per-slice prompt — not just the fast-path attempt — is id-free."""
    provider = _RecordingProvider(
        responses=[
            {"ops": [{"op": "upsert_work", "ref": "w1", "company": "Schwarzwald Präzision GmbH",
                      "role": "Financial Controller"}], "ambiguities": [], "denials": []},
            {"ops": [{"op": "upsert_skill", "name": "SAP CO", "evidence": []}],
             "ambiguities": [], "denials": []},
            {"ops": [], "ambiguities": [], "denials": []},
        ],
        truncate_first=True,
    )
    await reconcile_import(_existing(), _incoming(), "cv_upload", provider)
    # The first (truncated) call plus one call per non-empty slice group.
    assert len(provider.prompts) >= 3
    for prompt in provider.prompts:
        new_info_block = prompt.split("NEW INFORMATION")[-1]
        assert '"id"' not in new_info_block, prompt
        assert "WorkEntry(" not in new_info_block, prompt


@pytest.mark.asyncio
async def test_reconcile_import_batched_directly_prompts_carry_no_ids():
    """`_reconcile_import_batched` is the other named call site — drive it
    directly (not only via the truncation trigger above)."""
    provider = _RecordingProvider(
        responses=[
            {"ops": [], "ambiguities": [], "denials": []},
            {"ops": [], "ambiguities": [], "denials": []},
            {"ops": [], "ambiguities": [], "denials": []},
        ]
    )
    await _reconcile_import_batched(_existing(), _incoming(), "cv_upload", provider, "en")
    assert provider.prompts, "the segmented path must call the provider at least once"
    for prompt in provider.prompts:
        new_info_block = prompt.split("NEW INFORMATION")[-1]
        assert '"id"' not in new_info_block, prompt

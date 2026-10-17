# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""US367 / ADR-094 cl. 6 — the model sees short ids, the pipeline keeps UUIDs."""
import sys
from pathlib import Path

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.model_ids import UUID_RE, ShortIdMap, ShortIdProvider  # noqa: E402

W1 = "2fac2217-b79f-4e25-bf3d-ef8d43199836"
W2 = "fca8e541-e976-495f-ad69-7ad0ee6e5945"
P1 = "11111111-2222-4333-8444-555555555555"
E1 = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
OTHER = "99999999-8888-4777-8666-555555555555"

PROFILE = {
    "work_experience": [{"id": W1, "company": "A"}, {"id": W2, "company": "B"}],
    "projects": [{"id": P1, "name": "X", "associated_experience": W1}],
    "education": [{"id": E1, "degree": "MSc"}],
}


def test_seeding_is_deterministic_and_section_prefixed():
    m = ShortIdMap.from_profile(PROFILE)
    assert (m.short(W1), m.short(W2), m.short(P1), m.short(E1)) == ("w1", "w2", "p1", "e1")
    assert ShortIdMap.from_profile(PROFILE).short(W2) == "w2"


def test_shorten_text_removes_every_uuid_and_restore_maps_back():
    m = ShortIdMap.from_profile(PROFILE)
    text = f'[{W1}] max 5 · project {P1} under {W1}; stray {OTHER}'
    short = m.shorten_text(text)
    assert UUID_RE.search(short) is None
    assert "[w1]" in short and "p1" in short and "x1" in short
    assert m.restore({"work": [{"id": "w1", "bullets": ["led w1 rollout"]}], "x": "x1"}) == {
        "work": [{"id": W1, "bullets": ["led w1 rollout"]}],
        "x": OTHER,
    }


def test_an_invented_short_id_is_not_mapped():
    m = ShortIdMap.from_profile(PROFILE)
    assert m.restore({"id": "w9"}) == {"id": "w9"}


def test_restore_json_text_on_raw_text():
    m = ShortIdMap.from_profile(PROFILE)
    assert m.restore_json_text('{"id": "w2"}') == f'{{"id": "{W2}"}}'
    assert m.restore_json_text('not json "w2" here') == f'not json "{W2}" here'


class _Inner:
    def __init__(self, response):
        self.response = response
        self.prompts: list[str] = []
        self.systems: list[str] = []
        self._model = "m"

    async def aparse_json(self, prompt, **kw):
        self.prompts.append(prompt)
        self.systems.append(kw.get("system"))
        return self.response

    async def acomplete(self, prompt, **kw):
        self.prompts.append(prompt)
        return '{"id": "w1"}'


@pytest.mark.asyncio
async def test_provider_wrapper_round_trip():
    inner = _Inner({"work": [{"id": "w2", "bullets": []}]})
    p = ShortIdProvider(inner, ShortIdMap.from_profile(PROFILE))
    out = await p.aparse_json(f"budget [{W2}]", system=f"ids like {W1}", max_tokens=10)
    assert out == {"work": [{"id": W2, "bullets": []}]}
    assert UUID_RE.search(inner.prompts[0]) is None and UUID_RE.search(inner.systems[0]) is None
    assert p._model == "m"
    assert await p.acomplete(f"x {W1}") == f'{{"id": "{W1}"}}'

# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""783:L79 (ADR-078 amended 2026-10-20) — the reconcile call addresses existing
entities by short, namespaced handles (``id_w1``) and the response is mapped back
to vault UUIDs before ``_parse_ops``.

Evidence for the change (Luna on Requesty, thinking off, replay of the captured
E2E-3 call, Strawberry build 2): a mistyped UUID handle on ``match_existing`` in
2/10 runs as captured (``eb557436-1fc4-5c8b…`` for ``…1fc5-4c8b…``), 0/10 through
short handles; two other captured calls 0/10 in both arms.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

_backend = Path(__file__).resolve().parents[2]
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.schemas.profile import MasterProfileData, ProfileMetadata  # noqa: E402
from applire.services.model_ids import ShortIdMap  # noqa: E402
from applire.services.profile.reconcile.engine import reconcile  # noqa: E402

_W = "31a79718-1111-4222-8333-444455556666"
_S = "eb557436-1fc5-4c8b-8283-a61d4c7d62d2"


def _vault() -> MasterProfileData:
    return MasterProfileData.model_validate(
        {
            "personal_info": {"name": "Lena Fischer"},
            "work_experience": [{"id": _W, "company": "Nordwind Logistik GmbH", "role": "Platform Engineer"}],
            "skills": [{"id": _S, "name": "REST APIs"}],
            "metadata": ProfileMetadata().model_dump(mode="json"),
        }
    )


class _Recording:
    """A provider double that answers with handles as the model would see them."""

    def __init__(self, answer_fn):
        self.prompts: list[str] = []
        self._answer_fn = answer_fn

    async def aparse_json(self, prompt: str, **kwargs: Any) -> Any:
        self.prompts.append(prompt)
        return self._answer_fn(prompt)

    async def acomplete(self, prompt: str, **kwargs: Any) -> str:  # pragma: no cover
        raise AssertionError("not used")


@pytest.mark.asyncio
async def test_the_model_sees_short_handles_and_the_ops_come_back_as_uuids():
    def answer(prompt: str) -> dict:
        assert _W not in prompt and _S not in prompt
        assert '"id_w1"' in prompt and '"id_s1"' in prompt
        return {
            "ops": [
                {"op": "add_bullets", "target": "id_w1", "achievements": ["EKS-Migration von 40 Services"]},
                {"op": "match_existing", "target": "id_s1", "incoming": "REST-Schnittstellen"},
                {"op": "upsert_work", "ref": "w1", "company": "Seeblick Reederei AG", "role": "SRE"},
                {"op": "add_bullets", "target": "w1", "responsibilities": ["Betrieb der Cluster"]},
            ],
            "ambiguities": [],
            "denials": [],
        }

    prov = _Recording(answer)
    result = await reconcile(_vault(), "Ich habe 40 Services nach EKS migriert.", "cv_paste", prov)
    targets = [getattr(o, "target", None) for o in result.ops]
    assert _W in targets and _S in targets
    # the local ref of a NEW entity is not confused with an existing entity
    assert "w1" in targets
    assert len(prov.prompts) == 1


@pytest.mark.asyncio
async def test_an_invented_handle_stays_unmapped():
    prov = _Recording(lambda p: {"ops": [{"op": "add_bullets", "target": "id_w9", "achievements": ["x y z"]}],
                                  "ambiguities": [], "denials": []})
    result = await reconcile(_vault(), "irgendwas", "cv_paste", prov)
    assert [getattr(o, "target", None) for o in result.ops] == ["id_w9"]


def test_namespace_keeps_writer_ids_unchanged():
    plain = ShortIdMap.from_profile({"work_experience": [{"id": _W}]})
    spaced = ShortIdMap.from_profile({"work_experience": [{"id": _W}]}, namespace="id_")
    assert plain.short(_W) == "w1"
    assert spaced.short(_W) == "id_w1"
    assert spaced.restore({"t": "id_w1", "u": "w1"}) == {"t": _W, "u": "w1"}
    assert plain.restore({"t": "w1", "u": "id_w1"}) == {"t": _W, "u": "id_w1"}

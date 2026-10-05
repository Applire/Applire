# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Adversarial ownership probes — file paths reachable through the vault (w4-adv-ownership).

``personal_info.photo_url`` is a **storage path**, and ``storage.read`` has no
containment check (``storage/local.py:78``; acknowledged in ``services/cv.py``
around ``render_agent_cv``). The section door (``field_edit.py``) and the agent
render door strip/refuse it because "a field another writer owns is not
reachable" (USER_MANAGED_PERSONAL_INFO_FIELDS). The reconcile applier only
honours that rule when the slot is ALREADY filled
(``reconcile/apply.py::_apply_set_personal_info``): on an empty photo slot a
``set_personal_info`` op — produced by the reconcile LLM from text the user
controls (CV import, testimony, agent claims) — writes any path. The CV render
then base64-embeds whatever file that path names: another user's photo or
upload (flat shared directory, ADR-092 cl. 13), or any server file.
"""

from __future__ import annotations

import pytest

from applire.schemas.profile import MasterProfileData
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.ops import SetPersonalInfo


def _empty_photo_profile() -> MasterProfileData:
    return MasterProfileData.model_validate({"personal_info": {"name": "User B"}})


@pytest.mark.parametrize("source", ["cv_upload", "testimony", "agent_interview"])
def test_adv_ownership_1_reconcile_cannot_write_photo_url_into_an_empty_slot(source):
    other_users_file = "/data/uploads/0f3c1d2e4b5a69788796a5b4c3d2e1f0.jpg"  # A's stored photo
    result = apply_ops(
        _empty_photo_profile(),
        [SetPersonalInfo(field="photo_url", value=other_users_file)],
        source,
    )
    assert result.profile.personal_info.photo_url is None, (
        "reconcile wrote a storage path into personal_info.photo_url "
        f"({result.profile.personal_info.photo_url!r}); the photo endpoints own this "
        "field, and the CV render reads the path with no containment check"
    )


@pytest.mark.asyncio
async def test_adv_ownership_2_render_photo_resolution_reads_only_the_storage_dir(tmp_path):
    """Second half of the chain: the render resolves any path, also outside storage."""
    from applire.services.cv import _resolve_photo_data_uri
    from applire.storage.local import LocalStorageProvider

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    secret = tmp_path / "outside" / "secret.jpg"
    secret.parent.mkdir()
    secret.write_bytes(b"NOT-A-PHOTO-SECRET")
    uri = await _resolve_photo_data_uri(str(secret), LocalStorageProvider(str(upload_dir)))
    assert uri is None, "a photo_url outside the upload directory was read and embedded"

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

"""#679 / US309 — `dismissed_explainers` over the real `/api/settings` app.

The sibling suite (`tests/unit/test_settings_endpoint.py`) drives the service
functions and a bare router app. This tree drives the FULL FastAPI app that
production serves — router mount, real `AuthProvider` resolution, real
response model — so the wire shape the frontend consumes (COPY.md §F) is
pinned where it is actually assembled, not only where it is computed.
"""

import pytest


@pytest.mark.asyncio
async def test_get_settings_serves_an_empty_list_by_default(async_client):
    resp = await async_client.get("/api/settings")
    assert resp.status_code == 200
    assert resp.json()["dismissed_explainers"] == []


@pytest.mark.asyncio
async def test_patch_dismiss_explainer_echoes_and_persists(async_client):
    resp = await async_client.patch(
        "/api/settings", json={"dismiss_explainer": "fact_pins_intro"}
    )
    assert resp.status_code == 200
    assert resp.json()["dismissed_explainers"] == ["fact_pins_intro"]

    resp = await async_client.get("/api/settings")
    assert resp.json()["dismissed_explainers"] == ["fact_pins_intro"]


@pytest.mark.asyncio
async def test_patch_dismiss_explainer_is_idempotent_over_http(async_client):
    for _ in range(3):
        resp = await async_client.patch(
            "/api/settings", json={"dismiss_explainer": "fact_pins_intro"}
        )
        assert resp.status_code == 200

    resp = await async_client.get("/api/settings")
    assert resp.json()["dismissed_explainers"] == ["fact_pins_intro"]


@pytest.mark.asyncio
async def test_unknown_explainer_id_is_422_and_leaves_the_set_untouched(async_client):
    await async_client.patch(
        "/api/settings", json={"dismiss_explainer": "fact_pins_intro"}
    )

    resp = await async_client.patch(
        "/api/settings", json={"dismiss_explainer": "typo_in_a_frontend_build"}
    )
    assert resp.status_code == 422

    resp = await async_client.get("/api/settings")
    assert resp.json()["dismissed_explainers"] == ["fact_pins_intro"]


@pytest.mark.asyncio
async def test_dismissal_leaves_hide_predownload_notice_alone(async_client):
    # ADR-040 §4 keeps its own boolean; #679's set is additive next to it.
    resp = await async_client.patch(
        "/api/settings", json={"hide_predownload_notice": True}
    )
    assert resp.status_code == 200

    resp = await async_client.patch(
        "/api/settings", json={"dismiss_explainer": "fact_pins_intro"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["hide_predownload_notice"] is True
    assert body["dismissed_explainers"] == ["fact_pins_intro"]


@pytest.mark.asyncio
async def test_an_unrelated_patch_does_not_clear_the_set(async_client):
    await async_client.patch(
        "/api/settings", json={"dismiss_explainer": "fact_pins_intro"}
    )
    resp = await async_client.patch("/api/settings", json={"ui_language": "de"})
    assert resp.status_code == 200
    assert resp.json()["dismissed_explainers"] == ["fact_pins_intro"]

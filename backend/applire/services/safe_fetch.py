# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
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

"""One safe outbound fetcher (ADR-092 cl. 15, RD-7, SF-SCRAPER.2).

Strawberry W0 (frozen interface F8): the signature is the contract; the body
is a **passthrough** — a plain ``httpx`` GET that follows redirects, i.e.
exactly what ``scraper._fetch_tier1`` and the two ``color_detection`` fetches
do today. Package 4a fills the body (host resolution, refusal of loopback /
private / link-local / multicast / unspecified addresses, manual per-hop
redirect re-check, connection pinned to the checked address) and raises
:class:`UnsafeFetchRefused` for a refused target; callers (4a's scraper,
3d's colour detection) catch that name.
"""

from __future__ import annotations

from collections.abc import Mapping

import httpx

__all__ = ["UnsafeFetchRefused", "safe_get"]


class UnsafeFetchRefused(Exception):
    """The URL (or a redirect hop) resolves to a refused address range.

    Not raised in W0 — named now so callers can be written against it.
    """


async def safe_get(
    url: str,
    *,
    timeout: float | httpx.Timeout,
    headers: Mapping[str, str] | None,
    max_redirects: int = 5,
) -> httpx.Response:
    """GET ``url`` and return the final response (redirects followed).

    W0 passthrough: no address checks yet (see module docstring).
    """
    async with httpx.AsyncClient(
        follow_redirects=True,
        max_redirects=max_redirects,
        timeout=timeout,
        headers=dict(headers) if headers else None,
    ) as client:
        return await client.get(url)

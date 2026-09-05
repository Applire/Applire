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

"""Playwright PDF renderer for cover letters.

Separated from cover_letter.py to keep the main service file testable
without a Playwright dependency.
"""
import uuid

from playwright.async_api import async_playwright
from sqlalchemy import select

from applire.db.session import AsyncSessionLocal
from applire.models.cover_letter import CoverLetterStatus, GeneratedCoverLetter
from applire.services.cover_letter import get_cover_letter_html
from applire.services.pdf_provenance import (
    current_provenance,
    digital_source_type_for_origin,
    render_marked_pdf,
)


async def render_pdf(cl_id: uuid.UUID, allow_unready: bool = False) -> bytes:
    """Render the cover letter to PDF using Playwright. Returns raw PDF bytes.

    ``allow_unready`` (E037 PQ #2): the generation path renders the smoke PDF while the
    letter is still 'generating' — the ATS audit must complete BEFORE status flips to
    'ready' so "ready implies report available". Pass-through to the HTML renderer's
    status guard. Default False keeps the public download path ready-only.

    ADR-085 / founder ruling 14 (2026-09-05): the row's ``origin`` (ADR-054,
    ``"agent"`` for a BYOI verbatim render through ``render_agent_letter``)
    decides the mark's ``DigitalSourceType`` — Applire only rendered that
    content and cannot attest its authorship. Read here, at the ONE letter PDF
    seam, so the pre-audit render inside the agent door and every later download
    of the same row agree.
    """
    async with AsyncSessionLocal() as db:
        html = await get_cover_letter_html(cl_id, db, require_ready=not allow_unready)
        origin = await db.scalar(
            select(GeneratedCoverLetter.origin).where(GeneratedCoverLetter.id == cl_id)
        )

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.set_content(html, wait_until="networkidle")
        # ADR-085: the AI-provenance mark is applied at this one seam (see
        # services/pdf_provenance.py — no module may call page.pdf() itself).
        pdf_bytes = await render_marked_pdf(
            page,
            format="A4",
            margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
            print_background=True,
            provenance=current_provenance(
                digital_source_type=digital_source_type_for_origin(origin)
            ),
        )
        await browser.close()
    return pdf_bytes

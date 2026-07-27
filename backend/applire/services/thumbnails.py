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

# backend/applire/services/thumbnails.py
"""Generate static PNG preview thumbnails for CV templates.

Called from lifespan() at startup. Skips any thumbnail that already exists.
Uses the same Playwright + Jinja2 stack as the PDF renderer.
"""
import logging
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from applire.templates.filters import register_filters

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

_TEMPLATE_FILES: dict[str, str] = {
    "classic_german": "lebenslauf.html.j2",
    "modern_swiss": "modern_swiss.html.j2",
    "executive": "executive.html.j2",
    "tech_developer": "tech_developer.html.j2",
    "creative_sidebar": "creative_sidebar.html.j2",
    "academic": "academic.html.j2",
    "compact_pro": "compact_pro.html.j2",
}

from applire.schemas.cv import (
    TailoredCVData,
    TailoredContact,
    TailoredWorkEntry,
    TailoredProjectEntry,
    TailoredEducationEntry,
    TailoredLanguage,
)
from applire.services.color_detection import _make_color_context

_DEFAULT_COLOR = _make_color_context("#2b5fa8")

_SAMPLE_DATA = TailoredCVData(
    contact=TailoredContact(
        name="Anna Bauer",
        email="anna.bauer@example.de",
        phone="+49 89 123456",
        location="München",
    ),
    summary="Erfahrene Senior Software Ingenieurin mit 8 Jahren Erfahrung in Python, FastAPI und PostgreSQL.",
    work_history=[
        TailoredWorkEntry(
            company="Roche GmbH",
            role="Senior Software Engineer",
            start_date="Jan 2019",
            end_date=None,
            bullets=["Led backend architecture redesign", "Mentored junior engineers"],
            projects=[
                TailoredProjectEntry(
                    name="Plattform-Migration",
                    bullets=["Migration auf eine containerisierte Microservice-Architektur"],
                )
            ],
        ),
        TailoredWorkEntry(
            company="Novartis AG",
            role="Software Engineer",
            start_date="Mär 2016",
            end_date="Dez 2018",
            bullets=["Developed REST APIs with FastAPI", "Maintained PostgreSQL databases"],
        ),
    ],
    skills=["Python", "FastAPI", "PostgreSQL", "Docker", "SQLAlchemy", "Git", "CI/CD"],
    education=[
        TailoredEducationEntry(
            institution="TU München",
            degree="MSc",
            field="Informatik",
            start_date="2014",
            end_date="2018",
        ),
    ],
    languages=[
        TailoredLanguage(language="Deutsch", level="Muttersprache"),
        TailoredLanguage(language="Englisch", level="Fließend C1"),
    ],
)


async def ensure_thumbnails(static_dir: Path) -> None:
    """Generate PNG previews for all CV templates if they don't already exist."""
    thumbs_dir = static_dir / "templates"
    thumbs_dir.mkdir(parents=True, exist_ok=True)

    missing = [
        (name, tpl_file)
        for name, tpl_file in _TEMPLATE_FILES.items()
        if not (thumbs_dir / f"{name}.png").exists()
    ]

    if not missing:
        logger.info("All template thumbnails already exist — skipping generation")
        return

    logger.info("Generating %d template thumbnail(s): %s", len(missing), [n for n, _ in missing])

    jinja_env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    register_filters(jinja_env)  # #307 — every env must register (see filters.py)

    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        for name, tpl_file in missing:
            try:
                template = jinja_env.get_template(tpl_file)
                # #4 (ADR-038): templates now require lang/labels in context. Thumbnails are
                # language-agnostic previews — render in the DACH default (German).
                from applire.templates.labels import cv_labels
                html = template.render(
                    cv=_SAMPLE_DATA, color=_DEFAULT_COLOR, lang="de", labels=cv_labels("de")
                )
                page = await browser.new_page()
                await page.set_viewport_size({"width": 794, "height": 1123})
                await page.set_content(html, wait_until="networkidle")
                png_bytes = await page.screenshot(full_page=False)
                await page.close()
                (thumbs_dir / f"{name}.png").write_bytes(png_bytes)
                logger.info("Generated thumbnail: %s.png", name)
            except Exception:
                logger.exception("Failed to generate thumbnail for template '%s'", name)
        await browser.close()

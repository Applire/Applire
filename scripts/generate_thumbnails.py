#!/usr/bin/env python3
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
"""
Generate 400×566 thumbnail PNGs for all CV templates.
Usage: python scripts/generate_thumbnails.py
"""
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.async_api import async_playwright


# Minimal schema definitions to avoid db initialization
@dataclass
class TailoredContact:
    name: str
    email: str
    phone: str
    location: str
    linkedin: str
    photo_url: Optional[str] = None


@dataclass
class TailoredWorkEntry:
    company: str
    role: str
    start_date: str
    end_date: Optional[str]
    bullets: list[str]


@dataclass
class TailoredEducationEntry:
    institution: str
    degree: str
    field: str
    start_date: str
    end_date: str


@dataclass
class TailoredLanguage:
    language: str
    level: str


@dataclass
class TailoredCVData:
    contact: TailoredContact
    summary: str
    work_history: list[TailoredWorkEntry]
    skills: list[str]
    education: list[TailoredEducationEntry]
    languages: list[TailoredLanguage]
    show_photo: bool


# Simple color context
def _make_color_context(hex_color: str) -> dict:
    return {
        "hex": hex_color,
        "rgb": hex_color,  # simplified
        "surface_text": "#000000",
    }

SAMPLE_CV = TailoredCVData(
    contact=TailoredContact(
        name="Anna Musterfrau",
        email="anna@example.com",
        phone="+49 89 123456",
        location="München",
        linkedin="linkedin.com/in/anna",
        photo_url=None,
    ),
    summary="Erfahrene Managerin mit Fokus auf digitale Transformation und agile Methoden.",
    work_history=[
        TailoredWorkEntry(
            company="Digitale AG", role="Head of Product",
            start_date="2020", end_date=None,
            bullets=["Aufbau des Produktteams", "OKR-Einführung"],
        ),
        TailoredWorkEntry(
            company="Beispiel GmbH", role="Senior Managerin",
            start_date="2017", end_date="2020",
            bullets=["Projektleitung", "Stakeholder Management"],
        ),
    ],
    skills=["Python", "Agile", "Stakeholder Management", "OKR", "SQL"],
    education=[
        TailoredEducationEntry(
            institution="Hochschule Musterstadt", degree="MBA",
            field="Betriebswirtschaft", start_date="2014", end_date="2016",
        )
    ],
    languages=[
        TailoredLanguage(language="Deutsch", level="Muttersprache"),
        TailoredLanguage(language="Englisch", level="C1"),
    ],
    show_photo=False,
)

TEMPLATES_DIR = Path(__file__).parent.parent / "backend" / "applire" / "templates"
# Use temp dir first, then copy to final location
OUT_DIR = Path("/tmp/thumbnails")

FILE_MAP = {
    "classic_german": "lebenslauf.html.j2",
    "modern_swiss": "modern_swiss.html.j2",
    "executive": "executive.html.j2",
    "tech_developer": "tech_developer.html.j2",
    "creative_sidebar": "creative_sidebar.html.j2",
    "academic": "academic.html.j2",
    "compact_pro": "compact_pro.html.j2",
}

COLOR = _make_color_context("#2b5fa8")


async def main():
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        for key, filename in FILE_MAP.items():
            tmpl = env.get_template(filename)
            html = tmpl.render(cv=SAMPLE_CV, color=COLOR)
            page = await browser.new_page(viewport={"width": 794, "height": 1123})
            await page.set_content(html, wait_until="networkidle")
            out_path = OUT_DIR / f"{key}.png"
            await page.screenshot(path=str(out_path), clip={"x": 0, "y": 0, "width": 400, "height": 566})
            print(f"  wrote {out_path}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())

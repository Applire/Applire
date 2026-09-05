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
Color detection and derivation for CV brand color profiles.

Cascade (detect_and_cache_company_color):
  1. Cache check — companies table by domain (scraped_at within 30 days)
  2. Favicon extraction — Google CDN + colorgram
  3. Meta-tag scraping — theme-color, CSS :root vars
  4. LLM fallback — ~50 tokens, structured JSON prompt
  Steps 1–4 populate the companies table and set job_analyses.company_id.

Resolution (resolve_color_context):
  1. generated_cvs.color_profile_id (user override)
  2. job_analyses → companies → color_profile_id (auto-detected)
  3. user_settings.default_color_profile_id
  4. System default (#2b5fa8)
"""

import colorsys
import io
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import colorgram
import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from applire.models.color_profile import ColorProfile
from applire.models.company import Company
from applire.models.user_settings import UserSettings

logger = logging.getLogger(__name__)

DEFAULT_ACCENT = "#2b5fa8"
_SCRAPE_TTL_DAYS = 30
# CE stub user — see ADR-022; replace with real user lookup when multi-user lands
_CE_STUB_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@dataclass
class ColorContext:
    primary: str        # hex — main brand color
    primary_tint: str   # hex — light version of primary (L=95%, S=10%)
    surface: str        # hex — sidebar/header bg (Phase 1: = primary)
    surface_text: str   # "#ffffff" or "#1a1a1a" — WCAG auto-computed
    secondary: str      # hex — second accent (Phase 1: = primary)
    # Backward-compat aliases — existing templates use color.accent / color.tint
    accent: str         # = primary
    tint: str           # = primary_tint


def _srgb_to_linear(c: float) -> float:
    """Convert an sRGB channel value [0.0, 1.0] to linear light (IEC 61966-2-1)."""
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def derive_tint(hex_color: str) -> str:
    """Return a light tint derived from the accent color (L=95%, S=10%, hue preserved)."""
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    h, _l, _s = colorsys.rgb_to_hls(r, g, b)
    r2, g2, b2 = colorsys.hls_to_rgb(h, 0.95, 0.10)
    return "#{:02x}{:02x}{:02x}".format(int(r2 * 255), int(g2 * 255), int(b2 * 255))


def derive_surface_text(hex_color: str) -> str:
    """Return white or black for legible text on hex_color background.

    Uses the WCAG relative-luminance formula (IEC 61966-2-1 sRGB).
    Threshold 0.179 is the geometric mean of 4.5:1 contrast against #000 and #fff.
    """
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    luminance = 0.2126 * _srgb_to_linear(r) + 0.7152 * _srgb_to_linear(g) + 0.0722 * _srgb_to_linear(b)
    return "#ffffff" if luminance < 0.179 else "#1a1a1a"


def _make_color_context(hex_primary: str) -> ColorContext:
    tint = derive_tint(hex_primary)
    return ColorContext(
        primary=hex_primary,
        primary_tint=tint,
        surface=hex_primary,      # Phase 1: surface = primary
        surface_text=derive_surface_text(hex_primary),
        secondary=hex_primary,    # Phase 1: secondary = primary
        accent=hex_primary,       # backward compat
        tint=tint,                # backward compat
    )


def _default_context() -> ColorContext:
    return _make_color_context(DEFAULT_ACCENT)


async def resolve_color_context(record: "GeneratedCV", db: AsyncSession) -> ColorContext:  # noqa: F821
    """Walk the 4-step resolution cascade and return a ColorContext."""
    from applire.models.job import JobAnalysis

    # Step 1: CV-specific override
    if record.color_profile_id:
        cp = await db.get(ColorProfile, record.color_profile_id)
        if cp:
            return _make_color_context(cp.derived["--cv-accent"])

    # Step 2: Auto-detected company color
    job = await db.get(JobAnalysis, record.job_analysis_id)
    if job and job.company_id:
        company = await db.get(Company, job.company_id)
        if company and company.color_profile_id:
            cp = await db.get(ColorProfile, company.color_profile_id)
            if cp:
                return _make_color_context(cp.derived["--cv-accent"])

    # Step 3: User default (CE: always stub user)
    result = await db.execute(
        select(UserSettings).where(UserSettings.user_id == _CE_STUB_USER_ID)
    )
    settings = result.scalar_one_or_none()
    if settings and settings.default_color_profile_id:
        cp = await db.get(ColorProfile, settings.default_color_profile_id)
        if cp:
            return _make_color_context(cp.derived["--cv-accent"])

    # Step 4: System default
    return _default_context()


def _extract_domain(url: str | None) -> str | None:
    """Extract the bare domain from a URL. Returns None if URL is missing or unparseable."""
    if not url:
        return None
    try:
        parsed = urlparse(url)
        host = parsed.netloc or parsed.path
        # Strip www. prefix
        return re.sub(r"^www\.", "", host).lower() or None
    except Exception:
        return None


async def _fetch_favicon_color(domain: str) -> str | None:
    """Fetch favicon via Google CDN and extract the most saturated non-neutral color."""
    url = f"https://www.google.com/s2/favicons?domain={domain}&sz=128"
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            resp = await client.get(url)
        if resp.status_code != 200 or not resp.content:
            return None
        colors = colorgram.extract(io.BytesIO(resp.content), 5)
        if not colors:
            return None

        def _saturation(c: colorgram.Color) -> float:
            r, g, b = c.rgb.r / 255, c.rgb.g / 255, c.rgb.b / 255
            _, l, s = colorsys.rgb_to_hls(r, g, b)
            # Penalise near-white (l>0.85) and near-black (l<0.15)
            if l > 0.85 or l < 0.15:
                return -1.0
            return s

        best = max(colors, key=_saturation)
        if _saturation(best) < 0.1:
            return None  # All grayscale
        r, g, b = best.rgb.r, best.rgb.g, best.rgb.b
        return "#{:02x}{:02x}{:02x}".format(r, g, b)
    except Exception as exc:
        logger.debug("Favicon fetch failed for %s: %s", domain, exc)
        return None


async def _fetch_meta_color(domain: str) -> str | None:
    """Scrape homepage for theme-color meta-tag or CSS :root color variables."""
    url = f"https://{domain}"
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Applire/1.0 brand-color-bot"})
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        # theme-color meta
        tag = soup.find("meta", attrs={"name": "theme-color"})
        if tag and tag.get("content", "").startswith("#"):
            return tag["content"][:7]
        # msapplication-TileColor
        tag = soup.find("meta", attrs={"name": "msapplication-TileColor"})
        if tag and tag.get("content", "").startswith("#"):
            return tag["content"][:7]
        # CSS :root custom properties (first <style> block)
        for style in soup.find_all("style"):
            match = re.search(
                r"--(?:primary|brand|accent|main)(?:-color)?:\s*(#[0-9a-fA-F]{6})",
                style.string or "",
            )
            if match:
                return match.group(1)
        return None
    except Exception as exc:
        logger.debug("Meta-tag scrape failed for %s: %s", domain, exc)
        return None


async def _llm_color_fallback(company_name: str) -> str | None:
    """Ask the LLM for the brand primary color. ~50 tokens total."""
    try:
        from applire.providers import get_provider
        from applire.services.untrusted_text import fence_inline

        provider = get_provider()
        # ADR-084 embedding point 27 (Form A, inline). Harm here is bounded by
        # the `#RRGGBB` regex below — the response is discarded unless it is a
        # hex colour — but the point is in the positive set and marking it costs
        # one call. An unmarked embedding point is a hole in the enumeration,
        # not a judgement about how bad that particular hole is.
        prompt = (
            f'Brand primary color of {fence_inline(company_name)} as JSON with '
            'one key: {"primary":"#hex"}'
        )
        result = await provider.aparse_json(prompt, system="Return only the JSON.", temperature=0.0, max_tokens=20)
        color = result.get("primary", "")
        if re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            return color
    except Exception as exc:
        logger.debug("LLM color fallback failed: %s", exc)
    return None


async def _upsert_company_color(
    domain: str,
    name: str | None,
    hex_color: str,
    source: str,
    db: AsyncSession,
) -> "Company":  # noqa: F821
    """Create or update the company record with the detected color profile."""
    # Upsert color profile
    cp = ColorProfile(
        seed_primary=hex_color,
        derived={"--cv-accent": hex_color, "--cv-accent-tint": derive_tint(hex_color)},
        source=source,
    )
    db.add(cp)
    await db.flush()

    # Upsert company by domain
    result = await db.execute(select(Company).where(Company.domain == domain))
    company = result.scalar_one_or_none()
    if company is None:
        company = Company(domain=domain, name=name or domain)
        db.add(company)
    company.color_profile_id = cp.id
    company.scraped_at = datetime.now(timezone.utc)
    if name:
        company.name = name
    await db.flush()
    return company


async def detect_and_cache_company_color(job: "JobAnalysis", db: AsyncSession) -> None:  # noqa: F821
    """Run the detection cascade for a job's company. Updates companies table and job.company_id.

    Called from _render_cv_background. Silently logs and returns on any failure.
    """
    domain = _extract_domain(job.source_url)
    if not domain:
        logger.debug("No domain derivable from source_url for job %s — skipping detection", job.id)
        return

    # Step 1: Cache check
    result = await db.execute(select(Company).where(Company.domain == domain))
    company = result.scalar_one_or_none()
    if company and company.scraped_at:
        age = datetime.now(timezone.utc) - company.scraped_at.replace(tzinfo=timezone.utc)
        if age < timedelta(days=_SCRAPE_TTL_DAYS) and company.color_profile_id:
            job.company_id = company.id
            await db.flush()
            logger.debug("Cache hit for domain %s", domain)
            return

    # Step 2: Favicon
    hex_color = await _fetch_favicon_color(domain)
    source = "favicon"

    # Step 3: Meta-tag scrape (if favicon failed or grayscale)
    if not hex_color:
        hex_color = await _fetch_meta_color(domain)
        source = "meta_tag"

    # Step 4: LLM fallback
    if not hex_color and job.company_name:
        hex_color = await _llm_color_fallback(job.company_name)
        source = "llm"

    if not hex_color:
        logger.debug("Color detection yielded no result for domain %s", domain)
        return

    company = await _upsert_company_color(domain, job.company_name, hex_color, source, db)
    job.company_id = company.id
    await db.flush()
    logger.info("Detected %s color for domain %s via %s", hex_color, domain, source)

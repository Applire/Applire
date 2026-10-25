# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""789:36 (E-12 walk, founder 2026-10-22): a light backing plate under the signature
image on the dark ``tech_developer*`` templates, so a dark-ink signature reads.

Pixel evidence, not a CSS-string check: each template is rendered in Chromium with a
black-ink, transparent-background signature PNG, and the screenshot is sampled in the
image's padding ring (must be light — the plate) and at its ink stroke (must stay
dark — the ink is never filtered). A light template is the control: no plate needed,
its ring is the page's own light paper.
"""
import base64
import io

import pytest
from PIL import Image, ImageDraw

from applire.schemas.cv import TailoredCVData
from applire.services.color_detection import _default_context
from applire.services.cover_letter import _TEMPLATE_FILES as LETTER_TEMPLATES
from applire.services.cover_letter import _default_color_context
from applire.services.cv import _TEMPLATE_FILES as CV_TEMPLATES
from applire.services.cv import _jinja_env
from applire.templates.labels import cover_letter_labels, cv_labels


def _black_ink_signature() -> str:
    img = Image.new("RGBA", (400, 100), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle((150, 30, 250, 70), fill=(0, 0, 0, 255))  # a solid ink stroke mid-image
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


_CV = TailoredCVData.model_validate({
    "contact": {"name": "Ink Probe", "email": "ink@example.de", "phone": None,
                "location": "Berlin", "photo_url": None},
    "show_photo": False,
    "summary": "Signaturprobe.",
    "work_history": [{"company": "Probe GmbH", "role": "Tester", "start_date": "2020-01",
                      "end_date": None, "bullets": ["Eine Zeile."]}],
    "skills": ["Python"],
})
_LETTER = {
    "header": {"name": "Ink Probe", "address": "Testweg 1, Berlin", "phone": None, "email": None},
    "recipient": {"company": "Probe GmbH", "date": "1. Januar 2026"},
    "body": {"paragraphs": ["Sehr geehrte Damen und Herren,", "Ein kurzer Absatz.", "Gern im Gespräch."]},
    "signature": {"closing": "Mit freundlichen Grüßen", "name": "Ink Probe"},
}


def _render(kind: str, template: str) -> str:
    sig = _black_ink_signature()
    if kind == "cv":
        return _jinja_env.get_template(CV_TEMPLATES[template]).render(
            cv=_CV, color=_default_context(), lang="de", labels=cv_labels("de"),
            signature_image=sig, signature_place_date="Berlin, 1. Januar 2026",
        )
    return _jinja_env.get_template(LETTER_TEMPLATES[template]).render(
        letter=_LETTER, color=_default_color_context(), lang="de",
        labels=cover_letter_labels("de"), subject="Bewerbung", signature_image=sig,
    )


async def _sample(html_out: str) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """(padding-ring pixel, ink-stroke pixel) of the rendered signature image."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            page = await browser.new_page(viewport={"width": 900, "height": 1400})
            await page.set_content(html_out, wait_until="load")
            img = page.locator("img.signature-image")
            await img.scroll_into_view_if_needed()
            box = await img.bounding_box()
            assert box and box["width"] > 10, "signature image not rendered"
            shot = Image.open(io.BytesIO(await page.screenshot(full_page=False))).convert("RGB")
            x0, y0 = box["x"], box["y"]
            # The left padding at mid-height (clear of the rounded corners).
            ring = shot.getpixel((int(x0 + 4), int(y0 + box["height"] / 2)))
            centre = shot.getpixel((int(x0 + box["width"] / 2), int(y0 + box["height"] / 2)))
            return ring, centre
        finally:
            await browser.close()


def _lum(rgb) -> float:
    r, g, b = rgb
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,template", [("cv", "tech_developer"), ("letter", "tech_developer")])
async def test_a_dark_ink_signature_sits_on_a_light_plate(kind, template):
    ring, centre = await _sample(_render(kind, template))
    assert _lum(ring) > 220, f"{kind}/{template}: padding ring {ring} is not a light plate"
    assert _lum(centre) < 40, f"{kind}/{template}: the ink {centre} must stay dark (no filter)"


@pytest.mark.asyncio
async def test_the_light_templates_need_no_plate():
    """Control: on a light page the ring is light anyway — the plate is scoped to the
    dark templates and changes nothing here."""
    ring, centre = await _sample(_render("letter", "classic_german"))
    assert _lum(ring) > 220 and _lum(centre) < 40

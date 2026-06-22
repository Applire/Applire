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

"""US175 + US176 — CV extraction hygiene, REAL-LLM tier (E034 Chocolate riders).

These are the literal behavioural acceptance criteria, which an LLM-mock CI structurally
cannot validate (the prompt rule only constrains a real model). Run:

    INTEGRATION_LLM=1 pytest tests/integration/test_extraction_hygiene_llm.py -v

US175 — two competencies shown at 4-of-5 dots both normalise to the same proficiency level.
US176 — a CV mixing real tools with standards yields a technologies list free of the standards.
"""
import os
import sys
from pathlib import Path

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

pytestmark = pytest.mark.skipif(
    not os.getenv("INTEGRATION_LLM"),
    reason="Real-LLM test — set INTEGRATION_LLM=1 to run",
)


async def _extract(cv_text: str) -> "MasterProfileData":  # noqa: F821
    from applire.providers.llm import get_provider
    from applire.prompts.cv_extraction import (
        GENERIC_CV_EXTRACTION_PROMPT,
        build_generic_prompt,
    )
    from applire.schemas.profile import MasterProfileData

    provider = get_provider()
    raw = await provider.aparse_json(
        build_generic_prompt(cv_text),
        system=GENERIC_CV_EXTRACTION_PROMPT,
        temperature=0.1,
    )
    return MasterProfileData.model_validate(raw)


# ── US175 — deterministic proficiency-scale mapping ────────────────────────────

_DOTS_CV = """\
Lena Hoffmann
Software Engineer

Skills (rated out of 5 dots):
- Python      ●●●●○
- Kubernetes  ●●●●○
- Go          ●●●●●
- Bash        ●●○○○

Experience:
Backend Engineer, Acme GmbH (2020-2024)
- Built and operated microservices.
"""


@pytest.mark.asyncio
async def test_equal_dot_scores_normalise_to_same_level():
    """Python and Kubernetes are both shown at 4/5 dots → both must land on the SAME level
    (US175 determinism), and per the mapping that level is 'advanced'."""
    profile = await _extract(_DOTS_CV)
    by_name = {s.name.lower(): s.proficiency for s in profile.skills}

    assert "python" in by_name, f"Python skill missing; got {list(by_name)}"
    assert "kubernetes" in by_name, f"Kubernetes skill missing; got {list(by_name)}"

    assert by_name["python"] == by_name["kubernetes"], (
        f"equal 4/5 dot scores diverged: python={by_name['python']} "
        f"kubernetes={by_name['kubernetes']}"
    )
    assert by_name["python"] == "advanced", (
        f"4/5 dots must map to 'advanced', got {by_name['python']}"
    )


@pytest.mark.asyncio
async def test_full_dot_score_maps_to_expert():
    """Go at 5/5 dots maps to 'expert' (top of the deterministic scale)."""
    profile = await _extract(_DOTS_CV)
    by_name = {s.name.lower(): s.proficiency for s in profile.skills}
    assert by_name.get("go") == "expert", f"5/5 dots must map to 'expert', got {by_name.get('go')}"


# ── US176 — technologies vs practices hygiene ──────────────────────────────────

_MIXED_CV = """\
Markus Weber
Senior Engineer

Experience:
Lead Developer, Beispiel AG (2019-2024)
- Delivered a payments platform.
- Stack and ways of working: Python, Docker, PostgreSQL, React, Scrum, Agile,
  ISO 25010, V-Model, internal audit-trail processes.
"""

_STANDARDS = {"scrum", "agile", "iso 25010", "v-model", "audit-trail", "internal audit-trail processes"}


@pytest.mark.asyncio
async def test_technologies_free_of_standards():
    """The work entry's technologies list must contain the real tools and exclude every
    practice/standard/methodology (US176)."""
    profile = await _extract(_MIXED_CV)
    assert profile.work_experience, "no work experience extracted"
    techs = {t.lower().strip() for e in profile.work_experience for t in e.technologies}

    # Real tools present
    assert "python" in techs and "docker" in techs, f"expected real tools, got {techs}"
    # Standards/practices absent
    leaked = techs & _STANDARDS
    assert not leaked, f"practices/standards leaked into technologies: {leaked} (full: {techs})"

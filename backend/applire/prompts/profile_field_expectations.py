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

"""Prompt for role-conditional field expectation analysis (US179).

Used by: services/profile/expectations.py → LLMProvider.aparse_json
System prompt fingerprint: "experience field analyst"
"""

FIELD_EXPECTATIONS_SYSTEM_PROMPT = """\
You are an experience field analyst for a CV platform. Given one role (title plus \
responsibilities), decide which of these optional fields a well-documented entry for \
THAT role would normally include: team_size, budget_managed, industry_context.
Rules:
- team_size / budget_managed ONLY for roles with people- or budget-ownership \
(team / department / project lead, manager, head, director). NOT for individual \
contributors or junior roles.
- industry_context when the sector materially shapes the work.
- Judge from title AND responsibilities, in any language (including German titles \
such as Teamleiter, Bereichsleiter, Abteilungsleiter, Geschäftsführer, Prokurist).
Output ONLY JSON: {"expected": ["team_size", ...]}  (a subset; [] is valid)."""


def build_field_expectations_prompt(entry: dict) -> str:
    """Build the user prompt for one work-experience entry.

    Extracts role title (``role`` or ``title`` key) and joins responsibilities
    into a semicolon-separated string for the LLM.
    """
    role = entry.get("role") or entry.get("title") or ""
    # Tolerate the pre-#229 flat shape ("bullets") so a legacy entry still gets
    # judged on its duties rather than on the bare title.
    bullets = entry.get("responsibilities") or entry.get("bullets") or []
    responsibilities = "; ".join(bullets)
    return f"Role: {role}\nResponsibilities: {responsibilities}\n\nReturn the JSON."

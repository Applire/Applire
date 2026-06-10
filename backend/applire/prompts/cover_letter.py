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

"""LLM prompt builder for cover letter generation.

The system prompt instructs the model to output strictly valid JSON
matching the letter_data schema. The user prompt provides all context.
"""

import json
from typing import Any

SYSTEM_PROMPT = """You are an expert DACH career coach writing a professional Bewerbungsschreiben (German cover letter).
Output ONLY a single valid JSON object. No markdown, no explanation, no prose outside the JSON.

The JSON must match this schema exactly:
{
  "header": {
    "name": "string",
    "address": "string",
    "phone": "string or null",
    "email": "string or null",
    "photo_url": "string or null"
  },
  "recipient": {
    "name": "string or null",
    "title": "string or null",
    "company": "string or null",
    "address": "string or null",
    "date": "null — the system inserts the letter date after generation; always output null"
  },
  "body": {
    "paragraphs": ["opening paragraph", "main paragraph 1", "main paragraph 2", "closing paragraph"]
  },
  "signature": {
    "closing": "Mit freundlichen Grüßen",
    "name": "string"
  }
}

Rules:
- Write the ENTIRE letter in the language given in the LANGUAGE line of the user message (DE = German, EN = English).
  Never mirror the language of the job description or the candidate profile when it differs from LANGUAGE.
- For German letters: use formal Sie-form, classic Bewerbungsschreiben structure.
- Include Gehaltswunsch in body only if salary is provided.
- Include Eintrittstermin in body only if availability is provided.
- Body should have 3-4 paragraphs: opening (interest + role), why-me (key achievements), company-fit, closing.
- Keep total letter body under 400 words.
- Use the tone specified: formal=sehr geehrte/r, professional=warm but polished, conversational=direct.
"""


def build_cover_letter_prompt(
    cv_data: dict[str, Any],
    jd_text: str,
    pre_gen_inputs: dict[str, Any],
    detected_language: str,
) -> str:
    """Build the user-turn prompt for the LLM.

    Returns a single string to pass as the user message.
    cv_data: the tailored_data dict from GeneratedCV (contact, summary, work_history, skills).
    jd_text: job.raw_text
    pre_gen_inputs: dict with keys salary, availability, motivation, tone, recipient_name, recipient_company.
    detected_language: 'de' or 'en'
    """
    salary = pre_gen_inputs.get("salary", "")
    availability = pre_gen_inputs.get("availability", "")
    motivation = pre_gen_inputs.get("motivation", "")
    tone = pre_gen_inputs.get("tone", "formal")
    recipient_name = pre_gen_inputs.get("recipient_name", "")
    recipient_company = pre_gen_inputs.get("recipient_company", "")

    contact = cv_data.get("contact", {})
    summary = cv_data.get("summary", "")
    skills = cv_data.get("skills", [])
    work_history = cv_data.get("work_history", [])

    # Build a condensed profile snippet (top 3 work entries, top 10 skills)
    work_snippet = ""
    for entry in work_history[:3]:
        work_snippet += f"- {entry.get('role', '')} at {entry.get('company', '')} ({entry.get('start_date', '')}–{entry.get('end_date', 'present')})\n"
        for bullet in entry.get("bullets", [])[:2]:
            work_snippet += f"  • {bullet}\n"

    skills_snippet = ", ".join(
        s if isinstance(s, str) else s.get("name", "")
        for s in skills[:10]
    ) if skills else "—"

    lines = [
        f"LANGUAGE: {detected_language.upper()}",
        f"TONE: {tone}",
        "",
        "=== CANDIDATE PROFILE ===",
        f"Name: {contact.get('name', '')}",
        f"Email: {contact.get('email', '')}",
        f"Phone: {contact.get('phone', '')}",
        f"Location: {contact.get('location', '')}",
        f"Summary: {summary}",
        f"Key skills: {skills_snippet}",
        "Recent experience:",
        work_snippet.strip(),
        "",
        "=== JOB DESCRIPTION ===",
        jd_text[:3000],  # truncate very long JDs
        "",
        "=== PRE-GENERATION INPUTS ===",
        f"Recipient name: {recipient_name or '(extract from JD or use generic salutation)'}",
        f"Recipient company: {recipient_company or '(extract from JD)'}",
    ]

    if salary:
        lines.append(f"Gehaltswunsch (salary expectation): {salary}")
    if availability:
        lines.append(f"Eintrittstermin (availability/notice period): {availability}")
    if motivation:
        lines.append(f"Personal motivation (incorporate naturally): {motivation}")

    lines += [
        "",
        "Generate the cover letter JSON now.",
    ]

    return "\n".join(lines)

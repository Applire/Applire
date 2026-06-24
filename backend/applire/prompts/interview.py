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

# Prompt version: v4
# Used by: services/interview_graph.py
#
# v4 changes vs v3 (Sprint 15):
#   - RESPONSE_PARSER_SYSTEM_PROMPT: schema extended with certifications_to_add,
#     languages_to_add, education_to_add, gap_resolution ("full"|"partial"|"none"),
#     follow_up_hint, gaps_also_addressed. gap_addressed bool replaced by gap_resolution enum.
#   - build_response_parser_prompt: accepts optional remaining_gaps list for cross-gap context
#
# v3 changes vs v2:
#   - Add GUIDED_QUESTION_SYSTEM_PROMPT for MODE B (section-building questions)
#   - Add build_guided_question_prompt() for MODE B
#   - v2 functionality unchanged
#
# v2 changes vs v1:
#   - build_question_prompt accepts an optional gap_category ("B" | "C" | None)
#   - Category B gaps produce confirmation questions ("You likely have X — can you describe it?")
#   - Category C gaps produce exploratory questions ("Tell me about your experience with X")

import json

_LANG_NAMES = {"de": "German", "en": "English"}


def with_language(system_prompt: str, lang: str) -> str:
    """Append an explicit output-language directive to a question system prompt.

    The directive is placed at the end of the system prompt and instructs the
    model to write the question (and choices) in the UI language regardless of
    the language of any context — overriding the JD-language ``jd_context``.
    """
    name = _LANG_NAMES.get(lang, "English")
    return system_prompt + (
        f"\n\nIMPORTANT — OUTPUT LANGUAGE: Write the question and any answer "
        f"choices in {name}. This is fixed regardless of the language of the "
        f"candidate's profile, the job description, or any context provided "
        f"below. Translate any context as needed; never mirror its language."
    )


def language_name(lang: str) -> str:
    """Human-readable language name for a UI locale code (defaults to English)."""
    return _LANG_NAMES.get(lang, "English")


# ---------------------------------------------------------------------------
# QuestionGenerator node — MODE A (Targeted Gap-Fill)
# ---------------------------------------------------------------------------

QUESTION_SYSTEM_PROMPT = """\
You are an expert career coach specialised in the DACH (Germany, Austria, Switzerland) job market.
Your task is to generate ONE targeted, open-ended question to help a job seeker articulate concrete \
experience that addresses a specific skill cluster gap in their profile.

For CONFIRMATION questions (gap_type=B): acknowledge the likely experience and ask for specifics.
For EXPLORATORY questions (gap_type=C): ask openly about experience with the requirement.

Generate 2-3 short answer choices when:
- The cluster has 2 or more constituent gaps, OR
- The cluster category is B (confirmation question)
Choices are starting-point options the candidate can select and expand; they are not exhaustive.
Otherwise set choices to null.

Requirements:
- Ask about exactly ONE aspect related to the cluster
- Be encouraging and conversational in tone
- Invite specific examples: projects, companies, dates, measurable outcomes
- Output ONLY a valid JSON object - no markdown, no explanations

Schema:
{
  "question": "The question text",
  "choices": ["Option A", "Option B"] or null
}"""


def build_question_prompt(
    cluster: dict,
    profile: dict,
    recent_messages: list[dict],
    gap_category: str | None = None,
) -> str:
    history = ""
    if recent_messages:
        lines = [f"{m['role'].capitalize()}: {m['content']}" for m in recent_messages[-4:]]
        history = "\n\nRecent conversation:\n" + "\n".join(lines)

    profile_summary = json.dumps(
        {
            "skills": profile.get("skills", []),
            "work_history": [
                {"company": e.get("company"), "role": e.get("role")}
                for e in profile.get("work_experience", [])
            ],
        },
        ensure_ascii=False,
    )

    cluster_label = cluster.get("label", cluster.get("id", "unknown"))
    constituent_gaps = cluster.get("gaps", [])
    jd_skills = cluster.get("jd_skills", [])
    jd_context = cluster.get("jd_context", "")
    num_gaps = len(constituent_gaps)

    if gap_category == "B":
        gap_type_hint = (
            f"Gap type: CONFIRMATION (Category B) — "
            f"the system inferred the candidate likely has experience with '{cluster_label}'. "
            f"Generate a confirmation question that acknowledges this likelihood and asks for concrete specifics."
        )
    else:
        gap_type_hint = (
            f"Gap type: EXPLORATORY (Category C) — "
            f"no signal for '{cluster_label}' was found in the profile. "
            f"Generate an open question to uncover any relevant experience."
        )

    choices_hint = (
        f"Generate 2-3 choices (num_gaps={num_gaps}, category={gap_category or 'C'})."
        if (num_gaps >= 2 or gap_category == "B")
        else "Set choices to null."
    )

    cluster_context = f"Cluster: {cluster_label}"
    if constituent_gaps:
        cluster_context += f"\nConstituent gaps: {', '.join(constituent_gaps)}"
    if jd_skills:
        cluster_context += f"\nRelevant JD skills: {', '.join(jd_skills)}"
    if jd_context:
        cluster_context += f"\nJD context: {jd_context}"

    return (
        f"{cluster_context}\n"
        f"{gap_type_hint}\n"
        f"{choices_hint}\n\n"
        f"Candidate profile summary:\n{profile_summary}"
        f"{history}\n\n"
        "Generate the JSON response."
    )


# ---------------------------------------------------------------------------
# QuestionGenerator node — MODE B (Guided Build)
# ---------------------------------------------------------------------------

# Section labels shown in the prompt — maps internal key to human-readable name
_SECTION_LABELS: dict[str, str] = {
    "personal_info": "Personal Information",
    "work_experience": "Work Experience",
    "education": "Education",
    "skills": "Skills",
    "languages": "Languages",
    "certifications": "Certifications",
    "professional_summary": "Professional Summary",
    "publications": "Publications",
    "volunteer_activities": "Volunteer Activities",
}

_SECTION_GUIDANCE: dict[str, str] = {
    "personal_info": (
        "Ask for the candidate's full name, location (city and country), phone number, "
        "email address, and any relevant professional profile URLs (LinkedIn, GitHub, etc.)."
    ),
    "work_experience": (
        "Ask the candidate to describe their most recent or most relevant role: "
        "company name, job title, dates, key responsibilities, and notable achievements. "
        "Encourage quantified outcomes (team size, budget, revenue impact)."
    ),
    "education": (
        "Ask about the candidate's highest or most relevant educational qualification: "
        "institution, degree, field of study, graduation year, and any notable thesis or "
        "coursework relevant to the target role."
    ),
    "skills": (
        "Ask the candidate to list their technical skills: programming languages, frameworks, "
        "tools, platforms, and methodologies. Encourage honesty about proficiency level."
    ),
    "languages": (
        "Ask which languages the candidate speaks and at what proficiency level "
        "(native, fluent, professional, basic). German and English are particularly relevant "
        "for the DACH market."
    ),
    "certifications": (
        "Ask about professional certifications, licences, or accreditations the candidate holds: "
        "name, issuing body, and year obtained."
    ),
    "professional_summary": (
        "Ask the candidate to describe their professional identity in 2–3 sentences: "
        "their core expertise, years of experience, and what type of role they are targeting."
    ),
    "publications": (
        "Ask whether the candidate has any publications, conference papers, or technical articles "
        "relevant to the role."
    ),
    "volunteer_activities": (
        "Ask whether the candidate has any volunteer work, open-source contributions, or community "
        "involvement relevant to their professional profile."
    ),
}

GUIDED_QUESTION_SYSTEM_PROMPT = """\
You are an expert career coach specialised in the DACH (Germany, Austria, Switzerland) job market.
Your task is to generate ONE warm, encouraging question to help a job seeker build their professional \
profile from scratch — section by section.

Requirements:
- Ask about exactly ONE profile section
- Be welcoming and conversational — the candidate has no existing CV to fall back on
- Invite specific, concrete details (dates, names, numbers, outcomes)
- Adapt your tone to the DACH market: professional, precise, and respectful
- Output ONLY the question text — no preamble, no numbering, no explanation"""


def build_guided_question_prompt(
    section: str,
    job_context: dict,
    recent_messages: list[dict],
) -> str:
    """Build the prompt for a MODE B (Guided Build) section question.

    section: one of _VALID_SECTIONS keys
    job_context: {"role_title": str, "seniority_level": str}
    recent_messages: last N messages from the conversation
    """
    history = ""
    if recent_messages:
        lines = [f"{m['role'].capitalize()}: {m['content']}" for m in recent_messages[-4:]]
        history = "\n\nRecent conversation:\n" + "\n".join(lines)

    label = _SECTION_LABELS.get(section, section.replace("_", " ").title())
    guidance = _SECTION_GUIDANCE.get(section, f"Ask the candidate about their {label}.")

    role_ctx = ""
    if job_context.get("role_title"):
        role_ctx = (
            f"\nTarget role context: {job_context['role_title']}"
            + (
                f" ({job_context['seniority_level']})"
                if job_context.get("seniority_level")
                else ""
            )
        )

    return (
        f"Profile section to build: {label}\n"
        f"Guidance: {guidance}"
        f"{role_ctx}"
        f"{history}\n\n"
        "Generate the question."
    )


# ---------------------------------------------------------------------------
# QuestionGenerator node — Follow-up (lateral probe, Sprint 15)
# ---------------------------------------------------------------------------

FOLLOW_UP_QUESTION_SYSTEM_PROMPT = """\
You are an expert career coach specialised in the DACH (Germany, Austria, Switzerland) job market.
The candidate has not yet fully addressed a specific gap in their profile.
Do NOT re-ask the original question. Instead, probe an adjacent domain or analogous
context that is likely to surface the missing experience indirectly.

Requirements:
- Lead with the adjacent domain suggested in the follow-up hint
- Be concrete: name the technology, regulation, industry, or context to explore
- Remain encouraging — the candidate may simply not have recognised the connection
- Output ONLY the question text — no preamble, no numbering, no explanation"""


def build_follow_up_question_prompt(
    gap: str,
    follow_up_hint: str,
    profile: dict,
    recent_messages: list[dict],
    gap_category: str | None = None,
) -> str:
    """Build the prompt for a lateral-probe follow-up question.

    gap: the gap that was not fully addressed
    follow_up_hint: suggested adjacent domain (from ResponseParser)
    profile: candidate's current profile dict
    recent_messages: last N messages from the conversation
    gap_category: "B" | "C" | None
    """
    history = ""
    if recent_messages:
        lines = [f"{m['role'].capitalize()}: {m['content']}" for m in recent_messages[-4:]]
        history = "\n\nRecent conversation:\n" + "\n".join(lines)

    profile_summary = json.dumps(
        {
            "skills": profile.get("skills", []),
            "work_history": [
                {"company": e.get("company"), "role": e.get("role")}
                for e in profile.get("work_experience", [])
            ],
        },
        ensure_ascii=False,
    )

    return (
        f"Gap not yet addressed: {gap}\n"
        f"Follow-up direction: {follow_up_hint}\n\n"
        f"Candidate profile summary:\n{profile_summary}"
        f"{history}\n\n"
        "Generate the follow-up question probing the adjacent domain."
    )


# ---------------------------------------------------------------------------
# QuestionGenerator node — MODE C (Field-Gap, Profile Enrich)
# ---------------------------------------------------------------------------

_FIELD_GAP_GUIDANCE: dict[str, str] = {
    "achievements": "Ask for ONE concrete, quantified achievement the candidate delivered in THIS specific role.",
    "team_size": "Ask how many people reported to them in THIS role (zero is a valid answer).",
    "budget_managed": "Ask what budget (size / currency) they were responsible for in THIS role.",
    "industry_context": "Ask which industry or sector THIS role was in.",
    "start_date": "Ask when they started THIS role (month and year).",
    "end_date": "Ask when they left THIS role, or whether it is current.",
}


def build_field_gap_question_prompt(field: str, entry: dict, recent_messages: list[dict]) -> str:
    """MODE C (profile enrich): a targeted question about ONE missing field on ONE
    known position. Anchors on the actual role/company/responsibilities so the model
    fills the specific gap rather than exploring openly (issue #66)."""
    role = entry.get("role") or entry.get("title") or ""
    company = entry.get("company") or ""
    responsibilities = "; ".join(entry.get("responsibilities", []) or [])
    guidance = _FIELD_GAP_GUIDANCE.get(field, f"Ask the candidate about the {field} for this role.")
    history = ""
    if recent_messages:
        lines = [f"{m['role'].capitalize()}: {m['content']}" for m in recent_messages[-4:]]
        history = "\n\nRecent conversation:\n" + "\n".join(lines)
    return (
        f"Position: {role} at {company}\n"
        f"What they did: {responsibilities}\n"
        f"Missing detail: {field}\n"
        f"{guidance}\n"
        f"Ask ONE specific question about THIS position only.{history}\n\n"
        "Generate the question."
    )


# ---------------------------------------------------------------------------
# ResponseParser node
# ---------------------------------------------------------------------------

RESPONSE_PARSER_SYSTEM_PROMPT = """\
You are an expert career coach extracting structured profile data from a candidate's free-text answer.
Respond ONLY with a valid JSON object matching the schema below — no markdown, no explanations.

Schema:
{
  "skills_to_add": ["list of concrete skills explicitly mentioned"],
  "work_history_to_add": [
    {
      "company": "Company name or null",
      "role": "Job title or null",
      "start_date": "YYYY-MM or YYYY or null",
      "end_date": "YYYY-MM or YYYY or null (null = current)",
      "bullets": ["achievement or responsibility mentioned"]
    }
  ],
  "certifications_to_add": [
    {"name": "Certification name", "issuing_body": "Issuing body or null", "year": "YYYY or null"}
  ],
  "languages_to_add": [
    {"language": "Language name", "level": "native|fluent|professional|basic"}
  ],
  "education_to_add": [
    {
      "institution": "Institution name",
      "degree": "Degree title",
      "field": "Field of study or null",
      "graduation_year": "YYYY or null"
    }
  ],
  "gap_resolution": "full or partial or declined or none",
  "follow_up_hint": "Short suggestion for adjacent domain to probe, or null"
}

Rules:
- Only include data EXPLICITLY stated in the answer — do not infer or fabricate
- gap_resolution: "full" if the answer provides concrete, specific information about the gap;
  "partial" if relevant but incomplete or vague;
  "declined" if the candidate clearly states they have NO experience with this gap and offers
  no transferable/adjacent experience either (e.g. "I've never worked with that", "no IoT experience");
  "none" if off-topic or empty
- follow_up_hint: when gap_resolution is "partial", suggest a related domain or context the
  candidate might have experience in. Set to null for "full" and "declined" — do not keep probing
  a gap the candidate has explicitly ruled out.
- Omit work_history_to_add entries where role is null or empty"""


def build_response_parser_prompt(
    cluster_label: str,
    question: str,
    answer: str,
) -> str:
    return (
        f"Gap cluster being addressed: {cluster_label}\n\n"
        f"Question asked: {question}\n\n"
        f"Candidate's answer: {answer}\n\n"
        "Extract the structured profile data."
    )

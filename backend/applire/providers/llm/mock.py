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

"""Mock LLM provider for CI/CD and E2E testing — no API key required.

Activated via LLM_PROVIDER=mock in .env.ci.

Detection strategy: inspects the `system` prompt to identify which service
is calling, then returns a canned schema-valid response instantly.

System prompt fingerprints:
  "HR analyst"                     → job analysis          (aparse_json → dict)
  "CV analyst"                     → profile parsing       (aparse_json → dict)
  "three-category gap analysis"    → gap analysis          (aparse_json → dict)
  "extracting structured profile"  → response parser       (aparse_json → dict)
  "dach career consultant"         → CV tailoring          (aparse_json → dict)
  "expert career analyst"          → gap clustering        (aparse_json → list)
  "expert career coach"            → targeted question     (aparse_json → dict)
  "expert dach career coach"       → cover letter          (aparse_json → dict)
  "cv profile corrector"           → CV extraction refinement   (aparse_json → dict)
  "profile data corrector"         → profile extraction refinement (aparse_json → dict)
  "tailored cv corrector"          → CV tailoring refinement    (aparse_json → dict)
  "answer parser corrector"        → response parser refinement (aparse_json → dict)
  "experience field analyst"       → role field expectations    (aparse_json → dict, prompt-keyed)
  (acomplete, any)                 → interview question    (acomplete → str)
"""

import copy
import json
import re
from typing import Any

from applire.providers.llm.base import LLMProvider


_JOB_ANALYSIS_RESPONSE: dict[str, Any] = {
    "company_name": "TechVision GmbH",
    "role_title": "Senior Software Engineer",
    "required_skills": ["Python", "FastAPI", "PostgreSQL", "Docker", "REST APIs", "CI/CD pipelines", "5+ years Python experience"],
    "nice_to_have_skills": ["Kubernetes", "GraphQL", "Redis"],
    "keywords": ["backend", "microservices", "CI/CD", "agile", "DACH"],
    "seniority_level": "Senior",
    "company_culture_signals": ["agile", "remote-friendly", "innovation-driven"],
    "language_requirement": "German B2 or English fluent",
}

_PROFILE_PARSE_RESPONSE: dict[str, Any] = {
    # Rich profile: completeness = personal_info (0.15) + work_experience (0.40)
    #               + languages (0.10) = 0.65 — passes the > 0.6 upload/import assertions.
    # NOTE: _RESPONSE_PARSER_RESPONSE stays sparse so user_type stays "new" in
    #       interview tests (those go through "extracting structured profile", not here).
    "personal_info": {
        "name": "Anna Bauer",
        "email": "anna.bauer@example.de",
        "phone": "+49 170 1234567",
        "location": "Munich, Germany",
    },
    "work_experience": [
        {
            "company": "TechVision GmbH",
            "role": "Senior Software Engineer",
            "start_date": "2021-03",
            "end_date": None,
            "is_current": True,  # #155 — extraction marks ongoing roles explicitly
            "description": "Backend development with Python and FastAPI.",
            "bullets": [
                "Built REST APIs serving 50k daily active users.",
                "Introduced CI/CD pipelines, reducing deploy time by 40%.",
            ],
        },
        {
            "company": "StartupX AG",
            "role": "Software Engineer",
            "start_date": "2018-06",
            "end_date": "2021-02",
            "description": "Full-stack development in an agile team.",
            "bullets": [
                "Improved test coverage from 30% to 85% using pytest.",
            ],
        },
    ],
    "education": [
        {
            "institution": "Technische Universität Berlin",
            "degree": "Master of Science",
            "field": "Computer Science",
            "start_date": "2016-10",
            "end_date": "2018-05",
        }
    ],
    "skills": [
        {"name": "Python", "category": "technical", "proficiency": "expert"},
        {"name": "FastAPI", "category": "technical", "proficiency": "advanced"},
        {"name": "PostgreSQL", "category": "technical", "proficiency": "advanced"},
        {"name": "Docker", "category": "technical", "proficiency": "intermediate"},
        {"name": "Git", "category": "technical", "proficiency": "advanced"},
    ],
    "languages": [
        {"language": "German", "level": "Native"},
        {"language": "English", "level": "C1"},
    ],
    "projects": [
        {
            "name": "CI/CD Migration",
            "description": "Migrated legacy Jenkins pipelines to GitHub Actions.",
            "role": "Lead Developer",
            "start_date": "2022-01",
            "end_date": "2022-06",
            "responsibilities": ["Designed pipeline architecture", "Wrote reusable workflow templates"],
            "achievements": ["Reduced average build time from 18 min to 4 min (−78%)"],
            "technologies": ["GitHub Actions", "Docker", "Python"],
            "url": None,
            "associated_experience": "TechVision GmbH",
        }
    ],
}

_GAP_ANALYSIS_RESPONSE: dict[str, Any] = {
    # One entry per JD requirement (required_skills + nice_to_have_skills).
    # Statuses are grounded in _PROFILE_PARSE_RESPONSE for Anna Bauer.
    # Score (REQUIRED_SLOT=1.0, NICE_TO_HAVE_SLOT=0.5, direct=1.0, partial=0.5, gap=0.0):
    #   earned = 6×1.0 + 0×1.0 + 1×0.5×0.5 + 0×0.5 + 0×0.5 = 6.25
    #   total  = 7×1.0 + 3×0.5 = 8.5  →  score ≈ 0.735
    # Each item also carries surface_forms (literal ATS aliases, ADR-048) — the
    # gap LLM groups keyword variants under the concept the candidate holds.
    "classifications": [
        # required_skills — all 7 must be covered
        {"requirement": "Python",                    "status": "direct",  "reason": "listed as expert in skills", "surface_forms": ["Python"]},
        {"requirement": "FastAPI",                   "status": "direct",  "reason": "listed as advanced in skills", "surface_forms": ["FastAPI"]},
        {"requirement": "PostgreSQL",                "status": "direct",  "reason": "listed as advanced in skills", "surface_forms": ["PostgreSQL", "Postgres"]},
        {"requirement": "Docker",                    "status": "direct",  "reason": "listed as intermediate in skills", "surface_forms": ["Docker"]},
        {"requirement": "REST APIs",                 "status": "direct",  "reason": "built REST APIs serving 50k DAU", "surface_forms": ["REST APIs", "REST"]},
        {"requirement": "CI/CD pipelines",           "status": "direct",  "reason": "introduced CI/CD pipelines in current role", "surface_forms": ["CI/CD pipelines", "CI/CD"]},
        {"requirement": "5+ years Python experience","status": "gap",     "reason": "duration not explicitly stated in profile", "surface_forms": ["5+ years Python experience"]},
        # nice_to_have_skills — all 3 covered
        {"requirement": "Kubernetes",                "status": "partial", "reason": "adjacent Docker experience, no Kubernetes explicitly", "surface_forms": ["Kubernetes", "K8s"]},
        {"requirement": "GraphQL",                   "status": "gap",     "reason": "no signal in profile", "surface_forms": ["GraphQL"]},
        {"requirement": "Redis",                     "status": "gap",     "reason": "no signal in profile", "surface_forms": ["Redis"]},
        # keyword-only ATS terms (fit_weight 0 — classified for coverage, ADR-048)
        {"requirement": "backend",                   "status": "direct",  "reason": "backend engineer across roles", "surface_forms": ["backend", "back-end"]},
        {"requirement": "microservices",             "status": "gap",     "reason": "no microservices signal in profile", "surface_forms": ["microservices", "microservices architecture"]},
        {"requirement": "agile",                     "status": "direct",  "reason": "agile delivery in current role", "surface_forms": ["agile", "Scrum"]},
        {"requirement": "DACH",                      "status": "partial", "reason": "German-speaking, DACH market context", "surface_forms": ["DACH"]},
    ],
    "strengths": ["Python", "FastAPI", "PostgreSQL", "REST APIs", "CI/CD pipelines"],
    "keyword_gaps": ["microservices architecture", "Kubernetes", "GraphQL"],
}

_CV_TAILORING_RESPONSE: dict[str, Any] = {
    # Valid TailoredCVData — all required fields present.
    # contact.name matches the iter9 LinkedIn fixture (Anna Bauer) used by CV template tests.
    "contact": {
        "name": "Anna Bauer",
        "email": "anna.bauer@example.de",
        "phone": "+49 170 1234567",
        "location": "Berlin, Germany",
        "linkedin": None,
    },
    "summary": (
        "Experienced software engineer with a strong background in Python and FastAPI, "
        "specialising in backend systems for the DACH market. "
        "Proven track record delivering scalable REST APIs and CI/CD pipelines."
    ),
    "work_history": [
        {
            "company": "TechVision GmbH",
            "role": "Senior Software Engineer",
            "start_date": "2021-03",
            "end_date": None,
            "bullets": [
                "Designed and implemented microservices with FastAPI and PostgreSQL.",
                "Introduced CI/CD pipelines via GitHub Actions, reducing deploy time by 40%.",
                "Led migration from monolith to containerised Docker architecture.",
            ],
        },
        {
            "company": "StartupX AG",
            "role": "Software Engineer",
            "start_date": "2018-06",
            "end_date": "2021-02",
            "bullets": [
                "Built REST APIs serving 50k daily active users.",
                "Improved test coverage from 30% to 85% using pytest.",
            ],
        },
    ],
    "skills": ["Python", "FastAPI", "PostgreSQL", "Docker", "REST APIs", "CI/CD", "Git"],
    "education": [
        {
            "institution": "Technische Universität Berlin",
            "degree": "Master of Science",
            "field": "Computer Science",
            "start_date": "2016-10",
            "end_date": "2018-05",
        }
    ],
    "languages": [
        {"language": "German", "level": "Native"},
        {"language": "English", "level": "C1"},
    ],
}

_RECONCILE_RESPONSE: dict[str, Any] = {
    "ops": [
        {"op": "upsert_skill", "name": "Python", "category": "technical",
         "proficiency": "advanced", "evidence": []},
    ],
    "ambiguities": [],
}

# US185 — when the reconciler is unsure whether a new role is the same as an
# existing one it must ASK, not guess. Triggered (mock-side) by the synonym-fold
# UAT fixture ("Owner at applire" vs an existing "Founder & Lead Developer") so
# the confirmation surfacing path is exercised deterministically under the mock.
_RECONCILE_AMBIGUITY_RESPONSE: dict[str, Any] = {
    "ops": [],
    "ambiguities": [
        {
            "op": "request_confirmation",
            "question": (
                "Is 'Owner at applire' the same as your existing "
                "'Founder & Lead Developer' role?"
            ),
            "options": ["Yes, same role", "No, separate roles"],
            "context": {
                "existing": "Founder & Lead Developer",
                "incoming": "Owner at applire",
            },
        }
    ],
}

_RESPONSE_PARSER_RESPONSE: dict[str, Any] = {
    # skills_to_add must include at least one of the skills named in _RICH_ANSWER
    # (iter4 test_profile_updated_after_answer checks for "salesforce", "veeva vault", "crm").
    "skills_to_add": ["Salesforce", "Veeva Vault", "CRM"],
    "work_history_to_add": [],
    "gap_addressed": True,
    "gap_resolution": "full",
    "gaps_also_addressed": [],
}

_CLUSTERING_RESPONSE: list[dict] = [
    {
        "id": "cluster-python-experience",
        "label": "Python Experience Depth",
        "category": "C",
        "gaps": ["5+ years Python experience"],
        "jd_skills": ["Python", "FastAPI"],
        "jd_context": "Senior role requiring deep Python expertise",
    },
    {
        "id": "cluster-cloud-infra",
        "label": "Cloud & Infrastructure",
        "category": "B",
        "gaps": ["Kubernetes", "microservices architecture"],
        "jd_skills": ["Kubernetes", "Docker"],
        "jd_context": "Containerised microservices with Kubernetes orchestration",
    },
]

_QUESTION_RESPONSE: dict[str, Any] = {
    "question": (
        "Can you describe your experience with Python in a professional context, "
        "including projects where you used it extensively?"
    ),
    "choices": [
        "5+ years hands-on Python in production systems",
        "Used Python primarily for scripting or smaller projects",
        "Mostly self-taught or academic Python experience",
    ],
}

_COVER_LETTER_RESPONSE: dict[str, Any] = {
    "header": {
        "name": "Anna Bauer",
        "address": "Hauptstraße 42, 10115 Berlin",
        "phone": "+49 170 1234567",
        "email": "anna.bauer@example.de",
        "photo_url": None,
    },
    "recipient": {
        "name": "Herr Dr. Müller",
        "title": "Personalleiter",
        "company": "TechVision GmbH",
        "address": "Unter den Linden 1, 10117 Berlin",
        "date": "02. Mai 2026",
    },
    "body": {
        "paragraphs": [
            (
                "Sehr geehrter Herr Dr. Müller, mit großem Interesse habe ich Ihre "
                "Stellenausschreibung als Senior Software Engineer gelesen und bewerbe "
                "mich hiermit auf diese Position."
            ),
            (
                "Als erfahrene Software-Ingenieurin mit über sechs Jahren Praxis in "
                "Python und FastAPI bringe ich die gesuchten Kernkompetenzen vollständig "
                "mit. Bei TechVision GmbH habe ich skalierbare REST-APIs entwickelt und "
                "CI/CD-Prozesse eingeführt, die die Deployment-Zeit um 40 % reduzierten."
            ),
            (
                "Ihr Fokus auf Microservice-Architekturen und agile Entwicklungsmethoden "
                "spricht mich besonders an, da ich in diesem Umfeld bereits erfolgreich "
                "gearbeitet habe und weitere Impulse setzen möchte."
            ),
            (
                "Über die Möglichkeit, mich in einem persönlichen Gespräch vorzustellen, "
                "würde ich mich sehr freuen. Meine Gehaltsvorstellung liegt bei "
                "95.000 € brutto jährlich."
            ),
        ]
    },
    "signature": {
        "closing": "Mit freundlichen Grüßen",
        "name": "Anna Bauer",
    },
}

_INTERVIEW_QUESTION = (
    "Can you describe a specific project where you implemented CI/CD pipelines "
    "and explain the tools and processes you used?"
)


class MockLLMProvider(LLMProvider):
    """Instant, deterministic LLM provider for CI/CD and E2E tests.

    Returns canned schema-valid responses without any network call.
    Identifies the calling service from the system prompt fingerprint.
    """

    async def acomplete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        disable_thinking: bool | None = None,
    ) -> str:
        return _INTERVIEW_QUESTION

    async def aparse_json(  # type: ignore[override]
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        disable_thinking: bool | None = None,
    ) -> Any:
        system_lower = (system or "").lower()

        if "language reviewer" in system_lower:
            return {"approved": True, "issues": [], "feedback": ""}

        # ADR-046 / US181-US182a — single-call profile reconciler. Returns a
        # representative op batch (one upsert_skill) so the interview loop's
        # apply + advance path can be exercised deterministically under the mock.
        if "profile reconciler" in system_lower:
            # US185 — the synonym-fold UAT answer triggers a confirmation request
            # so the human-in-the-loop path is exercised under the mock.
            if "owner at applire" in prompt.lower():
                return copy.deepcopy(_RECONCILE_AMBIGUITY_RESPONSE)
            return copy.deepcopy(_RECONCILE_RESPONSE)

        if "hr analyst" in system_lower:
            return dict(_JOB_ANALYSIS_RESPONSE)

        if "cv analyst" in system_lower:
            return dict(_PROFILE_PARSE_RESPONSE)

        if "expert career analyst" in system_lower:
            # #166: return the DICT envelope every real provider produces under
            # forced JSON-object mode. The mock previously returned a bare list —
            # a shape NO real provider can emit — so CI stayed green while every
            # production clustering call collapsed to [] (false "strong match").
            return {"clusters": list(_CLUSTERING_RESPONSE)}

        if "three-category gap analysis" in system_lower:
            return dict(_GAP_ANALYSIS_RESPONSE)

        if "extracting structured profile" in system_lower:
            return dict(_RESPONSE_PARSER_RESPONSE)

        # ADR-047 / US195 — segmented CV extraction (outline → per-role detail → core).
        # Each slice mirrors _PROFILE_PARSE_RESPONSE so the assembled profile matches the
        # monolithic mock. Unrecognised → {"mock":...} fallback corrupts a capped-mock
        # extraction run.
        if "cv experience outliner" in system_lower:
            return {"work_experience": [
                {k: w.get(k) for k in ("company", "role", "start_date", "end_date", "is_current")}
                for w in (_PROFILE_PARSE_RESPONSE.get("work_experience") or [])
            ]}
        if "cv experience detail extractor" in system_lower:
            return {"responsibilities": [], "achievements": [], "technologies": []}
        if "cv core profile extractor" in system_lower:
            core = {k: v for k, v in _PROFILE_PARSE_RESPONSE.items() if k != "work_experience"}
            return copy.deepcopy(core)

        # ADR-047 / US189 — segmented CV tailoring (outline-then-expand). Each section
        # writer returns its own small schema-valid slice; the orchestrator assembles them.
        if "cv outline planner" in system_lower:
            return {
                "role_order": [],
                "summary_angle": "backend delivery focus for the DACH market",
                "skills_focus": ["Python", "FastAPI", "PostgreSQL"],
                "per_role_themes": {},
            }
        if "cv work experience writer" in system_lower:
            return {
                "bullets": [
                    "Designed and implemented microservices with FastAPI and PostgreSQL.",
                    "Introduced CI/CD pipelines via GitHub Actions.",
                ],
                "projects": [],
            }
        if "cv summary writer" in system_lower:
            return {"summary": _CV_TAILORING_RESPONSE["summary"]}
        if "cv skills writer" in system_lower:
            return {"skills": list(_CV_TAILORING_RESPONSE["skills"])}
        if "cv education writer" in system_lower:
            return {
                "education": [dict(e) for e in _CV_TAILORING_RESPONSE["education"]],
                "languages": [{"language": "German", "level": "Native"},
                              {"language": "English", "level": "C1"}],
            }
        if "cv projects writer" in system_lower:
            return {"projects": []}

        if "dach career consultant" in system_lower:
            return dict(_CV_TAILORING_RESPONSE)

        if "expert career coach" in system_lower:
            return dict(_QUESTION_RESPONSE)

        if "dach career coach" in system_lower:
            return dict(_COVER_LETTER_RESPONSE)

        # US170 — cover-letter grounding reviewer + corrector (review_cover_letter).
        # Must be recognised: an unrecognised reviewer falls through to the fallback
        # ({"mock": ...}, approved=None), which fails review_and_refine and ships a
        # corrupt letter under the mock provider.
        if "cover-letter quality auditor" in system_lower:
            return {"approved": True, "issues": [], "feedback": ""}

        # US171 — CV grounding reviewers (review_cv_extraction + review_profile_extraction
        # both open "CV data quality auditor"; review_cv_tailoring opens "CV quality auditor").
        # Unrecognised → fallback approved=None → review_and_refine retries to exhaustion on
        # every mock CV upload / generation (the PQ-timeout class). The mock approves them.
        if "cv data quality auditor" in system_lower or "cv quality auditor" in system_lower:
            return {"approved": True, "issues": [], "feedback": ""}

        if "cover-letter corrector" in system_lower:
            return dict(_COVER_LETTER_RESPONSE)

        if "cv profile corrector" in system_lower:
            return dict(_PROFILE_PARSE_RESPONSE)

        if "profile data corrector" in system_lower:
            return dict(_PROFILE_PARSE_RESPONSE)

        if "tailored cv corrector" in system_lower:
            return dict(_CV_TAILORING_RESPONSE)

        if "answer parser corrector" in system_lower:
            return dict(_RESPONSE_PARSER_RESPONSE)

        # US179 — role-conditional field expectation analysis.
        # Inspects the user *prompt* for management keywords so mock-stack PQ
        # reflects realistic role differentiation rather than blanket all-three.
        if "experience field analyst" in system_lower:
            p = prompt.lower()
            mgmt = bool(re.search(
                r"\b(lead|head|manager|director|leiter|geschäftsführer|vp|chief|prokurist)\b",
                p,
            ))
            return {
                "expected": (
                    ["team_size", "budget_managed", "industry_context"] if mgmt else []
                )
            }

        # Fallback: return a minimal valid dict for any unrecognised prompt
        return {"mock": True, "raw_prompt_length": len(prompt)}

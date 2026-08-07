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
  "dach career consultant"         → CV tailoring — PROSE shape, ids parsed from
                                     the caller's prompt (aparse_json → dict)
  "expert career analyst"          → gap clustering        (aparse_json → list)
  "expert career coach"            → targeted question     (aparse_json → dict)
  "expert dach career coach"       → cover letter          (aparse_json → dict)
  "cv profile corrector"           → CV extraction refinement   (aparse_json → dict)
  "profile data corrector"         → profile extraction refinement (aparse_json → dict)
  "tailored cv corrector"          → CV tailoring refinement    (aparse_json → dict)
  "experience field analyst"       → role field expectations    (aparse_json → dict, prompt-keyed)
  "verifying one narrow claim"     → ADR-061 stance adjudication (aparse_json → dict, prompt-keyed)
  "strict verification function"   → Oracle narrow entailment (#404 retrofit) (aparse_json → dict)
  "truthfulness oracle's equivalence judge" → ADR-068 bounded equivalence judgement
                                     (cross-language + restatement seams) (aparse_json → dict, prompt-keyed)
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
    # #190 — a certification whose name also reads as a framework/standard (ITIL),
    # so the mock exercises the cert-import path end-to-end; without this key the
    # whole suite was blind to certification loss on import.
    "certifications": [
        {
            "name": "ITIL Foundation",
            "issuing_organization": "AXELOS",
            "date_obtained": "2020-05-01",
            "expiry_date": None,
            "credential_id": None,
            "credential_url": None,
        }
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

# E049 / ADR-067: the CV writer's response is PROSE ONLY — summary, id-keyed work
# bullets, skills. Contact, employer/role/dates, education and languages are joined
# deterministically from the vault by services.cv.assemble_tailored_cv, so the mock
# no longer hardcodes any of them. Work-entry ids are NOT canned either: they are
# parsed from the caller's own prompt (see _mock_cv_tailoring), because assembly
# fails closed on an id that is not in the vault set.
_CV_TAILORING_SUMMARY = (
    "Experienced software engineer with a strong background in Python and FastAPI, "
    "specialising in backend systems for the DACH market. "
    "Proven track record delivering scalable REST APIs and CI/CD pipelines."
)

_CV_TAILORING_SKILLS = ["Python", "FastAPI", "PostgreSQL", "Docker", "REST APIs", "CI/CD", "Git"]

# Per-entry bullet sets, assigned to the prompt's work-entry ids in order (cycling).
_CV_TAILORING_BULLETS: list[list[str]] = [
    [
        "Designed and implemented microservices with FastAPI and PostgreSQL.",
        "Introduced CI/CD pipelines via GitHub Actions, reducing deploy time by 40%.",
        "Led migration from monolith to containerised Docker architecture.",
    ],
    [
        "Built REST APIs serving 50k daily active users.",
        "Improved test coverage from 30% to 85% using pytest.",
    ],
]

# The id channel's own header (``cv_budget.render_budget_table``), anchored at
# LINE START: prompt prose legitimately names the block mid-sentence (the #303
# evidence digest tells the writer "within the ROLE BULLET BUDGETS ceiling"),
# and a bare substring search would start the block at that mention and sweep
# in every bulleted line beneath it. Matching is scoped to the block — see
# ``_budget_block``.
_BUDGET_HEADER_RE = re.compile(r"^ROLE BULLET BUDGETS", re.MULTILINE)
_BUDGET_ID_RE = re.compile(r"^\s*-\s*\[([^\]]+)\]", re.MULTILINE)
_PROFILE_BLOCK_RE = re.compile(
    r"CANDIDATE PROFILE[^\n]*:\n(?P<json>\{.*?\})\s*(?:\n\n[A-Z]|\Z)", re.DOTALL
)
_ID_RE = re.compile(r'"id"\s*:\s*"([^"]+)"')


def _budget_block(prompt: str) -> str:
    """The ROLE BULLET BUDGETS block only, or "" when the prompt has none.

    The id channel is a BLOCK, not a line shape, and reading it as a line
    shape is how PR #473's integration stack came to key CV prose to the
    work-entry id ``'Python'``: ``_BUDGET_ID_RE`` was run over the whole
    prompt, so any other block leading its items with ``- [label]`` was read
    as ids. The mock is what the entire integration tier's determinism rests
    on, so it fails closed (no block → no ids → the assembled CV keeps every
    vault entry with empty bullets) rather than guessing from stray text.
    """
    m = _BUDGET_HEADER_RE.search(prompt)
    if m is None:
        return ""
    start = m.start()
    end = prompt.find("\n\n", start)
    return prompt[start:] if end < 0 else prompt[start:end]


def _prompt_work_ids(prompt: str) -> list[str]:
    """Extract the vault work-entry ids the real writer would key its response to.

    Priority: (1) the ROLE BULLET BUDGETS block's `- [<id>] ...` lines — the
    production id channel, ADR-067 clause 3, matched inside that block only
    (see :func:`_budget_block`); (2) the CANDIDATE PROFILE JSON block's
    work_experience ids; (3) any `"id": "..."` occurrences as a last resort.
    Deduped, order-preserving. Empty when the prompt carries no ids at all —
    the assembled CV then keeps every vault entry with empty bullets.
    """
    ids = _BUDGET_ID_RE.findall(_budget_block(prompt))
    if not ids:
        m = _PROFILE_BLOCK_RE.search(prompt)
        if m:
            try:
                profile = json.loads(m.group("json"))
                ids = [
                    str(w.get("id"))
                    for w in (profile.get("work_experience") or [])
                    if isinstance(w, dict) and w.get("id")
                ]
            except (ValueError, AttributeError):
                ids = _ID_RE.findall(m.group("json"))
    if not ids:
        ids = _ID_RE.findall(prompt)
    seen: set[str] = set()
    out: list[str] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def _mock_cv_tailoring(prompt: str) -> dict[str, Any]:
    """Schema-valid prose response for the CV writer AND its review-loop corrector
    (both receive the vault ids in their prompt: the writer via the budget block /
    profile JSON, the corrector via the re-sent CANDIDATE PROFILE)."""
    work = [
        {
            "id": wid,
            "bullets": list(_CV_TAILORING_BULLETS[i % len(_CV_TAILORING_BULLETS)]),
            "projects": [],
        }
        for i, wid in enumerate(_prompt_work_ids(prompt))
    ]
    return {
        "summary": _CV_TAILORING_SUMMARY,
        "work": work,
        "skills": list(_CV_TAILORING_SKILLS),
    }

_RECONCILE_RESPONSE: dict[str, Any] = {
    "ops": [
        {"op": "upsert_skill", "name": "Python", "category": "technical",
         "proficiency": "advanced", "evidence": []},
        # #190 — a certification in the new information is emitted as its own
        # upsert_certification op, never folded into upsert_skill (reconcile rule 10).
        {"op": "upsert_certification", "name": "ITIL Foundation",
         "issuing_organization": "AXELOS", "date_obtained": "2020-05-01"},
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

# #187 — a skill whose only relation to an existing skill is bare single-token
# containment ('Docker Compose' ⊃ 'Docker'). The reconciler is STATELESS, so it
# re-emits this identical op even on the confirmation-resolution turn — which is
# exactly what makes the interview confirmation loop reproducible under the mock.
_RECONCILE_DOCKER_COMPOSE_RESPONSE: dict[str, Any] = {
    "ops": [
        {"op": "upsert_skill", "name": "Docker Compose", "category": "technical",
         "proficiency": "intermediate", "evidence": []},
    ],
    "ambiguities": [],
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
    # The body deliberately sits inside the DACH norm band (REGION_NORMS["DACH"]:
    # floor 200, budget 300 words). #272 Task 6 added the lower bound, and a mock
    # that mirrors the real contract must satisfy it — otherwise every mock-stack
    # E2E letter trips the word-floor reviewer block and the mock tier stops
    # reflecting the production path (the ADR-047 "mock mirrors the shape"
    # discipline).
    "body": {
        "paragraphs": [
            (
                "Sehr geehrter Herr Dr. Müller, mit großem Interesse habe ich Ihre "
                "Stellenausschreibung als Senior Software Engineer gelesen und bewerbe "
                "mich hiermit auf diese Position. Dass Sie Ihre Backend-Plattform "
                "konsequent auf Microservices und automatisierte Auslieferung "
                "ausrichten, deckt sich unmittelbar mit dem, woran ich in den "
                "vergangenen Jahren am liebsten gearbeitet habe."
            ),
            (
                "Als erfahrene Software-Ingenieurin mit über sechs Jahren Praxis in "
                "Python und FastAPI bringe ich die gesuchten Kernkompetenzen vollständig "
                "mit. Bei TechVision GmbH habe ich skalierbare REST-APIs entwickelt und "
                "CI/CD-Prozesse eingeführt, die die Deployment-Zeit um 40 % reduzierten. "
                "Darüber hinaus habe ich Microservices in Docker containerisiert und "
                "mit PostgreSQL als zentralem Datenspeicher betrieben. In diesem Umfeld "
                "habe ich Code-Reviews als festen Bestandteil der Entwicklung etabliert "
                "und jüngere Kolleginnen und Kollegen fachlich begleitet."
            ),
            (
                "Ihr Fokus auf Microservice-Architekturen und agile Entwicklungsmethoden "
                "spricht mich besonders an, da ich in diesem Umfeld bereits erfolgreich "
                "gearbeitet habe und weitere Impulse setzen möchte. Besonders reizt mich "
                "daran, technische Standards aktiv mitzugestalten und Verantwortung für "
                "die Qualität ganzer Systeme zu übernehmen. Aus der Arbeit an verteilten "
                "Systemen weiß ich, wie sehr belastbare Schnittstellen und automatisierte "
                "Tests über die Geschwindigkeit eines wachsenden Produkts entscheiden."
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

# ADR-061 clause 2 — parses applire.prompts.stance_adjudication's exact
# user-prompt shape: "TOKEN (kind): token\n\nTURN:\nturn_text\n\nDoes TURN...".
_STANCE_ADJUDICATION_RE = re.compile(
    r"TOKEN \([^)]*\): (?P<token>.*?)\n\nTURN:\n(?P<turn>.*?)\n\nDoes TURN state",
    re.DOTALL,
)
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _mock_stance_adjudication(prompt: str) -> dict[str, Any]:
    """Deterministic stand-in for the real testimony adjudication call.

    Real-shape response (``{"answer": ..., "quote": ...}``, ADR-047's "mock
    mirrors the response shape" precedent) so the mock stack exercises the
    SAME confirmed/unconfirmed branching a real provider drives, rather than
    always falling back to unconfirmed via the generic ``{"mock": ...}``
    fallback (2026-07-27 review finding — that silent difference makes the
    mock IQ/OQ/PQ suites test a different product than production).

    No randomness: "yes" is returned ONLY when a content word of TOKEN (the
    longest word first — a rough "head noun" heuristic, case-insensitive,
    word-boundary matched) appears literally in TURN, and the returned
    "quote" is always a VERBATIM substring of TURN — sliced out around the
    match, never paraphrased or invented — so it also passes the caller's own
    deterministic citation check exactly as a correct real-provider answer
    would. No match -> {"answer": "no", "quote": ""}, same as a real
    provider correctly declining to confirm.
    """
    m = _STANCE_ADJUDICATION_RE.search(prompt)
    if not m:
        # Prompt shape changed underneath this parser — fail toward the
        # caller's own safe default (malformed -> unconfirmed) rather than
        # guessing.
        return {"mock": True, "raw_prompt_length": len(prompt)}

    token = m.group("token").strip()
    turn = m.group("turn")
    turn_lower = turn.lower()

    words = sorted({w for w in _WORD_RE.findall(token) if len(w) >= 2}, key=len, reverse=True)
    match_word: str | None = None
    for w in words:
        if re.search(rf"(?<!\w){re.escape(w.lower())}(?!\w)", turn_lower):
            match_word = w
            break

    if match_word is None:
        return {"answer": "no", "quote": ""}

    # Slice a verbatim "sentence" around the match out of the ORIGINAL (not
    # lower-cased) turn text, so the quote is a real substring of TURN.
    idx = turn_lower.find(match_word.lower())
    boundary_chars = ".!?;\n"
    start = max((turn.rfind(c, 0, idx) for c in boundary_chars), default=-1) + 1
    end_candidates = [turn.find(c, idx) for c in boundary_chars]
    end_candidates = [e for e in end_candidates if e != -1]
    end = (min(end_candidates) + 1) if end_candidates else len(turn)
    quote = turn[start:end].strip() or turn.strip()
    return {"answer": "yes", "quote": quote}


# ADR-068 — the bounded equivalence judgement's user prompt shape
# (applire.prompts.oracle_judgement.build_judgement_user_prompt):
# "ITEM <n> (mode: ...):\n...".
_JUDGEMENT_ITEM_RE = re.compile(r"^ITEM (\d+) \(mode: \w+\):", re.MULTILINE)


def _mock_oracle_judgement(prompt: str) -> dict[str, Any]:
    """Hermetically stable, ALWAYS fail-safe: every item comes back
    ``corresponds="uncertain"`` with an empty ``vault_quote`` — a citation
    that will never verify, so the caller's own citation-drop path is what
    resolves it, exactly the way an honest "I looked and couldn't tell"
    provider answer would. Tests that need a specific grant/deny outcome use
    a targeted stub provider instead (the ADR-061 stance-adjudication /
    outcome-critic precedent — this mock only proves the CHAIN is recognised,
    never substitutes for a real judgement)."""
    indices = [int(m) for m in _JUDGEMENT_ITEM_RE.findall(prompt)]
    if not indices:
        indices = [0]
    return {
        "items": [
            {"index": i, "corresponds": "uncertain", "vault_quote": ""} for i in indices
        ]
    }


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
            # #187 — a bare single-token containment ('Docker Compose' vs an
            # existing 'Docker') re-emits the same skill op on EVERY turn, so the
            # interview confirmation-resolution loop is reproducible under the mock.
            if "docker compose" in prompt.lower():
                return copy.deepcopy(_RECONCILE_DOCKER_COMPOSE_RESPONSE)
            return copy.deepcopy(_RECONCILE_RESPONSE)

        # ADR-061 clause 2 — the stance-guard testimony adjudication call
        # (applire/prompts/stance_adjudication.py). Must mirror the REAL
        # provider's response shape (ADR-047 precedent: the mock mirrors the
        # response shape of the real provider) — an unrecognised prompt would
        # fall through to the generic {"mock": ...} fallback, which the caller
        # correctly treats as malformed and resolves to `unconfirmed`. That is
        # *safe* but WRONG as mock-stack behaviour: every uncertain-band token
        # would silently land unconfirmed on every mock run, so the mock IQ/OQ/
        # PQ suites would never exercise the confirmed-via-adjudication path at
        # all (2026-07-27 review finding). Deterministic, not random: a real
        # citation is only returned when it can be lifted verbatim from the
        # turn text the caller supplied, so the mock's own quote always passes
        # the caller's citation check honestly, exactly as a real provider's
        # correct answer would.
        if "verifying one narrow claim" in system_lower:
            return _mock_stance_adjudication(prompt)

        # ADR-068 — the bounded equivalence judgement (cross-language +
        # restatement seams, services/oracle/audit.py). Recognised on the
        # system prompt's distinctive first line
        # (applire.prompts.oracle_judgement.ORACLE_JUDGEMENT_SYSTEM_PROMPT) —
        # an unrecognised prompt would fall to the generic {"mock": ...}
        # fallback, which fails the caller's own "items" shape check and
        # every candidate degrades to judgement_unavailable on every mock
        # run (the #264 lesson).
        if "truthfulness oracle's equivalence judge" in system_lower:
            return _mock_oracle_judgement(prompt)

        # #404 retrofit — the Oracle's narrow entailment call
        # (services/oracle/audit.py's ``_entailment``) had NO ``system=`` at
        # all until this fix, so it was invisible to this fingerprint
        # strategy and always fell to the generic fallback. Canned response
        # is the SAME safe/neutral shape that fallback already produced
        # (``unverifiable`` — never overrules a deterministic red flag
        # either way, ADR-052 §2), now via a real recognised branch instead
        # of an accident of the generic {"mock": ...} shape being invalid.
        if "strict verification function" in system_lower:
            return {"verdict": "unverifiable"}

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
            return {"summary": _CV_TAILORING_SUMMARY}
        if "cv skills writer" in system_lower:
            return {"skills": list(_CV_TAILORING_SKILLS)}
        # E049/ADR-067: no "cv education writer" branch — the education/languages
        # section call is retired (transcription is copied from the vault at assembly).
        if "cv projects writer" in system_lower:
            return {"projects": []}

        # E049/ADR-067: the single-call writer returns PROSE keyed to the prompt's
        # own vault work-entry ids (assembly fails closed on an unknown id, so a
        # canned id list would break every mock generation).
        if "dach career consultant" in system_lower:
            return _mock_cv_tailoring(prompt)

        if "expert career coach" in system_lower:
            return dict(_QUESTION_RESPONSE)

        if "dach career coach" in system_lower:
            return dict(_COVER_LETTER_RESPONSE)

        # US170 — cover-letter grounding reviewer + corrector (review_cover_letter).
        # Must be recognised: an unrecognised reviewer falls through to the fallback
        # ({"mock": ...}, approved=None), which fails review_and_refine and ships a
        # corrupt letter under the mock provider.
        #
        # The v1 fingerprint was "cover-letter quality auditor" — the prompt's opening
        # line. v2 (2026-07-28) rewrote that line, and the whole mock stack silently
        # stopped recognising the chain. Fingerprint on the SUBJECT TEST instead: it is
        # the structural core of the rebuild (SF-WRITE.7) rather than an opening
        # flourish, so a future reword is far less likely to move it. Both are matched,
        # so a prompt rollback still works.
        if "the subject test" in system_lower or "cover-letter quality auditor" in system_lower:
            return {"approved": True, "issues": [], "feedback": ""}

        # US171 — CV grounding reviewers (review_cv_extraction + review_profile_extraction
        # both open "CV data quality auditor"; review_cv_tailoring opens "CV quality auditor").
        # Unrecognised → fallback approved=None → review_and_refine retries to exhaustion on
        # every mock CV upload / generation (the PQ-timeout class). The mock approves them.
        if "cv data quality auditor" in system_lower or "cv quality auditor" in system_lower:
            return {"approved": True, "issues": [], "feedback": ""}

        # #264 — JD-analysis grounding reviewer (review_job_analysis). Same trap as the
        # two above: unrecognised → fallback approved=None → review_and_refine exhausts
        # its retries and analyze_jd ships the fallback dict, which fails JobAnalysis
        # validation and surfaces as HTTP 422 on /api/jobs/analyze (caught by the CI
        # Integration & E2E job, not by unit tests that patch LLM_REVIEW_MAX_RETRIES=0).
        if "job-description data quality auditor" in system_lower:
            return {"approved": True, "issues": [], "feedback": ""}

        if "cover-letter corrector" in system_lower:
            return dict(_COVER_LETTER_RESPONSE)

        if "cv profile corrector" in system_lower:
            return dict(_PROFILE_PARSE_RESPONSE)

        if "profile data corrector" in system_lower:
            return dict(_PROFILE_PARSE_RESPONSE)

        if "tailored cv corrector" in system_lower:
            return _mock_cv_tailoring(prompt)

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

        # ADR-060 outcome critic — one engine, two mounts (E049 49.6). The mock
        # mirrors the real response shape AND the citation discipline: any
        # quote it returns is lifted VERBATIM from the prompt's own document
        # block, so the service's citation verification always passes (the
        # stance-adjudication precedent: an unrecognised prompt would fall to
        # the generic {"mock": ...} fallback, which the caller treats as
        # malformed → judgement_error on EVERY mock run, so the mock IQ/OQ/PQ
        # suites would never exercise the advisory path at all — exactly what
        # happened to Pass B between 2026-07-30 and 2026-07-31, when this
        # fingerprint did not exist).
        if "outcome critic" in system_lower:
            letter_unit = re.search(r"=== COVER LETTER[^\n]*===\n\[1\] (.+)", prompt)
            if letter_unit:
                return {
                    "findings": [
                        {
                            "kind": "letter_only",
                            "concept": "Mock coherence probe",
                            "cv_quote": None,
                            "cv_detail_quote": None,
                            "letter_quote": letter_unit.group(1).strip(),
                            "worth_surfacing": True,
                        }
                    ]
                }
            # Pass A (no letter block): a clean ran-and-found-nothing report.
            return {"findings": []}

        # Fallback: return a minimal valid dict for any unrecognised prompt
        return {"mock": True, "raw_prompt_length": len(prompt)}

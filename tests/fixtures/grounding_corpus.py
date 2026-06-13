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
US144 — labelled grounding/hallucination regression corpus.

Each case is a known fabrication the ADR-021 judges must catch. Used by:
  - tests/unit/test_grounding_corpus.py        (deterministic, no LLM — corpus + prompt contract)
  - tests/integration/test_grounding_corpus_llm.py (real LLM — asserts the judge rejects)

Case keys:
  id              short stable identifier
  failure_class   the FMEA failure mode (JF-M-*)
  source          the truthful source material (raw CV text for extraction; profile dict for tailoring)
  source_anchor   a verbatim substring of the source that MUST be carried into the reviewer prompt
  draft           the extracted/tailored JSON containing the fabrication
  fabricated_token a verbatim substring of the fabrication that is ABSENT from the source
  why             human note
"""

# ── Extraction-side fabrications (JF-M-3.1) ───────────────────────────────────
# build_cv_extraction_review_prompt(raw_cv_text: str, extracted_json: dict)

_EXTRACTION_SOURCE = (
    "Siemens AG — Software Engineer (Jan 2020 – Dec 2022)\n"
    "- Built internal tooling in Python\n"
    "- Supported the migration of legacy services\n"
)

EXTRACTION_CASES = [
    {
        "id": "ext-invented-cert",
        "failure_class": "JF-M-3.1",
        "source": _EXTRACTION_SOURCE,
        "source_anchor": "Siemens AG",
        "draft": {
            "work_experience": [
                {"company": "Siemens AG", "role": "Software Engineer",
                 "start_date": "2020-01", "end_date": "2022-12",
                 "responsibilities": ["Built internal tooling in Python"], "achievements": [], "technologies": ["Python"]}
            ],
            "certifications": [{"name": "AWS Certified Solutions Architect"}],
        },
        "fabricated_token": "AWS Certified Solutions Architect",
        "why": "No certification appears anywhere in the source CV text.",
    },
    {
        "id": "ext-invented-date",
        "failure_class": "JF-M-3.1",
        "source": "Globex — Analyst\n- Reporting and dashboards\n",  # no dates in source
        "source_anchor": "Analyst",
        "draft": {
            "work_experience": [
                {"company": "Globex", "role": "Analyst",
                 "start_date": "2015-03", "end_date": "2019-08",
                 "responsibilities": ["Reporting and dashboards"], "achievements": [], "technologies": []}
            ],
        },
        "fabricated_token": "2015-03",
        "why": "Source states no dates; start_date must be null, not invented.",
    },
    {
        "id": "ext-garbled-employer",
        "failure_class": "JF-M-3.1",
        "source": _EXTRACTION_SOURCE,
        "source_anchor": "Siemens AG",
        "draft": {
            "work_experience": [
                {"company": "Siemes AG", "role": "Software Engineer",
                 "start_date": "2020-01", "end_date": "2022-12",
                 "responsibilities": ["Built internal tooling in Python"], "achievements": [], "technologies": ["Python"]}
            ],
        },
        "fabricated_token": "Siemes AG",
        "why": "Employer name is garbled (mis-transcribed) from 'Siemens AG'.",
    },
    {
        "id": "ext-fabricated-entry",
        "failure_class": "JF-M-3.1",
        "source": _EXTRACTION_SOURCE,
        "source_anchor": "Siemens AG",
        "draft": {
            "work_experience": [
                {"company": "Siemens AG", "role": "Software Engineer",
                 "start_date": "2020-01", "end_date": "2022-12",
                 "responsibilities": ["Built internal tooling in Python"], "achievements": [], "technologies": ["Python"]},
                {"company": "Initech LLC", "role": "Senior Developer",
                 "start_date": "2017-01", "end_date": "2019-12",
                 "responsibilities": ["Led a team of 8"], "achievements": [], "technologies": []},
            ],
        },
        "fabricated_token": "Initech LLC",
        "why": "Second employer has no basis in the source text.",
    },
]


# ── Tailoring-side fabrications (JF-M-6.1 / 6.2) ───────────────────────────────
# build_review_prompt(source_material: str, tailored_json: dict)  (source serialised to JSON)

_TAILORING_SOURCE = {
    "work_history": [
        {"company": "Acme GmbH", "role": "Software Developer",
         "start_date": "2020-01", "end_date": "2022-12",
         "bullets": ["Built REST APIs", "Contributed to a database migration"]}
    ],
    "skills": ["Python", "PostgreSQL"],
    "certifications": [],
}

TAILORING_CASES = [
    {
        "id": "tail-fabricated-bullet",
        "failure_class": "JF-M-6.1",
        "source": _TAILORING_SOURCE,
        "source_anchor": "Acme GmbH",
        "draft": {
            "contact": {"name": "Max Muster"},
            "summary": "Backend engineer.",
            "work_history": [
                {"company": "Acme GmbH", "role": "Software Developer",
                 "start_date": "2020-01", "end_date": "2022-12",
                 "bullets": ["Built REST APIs", "Architected a Kubernetes platform serving 10M users"]}
            ],
            "skills": ["Python", "PostgreSQL"],
        },
        "fabricated_token": "Kubernetes platform serving 10M users",
        "why": "Kubernetes/scale claim has no basis in the profile bullets.",
    },
    {
        "id": "tail-fabricated-cert",
        "failure_class": "JF-M-6.1",
        "source": _TAILORING_SOURCE,
        "source_anchor": "Acme GmbH",
        "draft": {
            "contact": {"name": "Max Muster"},
            "summary": "PMP certified delivery lead.",
            "work_history": [
                {"company": "Acme GmbH", "role": "Software Developer",
                 "start_date": "2020-01", "end_date": "2022-12",
                 "bullets": ["Built REST APIs"]}
            ],
            "skills": ["Python", "PostgreSQL"],
        },
        "fabricated_token": "PMP certified",
        "why": "No PMP (or any) certification exists in the source profile.",
    },
    {
        "id": "tail-oversell-title",
        "failure_class": "JF-M-6.2",
        "source": _TAILORING_SOURCE,
        "source_anchor": "Software Developer",
        "draft": {
            "contact": {"name": "Max Muster"},
            "summary": "Lead Architect.",
            "work_history": [
                {"company": "Acme GmbH", "role": "Lead Architect",
                 "start_date": "2020-01", "end_date": "2022-12",
                 "bullets": ["Led a company-wide database migration"]}
            ],
            "skills": ["Python", "PostgreSQL"],
        },
        "fabricated_token": "Lead Architect",
        "why": "Title inflated from 'Software Developer'; 'Led' overstates 'Contributed to'.",
    },
    {
        "id": "tail-mutated-date",
        "failure_class": "JF-M-6.1",
        "source": _TAILORING_SOURCE,
        "source_anchor": "Acme GmbH",
        "draft": {
            "contact": {"name": "Max Muster"},
            "summary": "Backend engineer.",
            "work_history": [
                {"company": "Acme GmbH", "role": "Software Developer",
                 "start_date": "2017-01", "end_date": "2022-12",
                 "bullets": ["Built REST APIs"]}
            ],
            "skills": ["Python", "PostgreSQL"],
        },
        "fabricated_token": "2017-01",
        "why": "start_date mutated from 2020-01 to 2017-01 — extends tenure by 3 years.",
    },
]

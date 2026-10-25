# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""789:201 (Hazelnut build 4) — a vault ``section`` + ``field`` in the
candidate's words, for the BACKEND-written questions (the profile-review
conflict question said "zwei Werte für personal info.location",
"languages.level"). Mirrors the frontend catalogs ``health.fieldLabel.*``,
``profile.field*`` and ``review.section.*``; a parity test pins the mirror.
An unknown field degrades to the words of its key, never to a dotted path."""

from __future__ import annotations

#: field -> {lang: label}
FIELD_LABELS: dict[str, dict[str, str]] = {
    "start_date": {"de": "Startdatum", "en": "Start date"},
    "end_date": {"de": "Enddatum", "en": "End date"},
    "role": {"de": "Rolle", "en": "Role"},
    "title": {"de": "Titel", "en": "Title"},
    "position": {"de": "Position", "en": "Position"},
    "company": {"de": "Unternehmen", "en": "Company"},
    "location": {"de": "Ort", "en": "Location"},
    "is_current": {"de": "Aktuelle Position", "en": "Current position"},
    "responsibilities": {"de": "Aufgaben", "en": "Responsibilities"},
    "achievements": {"de": "Erfolge", "en": "Achievements"},
    "technologies": {"de": "Technologien", "en": "Technologies"},
    "industry_context": {"de": "Branche", "en": "Industry"},
    "team_size": {"de": "Teamgröße", "en": "Team size"},
    "budget_managed": {"de": "Verantwortetes Budget", "en": "Budget managed"},
    "role_aliases": {"de": "Rollenbezeichnungen", "en": "Role titles"},
    "name": {"de": "Name", "en": "Name"},
    "url": {"de": "URL", "en": "URL"},
    "description": {"de": "Beschreibung", "en": "Description"},
    "organization": {"de": "Organisation", "en": "Organization"},
    "cause": {"de": "Bereich", "en": "Cause area"},
    "institution": {"de": "Institution", "en": "Institution"},
    "degree": {"de": "Abschluss", "en": "Degree"},
    "field": {"de": "Studienfach", "en": "Field of study"},
    "grade": {"de": "Note", "en": "Grade"},
    "thesis_title": {"de": "Titel der Abschlussarbeit", "en": "Thesis title"},
    "language": {"de": "Sprache", "en": "Language"},
    "level": {"de": "Niveau", "en": "Level"},
    "issuing_organization": {"de": "Ausstellende Organisation", "en": "Issuing organization"},
    "credential_id": {"de": "Zertifikats-ID", "en": "Credential ID"},
    "credential_url": {"de": "Zertifikats-URL", "en": "Credential URL"},
    "full_name": {"de": "Name", "en": "Name"},
    "email": {"de": "E-Mail", "en": "Email"},
    "phone": {"de": "Telefon", "en": "Phone"},
    "address": {"de": "Adresse", "en": "Address"},
    "nationality": {"de": "Staatsangehörigkeit", "en": "Nationality"},
    "date_of_birth": {"de": "Geburtsdatum", "en": "Date of birth"},
    "linkedin_url": {"de": "LinkedIn", "en": "LinkedIn"},
    "xing_url": {"de": "XING", "en": "XING"},
    "website_url": {"de": "Webseite", "en": "Website"},
}

#: section -> {lang: label}
SECTION_LABELS: dict[str, dict[str, str]] = {
    "work_experience": {"de": "Berufserfahrung", "en": "Work experience"},
    "skills": {"de": "Fähigkeiten", "en": "Skills"},
    "education": {"de": "Ausbildung", "en": "Education"},
    "languages": {"de": "Sprachen", "en": "Languages"},
    "certifications": {"de": "Zertifikate", "en": "Certifications"},
    "personal_info": {"de": "Persönliche Daten", "en": "Personal details"},
    "professional_summary": {"de": "Profil", "en": "Profile"},
    "projects": {"de": "Projekte", "en": "Projects"},
    "publications": {"de": "Veröffentlichungen", "en": "Publications"},
    "volunteer_activities": {"de": "Ehrenamtliche Tätigkeiten", "en": "Volunteer work"},
    "signature_stories": {"de": "Erfolgsgeschichte", "en": "Signature story"},
}


def field_label(section: str, field: str, lang: str) -> str:
    """``"Ort (Persönliche Daten)"`` / ``"Level (Languages)"`` — the field's
    label with its section in parentheses; the key's own words for a field the
    catalog does not name (``"budget currency"``), never ``section.field``."""
    lang = lang if lang in ("de", "en") else "en"
    f = FIELD_LABELS.get(field, {}).get(lang) or field.replace("_", " ")
    s = SECTION_LABELS.get(section, {}).get(lang)
    return f"{f} ({s})" if s else f

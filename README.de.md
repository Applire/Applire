<div align="center">

![Applire](docs/images/applire_full.png)

# Applire

**Das quelloffene, agent-fähige Bewerbungstool für Europa — DACH-nativ zuerst**

*Aus stundenlangem Lebenslauf-Anpassen werden Sekunden. Lade deine Lebensläufe hoch, füge eine Stellenbeschreibung ein und lass dich von der KI durch ein intelligentes Interview führen — für perfekt zugeschnittene Bewerbungsunterlagen.*

[![License](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green.svg)](https://fastapi.tiangolo.com/)
[![Next.js](https://img.shields.io/badge/Next.js-15+-black.svg)](https://nextjs.org/)
[![Docker](https://img.shields.io/badge/docker-ready-blue.svg)](https://www.docker.com/)
[![GitHub Stars](https://img.shields.io/github/stars/Applire/Applire?style=social)](https://github.com/Applire/Applire)

[🌐 applire.de](https://applire.de) • [🚀 Schnellstart](#-installation) • [📖 Dokumentation](docs/) • [💬 Community](#-community--support) • [🐛 Fehler melden](https://github.com/Applire/Applire/issues)

**🌐 [English](README.md) · Deutsch**

</div>

---

## 📸 Applire in Aktion

Vom Lebenslauf und einer Stellenanzeige zur kompletten Bewerbungsmappe — in Minuten.

**1. Lebensläufe hochladen & Stellenanzeige einfügen**

![Lebensläufe hochladen und Stellenbeschreibung einfügen](docs/images/screenshots/de-upload.png)

Lege einen oder mehrere Lebensläufe ab und füge die Stellenanzeige als Text oder URL hinzu.

**2. Übereinstimmung & Lückenanalyse auf einen Blick**

![Match-Score und KI-gestützte Lückenanalyse](docs/images/screenshots/de-gaps.png)

Applire baut dein Master-Profil auf und zeigt dir sofort deine Eignung für die Rolle (hier 80 % „Strong Fit") sowie thematisch gruppierte Lücken.

**3. Gezieltes KI-Interview schließt die Lücken**

![KI-Interview, das die Lücken schließt](docs/images/screenshots/de-interview.png)

Ein kurzes, stellenspezifisches Interview schließt diese Lücken und schärft deine Story — mit editierbaren Antwortvorschlägen, Fortschrittsanzeige und einer Live-Checkliste der Rollenanforderungen.

**4. Vorlage wählen — sieben DACH-taugliche Layouts**

![Aus sieben DACH-tauglichen Lebenslauf-Vorlagen wählen](docs/images/screenshots/de-templates.png)

Wähle aus sieben Vorlagen (Klassischer Lebenslauf, Modern Swiss, Executive, Tech, Academic u. a.) und Farbvarianten — jede auf DACH-Konventionen ausgelegt.

**5. Zugeschnittener Lebenslauf & passendes Anschreiben**

![Passendes Anschreiben im selben Design](docs/images/screenshots/de-cover-letter.png)

Das Ergebnis ist ein DACH-tauglicher Lebenslauf, zugeschnitten auf die Zielrolle — plus, auf Wunsch, ein passendes Anschreiben im selben Design, dessen Empfänger und Betreff automatisch aus der Stellenanzeige übernommen werden.

> _Die Screenshots zeigen synthetische Demodaten (Beispielprofil „Milan Novak") und die deutsche Oberfläche. Applire gibt es auf Deutsch und Englisch — die Oberflächensprache lässt sich in den Einstellungen umschalten. Die erzeugten Dokumente folgen der Sprache der Stellenanzeige, sodass aus einem englischen Quell-Lebenslauf für eine deutsche Stelle ein deutscher Lebenslauf wird._

---

## 💡 Was ist Applire?

**Applire** ist das quelloffene, agent-fähige Bewerbungstool für Europa. Es macht aus deiner gesamten Berufslaufbahn wahrheitstreue, perfekt zugeschnittene Bewerbungsunterlagen — DACH-Konventionen eingebaut, weitere Länder als von der Community beisteuerbare Länderpakete geplant.

Gebaut für **alle Jobsuchenden** — von Quereinsteiger:innen, die Jahre an Lebenslauf-Versionen zusammenführen, bis zu internationalen Fachkräften, die sich an deutsche Bewerbungsgepflogenheiten anpassen. Läuft auf deiner eigenen Hardware; dein KI-Agent kann es steuern — und ohne Agenten übernimmt der eingebaute Assistent.

Anders als generische Lebenslauf-Baukästen:
- 🧠 **Lernt von dir**: Baut ein dauerhaftes Master-Profil auf, das mit jedem hochgeladenen Lebenslauf klüger wird — jede Angabe bleibt auf ihre Quelle zurückführbar
- 💬 **Befragt dich intelligent**: Stellt gezielte Fragen, um Lücken zwischen deiner Erfahrung und den Stellenanforderungen zu schließen
- ✨ **Schneidet präzise zu**: Erzeugt kulturell passende Lebensläufe, optimiert für DACH-Recruiter und ATS-Systeme
- ✅ **Hält dich wahrheitstreu**: Jedes Stichwort der Stellenanzeige wird als belegt, belegbar oder ehrliche Lücke eingestuft — das System behauptet nie, was dein Profil nicht trägt. Zu jedem erzeugten Dokument gehört außerdem ein [Wahrheits-Report](#-für-das-zeitalter-der-ki-agenten-gebaut), der jede Aussage einzeln gegen dein Profil prüft
- 🤖 **Agent-fähig**: Dein KI-Assistent kann die gesamte Schleife über das Model Context Protocol (MCP) steuern
- 🔒 **Datenschutz by Design**: DSGVO-konform, selbst hostbar, volle Datenhoheit

**In 3 einfachen Schritten:**
1. 📄 Lade 2–4 Versionen deines Lebenslaufs hoch
2. 🔗 Füge die Stellenbeschreibung ein
3. 💬 Beantworte ein paar kluge Fragen → ✨ Erhalte einen perfekt zugeschnittenen Lebenslauf

---

## 👥 Für wen ist Applire?

Applire ist um zwei alltägliche Probleme von Jobsuchenden herum gebaut — plus einen dritten, agent-fähigen Weg, sie zu lösen.

### 📚 „Ich habe fünf Versionen meines Lebenslaufs und Angst vor Copy-Paste-Fehlern"
Die meisten Fachkräfte pflegen mehrere Lebensläufe — manche auf Englisch, manche in der Muttersprache — und jede neue Bewerbung bedeutet, Fragmente hin- und herzukopieren, das Layout neu zu bauen und zu hoffen, dass nichts Wichtiges verloren geht. Applire speichert **jede Tatsache über deine Karriere an einem Ort**, holt genau die Teile heraus, die zu einer konkreten Stelle passen, und befragt dich, um die verbleibenden Lücken bestmöglich zu schließen — sodass jeder Lebenslauf vollständig, konsistent und zugeschnitten ist, ohne manuelles Hin- und Herschieben.

### 🌍 „Ich will mich im DACH-Raum bewerben, aber mein Lebenslauf kommt von woanders"
Du hast deinen bestehenden Lebenslauf — sagen wir, einen indischen — aber wie wird daraus etwas, das ein:e deutsche:r, österreichische:r oder Schweizer Recruiter:in erwartet? Applire überführt dein Profil in einen Lebenslauf, der auf DACH-Konventionen feinjustiert ist (Lebenslauf-Struktur, erwartete Abschnitte, kulturelle Signale), sodass du auf Augenhöhe konkurrierst.

### 🤖 „Lass meinen KI-Agenten das erledigen"
Applire ist **agent-fähig**. Verbinde deinen KI-Agenten — Claude, ChatGPT oder jeden MCP-fähigen Assistenten — und lass ihn die gesamte Schleife interaktiv über das Model Context Protocol abwickeln: Lebensläufe importieren, Stellenanzeige analysieren, Lücken schließen und den fertigen Lebenslauf erzeugen. Ganz ohne Oberfläche. Und wenn dein Agent besser schreibt als unser eingebauter Generator: umso besser — Applire ist dafür gebaut, ihn zu *befähigen*. Dein Agent bleibt der Stratege; Applire liefert den Karriere-Tresor, die Lücken-Evidenz, die DACH-Normen und das Rendering.

---

## ✨ Wichtigste Funktionen

### 🧠 Intelligentes Master-Profil

- **Zusammenführung mehrerer Lebensläufe**: Lade mehrere Lebensläufe hoch und führe sie automatisch zu einem reichhaltigen, konfliktbewussten Master-Profil zusammen
- **Additive Anreicherung**: Jeder Lebenslauf-Upload, jede Interview-Sitzung und jede Bearbeitung reichert dein Profil an — es wird nie überschrieben, nur ergänzt
- **Quellen-Nachverfolgung**: Vollständige Nachvollziehbarkeit, woher jede Information stammt
- **Konfliktauflösung**: Intelligente Erkennung sachlicher Widersprüche (Daten, Abschlüsse) mit nutzergesteuerter Auflösung

### 🎯 Stellen-zentrierte Analyse & Lückenerkennung

- **Tiefe JD-Analyse**: Extrahiert Anforderungen, Fähigkeiten, kulturelle Signale und Branchenkontext aus Stellenbeschreibungen
- **Transparenter Lücken-Score**: 0–100 % Match-Score mit detaillierten Erklärungen, was fehlt
- **Kategorisierte Lücken**:
  - **Kategorie A** (harte Blocker): Muss-Anforderungen, die du nicht erfüllst
  - **Kategorie B** (Bestätigung nötig): Du hast das wahrscheinlich, es steht aber nicht klar da
  - **Kategorie C** (explorativ): Weiche Anforderungen, über die es sich zu sprechen lohnt

### 💬 Dialogbasierter Interview-Orchestrator

- **Drei Modi**:
  - **Gezielt** (für erfahrene Nutzer:innen): Fokussiert auf das Schließen konkreter, im Profil erkannter Lücken
  - **Geführt** (für neue Nutzer:innen): Baut dein Profil systematisch Abschnitt für Abschnitt auf
  - **Profilanreicherung** (ohne Stellenanzeige): Verbessert dein Master-Profil für sich, außerhalb jeder Bewerbung
- **Zustandsbehaftetes Backend**: Jederzeit pausieren und fortsetzen — dein Fortschritt wird serverseitig gespeichert
- **Intelligentes Beenden**: Erkennt automatisch, wann du fertig bist oder alle Lücken geschlossen sind
- **Profil-Aktualisierung**: Jede Antwort reichert dein Master-Profil in Echtzeit an

### 📄 Lebenslauf-Erstellung & Feinschliff

- **ATS-optimierte PDFs**: Erzeugt via Playwright/Chromium mit CSS-basierten Themes
- **Live-Vorschau im Browser**: Sieh genau, wie dein Lebenslauf aussieht, bevor du ihn herunterlädst
- **Bearbeitung auf Abschnittsebene**: Feintuning einzelner Abschnitte (Einleitung, Positionen, Fähigkeiten) mit Live-Neurendering
- **Doppelter Speicherpfad**: Speichere Änderungen im Master-Profil (dauerhaft) oder nur für diesen Lebenslauf (einmalig)
- **KI-gestützte Bearbeitung**: Optionales „Kaile hilft" zum gezielten Schließen von Lücken direkt im Editor
- **Anschreiben-Erstellung**: KI-gestütztes Anschreiben auf Basis der Stellenanzeige und des Master-Profils
- **Kulturelle Anpassung**: Automatische Erkennung und Formatierung für deutsche, österreichische und Schweizer Lebenslauf-Konventionen

### 🗺️ DACH-Kulturintelligenz

- **Marktspezifische Formatierung**: Lebenslauf vs. internationale CV-Formate
- **Erkennung kultureller Signale**: Erkennt, wann ein Lebenslauf angepasst werden muss (z. B. Lebenslauf im indischen Format → deutsche Lebenslauf-Konventionen)
- **Mehrsprachigkeit**: Deutsche und englische Oberfläche (in den Einstellungen umschaltbar). Interview-Fragen folgen deiner Oberflächensprache, während der erzeugte Lebenslauf und das Anschreiben der Sprache folgen, in der die Stellenanzeige verfasst ist — erkannt aus der Anzeige selbst, sodass aus einem englischen Quell-Lebenslauf für eine deutsche Stelle ein deutscher Lebenslauf wird. Französisch und Spanisch sind geplant.

### 🔒 Datenschutz & DSGVO-Konformität

- **Datenschutz by Design** (DSGVO Art. 25): Datenminimierung durchgängig — der von dir gewählte LLM-Anbieter ist der einzige Dritte, den deine Daten erreichen, und Applire schickt ihm so wenig wie möglich
- **Automatisierte Aufbewahrung**: Ein täglicher Worker setzt TTLs durch, alle über Umgebungsvariablen konfigurierbar:
  - Hochgeladene Dateien: 7 Tage (`UPLOAD_TTL_DAYS`)
  - Interview-Sitzungen: 30 Tage (`INTERVIEW_SESSION_TTL_DAYS`)
  - Erzeugte Lebensläufe und Anschreiben: 90 Tage (`GENERATED_DOCUMENTS_TTL_DAYS`)
  - Zurückgezogene Bewerbungen: 7 Tage (`CANCELLED_APPLICATION_TTL_DAYS`)
  - Master-Profil nach Inaktivität: 730 Tage (`PROFILE_INACTIVITY_TTL_DAYS`)
- **Recht auf Löschung** (DSGVO Art. 17): Vollständige Datenlöschung per Klick
- **Selbst gehostet**: Deine Daten verlassen nie deine Infrastruktur
- **Verschlüsselung im Ruhezustand liegt bei dir.** Applire legt außer PostgreSQL keinen eigenen Klartext-Speicher an und verschlüsselt die Datenbank *nicht* für dich — betreibe sie auf einem verschlüsselten Volume oder einem vollverschlüsselten Host, wenn dein Umfeld das erfordert

---

## 🤖 Gebaut für das Zeitalter der KI-Agenten

Applire ist das erste Lebenslauf-Tool, das **KI-Agenten als vollwertige Nutzer** behandelt — nach einem einfachen Prinzip: **Bring your own intelligence.** Dein Agent ist der Stratege und, wenn er stark ist, auch der Autor; Applire liefert, was ein Agent sich strukturell nicht selbst geben kann:

- **Zustand, der nicht stillschweigend driftet** — das Master-Profil ist ein abgeglichener Tresor, in dem jede Änderung samt Quelle protokolliert wird — keine verlustanfällige Notizdatei
- **Prüfungen, die er nicht vortäuschen kann** — deterministische Stichwort- und ATS-Checks, plus `audit_document`: ein Wahrheits-Audit jedes Dokuments gegen dein Profil, Aussage für Aussage (belegt / überhöht / falsch zugeordnet / nicht belegt / nicht prüfbar / nicht zutreffend, mit Profil-Belegen) — auch für Dokumente, die dein Agent selbst geschrieben hat. Die Grenze steht offen dabei: Geprüft wird die Übereinstimmung von Dokument und Profil; das Profil selbst kann der Audit nicht beweisen
- **Ein Renderer, gegen den er nicht ankämpfen muss** — `render_document`: die strukturierten Inhalte deines Agenten (öffentliche, versionierte Schemas als MCP-Ressourcen) durch Applires Vorlagen und DACH-Norm-Checks — heraus kommen PDF plus ATS- und Wahrheits-Report, und Applire schreibt deine Inhalte niemals um
- **Regeln, an die er sich nur halb erinnert** — DACH-Bewerbungsnormen als getestete, nachprüfbare Daten statt vager Modell-Erinnerung
- **Eine Kampagne statt eines Dokuments** — Bewerbungen, Versionen, Aktualitätsstatus und Follow-ups über die gesamte Suche hinweg
- **Wachsendes Gedächtnis** — Fakten aus Interviews landen mit Belegen im Profil und nützen jeder künftigen Bewerbung

Je schwächer das Modell deines Agenten, desto mehr von der eingebauten Pipeline kannst du nutzen — die Generierungs-Tools bleiben vollständig verfügbar:

### Model Context Protocol (MCP)
- **Nahtlose Integration**: First-Class-Unterstützung für Claude Desktop, ChatGPT, Cursor und eigene KI-Agenten
- **Vom Agenten gelieferte Dokumente**: Agenten können Lebensläufe (base64-codiertes PDF, mit Klartext-Fallback) und Stellenbeschreibungen (Rohtext **oder** eine serverseitig ausgelesene URL) direkt über stdio einspeisen — ganz ohne Oberfläche
- **Zustandsbehaftete Sitzungen**: Agenten können über eine stabile `flow_id` pausieren, fortsetzen und nach Unterbrechungen wieder aufsetzen
- **Flow-Orchestrator**: Führt Agenten durch die richtige Reihenfolge (JD-Analyse → CV-Import → Lückenanalyse → Interview → Erzeugung)
- **Datenminimal als Standard**: Schreib- und Einlese-Tools liefern Zusammenfassungen und Belege, nicht das Profil — `import_cv` berichtet, was es extrahiert hat, statt den Tresor zurückzuspiegeln. Das vollständige Profil ist verfügbar, wenn ein Agent es wirklich braucht — bewusst und beim Namen: `get_profile()` und die Ressource `profile://current`
- **Asynchrone Erzeugung**: Nicht-blockierende Lebenslauf-Erstellung mit Status-Abfrage per Polling

### REST-API
- **Vollständige HTTP-API**: Programmatischer Zugriff für entfernte Integrationen
- **OpenAPI-Dokumentation**: Interaktive Swagger-UI unter `/docs`

### Beispiel für einen Agenten-Workflow
```bash
# MCP-Server starten (stdio-Transport)
python -m applire.mcp

# Eine typische Agenten-Sitzung:
1. start_flow()                              → flow_id  (stabiler Wiederaufsetz-Handle)
2. import_cv(file_base64="<base64 PDF>")     → Profil-Zusammenfassung
3. analyze_jd(url="https://.../job-posting") → job_id
4. analyze_gaps(job_id)                      → gap_report
5. run_interview(job_id)                     → session_id + erste Frage
6. send_message(session_id, "Ich habe 5 J…") → nächste Frage / {complete: true}
7. generate_cv(job_id)                       → cv_id  (asynchron)
8. get_cv_status(cv_id)                      → {status: "ready", pdf_url: "…"}
9. create_application(job_id)                → Bewerbung in der Pipeline erfasst
```

---

## 🏗️ Architektur & Tech-Stack

### Backend

- **Python 3.12+**: Modernes asynchrones Python mit Type Hints
- **FastAPI**: Hochperformantes asynchrones Web-Framework
- **PostgreSQL 16**: JSONB für ein flexibles Master-Profil-Schema
- **Pydantic**: Typsichere Datenvalidierung und Serialisierung
- **SQLAlchemy 2.0**: Asynchrones ORM mit voller Typunterstützung
- **Alembic**: Datenbank-Migrationen

### Frontend

- **Next.js 15**: React-Framework mit App Router
- **TypeScript**: Typsicheres JavaScript
- **ShadCN/UI**: Barrierearme Komponentenbibliothek
- **Tailwind CSS v4**: Utility-first-Styling

### KI/ML

- **Bring Your Own Key**: Du wählst den LLM-Anbieter und stellst den API-Schlüssel — deine Daten gehen nur dorthin, wohin du zeigst
- **LLM-Provider-Abstraktion**: Austauschbare Backends — Mistral (EU-gehostet), Requesty (EU-gehostetes Gateway), OpenRouter, Anthropic (Claude, eigener API-Schlüssel), OpenAI (oder jeder OpenAI-kompatible Endpunkt) und Ollama (komplett offline, selbst gehostet)
- **Eigene State Machine**: Asynchroner Interview-Orchestrator (keine LangGraph-Abhängigkeit)
- **Playwright**: Headless-Chromium für die PDF-Erzeugung

### Infrastruktur

- **Docker & Docker Compose**: Containerisiertes Deployment
- **PostgreSQL 16**: Primäre Datenbank mit JSONB-Unterstützung
- **Retention-Worker**: Täglicher Cron für die Durchsetzung der DSGVO-TTLs
- **GitHub Actions**: CI/CD-Pipeline mit pytest und Playwright-E2E-Tests

### Agenten-Integration

- **Model Context Protocol (MCP)**: stdio-Transport für lokale KI-Agenten
- **REST-API**: Vollständige HTTP-API für entfernte Integrationen
- **Flow-Orchestrator**: State Machine für mehrstufige Agenten-Workflows
- **Sitzungs-Wiederherstellung**: Agenten können unterbrochene Sitzungen über `flow_id` fortsetzen

---

## 🚀 Installation

### Voraussetzungen

- **Docker & Docker Compose**
- **Ein LLM-Anbieter deiner Wahl** (eigener Schlüssel): Mistral, Requesty, OpenRouter, Anthropic, OpenAI (oder jeder OpenAI-kompatible Endpunkt) oder Ollama (lokal/kostenlos, kein Schlüssel nötig)

### Self-Hosting (kein Klonen nötig)

> **Welche Version wird hier installiert?** Diese Befehle holen immer das **neueste
> veröffentlichte Release** — genau das, auf das auch Dockers `:latest` zeigt. Compose-Datei,
> Env-Vorlage und Images sind damit stets ein zusammenpassender Satz, ohne dass du eine
> Versionsnummer nachschlagen musst. Diese Seite beschreibt die *Entwicklungsversion*; die
> Dokumentation zu dem, was du gerade installiert hast, findest du beim
> [neuesten Release](https://github.com/Applire/Applire/releases/latest).

```bash
# 1. Die beiden benötigten Dateien direkt aus dem neuesten Release herunterladen
curl -LO https://github.com/Applire/Applire/releases/latest/download/docker-compose.yml
curl -L -o .env.example https://github.com/Applire/Applire/releases/latest/download/env.example

# 2. Umgebung konfigurieren
cp .env.example .env
# .env bearbeiten: LLM_PROVIDER und den passenden API-Schlüssel setzen (siehe Konfiguration unten)

# 3. Images holen, dann alle Dienste starten.
#    Das ausdrückliche `pull` ist wichtig: `up -d` allein verwendet ein älteres,
#    lokal bereits vorhandenes `:latest` weiter. Die Datenbank-Migrationen laufen
#    automatisch beim Start des Backends — es gibt keinen separaten Migrationsschritt.
docker compose pull && docker compose up -d
```

Jeder Dienst — auch der Reverse-Proxy, dessen Konfiguration im `applire-nginx`-Image eingebacken ist — ist ein vorgefertigtes Image. `docker compose pull` holt damit einen vollständigen, lauffähigen Stack, ohne dass du Konfigurationsdateien auf dem Host ablegen musst.

Rufe die Anwendung unter **http://localhost** auf — der mitgelieferte nginx-Reverse-Proxy liefert das Frontend aus und leitet `/api/*` an das Backend weiter. Nur Port 80 muss veröffentlicht werden; die Backend- und Frontend-Container bleiben intern. Die vollständige Einstiegspunkt- und Port-Topologie findest du in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

> **Eigene Domain oder TLS?** Das Image bringt eine sinnvolle Standard-Proxy-Konfiguration
> mit. Um sie zu überschreiben, binde deine eigene Datei über die eingebackene — ergänze
> beim `nginx`-Dienst in `docker-compose.yml`:
> ```yaml
>     volumes:
>       - ./my-nginx.conf:/etc/nginx/conf.d/default.conf:ro
> ```

So aktualisierst du auf die neueste Version:
```bash
docker compose pull && docker compose up -d
```

> **Du aktualisierst von einer Version älter als `v0.37.0-beta`?** Gehe zuerst über
> `v0.37.2-beta`. Profile, die vor der Reconciliation-Engine (E035) importiert wurden,
> können flache Doppel-Arbeitgeber und verwaiste Projekte enthalten; der einmalige Lauf
> `scripts/migrate_flat_duplicates.py`, der sie in das typisierte Modell überführt, wurde
> nur in `v0.37.0-beta` … `v0.37.2-beta` ausgeliefert. Das ist Datenhygiene, kein Schema —
> Alembic-Migrationen laufen weiterhin automatisch beim Start, ein direkter Sprung
> aktualisiert also sauber, lässt diese Duplikate aber bestehen.

### Self-Hosting aus dem Quellcode

Du baust lieber selbst, was du betreibst (oder erreichst GHCR nicht)? Klone das Repository,
baue dieselben drei Images lokal und starte dieselbe Produktions-Topologie:

```bash
git clone https://github.com/Applire/Applire.git && cd Applire
cp .env.example .env   # LLM_PROVIDER und den passenden API-Schlüssel setzen

docker build -t ghcr.io/applire/applire-backend:latest ./backend
docker build --target runner -t ghcr.io/applire/applire-frontend:latest ./frontend
docker build -t ghcr.io/applire/applire-nginx:latest ./nginx

docker compose -f docker-compose.yml up -d
```

> **Beachte das ausdrückliche `-f docker-compose.yml`.** Innerhalb eines Klons wendet ein
> einfaches `docker compose up -d` zusätzlich `docker-compose.override.yml` an — den
> *Entwicklungs*-Stack (Hot-Reload-Server, Quellcode-Bind-Mounts, zusätzlich
> veröffentlichte Ports einschließlich der Datenbank). Gut zum Arbeiten an Applire, nicht
> zum Betrieb für echte Nutzer.

> **Mitwirken?** Siehe [CONTRIBUTING.md](CONTRIBUTING.md) für das Entwickler-Setup mit Build aus dem Quellcode.

---

## ⚙️ Konfiguration

### Umgebungsvariablen

Applire ist **Bring Your Own Key**: Wähle einen unterstützten Anbieter und stelle dessen Schlüssel bereit — deine Daten gehen nur zum Anbieter deiner Wahl. Kopiere `.env.example` nach `.env` und konfiguriere:

```env
# Datenbank
DATABASE_URL=postgresql+asyncpg://applire:applire@postgres:5432/applire

# LLM-Anbieter — wähle einen: mistral | requesty | openrouter | anthropic | openai | ollama
LLM_PROVIDER=mistral

# Mistral AI — EU-gehostet, starke Deutschkenntnisse
MISTRAL_API_KEY=your-mistral-api-key-here
MISTRAL_MODEL=mistral-medium-latest

# Requesty — EU-gehostetes Gateway (Frankfurt); zugleich ein EU-residenter Weg zu Claude/GPT/Gemini
REQUESTY_API_KEY=your-requesty-api-key-here
REQUESTY_MODEL=mistralai/mistral-large-latest   # EU-Region-Modell für volle Datenresidenz

# OpenRouter — Multi-Modell-Gateway: ein Schlüssel für Mistral, Claude und mehr (nicht EU-gehostet)
# Schlüssel unter https://openrouter.ai/keys
OPENROUTER_API_KEY=your-openrouter-api-key-here
OPENROUTER_MODEL=mistralai/mistral-medium-3

# Anthropic (Claude) — native API, nur mit eigenem API-Schlüssel (ein Claude-Abo ist nicht nutzbar)
ANTHROPIC_API_KEY=your-anthropic-api-key-here
# Modellnamen ändern sich schnell: nimm eine aktuelle ID aus Anthropics Modell-Liste,
# statt eine aus einer README zu kopieren. Zur Auswahl siehe docs/llm-models.md.
#ANTHROPIC_MODEL=<aktuelle-claude-modell-id>

# OpenAI oder ein OpenAI-kompatibler Server (z. B. LM Studio)
OPENAI_API_KEY=your-openai-api-key-here
#OPENAI_MODEL=gpt-4o
#OPENAI_BASE_URL=http://host.docker.internal:1234/v1

# Ollama — komplett offline (docker compose --profile ollama up)
# Vor dem ersten Lauf einmal ein Modell laden — der Server startet leer:
#   docker compose exec ollama ollama pull llama3.2      # oder OLLAMA_MODEL auf das geladene Modell setzen
# CPU-Inferenz ist langsam: LLM_TIMEOUT erhöhen (z. B. 600), damit die Generierung nicht abbricht.
OLLAMA_BASE_URL=http://ollama:11434
OLLAMA_MODEL=llama3.2

# LLM-Timeout in Sekunden (für Reasoning-Modelle erhöhen)
LLM_TIMEOUT=180

# Auth (none für den Einzelnutzer-Modus der Community Edition)
AUTH_PROVIDER=none

# CORS — kommagetrennte Liste erlaubter Origins
# Standard "*" (alle erlauben) ist für Einzelnutzer-Self-Hosting mit AUTH_PROVIDER=none in Ordnung
#CORS_ORIGINS=*

# nginx-Proxy-Timeout — muss größer als LLM_TIMEOUT sein
#NGINX_PROXY_TIMEOUT=300

# Frontend-API-URL
# docker compose: leer lassen — nginx auf :80 leitet /api/* an das Backend
# Standalone-Dev: auf http://localhost:8001 setzen
#NEXT_PUBLIC_API_URL=http://localhost:8001
```

### LLM-Anbieter-Optionen

Applire ist Bring Your Own Key — kein Anbieter ist bevorzugt. Wähle, was zu dir passt, und stelle den passenden Schlüssel über die austauschbare Abstraktionsschicht bereit:

| Anbieter | Konfiguration | Anwendungsfall |
|----------|---------------|----------------|
| **Mistral AI** | `LLM_PROVIDER=mistral`<br>`MISTRAL_API_KEY=...` | EU-gehostet, starke Deutschkenntnisse |
| **Requesty** | `LLM_PROVIDER=requesty`<br>`REQUESTY_API_KEY=...` | EU-gehostetes Gateway (Frankfurt, ohne Datenspeicherung); EU-residenter Weg zu Claude/GPT/Gemini über EU-Region-Deployments |
| **OpenRouter** | `LLM_PROVIDER=openrouter`<br>`OPENROUTER_API_KEY=...` | Multi-Modell-Gateway; Zugriff auf Mistral, Claude und andere mit einem Schlüssel (nicht EU-gehostet) |
| **Anthropic** (Claude) | `LLM_PROVIDER=anthropic`<br>`ANTHROPIC_API_KEY=...` | Claude über einen Console-API-Schlüssel (eigener Schlüssel) — ein Claude-Pro/Max-Abo ist nicht nutzbar. US-gehostet |
| **OpenAI** | `LLM_PROVIDER=openai`<br>`OPENAI_API_KEY=...` | Hohe Qualität, breit verfügbar; unterstützt via `OPENAI_BASE_URL` auch LM Studio |
| **Ollama** (lokal) | `LLM_PROVIDER=ollama`<br>`OLLAMA_BASE_URL=http://localhost:11434` | Komplett offline, keine API-Kosten, kein Schlüssel nötig |

---

## 📖 API-Dokumentation

### REST-API

Im Docker-Stack wird die REST-API über nginx unter `http://localhost/api/*` erreicht; die interaktive Swagger-UI ist verfügbar, wenn das Backend standalone in der Entwicklung läuft. Die Einstiegspunkt- und Port-Topologie findest du in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

#### Kern-Endpunkte

```bash
# Analyse der Stellenbeschreibung
POST /api/job/analyze
{
  "text": "Senior Software Engineer role...",
  "url": "https://example.com/job"  # Optional
}

# Lebenslauf-Upload & Profil-Anreicherung
POST /api/profile/upload
Content-Type: multipart/form-data
files: [cv1.pdf, cv2.pdf]

# Lückenanalyse (sitzungsbezogen)
POST /api/session/{session_id}/analyze-gaps

# Interview-Sitzung starten
POST /api/session
{ "job_id": "uuid", "mode": "targeted" }

# Interview-Nachricht senden
POST /api/session/{session_id}/message
{ "message": "Ich habe 5 Jahre Erfahrung mit Python..." }

# Lebenslauf erzeugen
POST /api/cv/generate
{ "job_id": "uuid", "template": "classic_german", "target_pages": 2 }
# template und target_pages sind optional; target_pages fällt auf deine
# Einstellungen und dann auf den Regionsstandard zurück

# Status der Lebenslauf-Erzeugung prüfen
GET /api/cv/{cv_id}/status
# Liefert: { "status": "pending" | "ready" | "failed" }

# Lebenslauf herunterladen
GET /api/cv/{cv_id}/pdf
```

### Model Context Protocol (MCP)

```bash
# MCP-Server starten (stdio-Transport)
python -m applire.mcp
```

Setze `APPLIRE_BASE_URL` auf die von außen erreichbare `scheme://host:port`-Adresse
deines Reverse Proxys für jedes Deployment außer lokalem, ungeproxtem Dev-Betrieb —
`generate_cv`/`get_cv_status`/`generate_cover_letter` bauen damit `html_url`/`pdf_url`
auf. Standard ist `http://localhost:8001`, was hinter nginx/Caddy falsch ist; der
Server loggt beim Start eine Warnung, wenn die Variable fehlt. Siehe `.env.example`.

> **Erster Aufruf:** Lass deinen Agenten nach dem Verbinden zuerst `get_guide`
> aufrufen — es liefert den Agent-Nutzungsleitfaden und Applires
> Ehrlichkeitsvertrag (Tool-Ablauf, À-la-carte-Pfade, Verankerungsregeln). Die
> kanonische Datei ist
> [`backend/applire/mcp/AGENT_GUIDE.md`](backend/applire/mcp/AGENT_GUIDE.md).

#### MCP-Tools

**Leitfaden**

| Tool | Beschreibung |
|------|--------------|
| `get_guide()` | Liefert den Agent-Nutzungsleitfaden + Ehrlichkeitsvertrag — vor dem ersten Bewerbungslauf aufrufen |

**Einspeisung & Profil**

| Tool | Beschreibung |
|------|--------------|
| `import_cv(file_base64?, filename?, text?)` | Master-Profil aus einem Lebenslauf anlegen oder erweitern. Primär: base64-codiertes PDF (≤10 MB); Fallback: vorab extrahierter Text. Liefert eine Extraktions-Zusammenfassung (nie das Rohprofil) |
| `analyze_jd(text?, url?)` | Eine Stellenbeschreibung analysieren. Gib genau eines an: `text` (JD-Inhalt) oder `url` (serverseitig ausgelesen); Reposts bereits erfasster Stellen tragen einen `duplicate_of`-Hinweis |
| `get_profile()` | Das aktuelle Master-Profil zurückgeben |
| `update_profile(section, data)` | Einen Abschnitt patchen (`personal_info`, `professional_summary`, `work_experience`, `education`, `certifications`, `skills`, `languages`, `publications`, `volunteer_activities`, `signature_stories`) |
| `add_role(title, company, start_date, location?, industry?, close_role_ids?)` | Eine neue laufende Rolle hinzufügen (Post-Hire-Update); `close_role_ids` schließt vorherige offene Rollen |

**Flow & Interview**

| Tool | Beschreibung |
|------|--------------|
| `start_flow(job_id?)` | Flow-Sitzung anlegen oder fortsetzen (idempotent pro Nutzer+Stelle); liefert `flow_id` + Status |
| `advance_flow(flow_id, step, artifact_id?)` | Zum nächsten Schritt weitergehen; artefakt-erzeugende Schritte benötigen `artifact_id` |
| `get_flow_state(flow_id)` | Aktuellen Flow-Status und verfügbare Aktionen abrufen |
| `analyze_gaps(job_id)` | Lücken zwischen Profil und JD erkennen |
| `run_interview(job_id)` | Ein Lücken-Interview starten; liefert `session_id` + erste Frage |
| `send_message(session_id, message)` | Eine Nachricht in einem aktiven Interview senden; liefert die nächste Frage oder `{complete: true}` |
| `resolve_gap(job_id, gap_id, answer)` | EINE Lücken-Gruppe in einem einzigen Aufruf schließen — die Agenten-Variante des gezielten Lückenschlusses in der Oberfläche. Zustandslos: für Agenten, die ihre Fragen lieber selbst stellen. `gap_id` stammt aus den `gap_clusters` von `analyze_gaps` |

**Eingebaute Generierung**

| Tool | Beschreibung |
|------|--------------|
| `generate_cv(job_id, target_pages?)` | Asynchrone Lebenslauf-Erzeugung anstoßen; optionales `target_pages` fixiert die Seitenzahl für diesen Lauf; liefert `cv_id`, `html_url`, `pdf_url` |
| `get_cv_status(cv_id)` | Status der Lebenslauf-Erzeugung abfragen (`pending` / `generating` / `ready` / `failed`) |
| `get_cv_ats_report(cv_id)` | Persistierter ATS-Prüfbericht für einen erzeugten Lebenslauf — benannte Pass/Fail-Checks + vorhandene/fehlende Keywords, kein Gesamtscore |
| `generate_cover_letter(job_id)` | Ein Anschreiben erzeugen (erfordert eine bestehende Flow-Sitzung für die Stelle); liefert `cover_letter_id`, `html_url`, `pdf_url` |
| `get_cover_letter_status(cover_letter_id)` | Status der Anschreiben-Erzeugung abfragen (`pending` / `generating` / `ready` / `failed`) |
| `get_cover_letter_ats_report(cover_letter_id)` | Persistierter ATS-Prüfbericht für ein erzeugtes Anschreiben |

**Bring your own intelligence (ADR-054) — à la carte, kein vorheriger `generate_*`-Aufruf nötig**

| Tool | Beschreibung |
|------|--------------|
| `submit_claims(claims, job_id?)` | Vom Agenten beim Kandidaten erhobene Fakten als *einzeln aufgeführte* Aussagen einreichen; Applire gleicht sie mit Agent-Interview-Provenienz ins Profil ab (Agent = Interviewer, Applire = Notar). Vertrag: `schema://claims` |
| `submit_testimony(text)` | EIN vollständiges Freitext-Aussagedokument mit Belegen ins Profil abgleichen — das unstrukturierte Gegenstück zu `submit_claims`. Vertrag: `schema://testimony` |
| `audit_document(document_id?, document_text?)` | Wahrheits-Orakel: Wahrheits-Report pro Aussage — für ein erzeugtes Dokument per ID oder für den Rohtext eines Dokuments, das dein Agent selbst geschrieben hat |
| `render_document(document_kind, content, job_id, template?, target_pages?)` | Vom Agenten verfasste strukturierte Inhalte (Verträge: `schema://cv` / `schema://cover-letter`) in ein normgeprüftes, vorlagenbasiertes PDF rendern, mit ATS- + Wahrheits-Reports — niemals umgeschrieben |

**Bewerbungen**

| Tool | Beschreibung |
|------|--------------|
| `create_application(job_id, start_workflow?, company_name?, role_title?, deadline?, source_url?)` | Eine Bewerbung in der Pipeline erfassen; `start_workflow=true` legt atomar die Flow-Sitzung an |
| `list_applications(status_filter?)` | Die Bewerbungs-Pipeline auflisten (`tracking`, `applied`, `rejected`, `offer`) |
| `get_application(application_id)` | Details zu einer bestimmten Bewerbung abrufen (inkl. `stale_cv`-Hinweis auf ein neues Tailoring) |
| `update_application(application_id, ...)` | Nutzerverwaltete Felder aktualisieren (Status, Notizen, Frist, Quell-URL), den eingereichten Lebenslauf/das Anschreiben anpinnen oder den Stale-CV-Hinweis stummschalten |

#### MCP-Ressourcen

- `profile://current` — Aktuelles Master-Profil (JSON)
- `job://{job_id}` — Stellen-Analyse
- `cv://{cv_id}` — Metadaten eines erzeugten Lebenslaufs
- `flow://{flow_id}` — Flow-Sitzungsstatus
- `schema://cv` — Versionierter Inhaltsvertrag für maßgeschneiderte Lebensläufe via `render_document` (`{schema_version, json_schema}`)
- `schema://cover-letter` — Versionierter Anschreiben-Inhaltsvertrag für `render_document`
- `schema://claims` — Versionierter Vertrag für einzeln aufgeführte Aussagen (`submit_claims`)
- `schema://testimony` — Versionierter Freitext-Aussagen-Vertrag für `submit_testimony`
- `guide://usage` — Der Agent-Nutzungsleitfaden + Ehrlichkeitsvertrag (gleicher Inhalt wie `get_guide`)

---

## 🧪 Tests

### Backend-Tests

```bash
# Alle Tests ausführen
pytest

# Mit Coverage ausführen (erzwingt ≥75 % Schwelle)
pytest --cov=applire --cov-fail-under=75

# HTML-Coverage-Report erzeugen
pytest --cov=applire --cov-report=html
```

### Frontend-Tests

```bash
# Unit-Tests ausführen
npm test

# E2E-Tests ausführen (Playwright)
npm run test:e2e

# E2E-Tests im UI-Modus ausführen
npm run test:e2e:ui
```

### CI/CD-Pipeline

GitHub Actions führt aus:
1. ATS-Render-Roundtrip-Garantie (Playwright)
2. Backend-Unit-Tests (pytest, ≥75 % Coverage)
3. Backend-Integrationstests (Docker-Stack)
4. MCP-stdio-Tests (der Agenten-Kanal)
5. Playwright IQ, OQ (Desktop + Mobile) und PQ — Chromium
6. Frontend-Unit-Tests (Vitest), Lint (ESLint + i18n-Parität) und ein Production-Build
7. Ein Modulsystem-Check

Alle Stufen müssen vor einem Merge bestehen.

---

## 📁 Projektstruktur

```
Applire/
├── backend/
│   ├── applire/
│   │   ├── main.py              # Einstiegspunkt der FastAPI-Anwendung
│   │   ├── models/              # SQLAlchemy-ORM-Modelle
│   │   ├── schemas/             # Pydantic-Request/Response-Schemas
│   │   ├── routers/             # FastAPI-Route-Handler
│   │   ├── services/            # Geschäftslogik-Schicht
│   │   │   ├── interview/       # Interview-Orchestrator (State Machine)
│   │   │   ├── flow/            # Flow-Orchestrator
│   │   │   ├── profile/         # Master-Profil-Merge-Logik
│   │   │   ├── cv/              # Lebenslauf-Erzeugung & Abschnitts-Bearbeitung
│   │   │   └── gap/             # Lückenanalyse
│   │   ├── providers/           # LLM-, Auth-, Storage-Abstraktionen
│   │   ├── mcp/                 # Model-Context-Protocol-Server
│   │   ├── retention/           # DSGVO-Retention-Worker
│   │   └── templates/           # Jinja2-Lebenslauf-Vorlagen
│   ├── alembic/                 # Datenbank-Migrationen
│   ├── tests/                   # Pytest-Test-Suite
│   └── requirements.txt
├── frontend/
│   ├── app/                     # Next.js-App-Router-Seiten
│   ├── components/              # React-Komponenten
│   ├── lib/                     # Utilities und API-Clients
│   └── public/
├── docs/
│   ├── TESTING.md               # Teststrategie und Befehle
│   └── CI_CD_GUIDE.md           # Dokumentation der CI/CD-Pipeline
├── tests/                       # Integrations- und E2E-Tests
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## 🗺️ Roadmap

### ✅ Aktuelle Version (v0.41.0-beta)

- [x] Das erzeugte Dokument bekommt den Bildschirm: ein Prüf-Panel ordnet jeden Befund nach der Frage, die man wirklich hat — im Dokument, aber nicht im Profil · fehlt, obwohl das Profil es abdeckt · fehlt und nicht abgedeckt · ist das Handwerk sauber — mit einem Urteilssatz, als Überblick oder geführt lesbar
- [x] Jedes gerenderte PDF und .docx trägt eine maschinenlesbare KI-Herkunftsmarkierung (EU AI Act Art. 50(2)): ein XMP-Paket mit IPTC `DigitalSourceType` und einem dokumentierten Applire-Namensraum, unterhalb aller Templates und beider Türen; ein nachgelagertes Neu-Rendern entfernt sie, und die Doku sagt das
- [x] Stellenanzeigentext ist Daten, nicht Anweisung: ein Grenz-Helfer markiert die Anzeige und jede daraus abgeleitete Zeichenkette an allen 33 Stellen, an denen sie einen Prompt erreichen; MCP-Ergebnisse kennzeichnen anzeigenabgeleiteten Text als nicht vertrauenswürdig; ein Prompt-Injection-Korpus ist die messbare Kontrolle
- [x] Der Prüfbericht sagt, ob die Prüfung fertig wurde: ein `terminal-review`-Check trägt Ausgang und offene Befunde der Terminal-Review auf beiden Dokumenten, ein `narrative-evidence`-Check nennt belegbare Konzepte, die der Lebenslauf nur als Skill-Tag führt
- [x] Untertreibung ist ein gebundenes Signal in der Terminal-Runde, und die Terminal-Review stellt die Ganzdokument-Fragen (Anspruchsbalance, Stimme) als benannte, nur sichtbar machende Checks
- [x] Das Anschreiben nennt den Arbeitgeber einmal pro Absatz statt einmal pro Satz, und eine vom Kandidaten in eigenen Worten benannte Grenze ist eine Offenlegungspflicht für den Writer
- [x] Das Keyword-Ledger wird bei jedem Generierungs-Lesen gegen den aktuellen Vault neu abgeleitet, damit ein seit der Analyse gelernter Begriff dem Writer nie verboten wird; eine Abdeckungsforderung nennt die Position, die den Beleg besitzt
- [x] Applire lässt sich auf dem Handy installieren und nimmt geteilte Anzeigen entgegen (Manifest, Service Worker, der nichts Eigenes cacht, Android-Share-Target nur als Vorbefüllung); Profil-Hub, Meine Dokumente und Dossier funktionieren bei 390 px
- [x] Die Dokumentsprache ist deine Entscheidung, keine Erkennung: Ein DE/EN-Schalter je Bewerbung bestimmt alle erzeugten Dokumente, jedes Dokument hält seine Sprache fest, und ein späterer Wechsel schreibt nie ein bestehendes Dokument um
- [x] Strukturierte Profil-Editoren für Berufserfahrung, Ausbildung, Fähigkeiten, Sprachen, Zertifikate und Projekte — das JSON-Textfeld ist verschwunden, und eine Bearbeitung auf veralteter Grundlage kann abgelehnt werden, statt still zu überschreiben
- [x] Angeheftete Fakten: Markiere einen Fakt für eine Bewerbung als gesetzt, und er behält seinen Platz im Budget des Dokuments — mit Ausweisung im Prüfbericht
- [x] Ein Schreibpfad ins Vault: Jeder Schreiber — Interview, Import, Abschnitts-Editor, Konfliktauflösung, Rollen-Lebenszyklus — schreibt über `commit_ops` mit Belegen je Eintrag; keine Ad-hoc-Profiländerungen mehr
- [x] Abschließende Dokumentprüfung schließt über Lebenslauf und Anschreiben **wie komponiert**, mit begrenztem Wiedereintritt und Ship-and-Report-Semantik
- [x] Anschreiben-Prüfung behandelt Vorhandensein als mitgeteilten Fakt, nicht als eigene Frage — falsche Forderungen und Falsch-Vorhanden-Befunde nahe null
- [x] Prosa-Hoheit im Lebenslauf: Der Writer liefert nur Prosa; Fakten werden deterministisch angefügt, und der deterministische Abschluss kann Belege nie stillschweigend löschen
- [x] Ehrlich begrenzte Teilaussagen (unterstützt statt eigenverantwortlich, in Arbeit) sind lieferbare Daten statt verworfener Aussagen
- [x] Ergebnis-Kritiker: Ein gespeicherter beratender Report zu jedem erzeugten Lebenslauf und Anschreiben, inklusive Abrechnung entfallener Belege
- [x] Wahrhaftigkeits-Schutz: Eine Interview-Antwort, die Erfahrung verneint, kann nie zur Lebenslauf-Aussage werden — die Ablehnungs-Urteile des Reconcilers überstimmen deterministisch seine eigenen Änderungen
- [x] Durchsetzung der Dokumentsprache erfasst auch Projekt-Bullets — nach dem Sprach-Durchlauf gelangt kein prosaförmiger Text mehr in einen generierten Lebenslauf
- [x] Abdeckung belegbarer Keywords heilt sich in der Generierungs-Pipeline selbst — die deterministische Prüfung, die das Dokument bewertet, kontrolliert auch jeden Schreibschritt einschließlich des Sprach-Durchlaufs
- [x] Wahrhaftiges Keyword-Ledger: Jedes Keyword der Stellenanzeige klassifiziert als vorhanden / belegbar / ehrliche Lücke — eine konsistente Quelle für Match-Score, ATS-Panel, Generatoren und Interview
- [x] Profil-Reconciliation-Engine: Typisierte, deterministische Zusammenführung von Lebenslauf-Importen und Interview-Antworten mit Konfliktauflösung und Anreicherungs-Historie
- [x] Master-Profil-Gesundheits-Hub mit Snapshots/Undo und Interviews ohne Stellenanzeige
- [x] ATS-Parsebarkeits-Prüfungen für jedes generierte Dokument (Panel + REST + MCP)
- [x] Einheitlicher Dokument-Arbeitsbereich für Lebenslauf + Anschreiben je Bewerbung
- [x] Asynchrone Import-/Lücken-/Anschreiben-Jobs — lange LLM-Schritte überstehen Refresh und Proxys
- [x] Cap-sichere segmentierte Lebenslauf-Erzeugung (keine abgeschnittenen Dokumente bei ausführlichen Modellen)
- [x] Upload und Parsing mehrerer Lebensläufe (PDF, DOCX, Bilder via OCR)
- [x] Master-Profil-Konsolidierung mit Konfliktauflösung
- [x] Analyse von Stellenbeschreibungen (Text + URL-Scraping)
- [x] Lückenerkennung und Match-Scoring
- [x] Dialogbasierter Interview-Flow (gezielter + geführter Modus)
- [x] Lebenslauf-Erzeugung (PDF via Playwright, mehrere Vorlagen)
- [x] Lebenslauf-Abschnitts-Editor (Finetuner) mit Live-Vorschau und KI-gestützter Bearbeitung
- [x] Anschreiben-Erstellung
- [x] Foto-Verwaltung (Upload, Zuschnitt, Entfernen)
- [x] Erkennung kultureller Anpassung (DACH-spezifisch)
- [x] MCP-Server (stdio-Transport für KI-Agenten)
- [x] Flow-Orchestrator (State Machine für die Nutzerreise)
- [x] DSGVO-Retention-Worker (automatisierte TTL-Durchsetzung)
- [x] Mehrsprachige Oberfläche (de/en via next-intl)

### ⏳ Als Nächstes

Applire erscheint in Releases mit Dessert-Namen, jedes als öffentlicher [Milestone](https://github.com/Applire/Applire/milestones) — zum Mitlesen gibt es den [Blog](https://applire.de/blog/):

- [x] **Spaghettieis** — ausgeliefert mit v0.38.0-beta (Milestone geschlossen, 27 Issues). Parallele Bewerbungen wurden erstklassig: Bewerbungs-Dashboard mit Status-Tracking, Re-Tailoring über mehrere Stellen mit einem Klick, aktualisierte Stellenanzeigen-Analyse und besseres Fortschritts-Feedback bei langen Schritten
- [x] **Tiramisu** — ausgeliefert mit v0.39.0-beta. **Wahrheits-Orakel**: Jedes erzeugte Dokument erhält einen deterministischen Wahrheits-Report — ist jede Aussage im Profil verankert, ist jede Zahl belegt, wurde aus „zielt auf 70 %" stillschweigend „70 % erreicht"? In der Oberfläche und als MCP-Tool `audit_document`, das auch Dokumente prüft, die dein Agent selbst geschrieben hat; ebenso **`render_document`** (die eigenen Inhalte deines Agenten durch Applires normgeprüften Renderer, niemals umgeschrieben), **`submit_claims`** / **`submit_testimony`** (Agent-Interviews landen mit Belegen im Profil) und **`resolve_gap`**. Der Flavour schloss mit der *Auswahl* der Belege: ein einziger Schreibpfad ins Profil und eine Prüfschleife, deren Urteil das Dokument wie komponiert abdeckt
- [ ] **Stracciatella** — Felix übernimmt das Steuer: die führende Dokumentsprache pro Bewerbung wählen (Erkennung wird zum Vorschlag statt zum Gesetz), strukturierte Master-Profil-Editoren statt Roh-JSON und Muss-Fakten ans Dokument pinnen — dazu Härtung der gelieferten Dokumente (die Ship-Gate-Befunde aus v0.39 und die Prompt-Injection-Abwehr)
- [ ] **Strawberry** — Mehrbenutzer-Fähigkeit: Nutzerrollen, Anmelde-UI, Admin-Panel zur Nutzerverwaltung und Operator-Einstellungen

Darüber hinaus, ohne Termine: **Länderpakete über DACH hinaus** als Beitragsfläche für die Community. Die gehostete Demo und die **Applire Cloud (SaaS) pausieren**, während wir uns auf den Open-Source-Kern und den Agenten-Kanal konzentrieren — die [Warteliste](https://applire.de) erfährt es zuerst, wenn sich das ändert.

### 🔭 Zukunftsvision

- [ ] **Vorbereitung auf Probe-Interviews**: KI-gestützte Übungssitzungen mit rollenspezifischen Fragen
- [ ] **Karrierepfad-Beratung**: Skill-Gap-Analyse und Weiterbildungsempfehlungen
- [ ] **Jobsuche & Empfehlungen**: Kuratierte Job-Vorschläge auf Basis des Master-Profils
- [ ] **MCP-Marketplace-Listings**: Distribution über Agent-Marketplaces

---

## 🤝 Mitwirken

Wir freuen uns über Beiträge! Bitte folge diesen Schritten:

1. **Repository forken**
2. **Feature-Branch erstellen**: `git checkout -b feature/amazing-feature`
3. **Änderungen committen**: `git commit -m 'feat: add amazing feature'`
4. **Branch pushen**: `git push origin feature/amazing-feature`
5. **Pull Request öffnen**

### Entwicklungs-Richtlinien

- **PEP 8** für Python-Code befolgen (durchgesetzt via `black`)
- **TypeScript** für allen Frontend-Code verwenden (Strict Mode, kein `any`)
- **Tests** für neue Features schreiben (≥75 % Backend-Coverage)
- Commits **atomar** halten und [Conventional Commits](https://www.conventionalcommits.org/) verwenden
- Alle Schema-Änderungen laufen über **Alembic-Migrationen** — nie rohes DDL

### Code-Stil

```bash
# Backend: mit Black formatieren
black .

# Frontend: mit ESLint linten
npm run lint
```

### Contributor License Agreement

Mit dem Einreichen eines Pull Requests stimmst du dem [Applire CLA](CLA.md) zu. Das erlaubt uns, das Open-Core-Modell aufrechtzuerhalten und zugleich die Community Edition vollständig quelloffen zu halten. Details siehe [CONTRIBUTING.md](CONTRIBUTING.md).

---

## 💛 Projekt unterstützen

Applire entsteht nebenberuflich bei einem Solo-Gründer und bleibt vollständig Open Source (AGPL-3.0, keine zurückgehaltenen Features). Jedes Feature und jedes Release wird vor dem Ausliefern gegen **echte LLM-Anbieter** geprüft — adversariale Pässe, blinde Hiring-Panel-Reviews, Agent-Kanal-Journeys — und Applire entsteht öffentlich gemeinsam mit KI-Coding-Agenten. Sponsoring bezahlt genau das: die API-Credits, die KI-Coding-Werkzeuge und die Infrastruktur hinter applire.de und den Release-Images.

Wenn Applire dir einen Abend Lebenslauf-Feinschliff erspart, überlege, **[das Projekt auf GitHub zu sponsern](https://github.com/sponsors/Applire)**. Sponsoren werden in den Release-Notes und hier im README genannt, und jeder Release-Beitrag nennt, was das Sponsoring in diesem Zyklus bezahlt hat.

---

## 💬 Community & Support

### Hilfe bekommen

- 🌐 **[applire.de](https://applire.de)** — Website, [Blog](https://applire.de/blog/) (Deutsch und Englisch) und Beta-Warteliste
- 📖 **[Dokumentation](docs/)** — Test-, CI/CD- und Architektur-Leitfäden
- 🐛 **[GitHub Issues](https://github.com/Applire/Applire/issues)** — Fehler melden und Features anfragen
- 💬 **[GitHub Discussions](https://github.com/Applire/Applire/discussions)** — Fragen stellen und Ideen teilen

---

## 📄 Lizenz

Dieses Projekt steht unter der **GNU Affero General Public License v3.0 (AGPL-3.0)** — Details siehe [LICENSE](LICENSE).

### Warum AGPL?

Wir haben AGPL gewählt, um sicherzustellen, dass:
- ✅ **Die Software frei und quelloffen bleibt** — immer für alle zugänglich
- ✅ **Änderungen geteilt werden müssen** — auch bei Nutzung als Dienst (SaaS)
- ✅ **Die Community profitiert** — alle Verbesserungen fließen zurück ins Projekt
- ✅ **Dein Datenschutz geschützt ist** — volle Transparenz darüber, wie deine Daten verarbeitet werden
- ✅ **Kein Vendor-Lock-in** — du kontrollierst deine Daten und Infrastruktur

### Kommerzielle Lizenzierung

Für Organisationen, die die AGPL-Anforderungen nicht erfüllen können (z. B. proprietäre SaaS-Angebote), sind kommerzielle Lizenzen verfügbar. Kontakt: **kontakt@applire.de**.

---

## 🙏 Danksagungen

- **Mistral AI** für EU-gehostete LLM-Infrastruktur
- Die **FastAPI**- und **Next.js**-Communities
- Alle Mitwirkenden und frühen Anwender:innen
- DACH-Branchenexpert:innen, die ihr Fachwissen beigesteuert haben
- Die Open-Source-Community für Inspiration und Werkzeuge

---

## 📬 Kontakt

- **Website**: [applire.de](https://applire.de) — [Blog](https://applire.de/blog/) und Beta-Warteliste
- **E-Mail**: kontakt@applire.de
- **Issues**: [GitHub Issues](https://github.com/Applire/Applire/issues)
- **Sicherheit**: kontakt@applire.de (siehe [SECURITY.md](SECURITY.md))

---

<div align="center">

**Mit ❤️ für Jobsuchende im DACH-Markt gebaut**

*Open Source. Datenschutz zuerst. Agent-fähig. Wahrheitstreu by Design.*

[⭐ Gib uns einen Stern auf GitHub](https://github.com/Applire/Applire)

</div>

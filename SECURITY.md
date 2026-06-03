# Security Policy

## Supported Versions

We provide security fixes for the latest released version only.

| Version | Supported |
| ------- | --------- |
| Latest  | ✅        |
| < 1.0   | ❌ (beta — no SLA) |

## Reporting a Vulnerability

**Do not report security vulnerabilities through public GitHub issues.**

Please email: **kontakt@applire.de**

Include in your report:
- A description of the vulnerability
- Steps to reproduce it
- Potential impact
- Suggested mitigation (optional)

We will acknowledge your report within **48 hours** and aim to release a fix within **14 days** for critical issues.

Public disclosure is coordinated after a fix is available (coordinated disclosure).

## Scope

In scope:
- `Applire-Core` backend (FastAPI, Python)
- `Applire-Core` frontend (Next.js)
- Authentication and session handling
- File upload and processing paths
- LLM prompt injection vectors

Out of scope:
- Third-party services (OpenRouter, Mistral AI, etc.)
- Vulnerabilities in dependencies without a proof-of-concept exploit
- Spam or social engineering

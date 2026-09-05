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

## Prompt injection — our position

A job posting is the one input Applire processes that the user did not write: it
comes from an arbitrary URL or a paste. We treat it, and everything derived from
it, as untrusted.

**What we do.** Every point where posting text or a posting-derived string enters
a model prompt marks it structurally as third-party content that is data and not
instructions, from one shared helper (`backend/applire/services/untrusted_text.py`).
MCP tool results that carry such text include an `untrusted_content` object
naming the fields, so an agent consuming our output can apply its own rules; the
agent guide explains it. A small corpus of hostile postings with benign twins
(`tests/files/injection_corpus/`) is run against a real model to measure what
the marking buys.

**What we do not claim.** The marking does not *prevent* injection, and we do not
filter, sanitise or classify posting text — deciding whether a sentence is an
attack is a judgement we deliberately keep out of the deterministic layer. What
bounds the damage is an architectural invariant, stated here so a report can be
aimed at it: **Applire's internal LLM is tool-less.** It has `complete` and
`parse_json` and nothing else — no function calling, no shell, no network, no
database. Model output is parsed into Pydantic models and written through a
parameterised ORM, and no secrets or other users' data are in the prompt context.
A report showing that invariant broken is far more valuable to us than one
showing that a model can be talked into odd prose.

**Agent channel.** Applire is agent-ready: an AI agent may drive it over MCP.
That agent's own injection hardening is its vendor's responsibility, not ours —
ours is the channel: never hand a stranger's text to an agent dressed as trusted
output.

**Uploaded CV text** is a different trust class: it is the user's own document,
in a single-user self-hosted product. We do not currently treat it as adversarial
input, and we say so rather than implying coverage we do not have.

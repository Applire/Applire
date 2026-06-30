# Choosing an LLM for Applire

Applire is **bring-your-own-key**: you pick the model, set `LLM_PROVIDER` (and the matching
API key), and Applire runs against it. No provider is privileged. This guide helps you
choose a model that gives the smoothest experience — especially for the two heaviest
operations, **CV tailoring** and **multi-CV profile merging**.

> **You are not locked out by a "weak" model.** Applire generates large documents in
> segments so that no single model call needs a big output (see
> [ADR-047 in ARCHITECTURE.md](ARCHITECTURE.md#adr-047--llm-output-robustness-segmentation-first)).
> That means even models with a small output ceiling, or a local Ollama, will produce a
> complete CV. The recommendations below are about *quality and smoothness*, not
> hard requirements. Where the limit is discoverable (OpenRouter, Ollama) Applire detects
> it and segments pre-emptively; everywhere else it falls back to segmenting on demand.

## What actually matters

LLMs have **two different limits**, and the one that bites is usually the smaller one:

| Limit | What it is | Why it matters |
|---|---|---|
| **Context window** | How much text the model can *read* (input + output combined) | Rarely the constraint for Applire — profiles and JDs are small. |
| **Max output tokens** | How much the model can *write* in one response | **This is the one that matters.** It is often far below the context window (e.g. a 128k-context model that only emits ~8k tokens), and a whole CV or a rich two-CV merge is a large output. |

A second, subtler factor is **reasoning ("thinking") behaviour**. On reasoning models the
"thinking" tokens come out of the *same* output budget as the answer, so a model that
reasons heavily can starve the actual output. Applire manages this for you
(`disable_thinking` for short calls, `OPENROUTER_REASONING_EFFORT` to bound it — see
ARCHITECTURE.md), but a model that lets you *bound or disable* reasoning is easier to run.

## Minimum-capability floor

A model at or above this floor will run every Applire flow comfortably:

- **Output budget:** ideally **≥ 8k** tokens per response; **≥ 4k** works fine with
  segmentation. (Below ~4k, segmentation still completes but uses more calls.)
- **Instruction-following for JSON:** Applire asks for structured JSON. Native
  structured-output / JSON mode is a plus but not required — Applire validates and repairs.
  Very small models (well under ~7B parameters) tend to struggle with structured output
  and produce more retries.
- **Reasoning:** any behaviour works. Models that let you bound/disable reasoning
  (most OpenRouter models, OpenAI/Anthropic) give the cleanest results; reasoning-mandatory
  models are handled automatically but spend more tokens.

## Picking by priority

**If EU data residency matters** (the typical DACH case):

- **Mistral** (`LLM_PROVIDER=mistral`) — EU-hosted, strong German. The shipped fallback.
  Note some Mistral models have a modest output cap; segmentation covers this, and Applire
  runs fine on them.
- **Requesty** (`LLM_PROVIDER=requesty`) — EU endpoint (Frankfurt, zero-retention) that
  also routes to frontier models (Claude / GPT / Gemini) via their EU deployments. The
  EU-resident way to use a top-tier model.
- **Ollama** (`LLM_PROVIDER=ollama`) — fully offline, no key, no cloud. Pick a model with a
  generous context and set `num_ctx` explicitly (Ollama defaults it low). Larger instruct
  models (e.g. a capable ~8B+ instruct model) handle the structured calls best.

**If you just want the smoothest quality** and residency is not a constraint:

- **OpenRouter** (`LLM_PROVIDER=openrouter`) — one key, many models; lets you pick a model
  with a high output ceiling and bounded reasoning. Convenient for trying models.
- **Anthropic** (`LLM_PROVIDER=anthropic`) — Claude via a Console **API key** (a Claude
  Pro/Max/Team *subscription* cannot be used in third-party apps). US-hosted.
- **OpenAI / OpenAI-compatible** (`LLM_PROVIDER=openai`) — also covers LM Studio, vLLM, and
  other local servers via `OPENAI_BASE_URL`. Output-ceiling discovery isn't available on
  generic endpoints, so Applire relies on segmentation here — which is exactly what it's for.

## Symptoms and what they mean

| You see… | Likely cause | What to do |
|---|---|---|
| Generation retries or takes several steps | Model has a small output ceiling — segmentation is doing its job | Nothing required; pick a higher-output model for fewer steps |
| Occasional "couldn't finish — try again" | Transient provider error or a reasoning-heavy model | Retry; consider bounding reasoning (`OPENROUTER_REASONING_EFFORT=low`) |
| Frequent malformed output on a tiny local model | Model too small for reliable structured output | Use a larger instruct model |

> Model names and limits change quickly — treat any specific model mentioned here as an
> example, not a pinned recommendation. The **capability floor** above is the durable guide.

## See also

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — ADR-009 (provider abstraction) and ADR-047 (output
  robustness) for how Applire keeps generation stable across models.
- `.env.example` — the environment variables for each provider.

# LLM measurement log

This is the record behind [Choosing an LLM for Applire](llm-models.md): every model run, oldest
first, with the prompt version it was measured against. If you only want to know which model to
pick, read that page instead — it carries the current result of each model and nothing else.

A row here is a statement about a **model**, a **route** (the gateway it was called through) and a
**prompt version** on a given date. Rows from older prompt versions are kept because they explain
why a model moved, not because they still describe it.

## Which models work — measured (2026-09-09)

The capability floor above is a *specification*. This section is a **measurement**: the
same interview turn, replayed on each model, counted. It exists because a model can meet
every stated requirement — big context, JSON mode, bounded reasoning — and still quietly
drop what you told Applire about yourself.

> **This does not introduce a default model.** Applire ships no recommendation and
> privileges no provider; the table says what was measured, and the choice stays yours.

### What is measured, and why this seam

The **reconcile seam** — the single LLM call (ADR-046) that turns one interview answer
into a batch of typed operations against your vault. It is the sharpest test in the
product for three reasons: it is a *write* path (a mistake changes your stored profile,
not a document you can regenerate), it demands a large structured output against a
14-rule prompt, and its failures are **silent** — an empty operation batch looks exactly
like "nothing new in that answer".

Each measured turn feeds the model a synthetic vault plus one gap answer that names three
different employers, and reads back what it emitted. Four failure classes are counted:

| Rate | What went wrong | Why it matters |
|---|---|---|
| **lost turn** | nothing the model emitted wrote anything to your vault — an empty batch, or only a question handed back to you | your answer is gone, with nothing on screen to say so |
| **malformed-op** | it emitted an operation the schema rejects | the same loss one layer down; Applire logs a warning and drops it |
| **wrong-slot** | a fact landed on a different employer than the one the answer named | your CV would credit the wrong job |
| **no response** | the call never came back (timeout, transport error) | not the model's judgement, so it is counted separately and excluded from the rates above |

"Lost turn" is deliberately stricter than "returned nothing". One model in the first
round answered every turn with a single *question back to the candidate* and no vault
operation at all — a perfectly well-formed response that lost the answer just as
completely as silence. And "no response" is separated out because Applire's reconciler
turns a failed call into an empty result: without splitting them, a model that timed out
would be published as a model that chose to say nothing.

### The bar — two tiers

A model has to clear **both** to be called good enough.

**Tier 1 — does your vault survive the interview?** The rates above, measured per input
shape; the **worst** shape decides.

| Verdict | Meaning |
|---|---|
| **qualified** | every shape clean: no lost turn, no malformed operation, no wrong slot |
| **usable, with caveats** | losses occurred but stayed under every bar below |
| **sub-par** | any bar crossed on any shape |

Bars: **lost turn > 5 %** · **malformed-op > 10 %** · **wrong-slot > 10 %** · **no response > 10 %**.

The lost-turn bar is the tightest because it is the one failure the product cannot show you.

**Tier 2 — do the documents get you invited?** A vault that survives is not the whole job.
For each model that clears tier 1, Applire generates a CV and a cover letter for a case
whose data is a *very good* match for the posting, and a blind hiring panel — an HR
screener and a hiring manager who never see which model wrote the documents — decide
whether to invite. A model that produces documents this panel will not invite is **sub-par
too**, however clean its operation batches were. A model that is already sub-par on tier 1
is not put through tier 2.

### Results

<!-- Rows come from a real run of scripts/model_matrix.py; never from a datasheet, a
     benchmark score, or a reputation. An unmeasured model has no row.
     Rates are per shape, written S6 / S7 / S8:
       S6 = three-employer vault  ·  S7 = one-employer vault  ·  S8 = five-employer vault
     "reasoning" is the effective setting for the run, and it changes results a lot —
     see the note under the table. -->

| Model | Gateway | Reasoning | n | lost turn S6/S7/S8 | malformed | wrong-slot | no response | Tier 1 | Panel (tier 2) | tokens in/out | ≈ cost/run |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `mistralai/ministral-3b-2512` | OpenRouter | on (model default) | 10 | 0% / 0% / 0% | 0% / 0% / 0% | 0% / 70% / 10% | 0/30 | **sub-par** | n/a (sub-par on tier 1) | 186,541 / 9,575 | $0.0196 |
| `mistralai/ministral-8b-2512` | OpenRouter | on (model default) | 10 | 0% / 0% / 10% | 0% / 20% / 20% | 0% / 10% / 0% | 0/30 | **sub-par** | n/a (sub-par on tier 1) | 224,180 / 16,813 | $0.0361 |
| `openai/gpt-5.6-luna` | OpenRouter | on (model default) | 10 | 0% / 20% / 0% | 0% / 0% / 0% | 0% / 30% / 0% | 0/30 | **sub-par** | n/a (sub-par on tier 1) | 155,934 / 20,359 | $0.0251 |
| `z-ai/glm-5.3-flash` | OpenRouter | on (model default) | 10 | 20% / 0% / 0% | 0% / 40% / 0% | 0% / 10% / 0% | 0/30 | **sub-par** | n/a (sub-par on tier 1) | 169,933 / 152,569 | $0.0509 |
| `glm-5.3-flash` | **Requesty** (an operator's actual route, run inside that install's container) | off (route default) | 10 | 20% / **70%** / 30% | 10% / 20% / 20% | 0% / 10% / 0% | 0/30 | **sub-par** | n/a (sub-par on tier 1) | 158,497 / 4,824 | ≈ $0.013 |
| `openai/gpt-5-nano` | OpenRouter | on (model default) | 10 | 10% / 20% / 40% | 0% / 0% / 0% | 0% / 60% / 10% | 0/30 | **sub-par** | n/a (sub-par on tier 1) | 161,562 / 116,042 | $0.0545 |
| `nvidia/nemotron-3-super-120b-a12b:free` | OpenRouter | on (model default) | 10 | 60% / 60% / 60% | 0% / 0% / 0% | 0% / 20% / 0% | 0/30 | **sub-par** | n/a (sub-par on tier 1) | 166,462 / 51,340 | $0 (free tier) |
| `deepseek/deepseek-v4-flash-0731` | OpenRouter | on (model default) | 10 | 30% / 14% / 89% | 0% / 0% / 0% | 0% / 0% / 0% | 4/30 | **sub-par** | n/a (sub-par on tier 1) | 150,002 / 223,345 | $0.0432 |
| `qwen/qwen3.8-flash` | OpenRouter | on (model default) | 10 | 17% / 0% / 43% | 0% / 0% / 0% | 0% / 0% / 0% | 17/30 | **sub-par** | n/a (sub-par on tier 1) | 83,609 / 103,556 | $0.0612 |
| `qwen/qwen3.8-flash` (600 s timeout) | OpenRouter | on (model default) | 10 | 40% / 20% / 40% | 0% / 20% / 0% | 0% / 0% / 0% | 0/30 | **sub-par** | n/a (sub-par on tier 1) | 184,360 / 341,405 | $0.1881 |
| `anthropic/claude-haiku-4.5` | OpenRouter | on (model default) | 10 | 100% / 0% / 0% | 0% / 0% / 0% | 0% / 100% / 0% | 0/30 | **sub-par** | n/a (sub-par on tier 1) | 172,478 / 7,060 | $0.2078 |
| `mistralai/mistral-small-2603` | OpenRouter | on (model default) | 10 | 100% / 100% / 30% | 0% / 0% / 0% | 0% / 0% / 0% | 0/30 | **sub-par** | n/a (sub-par on tier 1) | 163,150 / 3,009 | $0.0263 |
| `cohere/command-r7b-12-2024` | OpenRouter | on (model default) | 10 | 100% / 100% / 10% | 0% / 0% / 0% | 0% / 0% / 0% | 0/30 | **sub-par** | n/a (sub-par on tier 1) | 163,044 / 4,692 | $0.0068 |

**These rows were measured against the prompt as it stood on the morning of 2026-09-09.**
That prompt was changed the same day, and three of these models were re-measured against
the new one with different results — see *After the prompt was fixed* below. A row here is
a statement about a model **and** a prompt version, exactly as a malformed-op rate is a
statement about a model and a schema.

All rows measured 2026-09-09 through OpenRouter from a clean checkout with no `.env`, so
every knob sat at its code default — in particular reasoning was **left on**, which is what
an operator who never touches the setting gets. `n` = 10 turns per shape, 30 per model.
Rates are over the turns the model actually answered; the "no response" column says how many
it did not.

**Nothing in this table is qualified.** That is the finding, not a formatting accident. On
these three shapes every model measured — the cheapest and the most expensive alike — either
loses turns, mis-attributes facts, or emits operations the schema rejects. Two consequences:
the tier-2 panel has nothing to run on yet, and the next piece of work is the prompt, not the
model list.

Read the columns as different diseases, not one score:

- **Silence.** `mistralai/mistral-small-2603` and `cohere/command-r7b-12-2024` lost every
  single one-employer turn. The answers they were given open with a denial ("*while I have
  not worked with insulin in particular…*") and then name three employers; both models
  stopped at the denial. r7b did it by asking the question back instead of recording anything.
- **Mis-attribution.** `mistralai/ministral-3b-2512` never lost a turn and never emitted an
  invalid operation — and put a fact on the wrong employer in 7 of 10 one-employer turns.
  `anthropic/claude-haiku-4.5` credited a whole fifteen-year cross-employer career to the
  candidate's current job, in every one of those turns. A confident wrong answer is worse for
  a candidate than a blank.
- **Schema drift.** `z-ai/glm-5.3-flash` and `mistralai/ministral-8b-2512` are the only two
  that emitted operations the schema rejected; in glm's case by creating an employer the
  answer named but gave no job title for, which the schema currently requires.
- **Not finishing.** `qwen/qwen3.8-flash` did not return at all on 17 of 30 calls at the
  180 s client timeout this run used (`LLM_TIMEOUT`). Re-measured with a 600 s timeout it does
  return — and then loses 40 % / 20 % / 40 % of turns anyway, so the timeout was hiding a
  second problem rather than being the only one. `deepseek/deepseek-v4-flash-0731` spent the
  entire 32k output budget on 4 turns and emitted nothing usable.

Cost is not the deciding factor: the spread across this table is about 30× in price and the
outcomes do not follow it.


**The same model measures differently on a different setting or gateway.** Reasoning left
on, reasoning disabled, and one gateway versus another are not cosmetic differences here:
the first model measured moved from 60 % to 20 % zero-op on the same shapes when the run
came through a different gateway with reasoning enabled. Read a row together with its
`reasoning` and `gateway` cells, and re-measure before trusting a row for a configuration
it was not measured on.

Tier-1 rows were measured against the operation schema as of `main` @ `24ee8cd6`. The
harness stores the exact validation error per rejected operation, so every row can be
re-scored (`--score`, no new provider calls) when that schema changes — a malformed-op rate
is a statement about a model *and* a schema, not about the model alone.

### After the prompt was fixed (2026-09-09, prompt v2)

The table above measures the prompt as it stood when the eleven models were run. The
prompt was then **changed three times**, and every change was measured before and after on
the same shapes, the same fixture set and the same `n`, with each arm carrying its own change
and all earlier ones. Two of the three models re-measured moved verdict. The rows below are
the state this release ships: the changed prompt **with the schema on** (`LLM_STRUCTURED_OUTPUT=auto`,
the default — see the next section for what it costs).

**What changed in the prompt.** (1) The rule about denials said that an answer which does
not address the question contributes nothing about the question's topic, and stated its
"emit nothing" escape a second time. An answer like *"I have not worked with insulin in
particular, but I have 15+ years at A, at B and now at C"* therefore gave the model two
explicit reasons to record nothing. It now says a denial is about its own item and nothing
else, and the escape is stated once. (2) The rule about entity identity said nothing about
a sentence naming several employers; it now says each clause binds to the employer that
clause names, with a worked example. (3) Each operation now lists the fields that are
required *also when merging into an existing entry* — on its own this line bought nothing
measurable, but with the schema on it is the difference between 4 and 19 rejected operations
on the weakest model measured; the two are complements.

| Model | Gateway | Reasoning | n | lost turn S6/S7/S8 | malformed | wrong-slot | Tier 1 **before** | Tier 1 **after** | tokens in/out | Panel (tier 2) |
|---|---|---|---|---|---|---|---|---|---|---|
| `openai/gpt-5.6-luna` | OpenRouter | on (model default) | 10 | 0% / 0% / 0% | 0% / 0% / 0% | 0% / 0% / 0% | sub-par | **qualified** | 260,790 / 26,510 | **HR yes / HM yes** [1] |
| `z-ai/glm-5.3-flash` | OpenRouter | on (model default, cannot be disabled there) | 10 | 0% / 0% / 0% | 0% / 0% / 0% | 0% / 0% / 0% | sub-par | **qualified** | 257,451 / 42,163 | **sub-par — did not complete the application** [2] |
| `mistralai/ministral-8b-2512` | OpenRouter | on (model default) | 10 | 0% / 0% / 0% | 10% / 0% / 10% | 0% / 0% / 0% | sub-par | **usable, with caveats** | 233,294 / 19,337 | **HR yes / HM yes, with caveats** [3] |
| `glm-5.3-flash` | **Requesty** (an operator's actual route, run inside that install's container) | off (route default; the route does not reason even when asked) | 10 | **40%** / **30%** / 0% | 0% / 10% / 0% | 0% / 0% / 0% | sub-par | **sub-par** | 193,777 / 6,763 | n/a (sub-par on tier 1) |
| `glm-5.3-flash` | **Requesty**, schema off (control) | off | 10 | **50%** / **20%** / **20%** | 0% / 10% / 10% | 0% / 0% / 0% | sub-par | **sub-par** | 190,431 / 5,678 | n/a (sub-par on tier 1) |
| `openai/gpt-5.6-luna` | **Requesty** (an operator's actual route, run inside that install's container; model id `openai/gpt-5.6-luna`) | off (route default; luna reports no reasoning tokens on either gateway) | 10 | 0% / 0% / 0% | 0% / 0% / 0% | 0% / 0% / 0% | n/a (first measurement) | **qualified** | 260,790 / 25,640 | not measured (the tier-2 run below used the OpenRouter row) |

[1] Case `operations_marcus_de`, blind HR + hiring-manager panel, Nougat build-3 delivery run
(2026-09-13, 97 calls): both members decided yes, matching the case's designed outcome —
`Documents/Runs/Nougat/build-3/delivery-run/2026-09-13-operations-marcus-de.md`.
[2] Same case, tier-2 dispatch (2026-09-14, 47 pipeline calls, ≈$0.22): gap analysis failed 3/3
on `llm_timeout`, interview session creation failed 2/2 on `llm_timeout`, and CV generation
failed 3/3 (two uncaught malformed-JSON crashes, one timeout); the cover letter alone succeeded
1/1, but the panel needs both documents and did not run —
`Documents/Runs/Nougat/build-3/model-matrix/tier2-z-ai-glm-5.3-flash/report.md`.
[3] Same case, tier-2 dispatch (2026-09-14, 93 pipeline calls): both members decided yes, but the
delivered documents dropped a required, directly-evidenced skill bullet (ISO 9001), carried a
duplicate-bullet pair, and shipped a reviewer false positive that flagged the candidate's honest
denial as an "invented limit" —
`Documents/Runs/Nougat/build-3/model-matrix/tier2-mistralai-ministral-8b-2512/report.md`.

**Tier 1 does not guarantee tier 2.** `z-ai/glm-5.3-flash` is tier-1 **qualified** on this seam
and still failed to produce a CV at all over OpenRouter — gap analysis, interview-session
creation, and CV generation each hit `llm_timeout` or crashed on malformed JSON at the dev
stack's default `LLM_TIMEOUT=180`, so the panel never ran. A clean vault write does not mean
the model finishes the application in time.

The same model on the two gateways is the sharpest row in this table: over OpenRouter
`z-ai/glm-5.3-flash` reasons on every call (the gateway will not let it stop) and is clean;
over Requesty it does not reason (about 110 output tokens per call, and `REQUESTY_REASONING_EFFORT`
does not change that) and records nothing on 30–50 % of denial-opening answers with either
prompt — the prompt fix moved that silence between shapes rather than removing it. The
schema is accepted on that route and does not degrade it (the control row is the same prompt
with the schema off). If your install runs this model over Requesty, that is the row to read.

`openai/gpt-5.6-luna`'s move happened in two steps and only one of them is the prompt
review: the schema change that made a job title optional (a model that correctly refuses to
invent one could not create a station at all) already took it to a clean sheet, and the
prompt changes held it there. `z-ai/glm-5.3-flash` moved on the denial rule alone — **and
spent 48 % fewer output tokens doing it** (109,253 → 57,202 for the same 30 turns, and
again to 42,364 after the second change), with its median turn falling from 45 s to 6 s.
The model had been spending its reasoning budget deliberating a contradiction in the
instructions; removing the contradiction removed the deliberation. Better and cheaper on
the same call.

Every model in the **§Results** table above has now been re-measured: three against prompt v2
on 2026-09-09 (this section), and the remaining eight against the prompt and schema as they
stand on `feat/nougat-build-3` on 2026-09-14 — see the next section. A row's date is the prompt
version it describes; re-run the harness before trusting either one against a prompt that has
since changed again.

### Re-measured on the Nougat prompt (2026-09-14)

The eight models this section left untouched were re-measured today on `wt-nougat3` @
`61423bd0` — several rounds of prompt and schema work past the 2026-09-09 "prompt v2" above —
through `scripts/model_matrix.py`, same three shapes, `n=10` per shape, schema `auto`, over
OpenRouter. Verdict thresholds are unchanged: lost turn (zero-op) > 5 %, malformed-op > 10 %,
wrong-slot > 10 %, error (no response / transport failure) > 10 %.

| Model | Gateway | Reasoning | n | lost turn S6/S7/S8 | malformed | wrong-slot | error | Tier 1 **before** | Tier 1 **after** | tokens in/out | ≈ cost |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `anthropic/claude-haiku-4.5` | OpenRouter | on (model default) | 10 | 0% / 0% / 0% | 0% / 0% / 0% | 0% / 0% / 0% | 0% / 0% / 0% | sub-par | **qualified** | 219,200 / 8,520 | $0.114019 |
| `mistralai/mistral-small-2603` | OpenRouter | on (model default) | 10 | 0% / 0% / 0% | 0% / 0% / 0% | 0% / 10% / 0% | 0% / 0% / 0% | sub-par | **usable, with caveats** | 283,394 / 12,989 | $0.035595 |
| `mistralai/ministral-3b-2512` | OpenRouter | on (model default) | 10 | 0% / 0% / 0% | 0% / 40% / 0% | 0% / 0% / 0% | 0% / 0% / 0% | sub-par | **sub-par** | 212,725 / 13,219 | $0.003597 |
| `qwen/qwen3.8-flash` | OpenRouter | on (model default) | 10 | 0% / 0% / 20% | 0% / 0% / 20% | 0% / 0% / 0% | 0% / 10% / 0% | sub-par | **sub-par** | 303,272 / 194,801 | $0.112907 |
| `deepseek/deepseek-v4-flash-0731` | OpenRouter | on (model default) | 10 | 11% / 0% / 0% | 0% / 0% / 0% | 0% / 0% / 0% | 10% / 30% / 10% | sub-par | **sub-par** | 244,947 / 72,654 | $0.116225 |
| `nvidia/nemotron-3-super-120b-a12b:free` | OpenRouter | on (model default) | 10 | 0% / 11% / 0% | 0% / 0% / 0% | 0% / 0% / 0% | 20% / 10% / 20% | sub-par | **sub-par** | 182,445 / 137,605 | $0.041667 |
| `openai/gpt-5-nano` | OpenRouter | on (model default) | 10 | 44% / 0% / 30% | 0% / 0% / 0% | 0% / 0% / 0% | 10% / 10% / 0% | sub-par | **sub-par** | 256,344 / 140,964 | $0.068181 |
| `cohere/command-r7b-12-2024` | OpenRouter | on (model default) | 10 | 100% / 100% / 100% | 0% / 0% / 0% | 0% / 0% / 0% | 0% / 0% / 0% | sub-par | **sub-par** | 187,690 / 1,675 | $0.0 |

Two rows moved. `anthropic/claude-haiku-4.5` — sub-par under prompt v1 on lost turn (100 % on
S6) and wrong-slot (100 % on S7) — is now **qualified**, clean on all three shapes and every
rate. `mistralai/mistral-small-2603` moved from sub-par (100 % / 100 % / 30 % lost turn) to
**usable, with caveats** — one wrong-slot turn on S7, no lost turns anywhere. The other six
stay sub-par, each now failing a different bar than before: `cohere/command-r7b-12-2024` still
loses every single turn (100 % zero-op on all three shapes, unchanged); `mistralai/ministral-3b-2512`
now fails on malformed-op (40 % on S7) rather than wrong-slot; `qwen/qwen3.8-flash` and
`openai/gpt-5-nano` still lose turns, but on different shapes than before (S8 for qwen; S6/S8
for gpt-5-nano); and `deepseek/deepseek-v4-flash-0731` and
`nvidia/nemotron-3-super-120b-a12b:free` now cross the error-rate bar (transport failures,
10–30 %) rather than the lost-turn bar they crossed under prompt v1.

### Re-measured on the `match_existing` prompt (2026-09-16)

The reconciler's vocabulary gained a sixteenth operation on 2026-09-16 — `match_existing`, the
model's way to record that an incoming entry is one the vault already holds under another name
(a translation, a synonym, an abbreviation) instead of staying silent, which the import's
post-merge check could not tell from dropping. The prompt grew from 19,007 to 20,106 characters.
Per the rule this guide's numbers rest on, the change was measured before it shipped: the three
reference models, same five shapes (S6–S10), `n=10`, schema `auto`, reasoning at each model's own
default, against the 2026-09-11 baseline records of the same models on the previous prompt.

| Model | lost turn S6/S7/S8/S9 | malformed | wrong-slot | error | movement vs baseline |
|---|---|---|---|---|---|
| `openai/gpt-5.6-luna` | 0% / 0% / 0% / 0% | 0% | 0% | 0% | none |
| `z-ai/glm-5.3-flash` | 0% / 0% / 0% / 0% | 0% | 0% | 0% | S10 (a verbatim restatement, correct answer: write nothing) 100% → 90% zero-op — one turn restated the line as a bullet |
| `mistralai/ministral-8b-2512` | 0% / 0% / 0% / 0% | 0% / **10%** / 0% / 0% | 0% | 0% | S7 malformed 0% → 10%: one `upsert_work` without `company`, the shape this model has shown on S7 in earlier arms; at the threshold, not over it |

No model crossed a threshold it was under, and none emitted `match_existing` on these shapes —
none of them restates an entry in another language, so the matrix measures that the new operation
costs nothing here, not what it does. What it does was measured on a real German-then-English
two-CV import on the dev stack (three runs per arm): before, the import listed the two translated
languages as "not carried over" in two runs and duplicated them as new rows in the third; after,
the not-carried list was empty in all three, the vault kept exactly two language rows, and the
duplicated translated skills fell from three per run to three, one and none.

### The vault call now carries a schema (`LLM_STRUCTURED_OUTPUT`, on by default)

The single call that writes your profile is handed the 16 operations it may emit as a JSON
schema, not only as prose in the prompt. The schema is generated from the same types that
validate the answer, so the two cannot drift apart, and it lists exactly the fields the
prompt asks for — nothing more, so it cannot invite a field the prompt deliberately avoids.

What it bought, on the same fixtures as the table above: `mistralai/ministral-8b-2512`'s
station coverage on the one-employer answer went from 0.10 to 1.00 (it had been dropping
whole stations) and its malformed-operation rate halved. Neither of the two models that
were already clean regressed.

What it costs: **about 2,300 extra input tokens per interview turn** — roughly a third more
input on that one call, unchanged output, unchanged latency. To turn it off:

```bash
LLM_STRUCTURED_OUTPUT=off
```

If your model's endpoint does not support schemas it rejects the request once; Applire
notes that, falls back to plain JSON mode for the rest of that process, and the turn it
happened on still completes. You do not have to know in advance whether your model
supports it.

### One more thing about "turn reasoning off"

`OPENROUTER_DISABLE_THINKING` / `REQUESTY_DISABLE_THINKING` are a **request**, not a
switch. Two things were measured on 2026-09-09 that are worth knowing before you set them:

- **Some models refuse.** `z-ai/glm-5.3-flash` on OpenRouter answers *"Reasoning is
  mandatory for this endpoint and cannot be disabled"* (HTTP 400). Applire retries
  correctly with reasoning left on, so the model still works — but the setting saved you
  nothing, and until this release it re-sent the rejected request on **every** call, which
  doubled the round-trips and made timeouts more likely. It is now remembered per process.
- **Some routes never reason anyway.** The same model through a different gateway produced
  ~124 output tokens per call against ~2,724 on OpenRouter, with the setting untouched, and
  asking that route explicitly for `medium` reasoning effort did not change it. Whether a
  model reasons is a property of the model *and* the gateway, not of this setting.

If the goal is to spend less: the largest saving measured on this seam did not come from
the reasoning switch at all. It came from fixing the prompt.

### Reproduce it yourself

The harness is in the repository and opt-in — it never runs in CI, and it needs your own
API key:

```bash
# what the model is asked to read, without spending anything
PYTHONPATH=backend python3 scripts/model_matrix.py --dry-run

# measure one model (this spends real credit on your key)
PYTHONPATH=backend python3 scripts/model_matrix.py \
  --provider openrouter --model <model-id> --n 10 --shapes S6,S7,S8 \
  --out matrix.jsonl
```

Inputs are synthetic and committed (`tests/files/model_matrix/`), so two runs of the same
model on the same fixtures are comparable. The numbers describe *these* shapes on *this*
seam at the date given — a model that fails here may be perfectly good elsewhere, and
model behaviour changes under the same name over time.

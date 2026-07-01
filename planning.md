# Provenance Guard — Planning

Backend system that a creative-sharing platform can plug in to classify submitted
text as human-written or AI-generated, score confidence in that classification,
surface a plain-language transparency label, and let creators appeal.

> **Milestone status:** This document is written before implementation. Milestone 1
> content (architecture narrative, signal choices, false-positive trace, API surface,
> diagram) and Milestone 2 content (the five spec questions, calibration details, label
> variants, edge cases, and the AI Tool Plan) are below.

## Table of contents

1. [Architecture](#architecture) — diagram + narrative (both flows)
2. [Detection Signals](#detection-signals-milestone-1-decision)
3. [False-Positive Scenario](#false-positive-scenario-traced-through-the-system)
4. [API Surface](#api-surface-contract)
5. [Spec: The Five Questions](#spec-the-five-questions) — signals, uncertainty, labels, appeals, edge cases
6. [AI Tool Plan](#ai-tool-plan)

---

## Architecture

### Narrative — the path a submission takes

A creator sends `POST /submit` with their `text` and `creator_id`. The API assigns a
unique `content_id`, then runs the text through the **multi-signal detection pipeline**:

1. **Signal 1 — LLM classifier (Groq).** The raw text is sent to Groq
   (`llama-3.3-70b-versatile`) with a structured prompt asking how likely the text is
   AI-generated. Returns a score in `[0,1]` (1 = confidently AI).
2. **Signal 2 — Stylometric heuristics (pure Python).** The same raw text is measured
   for structural regularity (sentence-length variance, vocabulary diversity,
   punctuation density). Returns an independent score in `[0,1]`.

Both scores flow into the **confidence scorer**, which combines them into a single
calibrated confidence and maps the result to one of three attribution buckets
(`likely_ai`, `uncertain`, `likely_human`). The scorer is deliberately biased against
false positives (see below). The combined result is passed to the **label generator**,
which turns the bucket + confidence into plain-language transparency label text.

Everything — `content_id`, `creator_id`, timestamp, attribution, combined confidence,
both individual signal scores, and status — is written as one structured entry to the
**audit log** (SQLite). The API then returns `content_id`, `attribution`, `confidence`,
`signals`, and `label` to the caller. `GET /log` exposes recent audit entries.

### Appeal flow

A creator who disputes a verdict sends `POST /appeal` with the `content_id` and their
`creator_reasoning`. The system updates that content's status to `under_review`, writes
a new audit entry linking the appeal to the original decision (original scores preserved),
and returns a confirmation. No automated re-classification — a human reviewer would read
the appeal queue (`status = under_review` entries) with the original text, scores, and
the creator's reasoning side by side.

### Diagram

```
SUBMISSION FLOW
===============

  creator
     │  POST /submit  { text, creator_id }
     ▼
┌─────────────┐   raw text    ┌──────────────────────┐
│  /submit    │──────────────▶│ Signal 1: Groq LLM   │── llm_score (0–1) ─┐
│  (Flask)    │──────────────▶│ Signal 2: Stylometry │── style_score (0–1)┤
│  + rate     │   raw text    └──────────────────────┘                    │
│    limit    │                                                           ▼
│             │                                          ┌────────────────────────────┐
│             │◀── attribution, confidence, ─────────────│ Confidence Scorer          │
│             │    signals, label                        │ combine → confidence (0–1) │
└─────┬───────┘                                          │ → bucket (ai/unc/human)    │
      │                                                  └───────────┬────────────────┘
      │                                                     combined │ score + bucket
      │                                                              ▼
      │                                                  ┌────────────────────────────┐
      │                                                  │ Label Generator            │
      │                                                  │ bucket+conf → label text   │
      │                                                  └───────────┬────────────────┘
      │        content_id, creator_id, timestamp,                    │ label text
      │        attribution, confidence, llm_score,                   ▼
      │        style_score, status=classified            ┌────────────────────────────┐
      └─────────────────────────────────────────────────▶│ Audit Log (SQLite)         │
                                                          └────────────────────────────┘
      response { content_id, attribution, confidence, signals, label } ──▶ creator


APPEAL FLOW
===========

  creator
     │  POST /appeal  { content_id, creator_reasoning }
     ▼
┌─────────────┐   content_id     ┌────────────────────────────┐
│  /appeal    │─────────────────▶│ Content store: set          │
│  (Flask)    │                  │ status = under_review       │
│             │                  └───────────┬─────────────────┘
│             │   appeal + original decision │
│             │                              ▼
│             │                  ┌────────────────────────────┐
│             │─────────────────▶│ Audit Log (SQLite):        │
│             │                  │ new entry, links original  │
│             │                  │ scores + appeal_reasoning  │
│             │◀─────────────────└────────────────────────────┘
└─────┬───────┘   confirmation
      │
      └─▶ response { content_id, status: under_review, message } ──▶ creator


  GET /log ──▶ Audit Log ──▶ recent structured entries (JSON)
```

---

## Detection Signals (Milestone 1 decision)

We use **two genuinely distinct signals** — one semantic, one structural — so the pair is
more informative than either alone.

### Signal 1 — LLM classifier (Groq `llama-3.3-70b-versatile`)

- **Measures:** holistic semantic and stylistic coherence — whether the text *reads* like
  it was written by a person. Captures tone, idiom, argumentative flow, the "feel" of the
  prose.
- **Why it differs human vs. AI:** AI text tends to be smoothly coherent, evenly hedged,
  and topic-complete in a way human writing often isn't. An LLM is good at recognizing that
  gestalt.
- **Output:** a probability in `[0,1]` (1 = confidently AI) plus a short rationale string.
- **Blind spot:** it can be confidently wrong and is non-deterministic. Formal, polished
  human writing (academic prose, non-native-English writers who learned in a formal
  register) can read as "AI." It is also vulnerable to lightly-edited AI text that has had
  its tells smoothed out. It gives no *statistical* evidence — just a vibe with a number.

### Signal 2 — Stylometric heuristics (pure Python)

- **Measures:** structural regularity of the text using measurable statistics:
  - **Sentence-length variance / burstiness** — humans vary sentence length a lot; AI is
    more uniform.
  - **Type-token ratio (vocabulary diversity)** — repetition vs. lexical variety.
  - **Punctuation density** — humans use dashes, ellipses, parentheses, lowercase-i,
    irregular punctuation; AI is more even.
- **Why it differs human vs. AI:** AI output is statistically *smoother* — low variance,
  regular rhythm. Human writing is bursty and irregular.
- **Output:** a single score in `[0,1]` (1 = looks AI/uniform), combining the sub-metrics.
- **Blind spot:** it's content-blind and easily fooled by length and genre. Very short text
  has unstable statistics. A human poem with heavy repetition and simple vocabulary can look
  "uniform" (false AI). Casual, sloppy AI output prompted to "write like a tired college
  student" can look bursty (false human). It knows nothing about meaning.

**Why the pairing works:** Signal 1 is semantic and holistic; Signal 2 is structural and
statistical. Their failure modes barely overlap — the polished-human case that fools the LLM
is exactly where stylometry can add a dissenting vote, and vice versa. Disagreement between
them is itself a signal of genuine uncertainty.

---

## False-Positive Scenario (traced through the system)

**The worst error on a writing platform is labeling a real human's work as AI.** Scenario: a
non-native-English creator submits a heartfelt but formally-worded personal essay.

- Signal 1 (LLM) sees formal, even prose → returns a high AI score (~0.8). **Wrong.**
- Signal 2 (stylometry) sees moderate variance and normal vocabulary → returns a mid score
  (~0.45). **Partly dissenting.**
- The scorer sees **disagreement** and the false-positive bias kicks in: it does *not* let a
  single confident signal push the verdict to high-confidence AI. The combined confidence
  lands in the **uncertain** band, not "confidently AI."
- The **label** therefore reads as *uncertain* — hedged, non-accusatory language — rather
  than branding the work as AI.
- The creator can still disagree. They hit `POST /appeal` with their reasoning ("I wrote this
  myself; I'm a non-native speaker and write formally"). Status flips to `under_review`, the
  appeal is logged next to the original scores, and a human reviewer can take it from there.

This asymmetry — never let one confident signal alone brand a human, and always leave an
appeal path — drives the scoring and label design decisions in Milestone 2.

---

## API Surface (contract)

| Method | Path        | Accepts                                   | Returns |
|--------|-------------|-------------------------------------------|---------|
| POST   | `/submit`   | `{ "text": str, "creator_id": str }`      | `{ content_id, attribution, confidence, signals: {llm_score, style_score}, label }` |
| POST   | `/appeal`   | `{ "content_id": str, "creator_reasoning": str }` | `{ content_id, status: "under_review", message }` |
| GET    | `/log`      | — (optional `?limit=`)                    | `{ entries: [ …structured audit entries… ] }` |
| GET    | `/health`   | —                                         | `{ status: "ok" }` (liveness) |

- `/submit` is **rate limited** (limits chosen and justified in Milestone 5 / README).
- `content_id` is a UUID generated at submission; it is the join key between `/submit`,
  `/appeal`, and the audit log.
- Errors return a JSON `{ "error": ... }` body with an appropriate HTTP status
  (400 bad input, 404 unknown content_id, 429 rate limited).

---

## Spec: The Five Questions

### 1. Detection signals — what, output shape, how combined

| Signal | Type | Measures | Output |
|--------|------|----------|--------|
| **Signal 1 — Groq LLM** | Semantic | Holistic "does this read human?" — tone, idiom, flow | `llm_score ∈ [0,1]` (1 = confidently AI) + rationale string |
| **Signal 2 — Stylometry** | Structural | Sentence-length variance (burstiness), type-token ratio, punctuation density | `style_score ∈ [0,1]` (1 = uniform/AI-like) |

**Stylometry sub-metrics → `style_score`.** Each sub-metric is normalized to a
`[0,1]` "AI-likeness" partial, then averaged:

- **Burstiness** — coefficient of variation of sentence lengths. Low variation ⇒
  AI-like. `partial = 1 − clamp(cv / 0.6, 0, 1)` (cv ≥ 0.6 is very human).
- **Type-token ratio (TTR)** — unique words / total words over a capped window. Both
  extremes are suspect, but for our purposes *moderate-high, very even* diversity reads
  AI; we map TTR to AI-likeness with a gentle curve centered on human norms (~0.4–0.7).
- **Punctuation density** — fraction of "human-signal" punctuation (em/en dashes,
  ellipses, parentheses, semicolons, lowercase standalone "i"). More of it ⇒ more human.

**Combining the two signals into one confidence.** Confidence = **P(AI-generated)** in
`[0,1]` (see Q2 for why a single P(AI) number encodes uncertainty at its midpoint).

```
base         = 0.6 * llm_score + 0.4 * style_score      # LLM weighted higher
disagreement = |llm_score - style_score|
confidence   = 0.5 + (base - 0.5) * (1 - 0.5 * disagreement)
```

The **disagreement shrinkage** pulls the score toward 0.5 (uncertain) when the two
signals conflict. This is the mechanism that enforces the false-positive bias: a single
confident signal can never, on its own, push a verdict to high-confidence AI while the
other signal dissents. LLM is weighted higher (0.6) because it captures meaning; but it
is exactly the signal that false-positives on formal human prose, so stylometry's dissent
is allowed to blunt it.

### 2. Uncertainty representation

Confidence is a single **P(AI)** in `[0,1]`. That choice makes uncertainty *intrinsic*:

- **P(AI) ≈ 0.5** is maximum uncertainty — the system is saying "I genuinely can't tell."
- **P(AI) ≈ 0.95** is a confident AI verdict; **P(AI) ≈ 0.05** is a confident human verdict.
- A **0.51** result lands in the *uncertain* band and shows the hedged label; a **0.95**
  shows the confident-AI label. They are meaningfully different outputs — no binary flip.

**Bands (asymmetric, biased against false positives):**

| P(AI) range | Attribution | Label variant |
|-------------|-------------|---------------|
| `≥ 0.70`    | `likely_ai` | high-confidence AI |
| `0.35 – 0.70` (exclusive) | `uncertain` | uncertain |
| `≤ 0.35`    | `likely_human` | high-confidence human |

The AI threshold (0.70) sits well above the human threshold's mirror image — we demand
**strong** evidence before telling a reader something was AI-made, and give the benefit of
the doubt to a wider human/uncertain zone. "Calibration" here is behavioral, not
probabilistic: we validate (Milestone 4) that clearly-AI, clearly-human, and borderline
inputs land in visibly different bands, and that the disagreement-shrinkage keeps
single-signal false positives out of the AI band.

### 3. Transparency label design — the three variants (exact text)

Labels are plain-language, non-technical, and non-accusatory. They always state that the
verdict is an automated estimate and (for the AI verdict) point to the appeal path. The
displayed percentage is framed in the direction of the verdict so a reader isn't asked to
mentally invert a probability.

**High-confidence AI** (P(AI) ≥ 0.70):
```
🤖 Likely AI-generated
Our automated analysis suggests this content was most likely created with the help of
generative AI (estimated {P_ai}% likelihood). This is an automated estimate, not a
certainty. If you're the creator and believe this is wrong, you can appeal this result.
```

**High-confidence human** (P(AI) ≤ 0.35):
```
✍️ Likely human-written
Our automated analysis found no strong signs of AI generation in this content
(estimated {P_human}% likelihood it's human-written). This is an automated estimate and
not a guarantee of authorship.
```

**Uncertain** (0.35 < P(AI) < 0.70):
```
❓ Not enough signal to tell
Our automated analysis couldn't confidently determine whether a person wrote this or it
was generated with AI. Rather than risk mislabeling the creator's work, we're showing
this note instead of a verdict.
```

`{P_ai}` = `round(confidence*100)`; `{P_human}` = `round((1-confidence)*100)`.

### 4. Appeals workflow

- **Who can appeal:** the creator of a submitted piece (identified by `content_id`; in a
  real deployment this would be gated by auth tying `creator_id` to the caller — out of
  scope here).
- **What they provide:** `content_id` and free-text `creator_reasoning`.
- **What the system does on receipt:**
  1. Look up the content; 404 if `content_id` is unknown.
  2. Set that content's `status` → `under_review`.
  3. Write a new **audit-log entry** that links the appeal to the original decision —
     preserving the original `attribution`, `confidence`, `llm_score`, `style_score`, and
     recording `appeal_reasoning` and an appeal timestamp.
  4. Return `{ content_id, status: "under_review", message }`.
- **No automated re-classification.**
- **What a human reviewer sees in the queue:** all entries with `status = under_review`,
  each showing the original text, both signal scores, the combined confidence/verdict, and
  the creator's reasoning — enough to make a manual call.

### 5. Anticipated edge cases (specific)

1. **Repetition-heavy, simple-vocabulary poetry (e.g. a villanelle or a nursery-style
   rhyme).** Deliberate repetition tanks the type-token ratio and flattens sentence-length
   variance, so **stylometry reads it as "uniform" = AI** even though it's human art. Because
   the LLM signal often *disagrees* (it recognizes the poetic voice), disagreement-shrinkage
   should pull the result into the *uncertain* band rather than a false AI verdict — but this
   is a genuine weak spot and a likely source of appeals.

2. **Very short submissions (a haiku, a one-sentence caption, < ~40 words).** Stylometric
   statistics are unstable on tiny samples — variance is dominated by one or two sentences and
   TTR trends toward 1.0 mechanically. Both signals are unreliable, so short text should
   default toward *uncertain*. (Implementation note: treat very short input as low-confidence
   / uncertain rather than trusting the raw stats.)

3. **Non-native-English formal writing** — covered in the [false-positive scenario](#false-positive-scenario-traced-through-the-system):
   the LLM false-positives on even, formal prose; stylometry's dissent keeps it out of the AI band.

4. **Lightly-edited AI output** — an AI draft with its tells smoothed by a human editor. Both
   signals land mid-range and the system *correctly* reports *uncertain*, which is the honest
   answer; but it means the system will miss some real AI content (an accepted false-negative,
   consistent with our false-positive-averse posture).

---

## AI Tool Plan

For each implementation milestone: which spec sections feed the AI tool, what to ask for,
and how to verify the output against this spec.

### M3 — submission endpoint + first signal (Groq)
- **Provide:** [Detection Signals](#1-detection-signals--what-output-shape-how-combined) (Signal 1 row + output shape),
  the [Architecture diagram](#diagram), and the [API Surface](#api-surface-contract).
- **Ask for:** (1) Flask app skeleton with `POST /submit`, `GET /log`, `GET /health` stubs;
  (2) the `llm_signal(text) -> {score, rationale}` function calling Groq
  `llama-3.3-70b-versatile`; (3) SQLite audit-log helper.
- **Verify:** call `llm_signal` directly on 2–3 inputs and inspect that it returns a float in
  `[0,1]` + rationale (not free prose). Confirm `/submit` returns `content_id`, `attribution`,
  placeholder `confidence`, placeholder `label`, and that each call writes a structured log row.

### M4 — second signal + confidence scoring
- **Provide:** full [Detection Signals](#1-detection-signals--what-output-shape-how-combined)
  (stylometry sub-metrics + combination formula), [Uncertainty representation](#2-uncertainty-representation)
  (bands + thresholds), and the diagram.
- **Ask for:** (1) `stylometry_signal(text) -> score`; (2) `score_confidence(llm, style)`
  implementing the exact `base / disagreement / shrinkage` formula and the three bands.
- **Verify:** the generated scorer matches the formula and thresholds *exactly* (AI tools
  drift here). Run the four calibration inputs from the spec (clear-AI, clear-human, two
  borderline) and confirm they land in visibly different bands. Print `llm_score` and
  `style_score` separately when a result surprises us.

### M5 — production layer (labels, appeals, rate limit, full log)
- **Provide:** [Label variants](#3-transparency-label-design--the-three-variants-exact-text)
  (exact text + thresholds), [Appeals workflow](#4-appeals-workflow), and the diagram.
- **Ask for:** (1) `generate_label(confidence) -> text` mapping bands → the exact three
  variants; (2) `POST /appeal` endpoint; (3) Flask-Limiter config on `/submit`.
- **Verify:** ask the label function for all three variants and diff against the text above.
  Confirm `/appeal` sets `status=under_review` and logs the appeal linked to the original
  decision. Confirm rate limiting returns 429 past the limit.

---

## Stretch Features

*(Added after the required build, before implementing each stretch — per the assignment.)*

### Stretch 1 — Ensemble detection (3rd signal + documented weighting)

Adds a third, genuinely distinct signal so the pipeline is a 3-signal ensemble with a
documented weighted-vote scheme.

**Signal 3 — AI-phrase lexicon detector (pure Python) · *lexical*.** Counts occurrences of
phrases and words empirically over-represented in LLM output ("it is important to note",
"furthermore", "delve", "tapestry", "plays a crucial role", "paradigm shift",
"navigate the complexities", "seamless", "multifaceted", …). Output `phrase_score ∈ [0,1]`
(1 = dense AI boilerplate), computed as matched-phrase density per 100 words, capped.

- **Measures:** a *lexical* property — the presence of characteristic AI boilerplate
  vocabulary. Distinct from Signal 1 (semantic judgment) and Signal 2 (structural
  statistics): a text can be structurally bursty yet still stuffed with AI clichés, or
  perfectly uniform with none.
- **Why it differs human vs. AI:** instruction-tuned models lean heavily on a recognizable
  set of connective and "essayistic" phrases; casual human writing rarely stacks them.
- **Blind spot:** noisy on formal/academic *human* writing, which legitimately uses some of
  the same connectives — hence it gets the **lowest weight** and the disagreement penalty
  still applies.

**New weighting / voting scheme (3-signal ensemble):**

```
base         = 0.5·llm + 0.3·style + 0.2·phrase        # LLM highest, phrase lowest
disagreement = max(llm, style, phrase) − min(llm, style, phrase)   # spread of the 3
confidence   = 0.5 + (base − 0.5) · (1 − 0.5·disagreement)
```

This generalizes the 2-signal design: it's a **weighted vote** (LLM 0.5, stylometry 0.3,
phrase 0.2) with a **disagreement shrinkage** driven by the spread across all three signals.
The false-positive protection is preserved and strengthened — three signals must broadly
*agree* before content is branded AI; any lone dissenter widens the spread and pulls the
verdict toward *uncertain*. Bands (0.70 / 0.35) are unchanged. The phrase signal gets the
smallest weight because it is the noisiest on formal human prose.

### Stretch 2 — Analytics dashboard

A read-only view over the audit log surfacing detection patterns and appeal behavior.

- **`GET /analytics`** — JSON metrics computed from the audit log:
  - **Band distribution** — counts and % of `likely_ai` / `uncertain` / `likely_human`
    across all classifications (detection patterns).
  - **Appeal rate** — appeals ÷ classifications.
  - **Average signal disagreement** — mean spread across signals (my extra metric; a proxy
    for how often the signals conflict, i.e. how much genuine uncertainty the corpus carries).
  - Plus totals and per-signal mean scores.
- **`GET /dashboard`** — a simple self-contained HTML page rendering those metrics (bars +
  numbers) so a non-technical reviewer can read them at a glance.

Both are pure reads over existing data — no new storage. In a real system these would sit
behind auth; here they're for visibility and grading.

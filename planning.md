# Provenance Guard — Planning

Backend system that a creative-sharing platform can plug in to classify submitted
text as human-written or AI-generated, score confidence in that classification,
surface a plain-language transparency label, and let creators appeal.

> **Milestone status:** This document is written before implementation. Milestone 1
> content (architecture narrative, signal choices, false-positive trace, API surface,
> diagram) is below. Milestone 2 expands it with the five spec questions, calibration
> details, label variants, and the AI Tool Plan.

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

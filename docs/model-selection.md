# Model Selection — Recoupe (Build Phase)

## Migration note (read first)

The original plan (below, kept for history in Section 3) assigned a different OpenCode Go catalog model per phase. That plan is **superseded**: during Phase 3, OpenCode Go's model gateway hit an API usage limit, and all real LLM calls were migrated directly to **Groq**. This migration is **total and permanent**, not a stopgap — do not revert to OpenCode Go's per-phase model catalog for any future phase, including Phase 5 and beyond, unless a new decision explicitly supersedes this document again.

## Current setup (binding)

Two Groq models cover every LLM call in the codebase, selected automatically by `backend/app/core/llm_client.py`'s `running_under_pytest()` switch:

| Context | Model | Used for |
|---|---|---|
| Real runs (not under pytest) | `openai/gpt-oss-120b` | Actual agent reasoning — orchestrator classification, ambiguous-case classify/diagnose/scoring calls, live demo runs |
| Test mode (under pytest) | `openai/gpt-oss-20b` | Same call sites, swapped to the smaller/faster model so the test suite runs quickly without burning quota on the larger model |

`TEST_MODEL_NAME=openai/gpt-oss-20b` and the real-run model name live in `backend/.env` / `backend/app/core/config.py`. There is no third model and no OpenCode Go fallback path — if Groq is unreachable, that's a real outage to fix, not a signal to switch providers again mid-build.

## Where LLM calls actually happen (hybrid rule-first pattern)

Per `architecture.md`'s non-negotiable design principles, rules handle every clean signal directly; the LLM is invoked **only** for genuinely ambiguous cases. As of Phase 4, the LLM-touching call sites are:

| Sub-agent / component | Stage | When the LLM fires |
|---|---|---|
| Orchestrator | Classify/route | Event-type classification (Phase 1 used a simple rule on `event_type`; confirm current state before assuming this is still rule-only) |
| Payment Agent | Classify | Only when a decline code repeats and history makes the cause genuinely ambiguous (Phase 2) |
| Cart Agent | Diagnose | Always — Cart has no clean rule signal for likely abandonment reason (Phase 3) |
| Renewal Agent | Classify | Rules classify card-expiry vs. funds vs. processor error — confirm whether any ambiguous case escalates to LLM (Phase 3) |
| Invoice Agent | Call-priority scoring (`call_priority.py`) | Only when rules can't confidently resolve the score from days-overdue + reliability + broken-promise history (Phase 4) |

Everything else — Enrich (always plain DB lookup), Risk & Compliance Gate (always deterministic, never LLM), Execute & Log — stays rule-based per `architecture.md` Section 10, unaffected by this migration.

## Pre-flight sanity check (run at the start of every new session/phase)

Credentials, quota, or config can drift between sessions. Before writing any code that depends on the LLM:

```
Send a real call to both openai/gpt-oss-120b and openai/gpt-oss-20b
asking each to "reply with exactly: PONG". Confirm both respond
correctly before proceeding.
```

If either fails: check `backend/.env` for `GROQ_API_KEY` (or equivalent) and current Groq quota/usage — do not silently fall back to a different provider or model without flagging it first.

## Docs/polish work

No change from the original plan's reasoning — low-complexity, high-volume edits (README, comments, formatting) don't need a reasoning-heavy model. Use `openai/gpt-oss-20b` (the same fast model already configured for test mode) rather than reintroducing a third model just for this.

---

## 1. Superseded: original OpenCode Go tiering rationale (kept for history only)

- **Foundation/scaffolding work** → fast, reliable mid-tier model. Speed matters more than peak reasoning; mistakes here are cheap to catch since nothing depends on it yet.
- **Template-defining work** (the first sub-agent built in full) → strongest available coding/reasoning model. Bugs or bad patterns here get copy-pasted into three more sub-agents later, so it's worth paying for quality upfront.
- **Pattern-replication work** → solid mid-tier model. You're reapplying a proven shape, not inventing one.
- **Integration/debugging work** → a model known for strong long-context and agentic coding, since cross-component bugs require holding more context at once.
- **Docs/polish work** → fastest, cheapest model. Low complexity, high volume of small edits.

## 2. Superseded: original phase-by-phase table (kept for history only — do not use)

| Phase | Task | Originally planned (OpenCode Go) | Actual (Groq, current) |
|---|---|---|---|
| 1 | Repo scaffolding, DB schema, synthetic generator, orchestrator skeleton | GLM-5.3 | openai/gpt-oss-120b |
| 2 | Payment Agent — full six-stage pipeline (the template) | GPT 5.6 Luna | openai/gpt-oss-120b |
| 3 | Cart Agent, Renewal Agent (replicate Phase 2's pattern) | DeepSeek V4 Pro | openai/gpt-oss-120b (migration happened mid-phase) |
| 4 | Invoice Agent + full system integration testing | Kimi K3 | openai/gpt-oss-120b |
| 5a | Real Razorpay test-mode API adapter | DeepSeek V4 Pro | openai/gpt-oss-120b |
| 5b | Dashboard (React/Vite frontend) | GLM-5.3 | openai/gpt-oss-120b (or no LLM call needed at all — frontend scaffolding may not require live reasoning calls; confirm when Phase 5b starts) |
| 6 | Polish, README, docs, demo prep | Qwen3.8 Flash | openai/gpt-oss-20b |
| 6 (if hardening bugs appear) | Fallback for last-minute debugging | DeepSeek V4 Pro | openai/gpt-oss-120b |

## 3. Quick reference (current, use this)

```
All real runs        → openai/gpt-oss-120b   (Groq)
All test-mode runs    → openai/gpt-oss-20b    (Groq)
Provider              → Groq (direct), not OpenCode Go's model gateway
Switch logic           → backend/app/core/llm_client.py: running_under_pytest()
Sanity check          → "reply with exactly: PONG" against both models,
                          at the start of every new session
```

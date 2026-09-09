# Architecture — Revenue Recovery Agent

This is the single source of truth for system structure. Every phase's PRD refers back to this file — if a phase's implementation needs to deviate from what's written here, update this file first, then build.

## 1. Overview

A multi-agent system that watches four kinds of "leaking revenue" events (failed payments, abandoned checkouts, failed subscription renewals, overdue B2B invoices), diagnoses why each one happened, and executes a bounded recovery action — with every action gated by hard-coded compliance rules and logged to an audit trail.

One orchestrator classifies and routes each event. Four specialist sub-agents (Payment, Cart, Renewal, Invoice) each run the same six-stage internal pipeline, tuned to their domain. All four share one customer database, one risk/compliance gate, and one audit log.

## 2. System Architecture

```mermaid
flowchart TD
    A[Ingest Layer<br/>webhooks, polling, batch jobs] --> B[Orchestrator Agent<br/>classifies event, routes signal]
    B --> C[Payment Agent]
    B --> D[Cart Agent]
    B --> E[Renewal Agent]
    B --> F[Invoice Agent]
    C --> G[Shared Services]
    D --> G
    E --> G
    F --> G
    G --> G1[(Customer DB<br/>history & contacts)]
    G --> G2[Risk & Compliance Gate<br/>bounds & cooldowns]
    G --> G3[(Audit Log<br/>every action recorded)]
    G --> G4[Comms Layer<br/>email, SMS, WhatsApp, voice]
```

## 3. Components

| Component | Type | Responsibility |
|---|---|---|
| Ingest Layer | Deterministic code | Accepts events from synthetic generator or real Razorpay test-mode API, normalizes into the internal Event schema (Section 6) |
| Orchestrator Agent | LLM (DeepSeek V4 Flash) | Classifies event type, routes to correct sub-agent |
| Payment / Cart / Renewal / Invoice Agents | Mixed (rules + LLM) | Domain-specific diagnosis and recovery decision — see Section 5 |
| Customer DB | Deterministic code (Postgres/Supabase) | Stores contact history, retry counts, past failures — read by Enrich step, read/write by Risk Gate |
| Risk & Compliance Gate | Deterministic code — **never an LLM call** | Enforces max retries, cooldown windows, contact frequency caps |
| Audit Log | Deterministic code (Postgres/Supabase) | Records every action taken, with reasoning, for the demo dashboard and judge review |
| Comms Layer | Deterministic code (Twilio + SMTP) | Shared functions `send_email()`, `send_sms()`, `send_whatsapp()`, `place_voice_call()` — every agent calls into this, none implement their own messaging |

**⚠️ Provider status (as of Phase 2):** business-initiated WhatsApp delivery is currently **blocked** on the Twilio account in use — the `send_whatsapp()` function is fully implemented and tested, but live delivery does not go through. Every agent's "WhatsApp" channel currently falls back to `send_sms()` in practice. Do not spend Phase 3 time re-investigating this — build Cart and Renewal against SMS as the working channel, and treat WhatsApp delivery as a stretch fix only if time remains in Phase 5/6. Update this note the moment the block clears.

**⚠️ Production delivery status (as of the Railway deploy):** all four Comms Layer functions (`send_email()`, `send_sms()`, `send_whatsapp()`, `place_voice_call()`) work locally, but on the deployed Railway backend, real sends fail with **network-level** errors — a Twilio proxy connection refused with `403 Forbidden`, and SMTP failing DNS resolution before it can even attempt to connect. This is not an auth or code problem; the working hypothesis is a Railway platform-level restriction on outbound proxy/SMTP traffic. Unresolved — full detail and the reproduced error text in `docs/demo-notes.md`'s Deployment section. Every send failure is now at least fully diagnosable (the real error is logged and captured in the audit trail's `reasoning` field), but the underlying network restriction itself is still open.

## 4. Sub-Agent Internal Pipeline (generic pattern)

Every sub-agent follows this exact six-stage shape. This consistency is a deliberate design choice — don't let any sub-agent skip a stage or reorder it.

```mermaid
flowchart TD
    S1[Fetch Event] --> S2[Enrich Context]
    S2 --> S3[Classify / Diagnose]
    S3 --> S4[Risk & Compliance Gate]
    S4 --> S5[Choose Action]
    S5 --> S6[Execute & Log]
```

- **Fetch Event** — deterministic, reads the normalized event off the queue/table
- **Enrich Context** — deterministic, plain DB lookup joining customer history onto the event
- **Classify / Diagnose** — hybrid: rules handle clean signals (decline codes, days overdue), LLM only invoked for genuinely ambiguous cases
- **Risk & Compliance Gate** — deterministic, hard-coded if/else logic, provably bounded, no LLM ever
- **Choose Action** — LLM or rules depending on sub-agent (see Section 5)
- **Execute & Log** — deterministic, performs the mock action and writes to Audit Log

## 5. Per-Sub-Agent Domain Specifics

| Sub-agent | Enrich pulls | Diagnose logic | Gate checks | Decide options | Channels used |
|---|---|---|---|---|---|
| Payment | Past retries, method reliability | Rules map decline code → cause; LLM adds nuance if code is ambiguous | Max retries, cooldown window | Retry now / retry alt route / send payment link | SMS, WhatsApp* |
| Cart | Purchase history, price sensitivity | LLM scores likely reason (price, timeout, distraction) — no clean signal exists | Contact frequency cap | Nudge (+ discount if price-related) → collect feedback → optional voice call if unresolved | WhatsApp* (with discount), Email, feedback quick-reply, Hinglish voice call |
| Renewal | Tenure, past failures, LTV | Rules classify card-expiry vs funds vs processor error | Grace period rules, retry limit, separate call-frequency cap | Silent retry / prompt card update / grace period, multi-channel notify, escalate to voice call if high-value or unresponsive | SMS + Email + WhatsApp* simultaneously, Hinglish voice call |
| Invoice | Account reliability, tier, past promises | Rules bucket by days overdue (30/60/90) | Contact rules, escalation limits | Reminder / firm follow-up / escalate, with a call-priority score deciding who gets called | SMS, WhatsApp*, Email, Hinglish voice call (priority-scored) |

*WhatsApp is currently falling back to SMS — see the provider status note under Section 3, Comms Layer.

## 6. Internal Event Schema

Every external source (synthetic generator, real Razorpay API) must be converted into this shape by an adapter before it enters the pipeline. Nothing downstream should ever see a raw external payload.

```
Event {
  event_id: str
  event_type: enum[payment_failed, cart_abandoned, renewal_failed, invoice_overdue]
  customer_id: str
  amount: float
  currency: str
  reason_code: str | null      # raw decline/status code if available
  timestamp: datetime
  metadata: dict                # event-type-specific extra fields
}
```

## 7. Data Flow (end to end)

1. Event enters via Ingest Layer (synthetic generator or real API adapter) → normalized to Event schema
2. Orchestrator classifies `event_type`, routes to matching sub-agent
3. Sub-agent's Enrich step joins Customer DB history onto the event
4. Diagnose step determines root cause (rules and/or LLM)
5. Risk Gate checks bounds — if blocked, log the block and stop; if allowed, continue
6. Decide step picks the specific action
7. Execute step performs the (mocked) action and writes an Audit Log entry
8. Dashboard reads Audit Log + computes batch metrics (recovery rate, ₹ recovered)

## 8. Tech Stack Recap

| Layer | Choice | Why |
|---|---|---|
| Backend | FastAPI (Python) | Already used in Intra, async-friendly for ingestion |
| Backend hosting | Railway | Deployed from `backend/` as the service Root Directory — see `docs/demo-notes.md`'s Deployment section for the driver/path issues this surfaced |
| Frontend hosting | Vercel | Deployed from `frontend/`; `VITE_API_BASE_URL` points it at the Railway backend URL |
| Orchestration | LangGraph | Same primitive as Intra's six-node graph, proven pattern |
| LLM inference | Groq (`https://api.groq.com/openai/v1/chat/completions`, model `openai/gpt-oss-120b` via `llm_client.py`) | OpenCode Go gateway hit its usage limit mid-Phase 3; switched to Groq with a confirmed-live GPT OSS 120B model (131k context, tools + structured output support). Endpoint and model ID verified against Groq's live `/v1/models` API before wiring — not assumed from memory. |
| Database (production) | Supabase (Postgres) | Real hosted Postgres the deployed Railway backend connects to, via Supabase's transaction pooler — distinct from the local Postgres instance used in dev, which is not Supabase |
| Frontend | React + Vite + TypeScript | Already used in Intra, dark terminal aesthetic reusable |
| Synthetic data | Python `faker` | Realistic fake data with minimal code |

## 9. Phase-to-Architecture Mapping

| Phase | Builds |
|---|---|
| 1 | Ingest Layer skeleton, Event schema, Customer DB tables, Orchestrator routing skeleton (stubs only) |
| 2 | Payment Agent — full six-stage pipeline (the template for Phases 3–4); Comms Layer v1 (email, SMS, WhatsApp) built here since Payment's "send payment link" action needs it first |
| 3 | Cart Agent, Renewal Agent; Comms Layer extended with feedback collection and voice call (Hinglish TTS via Twilio Voice) |
| 4 | Invoice Agent (adds call-priority scoring), full system integration test (mixed batch through Orchestrator) |
| 5 | Real Razorpay test-mode adapter (Payment branch only), Dashboard |
| 6 | Hardening, docs, demo recording |

## 10. Non-Negotiable Design Principles

- The Risk & Compliance Gate is **always** deterministic code. Never route a gate decision through an LLM call.
- The Enrich step is **always** a plain DB lookup. Never an LLM call.
- Diagnosis is a **hybrid**: rules first wherever a clean signal exists (decline codes, days-overdue buckets); LLM only for genuinely ambiguous cases.
- There is exactly **one** internal Event schema. External sources are translated into it via adapters — nothing downstream ever branches on "which source did this come from."
- The Comms Layer is the only place that talks to Twilio/SMTP. No sub-agent implements its own email/SMS/WhatsApp/voice sending — they all call the shared `send_email()` / `send_sms()` / `send_whatsapp()` / `place_voice_call()` functions.

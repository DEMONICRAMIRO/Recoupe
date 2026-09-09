# Project Status — Recoupe

This file didn't exist before this update — it's created here for the first
time to give a single, current entry point into the project's state. If a
future session is told to "read `docs/PROJECT-STATUS.md` for context first,"
this is that file.

## Current situation

**Deployment is complete.** All 5 phases are built and the app is live:

- **Backend (Railway):** https://recoupe-production.up.railway.app
- **Frontend (Vercel):** https://recoupe-umber.vercel.app
- **Production database:** Supabase (Postgres), connected via its
  transaction pooler — distinct from the local Postgres used in dev

Getting from "tests pass locally" to "live on the internet" surfaced a real
debugging saga — Supabase connection setup, three separate Railway
deployment problems, three separate Vercel deployment problems, and a
silent 500 on the batch-run endpoint. Full details, in order, with real
error text, are in `docs/demo-notes.md`'s **Deployment (Phase 5)** section
— read that before touching deploy config again.

**One known limitation is still open:** real SMS/email/voice delivery
fails in production with network-level errors (a Twilio proxy connection
refused with `403 Forbidden`, and SMTP failing DNS resolution). This is not
an auth or code bug — the working hypothesis is a Railway platform-level
restriction on outbound proxy/SMTP traffic. It's documented, not silently
ignored, in both `docs/demo-notes.md` and `docs/architecture.md` (Section
3, Comms Layer). Every delivery failure is now fully diagnosable — the real
error is captured in the audit trail's `reasoning` field and logged — but
the underlying network restriction itself is unresolved.

## Doc map

| File | What it covers |
|---|---|
| `docs/architecture.md` | Single source of truth for system structure — components, sub-agent pipeline shape, event schema, tech stack (Section 8), non-negotiable design principles. Update this first if a phase needs to deviate from it. |
| `docs/demo-notes.md` | Running log of everything that broke and how it got fixed, phase by phase, ending with the Deployment (Phase 5) section — real error messages throughout, not summaries. |
| `docs/project-structure.md` | The folder layout every phase's PRD assumes. |
| `docs/prd-phase1.md` – `docs/prd-phase5.md` | Per-phase requirements documents, each pointing back to the relevant `architecture.md` sections. |
| `docs/fix-log-llm-provider.md` | Standalone deep-dive on the LLM provider migration (OpenCode Go → Groq) — the Groq/Grok naming collision, wrong base URLs, model-ID format issues. |
| `docs/model-selection.md` | Which model was assigned to which phase, and the permanent migration note superseding the original per-phase OpenCode Go catalog plan. |
| `docs/HANDOFF-antigravity.md` | A point-in-time handoff document from a coding-tool switch mid-Phase-3 — the LLM provider switch (now resolved, see `fix-log-llm-provider.md`) and the Phase 3 voice-verification investigation (now resolved, see `demo-notes.md` Phase 3). Historical; not current status. |
| `docs/recoupe-dashboard-mockup.html` | Design/information-architecture reference for the dashboard frontend — layout and page set only, its hardcoded data arrays were never meant to ship. |
| `docs/PROJECT-STATUS.md` | This file. |

## Phase status

| Phase | Scope | Status |
|---|---|---|
| 1 | Foundation — FastAPI skeleton, Event schema, DB tables, synthetic generator, LangGraph orchestrator | Complete |
| 2 | Payment Agent + Comms Layer v1 | Complete |
| 3 | Cart Agent, Renewal Agent, Comms Layer v2 (voice + feedback) | Complete — Twilio voice audio-level verification stayed inconclusive (infrastructure confirmed, audio not; see `demo-notes.md`) |
| 4 | Invoice Agent, full system integration test | Complete |
| 5 | Real Razorpay adapter, Dashboard, Live Run, **deployment** | **Deployed** — live at the URLs above. Open item: production SMS/email/voice delivery fails with network-level errors (see Current situation above) |

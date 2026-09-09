# Project Folder Structure

This is the structure to follow from Phase 1 onward. Every phase's PRD assumes this layout — don't restructure mid-build without updating `architecture.md` first.

**Deployment note:** Railway's service Root Directory is set to `backend/` — the deployed backend container only ever contains what's inside that folder. Anything the *deployed* app needs at runtime must live inside `backend/`, not beside it — `scripts/` and `data/` (below) are dev-only for exactly this reason. This bit the `/events/run-batch` endpoint once already; see `docs/demo-notes.md`'s Deployment section.

```
Recoupe/
├── backend/                             # Railway deploy root
│   ├── app/
│   │   ├── main.py                    # FastAPI app entrypoint, CORS middleware, /health endpoint
│   │   ├── api/
│   │   │   └── routes/
│   │   │       ├── events.py          # /events/ingest, /events/razorpay-webhook, /events/run-batch
│   │   │       ├── metrics.py         # /metrics/summary — batch metrics for dashboard
│   │   │       ├── audit.py           # /audit/entries — audit trail, filtered + customer-scoped
│   │   │       ├── customers.py       # /customers/, /customers/{id}
│   │   │       ├── gate_rules.py      # /gate-rules/ — deterministic gate constants from settings
│   │   │       ├── comms.py           # /comms/status — honest per-channel status
│   │   │       └── live_status.py     # /live-status/ — polled by the Live Run dashboard view
│   │   ├── agents/
│   │   │   ├── orchestrator.py        # classify + route node
│   │   │   ├── payment_agent.py
│   │   │   ├── cart_agent.py
│   │   │   ├── renewal_agent.py
│   │   │   ├── invoice_agent.py
│   │   │   └── graph.py               # LangGraph StateGraph wiring it all together
│   │   ├── core/
│   │   │   ├── config.py              # env vars, settings
│   │   │   ├── llm_client.py          # Groq client wrapper
│   │   │   ├── risk_gate.py           # deterministic gate logic — no LLM calls here, ever
│   │   │   ├── call_priority.py       # Invoice Agent's call-priority scoring
│   │   │   ├── live_status.py         # in-memory live-run state store, polled by the dashboard
│   │   │   └── batch_runner.py        # in-process synthetic batch runner for /events/run-batch —
│   │   │                              #   reads app/data/synthetic/events.json (below), NOT the
│   │   │                              #   top-level data/synthetic/ used by scripts/run_batch.py
│   │   ├── data/
│   │   │   └── synthetic/
│   │   │       └── events.json        # static demo snapshot bundled INTO the deployed backend —
│   │   │                              #   committed (not gitignored), unlike data/synthetic/ below
│   │   ├── db/
│   │   │   ├── models.py              # SQLAlchemy ORM models (events, customer_history, audit_log)
│   │   │   ├── session.py             # DB session management
│   │   │   └── migrations/            # Alembic
│   │   ├── schemas/
│   │   │   ├── event.py               # internal Event Pydantic schema
│   │   │   ├── audit.py
│   │   │   └── customer.py
│   │   ├── adapters/
│   │   │   ├── synthetic_adapter.py   # translates synthetic JSON → Event schema
│   │   │   └── razorpay_adapter.py    # translates real Razorpay webhook payloads → Event schema
│   │   └── services/
│   │       └── comms/                 # shared communication layer (architecture.md §3)
│   │           ├── email.py           # SMTP sender
│   │           ├── sms.py             # Twilio SMS sender
│   │           ├── whatsapp.py        # Twilio WhatsApp sandbox sender (falls back to SMS)
│   │           ├── voice.py           # Twilio Voice + Hinglish TTS call placer
│   │           ├── feedback.py        # structured quick-reply collector (Phase 3)
│   │           └── templates/         # message templates per agent/scenario
│   ├── tests/
│   │   ├── test_phase1.py … test_phase5.py
│   │   ├── test_synthetic_personas.py
│   │   └── conftest.py
│   ├── requirements.txt
│   └── .env.example
├── data/                                # dev-only — NOT included in the Railway deploy
│   └── synthetic/
│       ├── generate.py                # synthetic event generator script (not fully deterministic
│       │                              #   across runs — event/customer IDs use uuid4, unseeded)
│       ├── generate_persona.py        # fixed persona dataset generator (test_synthetic_personas.py)
│       ├── events.json                # generated output (gitignored, regeneratable) — NOT the
│       │                              #   same file as backend/app/data/synthetic/events.json
│       └── customer_history.json      # generated output (gitignored, regeneratable)
├── frontend/                            # Vercel deploy root
│   ├── src/
│   │   ├── components/                # scaffolded, currently empty — everything lives in App.tsx
│   │   ├── pages/                     # scaffolded, currently empty — see above
│   │   ├── api/
│   │   │   └── client.ts              # all calls to backend endpoints
│   │   ├── App.tsx                    # all 10 dashboard views live here
│   │   ├── main.tsx
│   │   ├── vite-env.d.ts              # `/// <reference types="vite/client" />` — needed for
│   │   │                              #   import.meta.env to typecheck; see demo-notes.md Deployment
│   │   └── index.css
│   ├── package.json
│   └── vite.config.ts                 # local dev proxy: /api/* → http://localhost:8000/*
├── docs/
│   ├── architecture.md
│   ├── PROJECT-STATUS.md              # current situation, live URLs, doc map, phase status
│   ├── model-selection.md
│   ├── fix-log-llm-provider.md        # standalone deep-dive: OpenCode Go → Groq migration
│   ├── prd-phase1.md … prd-phase5.md
│   ├── project-structure.md           # this file
│   ├── demo-notes.md                  # "what broke and how you fixed it" log, incl. Deployment
│   ├── HANDOFF-antigravity.md         # historical coding-tool-switch handoff — resolved, not current
│   └── recoupe-dashboard-mockup.html  # design/IA reference for the frontend — layout only, no
│                                      #   real data; do not treat as a source of current behavior
├── scripts/                             # dev-only — NOT included in the Railway deploy
│   └── run_batch.py                   # local CLI: runs a full batch, prints per-agent metrics.
│                                      #   Reads data/synthetic/events.json above. Distinct from
│                                      #   the deployed /events/run-batch endpoint, which runs
│                                      #   in-process via backend/app/core/batch_runner.py instead
├── .gitignore
└── README.md
```

## Notes on a few folders

- **`data/synthetic/*.json`** — these are generated outputs, not hand-written. Gitignore the actual JSON files but keep `generate.py` committed, so the repo is reproducible without bloating with data files. This folder is dev-only: it sits outside `backend/`, so it never reaches the deployed Railway container — anything the live app needs has to be duplicated inside `backend/app/data/` instead (see `batch_runner.py`'s note above, and `docs/demo-notes.md`'s Deployment section for the bug this caused before it was bundled).
- **`app/adapters/`** — this is the swap point discussed earlier. Both synthetic and real-API sources get translated into the same `Event` schema here, so nothing downstream ever needs to know which source an event came from.
- **`app/core/risk_gate.py`** — kept as its own file, separate from the agents, to make it easy for judges (or you, at 1am) to point at one file and say "here's the deterministic safety logic, it's not an LLM call."
- **`docs/demo-notes.md`** — start this in Phase 1 and add to it as you go. The buildathon explicitly wants "what broke at 2am, and how you got out" — don't try to reconstruct this from memory on Sep 4.
- **`app/services/comms/`** — shared by all four sub-agents. Built out incrementally: email/SMS/WhatsApp in Phase 2 (Payment Agent needs it first for "send payment link"), voice + feedback collection added in Phase 3. In production, all four functions currently fail with network-level errors — see `docs/architecture.md` §3 and `docs/demo-notes.md`'s Deployment section.
- **`frontend/src/components/` and `frontend/src/pages/`** — scaffolded early but never split out; the 10 dashboard views all ended up living directly in `App.tsx`. Listed here because the folders still exist (with `.gitkeep`), not because they're in active use.

# Project Folder Structure

This is the structure to follow from Phase 1 onward. Every phase's PRD assumes this layout — don't restructure mid-build without updating `architecture.md` first.

```
Recoupe/
├── backend/
│   ├── app/
│   │   ├── main.py                    # FastAPI app entrypoint, /health endpoint
│   │   ├── api/
│   │   │   └── routes/
│   │   │       ├── events.py          # endpoint to ingest events
│   │   │       ├── metrics.py         # batch metrics for dashboard
│   │   │       └── audit.py           # audit trail read endpoints
│   │   ├── agents/
│   │   │   ├── orchestrator.py        # classify + route node
│   │   │   ├── payment_agent.py
│   │   │   ├── cart_agent.py
│   │   │   ├── renewal_agent.py
│   │   │   ├── invoice_agent.py
│   │   │   └── graph.py               # LangGraph StateGraph wiring it all together
│   │   ├── core/
│   │   │   ├── config.py              # env vars, settings
│   │   │   ├── llm_client.py          # Groq/OpenRouter client wrapper
│   │   │   └── risk_gate.py           # deterministic gate logic — no LLM calls here, ever
│   │   ├── db/
│   │   │   ├── models.py              # SQLAlchemy/ORM models
│   │   │   ├── session.py             # DB session management
│   │   │   └── migrations/
│   │   ├── schemas/
│   │   │   ├── event.py               # internal Event Pydantic schema
│   │   │   ├── audit.py
│   │   │   └── customer.py
│   │   ├── adapters/
│   │   │   ├── synthetic_adapter.py   # translates synthetic JSON → Event schema
│   │   │   └── razorpay_adapter.py    # translates real API payloads → Event schema
│   │   └── services/
│   │       └── comms/                 # shared communication layer (architecture.md §3)
│   │           ├── email.py           # SMTP sender
│   │           ├── sms.py             # Twilio SMS sender
│   │           ├── whatsapp.py        # Twilio WhatsApp sandbox sender
│   │           ├── voice.py           # Twilio Voice + Hinglish TTS call placer
│   │           └── templates/         # message templates per agent/scenario
│   ├── tests/
│   │   ├── test_phase1.py
│   │   ├── test_phase2.py
│   │   └── ...
│   ├── requirements.txt
│   └── .env.example
├── data/
│   └── synthetic/
│       ├── generate.py                # synthetic event generator script
│       ├── events.json                # generated output (gitignored, regeneratable)
│       └── customer_history.json      # generated output (gitignored, regeneratable)
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   ├── pages/
│   │   ├── api/                       # calls to backend endpoints
│   │   └── App.tsx
│   ├── package.json
│   └── vite.config.ts
├── docs/
│   ├── architecture.md
│   ├── model-selection.md
│   ├── prd-phase1.md
│   ├── prd-phase2.md                  # (create as you reach each phase)
│   ├── project-structure.md           # this file
│   └── demo-notes.md                  # "what broke and how you fixed it" log
├── scripts/
│   └── run_batch.py                   # runs a full batch through the pipeline, prints metrics
├── .gitignore
├── README.md
└── docker-compose.yml                 # optional — only if you containerize for the demo
```

## Notes on a few folders

- **`data/synthetic/*.json`** — these are generated outputs, not hand-written. Gitignore the actual JSON files but keep `generate.py` committed, so the repo is reproducible without bloating with data files.
- **`app/adapters/`** — this is the swap point discussed earlier. Both synthetic and real-API sources get translated into the same `Event` schema here, so nothing downstream ever needs to know which source an event came from.
- **`app/core/risk_gate.py`** — kept as its own file, separate from the agents, to make it easy for judges (or you, at 1am) to point at one file and say "here's the deterministic safety logic, it's not an LLM call."
- **`docs/demo-notes.md`** — start this in Phase 1 and add to it as you go. The buildathon explicitly wants "what broke at 2am, and how you got out" — don't try to reconstruct this from memory on Sep 4.
- **`app/services/comms/`** — shared by all four sub-agents. Built out incrementally: email/SMS/WhatsApp in Phase 2 (Payment Agent needs it first for "send payment link"), voice + feedback collection added in Phase 3.

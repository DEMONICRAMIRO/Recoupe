# Recoupe — Revenue Recovery Agent

Multi-agent revenue recovery system for the Razorpay Buildathon (Track 03). It watches four kinds of leaking revenue events — failed payments, abandoned carts, failed subscription renewals, overdue B2B invoices — diagnoses why each happened, and executes a bounded recovery action, with every action gated by hard-coded compliance rules and logged to an audit trail.

Architecture: `docs/architecture.md` — Folder layout: `docs/project-structure.md`

## Status: Phase 1 (Foundation) complete

Plumbing only. Synthetic data flows from the generator, through the LangGraph orchestrator's classify node, into four stub sub-agents with 100% correct routing — proven by the test suite. Real diagnosis and decision logic arrives in Phase 2+.

## Re-run instructions

All commands from the repo root, using the backend virtualenv.

Regenerate synthetic data (outputs `data/synthetic/events.json` + `customer_history.json`, gitignored):

```
backend\.venv\Scripts\python data\synthetic\generate.py
```

Run the Phase 1 test suite:

```
backend\.venv\Scripts\python -m pytest backend\tests\
```

Run a full batch through the pipeline and print per-agent counts:

```
backend\.venv\Scripts\python scripts\run_batch.py
```

Run the API server (health check at http://localhost:8000/health):

```
cd backend
.venv\Scripts\uvicorn app.main:app --reload
```

## Fresh-machine setup

1. `python -m venv backend\.venv`
2. `backend\.venv\Scripts\python -m pip install -r backend\requirements.txt`
3. `copy backend\.env.example backend\.env` and set `DATABASE_URL` to your Postgres/Supabase instance
4. `cd backend && .venv\Scripts\alembic upgrade head`

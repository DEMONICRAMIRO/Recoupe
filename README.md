# Recoupe — Revenue Recovery Agent

Multi-agent revenue recovery system for the Razorpay Buildathon (Track 03). It watches four kinds of leaking revenue events — failed payments, abandoned carts, failed subscription renewals, overdue B2B invoices — diagnoses why each happened, and executes a bounded recovery action, with every action gated by hard-coded compliance rules and logged to an audit trail.

Architecture: `docs/architecture.md` — Folder layout: `docs/project-structure.md`

## Status: Phase 2 (Payment Agent + Comms Layer v1) complete

Phase 1 plumbing is complete. Phase 2 now routes payment failures through a real six-stage Payment Agent pipeline, applies deterministic retry and cooldown bounds, persists audit records, and sends through shared Twilio/SMTP Comms Layer functions. Cart, Renewal, and Invoice remain Phase 3/4 stubs.

## Re-run instructions

All commands from the repo root, using the backend virtualenv.

Regenerate synthetic data (outputs `data/synthetic/events.json` + `customer_history.json`, gitignored):

```
backend\.venv\Scripts\python data\synthetic\generate.py
```

Run the full Phase 1 + Phase 2 test suite:

```
backend\.venv\Scripts\python -m pytest backend\tests\
```

The Phase 2 suite performs real provider delivery checks: Twilio's trial SMS template path and SMTP email. WhatsApp plain-body delivery is implemented but unavailable for this account while Twilio business-initiated eligibility is pending; see `docs/demo-notes.md`.

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
3. `copy backend\.env.example backend\.env` and set the Postgres, Twilio, and SMTP values
4. `cd backend && .venv\Scripts\alembic upgrade head`

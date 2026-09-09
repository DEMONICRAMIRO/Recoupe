# Recoupe — Revenue Recovery Agent

Multi-agent revenue recovery system for the Razorpay Buildathon (Track 03). It watches four kinds of leaking revenue events — failed payments, abandoned carts, failed subscription renewals, overdue B2B invoices — diagnoses why each happened, and executes a bounded recovery action, with every action gated by hard-coded compliance rules and logged to an audit trail.

Architecture: `docs/architecture.md` — Folder layout: `docs/project-structure.md` — Current status, live URLs, doc map: `docs/PROJECT-STATUS.md`

## Status: deployed

All 5 phases are complete and the app is live:

- **Backend (Railway):** https://recoupe-production.up.railway.app
- **Frontend (Vercel):** https://recoupe-umber.vercel.app
- **Production database:** Supabase (Postgres), via its transaction pooler

One known limitation is still open: real SMS/email/voice delivery fails in
production with network-level errors (Twilio proxy `403`, SMTP DNS
resolution failure) — see `docs/demo-notes.md`'s Deployment section and
`docs/architecture.md` Section 3 for full detail. Everything else — all
four agents, the risk gates, the dashboard, the audit trail — is built and
working. The full "what broke and how it got fixed" log, including the
deployment saga, is in `docs/demo-notes.md`.

## Re-run instructions

All commands from the repo root, using the backend virtualenv. Shown for
macOS/Linux (`bash`); on Windows, replace `.venv/bin/` with
`.venv\Scripts\` and forward slashes with backslashes.

Regenerate synthetic data (outputs `data/synthetic/events.json` +
`customer_history.json`, gitignored — note this is separate from
`backend/app/data/synthetic/events.json`, a static snapshot bundled with
the deployed backend for the dashboard's "Run batch" button; regenerating
here does not update that bundled copy):

```
backend/.venv/bin/python data/synthetic/generate.py
```

Run the full test suite:

```
cd backend && .venv/bin/python -m pytest tests/
```

The comms-delivery tests (`test_real_sms_delivery`, `test_real_email_delivery`,
etc.) perform real provider sends and require real Twilio/SMTP credentials
in `backend/.env` — they'll fail without them, which is expected on a
machine that isn't configured for real delivery. WhatsApp plain-body
delivery is implemented but has historically been blocked pending Twilio
business-initiated eligibility on the account in use; see
`docs/demo-notes.md`.

Run a full batch through the pipeline locally and print per-agent counts
(reads the regeneratable `data/synthetic/events.json` above — this is the
local CLI path, distinct from the deployed `/events/run-batch` endpoint,
which runs in-process against the bundled snapshot):

```
backend/.venv/bin/python scripts/run_batch.py
```

Run the API server locally (health check at http://localhost:8000/health):

```
cd backend && .venv/bin/uvicorn app.main:app --reload
```

Run the frontend locally (proxies `/api/*` to `localhost:8000` — see
`frontend/vite.config.ts`):

```
cd frontend && npm install && npm run dev
```

## Fresh-machine setup

1. `python -m venv backend/.venv`
2. `backend/.venv/bin/python -m pip install -r backend/requirements.txt`
3. `cp backend/.env.example backend/.env` and set the Postgres/Supabase,
   Twilio, SMTP, Groq, and Razorpay values
4. `cd backend && .venv/bin/alembic upgrade head`
5. `cd frontend && npm install`

To point the frontend at a deployed backend instead of `localhost:8000`,
set `VITE_API_BASE_URL` (e.g. in a `frontend/.env.local`) to the backend's
base URL, with **no** trailing `/api` — the deployed backend's routes are
mounted at the root (`/metrics/summary`, not `/api/metrics/summary`); the
`/api` prefix only exists locally, stripped by Vite's dev proxy. See
`docs/demo-notes.md`'s Deployment section for the bug this exact mismatch
caused in production.

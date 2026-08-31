# PRD — Phase 1: Foundation

**Refer to `architecture.md` for the Event schema (Section 6), folder layout, and component responsibilities before writing any code. Do not invent structures that aren't defined there — if something's missing, update `architecture.md` first, then build.**

## 1. Objective

Build the skeleton every later phase plugs into. No agent intelligence yet — this phase is pure plumbing. Success means data can flow from a synthetic generator, through a router, into stub sub-agents, with correct routing proven by a passing test suite.

## 2. Scope

**In scope:**
- Repo/folder structure (per `project-structure.md`)
- FastAPI backend skeleton with a health-check endpoint
- Postgres/Supabase tables: `events`, `customer_history`, `audit_log`
- Internal Event schema as a Pydantic model (per `architecture.md` Section 6)
- Synthetic data generator producing all 4 event types
- LangGraph orchestrator with a real classification step, routing to 4 **stub** sub-agents (each stub just logs receipt and returns a placeholder)

**Out of scope (later phases):**
- Any real diagnosis/decision logic inside sub-agents
- Risk gate logic
- Real Razorpay API integration
- Frontend/dashboard

## 3. Deliverables

1. `backend/app/main.py` — FastAPI app with `/health` endpoint
2. `backend/app/schemas/event.py` — Event Pydantic model matching `architecture.md`
3. `backend/app/db/models.py` + migration — `events`, `customer_history`, `audit_log` tables
4. `data/synthetic/generate.py` — generator script, outputs `events.json` + `customer_history.json`
5. `backend/app/agents/graph.py` — LangGraph orchestrator with classify node + 4 stub nodes
6. `backend/tests/test_phase1.py` — automated test suite covering Section 5 below

## 4. Detailed Tasks

- [ ] Scaffold folder structure exactly as defined in `project-structure.md`
- [ ] Define `Event` schema as a Pydantic model — must match `architecture.md` Section 6 field-for-field
- [ ] Create DB tables: `events` (mirrors Event schema), `customer_history` (customer_id, event_type, last_contacted_at, contact_count_7d, past_failures, preferred_channel), `audit_log` (id, event_id, action_taken, reasoning, timestamp)
- [ ] Write synthetic generator: produce ≥100 events total, roughly evenly split across the 4 event types, using `faker` for realistic customer names/amounts/timestamps
- [ ] Pre-seed `customer_history` with matching synthetic rows for every generated customer_id
- [ ] Build LangGraph graph: one classify node (can be a simple rule on `event_type` field for now — real LLM classification isn't needed until routing logic is more complex) + 4 stub nodes that each just print/log the event they received and return a fixed placeholder response
- [ ] Write the test suite (Section 5)

## 5. Self-Testing Criteria

This phase is **not done** until every item below passes. Treat this as a loop, not a checklist you do once:

1. **Boot check** — `uvicorn app.main:app` starts without error; `GET /health` returns `200`.
2. **Schema validity** — every field in the `events` table matches the Event Pydantic model; migration applies cleanly on a fresh DB.
3. **Generator volume & coverage** — generator produces ≥100 events; each of the 4 event types has ≥20 events; zero events have null values in required fields.
4. **Schema conformance** — every generated event, when loaded, validates against the Event Pydantic model with zero validation errors.
5. **Routing correctness** — feed 20 hand-picked test events (5 per type) through the orchestrator graph; assert each one lands at its correct stub node, with 100% accuracy (0 misroutes).
6. **Stub response shape** — every stub node returns a response matching the placeholder output schema, so Phase 2 can swap in real logic without changing the interface.
7. **Full suite passes** — `pytest backend/tests/` reports 0 failures.

## 6. Self-Test Loop Instructions

Run `pytest backend/tests/` after every meaningful change. If any test in Section 5 fails:

1. Read the failure carefully — identify which criterion broke and why.
2. Fix the specific code causing the failure (do not weaken the test to make it pass).
3. Re-run the full suite.
4. Repeat until all 7 criteria pass with 0 failures.

**Do not move on to Phase 2 until this loop terminates green.** A Phase 1 that "mostly works" will compound into much harder-to-diagnose bugs once real sub-agent logic is layered on top in Phase 2.

## 7. Definition of Done

- [ ] All 6 deliverables exist and are committed
- [ ] All 7 self-test criteria pass
- [ ] `data/synthetic/events.json` and `customer_history.json` exist and are non-empty
- [ ] README has a short "Phase 1 complete" note with how to re-run the generator and test suite

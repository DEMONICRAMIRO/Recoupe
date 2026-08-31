# Demo Notes — "what broke and how you fixed it"

Running log for the buildathon demo. The judges want the 2am war stories, so entries get added as they happen — never reconstructed from memory.

## Phase 1 — Foundation

- **Alembic + `%` in the DB password crashed instantly.** The Postgres password contains `@`, so it must be percent-encoded (`%40`) in `DATABASE_URL`. The first env.py wrote the resolved URL back into `alembic.ini` via `set_main_option`, and Python's configparser blew up on the bare `%` ("invalid interpolation syntax"). Fix: env.py builds the engine directly from the URL and never round-trips it through the ini; the one place that sets the option programmatically escapes `%` as `%%`.
- **`str(sqlalchemy_url)` silently hides the password.** The test fixture passed the scratch-DB URL to Alembic as `str(url)` — which renders the password as `***`. Alembic then authed as `postgres:***` and Postgres (correctly) refused. Fix: `url.render_as_string(hide_password=False)`. Lesson: never pass a SQLAlchemy URL through `str()` when credentials matter.
- **3 of 45 synthetic customers had zero events.** Random customer picking left 3 pool customers with history rows but no events, failing the strict history-coverage test. Fixed the generator (a shuffled customer cycle now guarantees every customer appears in events) instead of loosening the test.
- **psql password probe hung the shell.** Verifying local Postgres auth with an empty `PGPASSWORD` made psql prompt interactively and the command timed out. Non-interactive probes only from now on.
- **Repo hygiene near-miss.** The working folder sat inside an accidental git repo rooted at the whole home directory, pointing at an unrelated remote. Initialized a clean repo scoped to the project instead — caught before anything was committed to the wrong place.

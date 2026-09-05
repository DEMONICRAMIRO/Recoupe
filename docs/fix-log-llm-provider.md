# Fix Log — LLM Provider Setup (Cart Agent Diagnose Step)

This documents everything that went wrong and got fixed while wiring an LLM provider for Cart Agent's Diagnose step. Kept as a standalone reference so Phase 4+ (or a future rebuild) doesn't repeat the same dead ends. Once Phase 3 wraps, the key findings here should also get folded into `docs/demo-notes.md` as a real war story — this is exactly the kind of thing the buildathon wants documented.

## Summary

What looked like one problem ("the LLM call doesn't work") was actually three separate, unrelated issues stacked on top of each other:

1. A naming collision between two unrelated companies (Groq vs Grok)
2. A wrong base URL for OpenCode Go's real API
3. A wrong model-ID format once the model was found in the catalog

## Timeline

| # | Symptom | Root cause | Fix |
|---|---|---|---|
| 1 | Setup wizard asked to pick an LLM provider for Cart's Diagnose step; "Groq" was selected | N/A — correct choice at the time | Selected Groq as the intended provider |
| 2 | Attempted to reuse "the OpenCode API key" instead of getting a dedicated Groq key | Assumption that one subscription's key works for any provider | Flagged as risky before testing — turned out to matter (see #3) |
| 3 | `.env` model field was set to `grok-4.6` | **Groq (the inference company) and Grok (xAI's model) are unrelated products that happen to sound alike.** Groq does not host Grok at any tier. | Identified via Groq's own supported-models documentation — Groq serves Llama/Mixtral/Gemma/`gpt-oss`, never Grok |
| 4 | Pasted "the OpenCode API key" into `.env` | Wrote `OPENCODE_API_KEY`, `OPENCODE_BASE_URL`, `MODEL_NAME` — OpenCode Go's own credentials, not a Groq key | Confirmed via a read-only key-presence check: `GROQ_API_KEY` was genuinely absent |
| 5 | Decision point: get a real Groq key, or make the OpenCode Go key work directly | User had prior success using the OpenCode key this way in another project | Proceeded to test the OpenCode Go gateway directly rather than switch providers |
| 6 | Probes to `https://opencode.ai/v1/chat/completions` and `https://api.opencode.ai/...` all returned `404` / `Not Found` | **Wrong base path.** The real endpoint lives under `/zen/go/`, not at the domain root and not on the `api.` subdomain | Found via web search of OpenCode Go's actual documented endpoint: `https://opencode.ai/zen/go/v1/chat/completions` |
| 7 | `MODEL_NAME=grok4.6` (no hyphen) still failed after the URL fix | Every real OpenCode Go model ID follows a lowercase-hyphenated convention (`deepseek-v4-pro`, `kimi-k2.6`, `gpt-5.6-luna`) | Corrected to `grok-4.6` |
| 8 | `grok-4.6` probed against the corrected endpoint → `401`, *"Model grok-4.6 is not supported for format oa-compat"* | Grok **is** in the OpenCode Go catalog (33 models total), but isn't callable through the OpenAI-compatible `/chat/completions` format this client uses | Ruled out — not usable for this integration regardless of naming |
| 9 | Tested `glm-5.3-flash` and `deepseek-v4-flash` as alternatives | — | Both returned real `200` responses. `glm-5.3-flash` burned its token budget on internal reasoning and returned empty content at `max_tokens=32`; `deepseek-v4-flash` returned clean output ("PONG") in 1.85s |
| 10 | Flash-tier model proposed as the final pick | Flash tier optimizes for speed, but Cart's Diagnose step is the one genuinely judgment-heavy LLM node in the whole system (`architecture.md` §5) — speed matters less here than reasoning quality | **Open** — `qwen3.8-max` queued for testing against the same confirmed endpoint before a final decision |

## Confirmed facts (safe to build against)

- **Real, working endpoint:** `https://opencode.ai/zen/go/v1/chat/completions`
- **Auth:** `Authorization: Bearer <OPENCODE_API_KEY>`
- **Request shape:** `{"model": "<model-id>", "messages": [{"role": "user", "content": "..."}], "max_tokens": <int>}`
- **Response shape:** OpenAI-style — `choices[0].message` containing both `content` and `reasoning_content`; `usage` and `cost` fields included
- **Model catalog (33 models):** includes MiniMax, Kimi, LongCat, GLM, DeepSeek V4 (Pro/Flash/Vision), Qwen3.x, MiMo, Hy3/4, GPT-5.6 Luna, Grok-4.5/4.6, Muse Spark — but **not every catalog-listed model is callable via the oa-compat format** (Grok-4.6 is the confirmed example of this)
- **`grok-4.6` is not usable for this integration** — confirmed via direct probe, not assumption

## Security incident — action required

Mid-troubleshooting, a full `.env` file was pasted directly into chat, including: the Postgres password, Twilio Account SID + Auth Token, the Gmail SMTP app password, and the OpenCode API key. **Treat all of these as compromised.**

- [ ] Rotate the Postgres password (`DATABASE_URL`)
- [ ] Regenerate the Twilio Auth Token from the Twilio console
- [ ] Revoke and recreate the Gmail app password used for `SMTP_PASS`
- [ ] Regenerate the OpenCode API key if the dashboard supports it
- [ ] Update `.env` with all new values once rotated

## Still open

- [x] Probe `qwen3.8-max` against the confirmed endpoint/shape — **done: `200`, 3.77s, real `"PONG"` content, passed the oa-compat check**
- [x] Finalize `MODEL_NAME` — **done: evaluated `qwen3.8-max` (passed the 5-case Cart diagnosis manual review, ~10s/event), switched to `deepseek-v4-flash` (~1.85s/event) for batch/test iteration speed; `llm_client.py` wired and confirmed end-to-end**
- [x] Update `architecture.md` §8 (Tech Stack) — **done: now documents the OpenCode Go gateway + `deepseek-v4-flash`**
- [x] Update `model-selection.md` if the catalog changes later-phase assignments — **checked: `kimi-k3` (P4), `deepseek-v4-pro` (P5a), `glm-5.3` (P5b), `qwen3.8-flash` (P6) all exist in the catalog — no changes needed**
- [ ] Complete the credential rotation checklist above — all five values still at original (compromised) values as of Phase 3

---

## Second switch — OpenCode Go gateway → Groq (Phase 3, mid-build)

**Why:** The OpenCode Go gateway's usage limit was hit mid-Phase 3, making all LLM-dependent calls fail. A real `GROQ_API_KEY` was obtained and switched in.

**What changed:**

| Item | Before | After |
|---|---|---|
| Endpoint | `https://opencode.ai/zen/go/v1/chat/completions` | `https://api.groq.com/openai/v1/chat/completions` |
| Auth key | `OPENCODE_API_KEY` (rate-limited) | `GROQ_API_KEY` |
| Model | `openai/gpt-oss-120b` (was `qwen3.8-max` in `.env` — OpenCode Go-specific) | `openai/gpt-oss-120b` (confirmed live on Groq via `/v1/models` API) |
| Config field | `settings.opencode_api_key` | `settings.groq_api_key` |

**Verification steps taken (to avoid repeating prior mistakes):**
1. Queried `https://api.groq.com/openai/v1/models` with the new key — got 200 and the full model list. `openai/gpt-oss-120b` is `active: true`, 131072 context.
2. Groq does NOT host Qwen or DeepSeek — those are OpenCode Go catalog models. `qwen3.8-max` (the old `MODEL_NAME`) does not exist on Groq and was replaced.
3. Ran one real test call through the updated `llm_client.py` before touching any agent code — prompt `"Reply with exactly: PONG"` → response `'PONG'`, model confirmed as `openai/gpt-oss-120b`, HTTP 200.
4. Updated `architecture.md` §8 and `backend/.env` and `config.py` atomically.

**Test result:** `HTTP 200`, content `'PONG'`, model `openai/gpt-oss-120b` — Groq is live and responding.

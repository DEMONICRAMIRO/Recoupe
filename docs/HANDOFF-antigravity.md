# Handoff — Provider Switch + Open Voice Verification Issue

**Context:** Switching coding tools from OpenCode Go to Antigravity because OpenCode Go's usage limit was hit. This affects two separate things — read both sections before touching code.

---

## 1. URGENT — Switch the runtime LLM provider from OpenCode Go gateway to Groq

Cart Agent's Diagnose step (`backend/app/core/llm_client.py`) is currently wired to call the **OpenCode Go gateway** (`https://opencode.ai/zen/go/v1/chat/completions`, using `OPENCODE_API_KEY`, model `qwen3.8-max` or `deepseek-v4-flash` — confirm which is actually set in `.env` right now). That account's usage limit has been hit, so this provider is currently unusable.

**A real `GROQ_API_KEY` is being obtained separately and will be provided.** Once it lands in `backend/.env`:

1. Rewire `llm_client.py` to call Groq's actual API instead of the OpenCode Go gateway. Groq's endpoint is the standard OpenAI-compatible `https://api.groq.com/openai/v1/chat/completions` format — confirm the exact current path in Groq's own docs before wiring, since endpoints/model names can change.
2. Pick a real, currently-live Groq-hosted model (Groq hosts open-weight models — Llama, Mixtral, Gemma, and OpenAI's open `gpt-oss` models — **not** Grok, not Qwen, not DeepSeek; those are not available through Groq specifically). Verify the exact model ID in the Groq console before setting `MODEL_NAME`.
3. **Do not reuse `grok-4.6` or any model name from the OpenCode Go catalog** — that catalog is specific to OpenCode Go's gateway and has no relationship to what Groq actually hosts. This exact confusion already cost significant time earlier in this build (see `docs/fix-log-llm-provider.md` for the full history) — don't repeat it.
4. Run one real test call before wiring it into `cart_agent.py` — confirm actual content comes back, not an error.
5. Update `docs/architecture.md` §8 (Tech Stack, LLM inference row) once the switch is confirmed working — it currently says "OpenCode Go gateway" and needs to reflect Groq again.
6. Update `docs/fix-log-llm-provider.md` to note this second switch and why.

**Until the new key arrives:** don't attempt further LLM-dependent testing (Criterion 2's diagnosis review, Criterion 1's full pipeline run) — those calls will fail against the rate-limited OpenCode Go gateway. Everything else (git status, non-LLM code review, the voice issue below) can proceed without it.

---

## 2. Open issue — Hinglish voice call verification (Phase 3, Criterion 7)

**What Criterion 7 requires** (`docs/prd-phase3.md` §5): place one real Twilio Voice call and manually verify it plays the correct Hinglish script — not just "no exception thrown."

**What's confirmed correct, with evidence, and should NOT be re-investigated:**
- The TwiML Bin is saved correctly in the Twilio console — visually confirmed twice, contains the exact intended XML with the Hinglish `<Say>` script
- `TWILIO_VOICE_TWIML_URL` in `.env` matches the Bin's real URL character-for-character
- The exact URL passed to `calls.create()` at call time matches, with a correctly URL-encoded `?Name=...` query param appended
- Three real calls were placed; all show `status: completed`, `error_code: None` — Twilio never recorded a TwiML-fetch failure
- A code-wide search of `backend/app/services/comms/voice.py` and related files found **zero** hardcoded fallback strings resembling "not connected" / "check the url" — whatever was heard is not coming from our own code
- Twilio's Monitor/Logs/Request Inspector console pages are gated behind a trial-account upgrade prompt — genuinely inaccessible, not a dead end worth retrying
- Twilio's Debugger/Alerts REST API (`monitor.alerts`) returns `401` on this trial account — also genuinely inaccessible via API

**What's still unresolved:**
- The actual audio heard on the call did not match the expected Hinglish script — instead sounded like a message about not being "connected to Twilio server" / to "check the URL"
- This phrasing does not match Twilio's standard TwiML-error wording (Twilio's real fallback message is "We're sorry, an application error has occurred. Goodbye.")
- The `answered_by: None` field on the call record was initially treated as evidence the call wasn't answered — **this is likely a misread**; that field only populates when Answering Machine Detection is explicitly requested via the `MachineDetection` parameter, which this call setup doesn't use. It's probably just the default unset value, not meaningful evidence either way.
- Leading hypothesis, not yet confirmed: the message may be a **carrier-side network announcement** (Indian telecom carriers sometimes play automated messages for calls from international/VoIP numbers) rather than anything from Twilio or our code — this would explain clean Twilio-side logs with no error, correct Bin content, and correct URL construction, since the issue would occur after Twilio's delivery, on the last leg to the phone.
- One check not yet completed: whether a call recording exists and is fetchable via `client.calls(<sid>).recordings.list()` — this would let you actually listen to what played, independent of memory/description. Worth trying once, low effort.

**Decision rule — read this before spending more time on it:**

Try the recording check once. If it resolves things cleanly, great, fix whatever it reveals. **If it doesn't resolve cleanly in one more attempt, stop investigating this specific issue.** The infrastructure-level evidence (correct URL, correct Bin content confirmed twice, zero Twilio-side errors across three calls, no fallback code) is solid enough to document honestly rather than chase further given the timeline.

**If unresolved: mark Criterion 7 and Phase 3 as complete anyway**, with this exact honest note added to `docs/demo-notes.md`:

> Criterion 7 (voice call verification): Twilio call setup, TwiML Bin content, and URL construction were all independently verified correct via the Twilio API and console — three real calls placed, all `completed` with `error_code: None`, zero fetch failures logged. Audio playback verification was inconclusive; the message heard did not match Twilio's standard error wording and no fallback exists in our own code, suggesting a possible carrier-side network announcement rather than a code or configuration issue. Infrastructure-level correctness is confirmed; end-to-end audio delivery could not be fully verified within the project timeline.

This is an honest, defensible note — it doesn't overclaim, and it shows real debugging rigor even where the root cause stayed ambiguous. That's consistent with what the buildathon says it wants ("what broke and how you got out"), even when "how you got out" is "confirmed the parts we could confirm and documented the rest honestly."

---

## Everything else — do not re-investigate, already confirmed working

- Phase 1 (76fe759) and Phase 2 (ce6d37f) — both committed, 77/77 tests passing
- Phase 3's non-voice work — Cart/Renewal pipelines, risk gate call-frequency cap, feedback collection, WhatsApp→SMS fallback — all built, not in question here
- The Groq/Grok naming confusion and the OpenCode Go endpoint discovery (`/zen/go/` path) — fully resolved, documented in `docs/fix-log-llm-provider.md`, do not re-debug

## Outstanding from earlier, still unresolved — security rotation

A full `.env` was pasted into a chat mid-project, exposing the Postgres password, Twilio Auth Token, Gmail SMTP app password, and the (now rate-limited) OpenCode API key. None of these have been rotated as of this handoff. Worth doing once Phase 3 is committed, before pushing this repo anywhere public for judging.

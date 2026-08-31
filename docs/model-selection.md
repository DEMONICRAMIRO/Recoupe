# Model Selection — OpenCode Go (Build Phase)

> ⚠️ **Switch-model instruction:** at the start of every phase (and at any sub-task boundary noted below), stop and prompt Arjun to switch OpenCode Go's active model to the one specified here before writing any code for that phase. Do not silently continue on the previous phase's model.

## Reasoning behind the tiering

- **Foundation/scaffolding work** → fast, reliable mid-tier model. Speed matters more than peak reasoning; mistakes here are cheap to catch since nothing depends on it yet.
- **Template-defining work** (the first sub-agent built in full) → strongest available coding/reasoning model. Bugs or bad patterns here get copy-pasted into three more sub-agents later, so it's worth paying for quality upfront.
- **Pattern-replication work** → solid mid-tier model. You're reapplying a proven shape, not inventing one.
- **Integration/debugging work** → a model known for strong long-context and agentic coding, since cross-component bugs require holding more context at once.
- **Docs/polish work** → fastest, cheapest model. Low complexity, high volume of small edits.

## Phase-by-phase table

| Phase | Task | Recommended model | Why |
|---|---|---|---|
| 1 | Repo scaffolding, DB schema, synthetic generator, orchestrator skeleton | **GLM-5.3** | Reliable mid-tier for repetitive, well-specified scaffolding work; foundation needs to be solid but doesn't need frontier reasoning |
| 2 | Payment Agent — full six-stage pipeline (the template) | **GPT 5.6 Luna** | This sub-agent's structure gets copied 3 more times — worth using your strongest available model to get the pattern right the first time |
| 3 | Cart Agent, Renewal Agent (replicate Phase 2's pattern) | **DeepSeek V4 Pro** | Pattern is proven; strong-but-cheaper model is enough for adapting it to new domains |
| 4 | Invoice Agent + full system integration testing | **Kimi K3** | Integration bugs span multiple files/nodes — favor a model with strong long-context and agentic debugging ability |
| 5a | Real Razorpay test-mode API adapter | **DeepSeek V4 Pro** | Precision-focused integration work against an external API's real quirks |
| 5b | Dashboard (React/Vite frontend) | **GLM-5.3** | Solid at UI/component code, same model as Phase 1 keeps context consistent for scaffolding-style work |
| 6 | Polish, README, docs, demo prep | **Qwen3.8 Flash** | High volume of small, low-complexity edits — speed and cost matter most |
| 6 (if hardening bugs appear) | Fallback for last-minute debugging | **DeepSeek V4 Pro** | Keep this on standby — switch back up if Phase 6 surfaces a real bug, don't debug complex issues on the Flash tier |

## Quick reference (copy into your terminal notes)

```
Phase 1        → GLM-5.3
Phase 2        → GPT 5.6 Luna
Phase 3        → DeepSeek V4 Pro
Phase 4        → Kimi K3
Phase 5 (API)  → DeepSeek V4 Pro
Phase 5 (UI)   → GLM-5.3
Phase 6 (docs) → Qwen3.8 Flash
Phase 6 (bugs) → DeepSeek V4 Pro (fallback)
```

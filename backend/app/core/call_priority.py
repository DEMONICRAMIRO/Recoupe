"""call_priority.py — Invoice Agent call-priority scoring.

Follows the hybrid pattern established in Phase 2 (Payment Classify) and Phase 3 (Cart Diagnose):
  - Rules handle clear-cut cases directly, no LLM needed.
  - LLM only invoked for genuinely ambiguous cases where a human account manager
    would need to weigh competing signals (moderate overdue + mixed reliability + no broken promise).

This module is intentionally isolated from invoice_agent.py so it can be unit-tested
independently, mirroring why risk_gate.py is separate from the agent files.
"""

import json
import logging
from dataclasses import dataclass

from app.core.llm_client import LLMUnavailable, chat, default_model_name

logger = logging.getLogger(__name__)

# Priority tiers
TIER_CALL = "call"    # Escalate to phone call
TIER_EMAIL = "email"  # Email-only, no call needed
TIER_SMS = "sms"      # SMS follow-up

# Thresholds for rule-based decisions
HIGH_OVERDUE_DAYS = 90   # >= 90 days → strong signal
LOW_OVERDUE_DAYS = 30    # <= 30 days first-time → low signal


@dataclass(frozen=True)
class CallPriorityResult:
    tier: str           # "call", "email", or "sms"
    score: float        # 0.0 (no call) to 1.0 (definitely call)
    reasoning: str
    llm_invoked: bool   # True if LLM was called for this decision


SCORE_PROMPT_TEMPLATE = (
    "You are assessing whether an overdue B2B invoice account should be escalated to a phone call "
    "or handled via lower-touch channels (email/SMS). "
    "Signals: days_overdue={days_overdue}, account_reliability={account_reliability}, "
    "broken_promise={broken_promise}.\n"
    "Rules alone cannot confidently resolve this case. Use your judgment as a senior account manager would.\n"
    "Respond with ONLY JSON, no markdown fences: "
    '{{"tier": "<call|sms|email>", "score": <0.0-1.0>, "reasoning": "<one concise sentence>"}}'
)


def score_call_priority(
    days_overdue: int,
    account_reliability: str,
    broken_promise: bool,
    *,
    llm_enabled: bool = True,
) -> CallPriorityResult:
    """Score an invoice account for call escalation priority.

    Args:
        days_overdue: Number of days the invoice is past due.
        account_reliability: One of "good", "fair", "poor".
        broken_promise: Whether the customer has previously promised to pay but didn't.
        llm_enabled: If False, falls back to heuristic for all cases (useful for disabling in tests).

    Returns:
        CallPriorityResult with tier, score, reasoning, and whether LLM was invoked.
    """
    reliability = (account_reliability or "fair").lower()

    # --- Rule-based clear-cut cases --- (deterministic, no LLM)

    # Clear call-worthy: high overdue + poor reliability + broken promise
    if days_overdue >= HIGH_OVERDUE_DAYS and reliability == "poor" and broken_promise:
        return CallPriorityResult(
            tier=TIER_CALL,
            score=1.0,
            reasoning=(
                f"Rule: {days_overdue}d overdue + poor reliability + broken promise "
                "— unambiguously call-worthy."
            ),
            llm_invoked=False,
        )

    # Clear call-worthy: very high overdue regardless of other signals
    if days_overdue >= HIGH_OVERDUE_DAYS and reliability == "poor":
        return CallPriorityResult(
            tier=TIER_CALL,
            score=0.9,
            reasoning=(
                f"Rule: {days_overdue}d overdue with poor reliability "
                "— sufficiently clear signal without LLM."
            ),
            llm_invoked=False,
        )

    # Clear non-call: fresh 30-day-late reliable account, no broken promise
    if days_overdue <= LOW_OVERDUE_DAYS and reliability == "good" and not broken_promise:
        return CallPriorityResult(
            tier=TIER_EMAIL,
            score=0.0,
            reasoning=(
                f"Rule: only {days_overdue}d overdue, good reliability, no broken promise "
                "— email-only, no call needed."
            ),
            llm_invoked=False,
        )

    # Clear non-call: good reliability, low overdue, even with minor risk
    if days_overdue <= LOW_OVERDUE_DAYS and reliability == "good":
        return CallPriorityResult(
            tier=TIER_EMAIL,
            score=0.1,
            reasoning=(
                f"Rule: {days_overdue}d overdue with good reliability "
                "— low urgency, email sufficient."
            ),
            llm_invoked=False,
        )

    # Clear escalation without call: 90+ days with good reliability (rare but honest account)
    if days_overdue >= HIGH_OVERDUE_DAYS and reliability == "good" and not broken_promise:
        return CallPriorityResult(
            tier=TIER_SMS,
            score=0.5,
            reasoning=(
                f"Rule: {days_overdue}d overdue but good reliability, no broken promise "
                "— firm SMS follow-up, hold on call."
            ),
            llm_invoked=False,
        )

    # --- Genuinely ambiguous cases --- invoke LLM
    # These are cases where rules alone can't confidently place the account:
    # - Moderate overdue (31-89 days) + mixed reliability + any broken_promise value
    # - High overdue + good reliability + broken promise (unusual combination)
    # - High overdue + fair reliability (could go either way)

    if not llm_enabled:
        # Heuristic fallback when LLM is disabled
        score = min(1.0, days_overdue / 90.0)
        if broken_promise:
            score = min(1.0, score + 0.2)
        if reliability == "poor":
            score = min(1.0, score + 0.15)
        tier = TIER_CALL if score >= 0.6 else (TIER_SMS if score >= 0.35 else TIER_EMAIL)
        return CallPriorityResult(
            tier=tier,
            score=round(score, 2),
            reasoning=(
                f"Heuristic (LLM disabled): days={days_overdue}, reliability={reliability}, "
                f"broken_promise={broken_promise} → score={score:.2f}"
            ),
            llm_invoked=False,
        )

    prompt = SCORE_PROMPT_TEMPLATE.format(
        days_overdue=days_overdue,
        account_reliability=reliability,
        broken_promise=broken_promise,
    )
    try:
        msg = chat(prompt, model=default_model_name(), max_tokens=256)
        raw = msg.content.strip()
        # Strip markdown fences if model adds them anyway
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
        tier = str(data.get("tier", TIER_SMS)).lower()
        if tier not in (TIER_CALL, TIER_EMAIL, TIER_SMS):
            tier = TIER_SMS
        score = float(data.get("score", 0.5))
        score = max(0.0, min(1.0, score))
        reasoning = str(data.get("reasoning", "LLM scored this case."))
        return CallPriorityResult(
            tier=tier,
            score=round(score, 2),
            reasoning=f"LLM: {reasoning}",
            llm_invoked=True,
        )
    except (LLMUnavailable, json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.warning("call_priority LLM failed (%s); falling back to heuristic", exc)
        score = min(1.0, days_overdue / 90.0)
        if broken_promise:
            score = min(1.0, score + 0.2)
        if reliability == "poor":
            score = min(1.0, score + 0.15)
        tier = TIER_CALL if score >= 0.6 else (TIER_SMS if score >= 0.35 else TIER_EMAIL)
        return CallPriorityResult(
            tier=tier,
            score=round(score, 2),
            reasoning=f"Heuristic fallback (LLM error: {exc}): score={score:.2f}",
            llm_invoked=False,
        )

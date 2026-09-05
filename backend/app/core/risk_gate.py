from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from app.core.config import settings


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    reason: str


def _value(history: Any, name: str, default: Any = None) -> Any:
    if history is None:
        return default
    if isinstance(history, Mapping):
        return history.get(name, default)
    return getattr(history, name, default)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def evaluate(history: Any = None, now: datetime | None = None) -> GateDecision:
    """Apply retry and cooldown bounds without involving an LLM."""
    contact_count = int(_value(history, "contact_count_7d", 0) or 0)
    if contact_count >= settings.risk_max_retries:
        return GateDecision(
            allowed=False,
            reason=(
                f"blocked: contact_count_7d={contact_count} reaches the "
                f"maximum of {settings.risk_max_retries}"
            ),
        )

    last_contacted_at = _value(history, "last_contacted_at")
    if last_contacted_at is not None:
        current_time = _as_utc(now or datetime.now(timezone.utc))
        elapsed = current_time - _as_utc(last_contacted_at)
        cooldown = timedelta(hours=settings.risk_cooldown_hours)
        if elapsed < cooldown:
            remaining = cooldown - elapsed
            return GateDecision(
                allowed=False,
                reason=(
                    "blocked: cooldown is active; "
                    f"approximately {remaining.total_seconds() / 3600:.1f} hours remain"
                ),
            )

    return GateDecision(allowed=True, reason="allowed: retry and cooldown bounds satisfied")


def evaluate_call(call_info: Any = None, now: datetime | None = None) -> GateDecision:
    """Call-frequency cap, independent from the message-frequency cap above.

    A blocked call must never block text channels, and vice versa.
    """
    call_count = int(_value(call_info, "call_count_7d", 0) or 0)
    if call_count >= settings.risk_max_calls_7d:
        return GateDecision(
            allowed=False,
            reason=(
                f"blocked: call_count_7d={call_count} reaches the "
                f"maximum of {settings.risk_max_calls_7d}"
            ),
        )

    last_call_at = _value(call_info, "last_call_at")
    if last_call_at is not None:
        current_time = _as_utc(now or datetime.now(timezone.utc))
        elapsed = current_time - _as_utc(last_call_at)
        cooldown = timedelta(hours=settings.risk_cooldown_hours)
        if elapsed < cooldown:
            remaining = cooldown - elapsed
            return GateDecision(
                allowed=False,
                reason=(
                    "blocked: call cooldown is active; "
                    f"approximately {remaining.total_seconds() / 3600:.1f} hours remain"
                ),
            )

    return GateDecision(allowed=True, reason="allowed: call-frequency bounds satisfied")

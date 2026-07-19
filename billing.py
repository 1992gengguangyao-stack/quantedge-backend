"""Paid-plan activation and expiry helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


BILLING_DAYS = {"monthly": 30, "annual": 365}
PLAN_PRICES = {"starter": 29.0, "pro": 79.0, "expert": 199.0}
ANNUAL_DISCOUNT = 0.20

# These limits are the paid product. Keep the pricing page and API enforcement
# in sync so customers receive a concrete upgrade instead of a cosmetic plan
# label. Execution remains testnet/paper-first; no return is promised.
PLAN_LIMITS = {
    "free": {
        "saved_strategies": 3,
        "backtests_per_day": 3,
        "bot_configs": 1,
    },
    "starter": {
        "saved_strategies": 15,
        "backtests_per_day": 25,
        "bot_configs": 10,
    },
    "pro": {
        "saved_strategies": 100,
        "backtests_per_day": 200,
        "bot_configs": 50,
    },
    "expert": {
        "saved_strategies": 500,
        "backtests_per_day": 1000,
        "bot_configs": 200,
    },
}


def get_plan_usd_price(plan: str, billing_period: str = "monthly") -> float:
    """Return the exact checkout total for a monthly or annual access period."""
    monthly = PLAN_PRICES.get(plan.lower(), 0)
    if not monthly:
        return 0
    if billing_period == "annual":
        return round(monthly * 12 * (1 - ANNUAL_DISCOUNT), 2)
    return monthly


def get_plan_limits(plan: str) -> dict[str, int]:
    """Return a copy of the enforced workspace limits for a plan."""
    return dict(PLAN_LIMITS.get((plan or "free").lower(), PLAN_LIMITS["free"]))


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def activate_user_plan(user, plan: str, billing_period: str, now: datetime | None = None) -> datetime:
    """Activate or extend a paid plan from the later of now or its current expiry."""
    now = _as_utc(now) or datetime.now(timezone.utc)
    period = billing_period if billing_period in BILLING_DAYS else "monthly"
    current_expiry = _as_utc(getattr(user, "plan_expires_at", None))
    base = current_expiry if user.plan == plan and current_expiry and current_expiry > now else now
    user.plan = plan
    user.plan_expires_at = base + timedelta(days=BILLING_DAYS[period])
    return user.plan_expires_at


def expire_user_plan_if_needed(user, now: datetime | None = None) -> bool:
    """Downgrade an expired paid plan to free. Returns True when changed."""
    now = _as_utc(now) or datetime.now(timezone.utc)
    expiry = _as_utc(getattr(user, "plan_expires_at", None))
    if user.plan != "free" and expiry and expiry <= now:
        user.plan = "free"
        user.plan_expires_at = None
        return True
    return False

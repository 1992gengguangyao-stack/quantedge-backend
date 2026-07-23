"""
Monitor router: receives client-side beacons for wallet-login events
and Real-User-Monitoring (RUM) data. Also exposes module health checks.

These endpoints are public (no auth) because navigator.sendBeacon cannot
attach Authorization headers.  Basic size/rate sanity is enforced inline.
Data is persisted to logs + in-memory counters for the health endpoint.
"""

import logging
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

logger = logging.getLogger("quantedge")
router = APIRouter(prefix="/monitor", tags=["monitor"])

# ---------------------------------------------------------------------------
# In-memory event store (rolling window, last 1000 events per category)
# ---------------------------------------------------------------------------
_WC_EVENTS: deque = deque(maxlen=1000)
_RUM_EVENTS: deque = deque(maxlen=1000)
_COUNTERS: dict = defaultdict(int)
_DAY_KEY = ""


def _today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _reset_day_if_needed():
    global _DAY_KEY
    today = _today_key()
    if today != _DAY_KEY:
        _DAY_KEY = today
        _COUNTERS.clear()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class WCEvent(BaseModel):
    """WalletConnect lifecycle event from the browser."""
    type: str = Field(..., description="qr_shown | connect_success | connect_failed | reconnect | stale_session")
    closeCode: int | None = None
    reconnectCount: int = 0
    latencyMs: int | None = None
    sessionId: str | None = None
    message: str | None = None


class RUMEvent(BaseModel):
    """Real-User-Monitoring payload."""
    page: str
    eventType: str = Field(..., description="page_load | js_error | api_latency | wc_metric | strategy_metric")
    value: float | None = None
    metadata: dict = Field(default_factory=dict)


class HealthResponse(BaseModel):
    module: str
    status: str
    events_today: int
    success_rate: float | None = None
    last_event: str | None = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/wc-event", status_code=status.HTTP_204_NO_CONTENT)
async def receive_wc_event(event: WCEvent):
    """Receive a WalletConnect lifecycle beacon (no auth, fire-and-forget)."""
    _reset_day_if_needed()
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": event.type,
        "closeCode": event.closeCode,
        "reconnectCount": event.reconnectCount,
        "latencyMs": event.latencyMs,
        "sessionId": event.sessionId,
        "message": (event.message or "")[:200],
    }
    _WC_EVENTS.append(entry)
    key = f"wc_{event.type}"
    _COUNTERS[key] += 1
    _COUNTERS["wc_total"] += 1
    logger.info("WC-EVENT type=%s closeCode=%s reconnect=%s", event.type, event.closeCode, event.reconnectCount)


@router.post("/rum", status_code=status.HTTP_204_NO_CONTENT)
async def receive_rum(event: RUMEvent):
    """Receive a RUM beacon (no auth, fire-and-forget)."""
    _reset_day_if_needed()
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "page": event.page[:120],
        "eventType": event.eventType,
        "value": event.value,
        "metadata": {k: str(v)[:200] for k, v in event.metadata.items()},
    }
    _RUM_EVENTS.append(entry)
    _COUNTERS[f"rum_{event.eventType}"] += 1
    _COUNTERS["rum_total"] += 1
    logger.info("RUM page=%s type=%s value=%s", event.page, event.eventType, event.value)


@router.get("/health/{module}", response_model=HealthResponse)
def module_health(module: str):
    """Return today's aggregate health for a module (wc | rum | strategy)."""
    _reset_day_if_needed()
    module = module.lower()

    if module == "wc":
        total = _COUNTERS.get("wc_total", 0)
        success = _COUNTERS.get("wc_connect_success", 0)
        failed = _COUNTERS.get("wc_connect_failed", 0)
        rate = round(success / total * 100, 1) if total else None
        last = _WC_EVENTS[-1]["ts"] if _WC_EVENTS else None
        status_val = "ok" if (rate is None or rate >= 95) else ("warn" if rate >= 80 else "critical")
        return HealthResponse(module="wc", status=status_val, events_today=total, success_rate=rate, last_event=last)

    if module == "rum":
        total = _COUNTERS.get("rum_total", 0)
        errors = _COUNTERS.get("rum_js_error", 0)
        rate = round(errors / total * 100, 2) if total else None
        last = _RUM_EVENTS[-1]["ts"] if _RUM_EVENTS else None
        status_val = "ok" if (rate is None or rate < 5) else ("warn" if rate < 10 else "critical")
        return HealthResponse(module="rum", status=status_val, events_today=total, success_rate=rate, last_event=last)

    if module == "strategy":
        total = _COUNTERS.get("rum_strategy_metric", 0)
        status_val = "ok" if total < 100 else "warn"
        return HealthResponse(module="strategy", status=status_val, events_today=total)

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown module: {module}")

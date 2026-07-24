"""
Monitor router: receives client-side beacons for wallet-login events
and Real-User-Monitoring (RUM) data. Also exposes module health checks.

These endpoints are public (no auth) because navigator.sendBeacon cannot
attach Authorization headers.  Basic size/rate sanity is enforced inline.
Data is persisted to logs + in-memory counters for the health endpoint.

v20260724-1: 增加 GET /monitor/wc/events 端点查询钱包事件明细
            增加文件持久化（./logs/wc_events.log），启动时自动加载历史
"""

import json
import logging
import os
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

logger = logging.getLogger("quantedge")
router = APIRouter(prefix="/monitor", tags=["monitor"])

# ---------------------------------------------------------------------------
# In-memory event store (rolling window, last 1000 events per category)
# ---------------------------------------------------------------------------
_WC_EVENTS: deque = deque(maxlen=1000)
_RUM_EVENTS: deque = deque(maxlen=1000)
_VOICE_EVENTS: deque = deque(maxlen=1000)
_COUNTERS: dict = defaultdict(int)
_DAY_KEY = ""

# ---------------------------------------------------------------------------
# 文件持久化（Railway 重启后内存数据丢失，文件作为历史归档）
# ---------------------------------------------------------------------------
_PERSIST_DIR = Path(os.environ.get("MONITOR_PERSIST_DIR", "./logs"))
_PERSIST_DIR.mkdir(parents=True, exist_ok=True)
_WC_PERSIST_FILE = _PERSIST_DIR / "wc_events.log"


def _persist_wc_event(entry: dict) -> None:
    """追加写入钱包事件到持久化文件（一行一条 JSON）。"""
    try:
        with _WC_PERSIST_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            f.flush()
    except Exception as exc:
        logger.warning("WC 持久化写入失败: %s", exc)


def _load_persisted_wc_events() -> None:
    """启动时从文件加载历史钱包事件到内存（最多 1000 条最近的）。"""
    if not _WC_PERSIST_FILE.exists():
        return
    try:
        lines = _WC_PERSIST_FILE.read_text(encoding="utf-8").splitlines()
        # 只加载最后 1000 条，保持与 deque maxlen 一致
        for line in lines[-1000:]:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                _WC_EVENTS.append(entry)
                # 重建计数器（按 type 累计）
                evt_type = entry.get("type", "")
                if evt_type:
                    _COUNTERS[f"wc_{evt_type}"] += 1
                    _COUNTERS["wc_total"] += 1
                # 更新 _DAY_KEY 为最近事件的日期
                ts = entry.get("ts", "")
                if ts:
                    day = ts[:10]
                    if day > _DAY_KEY:
                        _DAY_KEY = day
            except json.JSONDecodeError:
                continue
        logger.info("WC 历史事件加载完成: %d 条", len(_WC_EVENTS))
    except Exception as exc:
        logger.warning("WC 历史事件加载失败: %s", exc)


# 启动时自动加载历史
_load_persisted_wc_events()


# ---------------------------------------------------------------------------
# Voice event schema
# ---------------------------------------------------------------------------

class VoiceEvent(BaseModel):
    """Voice interaction event from the browser."""
    eventType: str = Field(..., description="recognition_start | recognition_result | recognition_error | synthesis_start | synthesis_end | command_executed")
    transcript: str | None = None
    intent: str | None = None
    confidence: float | None = None
    latencyMs: int | None = None
    error: str | None = None
    metadata: dict = Field(default_factory=dict)


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
    # 持久化到文件（v20260724-1）
    _persist_wc_event(entry)
    logger.info("WC-EVENT type=%s closeCode=%s reconnect=%s", event.type, event.closeCode, event.reconnectCount)


@router.get("/wc/events")
def list_wc_events(
    type: Optional[str] = Query(None, description="按事件类型过滤：qr_shown | connect_success | connect_failed | reconnect | stale_session"),
    limit: int = Query(100, ge=1, le=1000, description="返回条数，默认 100"),
    since: Optional[str] = Query(None, description="ISO 时间戳，只返回此时间之后的事件"),
):
    """
    查询钱包事件明细（v20260724-1 新增）。

    - 不传 type：返回所有类型
    - type=connect_success：只返回登录成功事件
    - since=2026-07-20T00:00:00：只返回此时间之后的事件
    """
    events = list(_WC_EVENTS)
    if type:
        events = [e for e in events if e.get("type") == type]
    if since:
        events = [e for e in events if e.get("ts", "") >= since]
    # 倒序返回最近的事件（最新在前）
    events = list(reversed(events))[:limit]
    return {
        "total": len(_WC_EVENTS),
        "filtered": len(events),
        "events": events,
    }


@router.get("/wc/stats")
def wc_stats():
    """
    返回钱包事件的统计汇总（v20260724-1 新增）。
    按事件类型分组计数，便于快速查看 connect_success 数量。
    """
    _reset_day_if_needed()
    by_type: dict = {}
    for evt in _WC_EVENTS:
        t = evt.get("type", "unknown")
        by_type[t] = by_type.get(t, 0) + 1
    return {
        "total_events": len(_WC_EVENTS),
        "by_type": by_type,
        "counters_today": {k: v for k, v in _COUNTERS.items() if k.startswith("wc_")},
        "first_event": _WC_EVENTS[0]["ts"] if _WC_EVENTS else None,
        "last_event": _WC_EVENTS[-1]["ts"] if _WC_EVENTS else None,
    }


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
    """Return today's aggregate health for a module (wc | rum | strategy | voice)."""
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

    if module == "voice":
        total = _COUNTERS.get("voice_total", 0)
        errors = _COUNTERS.get("voice_recognition_error", 0)
        success = _COUNTERS.get("voice_command_executed", 0)
        rate = round(success / total * 100, 1) if total else None
        last = _VOICE_EVENTS[-1]["ts"] if _VOICE_EVENTS else None
        status_val = "ok" if (rate is None or rate >= 90) else ("warn" if rate >= 70 else "critical")
        return HealthResponse(module="voice", status=status_val, events_today=total, success_rate=rate, last_event=last)

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown module: {module}")


@router.post("/voice-event", status_code=status.HTTP_204_NO_CONTENT)
async def receive_voice_event(event: VoiceEvent):
    """Receive a voice interaction beacon (no auth, fire-and-forget)."""
    _reset_day_if_needed()
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "eventType": event.eventType,
        "transcript": (event.transcript or "")[:200],
        "intent": event.intent,
        "confidence": event.confidence,
        "latencyMs": event.latencyMs,
        "error": (event.error or "")[:200],
        "metadata": {k: str(v)[:200] for k, v in event.metadata.items()},
    }
    _VOICE_EVENTS.append(entry)
    _COUNTERS[f"voice_{event.eventType}"] += 1
    _COUNTERS["voice_total"] += 1
    logger.info("VOICE-EVENT type=%s intent=%s confidence=%s", event.eventType, event.intent, event.confidence)

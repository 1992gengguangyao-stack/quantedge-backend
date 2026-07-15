"""Privacy-first first-party analytics ingestion.

The browser sends random anonymous IDs.  They are HMAC-hashed before storage,
and neither IP addresses nor wallet addresses are persisted.
"""

import hashlib
import hmac
import re

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from models import AnalyticsEvent
from schemas import AnalyticsEventCreate

router = APIRouter(prefix="/analytics", tags=["analytics"])

ALLOWED_EVENTS = {
    "page_view",
    "cta_click",
    "pricing_view",
    "wallet_connect_open",
    "wallet_connect_success",
    "wallet_connect_error",
    "checkout_start",
    "payment_method_selected",
    "payment_submitted",
    "payment_confirmed",
    "guide_cta",
    "language_change",
}

_SAFE_TEXT = re.compile(r"[^a-zA-Z0-9_./:@+\- ]")


def _clean_text(value: str, limit: int, fallback: str = "") -> str:
    cleaned = _SAFE_TEXT.sub("", (value or "").strip())
    return cleaned[:limit] or fallback


def _hash_identifier(value: str) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _origin_allowed(origin: str) -> bool:
    if not origin:
        return True
    if origin in {"https://aiquantbtc.com", "https://www.aiquantbtc.com"}:
        return True
    return bool(re.fullmatch(r"https://[a-z0-9-]+\.aiquantbtc\.pages\.dev", origin))


def _safe_properties(raw: dict) -> dict:
    result = {}
    for key, value in list(raw.items())[:16]:
        safe_key = _clean_text(str(key), 40)
        if not safe_key:
            continue
        if isinstance(value, str):
            result[safe_key] = value[:160]
        elif value is None or isinstance(value, (bool, int, float)):
            result[safe_key] = value
    return result


@router.post("/event", status_code=status.HTTP_202_ACCEPTED)
def record_event(
    payload: AnalyticsEventCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    """Store one anonymous event; duplicate delivery is idempotent."""
    if not _origin_allowed(request.headers.get("origin", "")):
        raise HTTPException(status_code=403, detail="Origin not allowed")
    if payload.event_name not in ALLOWED_EVENTS:
        raise HTTPException(status_code=400, detail="Unsupported event")

    event = AnalyticsEvent(
        event_id=_clean_text(payload.event_id, 64),
        visitor_id_hash=_hash_identifier(payload.visitor_id),
        session_id_hash=_hash_identifier(payload.session_id),
        event_name=payload.event_name,
        path=_clean_text(payload.path.split("?", 1)[0], 512, "/"),
        referrer_host=_clean_text(payload.referrer_host, 255),
        source=_clean_text(payload.source, 128, "direct"),
        medium=_clean_text(payload.medium, 128),
        campaign=_clean_text(payload.campaign, 128),
        plan=_clean_text(payload.plan, 32),
        properties=_safe_properties(payload.properties),
    )
    db.add(event)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return {"accepted": True, "duplicate": True}
    return {"accepted": True}

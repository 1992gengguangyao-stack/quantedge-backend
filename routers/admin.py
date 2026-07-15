"""
Admin dashboard router: view users, payments, strategies, bots, etc.
Protected by a simple admin secret key.
"""

from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Header, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, desc

from database import get_db
from models import User, Strategy, Subscription, Payment, Bot, Backtest, Referral, AnalyticsEvent
from config import settings
from quant.payment_verifier import PLAN_PRICES

router = APIRouter(prefix="/admin", tags=["admin"])

ADMIN_KEY = settings.SECRET_KEY


def verify_admin(x_admin_key: str = Header(default="")):
    """Verify admin access via custom header."""
    if x_admin_key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Admin access required")
    return True


@router.get("/stats")
def admin_stats(db: Session = Depends(get_db), _=Depends(verify_admin)):
    """Get dashboard overview statistics."""
    now = datetime.now(timezone.utc)

    total_users = db.query(User).count()
    total_strategies = db.query(Strategy).count()
    total_bots = db.query(Bot).count()
    total_payments = db.query(Payment).count()
    total_backtests = db.query(Backtest).count()
    total_referrals = db.query(Referral).count()

    # Revenue
    confirmed_payments = db.query(Payment).filter(Payment.status == "confirmed").all()
    total_revenue = sum(PLAN_PRICES.get(p.plan, 0) for p in confirmed_payments)

    # Active bots
    running_bots = db.query(Bot).filter(Bot.status == "running").count()

    # Published strategies
    published_strategies = db.query(Strategy).filter(Strategy.is_published == True).count()

    # Plan distribution
    plan_counts = (
        db.query(User.plan, func.count(User.id))
        .group_by(User.plan)
        .all()
    )
    plan_dist = {plan: count for plan, count in plan_counts}

    # Payment status distribution
    pay_status = (
        db.query(Payment.status, func.count(Payment.id))
        .group_by(Payment.status)
        .all()
    )
    pay_dist = {status: count for status, count in pay_status}

    return {
        "users": total_users,
        "strategies": total_strategies,
        "bots": total_bots,
        "running_bots": running_bots,
        "payments": total_payments,
        "backtests": total_backtests,
        "referrals": total_referrals,
        "revenue": round(total_revenue, 2),
        "published_strategies": published_strategies,
        "plan_distribution": plan_dist,
        "payment_status_distribution": pay_dist,
    }


@router.get("/analytics")
def admin_analytics(
    day: date | None = Query(default=None),
    tz_offset_minutes: int = Query(default=480, ge=-720, le=840),
    db: Session = Depends(get_db),
    _=Depends(verify_admin),
):
    """Daily anonymous traffic, acquisition, funnel, and revenue summary."""
    local_tz = timezone(timedelta(minutes=tz_offset_minutes))
    local_day = day or datetime.now(local_tz).date()
    start_local = datetime.combine(local_day, time.min, tzinfo=local_tz)
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = (start_local + timedelta(days=1)).astimezone(timezone.utc)

    events = (
        db.query(AnalyticsEvent)
        .filter(
            AnalyticsEvent.created_at >= start_utc,
            AnalyticsEvent.created_at < end_utc,
            AnalyticsEvent.source != "internal_qa",
        )
        .all()
    )

    event_counts = Counter(event.event_name for event in events)
    page_counts = Counter(event.path for event in events if event.event_name == "page_view")
    source_counts = Counter(event.source or "direct" for event in events if event.event_name == "page_view")
    visitors = len({event.visitor_id_hash for event in events})
    sessions = len({event.session_id_hash for event in events})

    payments = (
        db.query(Payment)
        .filter(
            Payment.status == "confirmed",
            Payment.created_at >= start_utc,
            Payment.created_at < end_utc,
        )
        .all()
    )
    revenue_usd = round(sum(PLAN_PRICES.get(payment.plan, 0) for payment in payments), 2)

    return {
        "day": local_day.isoformat(),
        "timezone_offset_minutes": tz_offset_minutes,
        "visitors": visitors,
        "sessions": sessions,
        "page_views": event_counts.get("page_view", 0),
        "events": dict(event_counts),
        "top_pages": [{"path": path, "views": count} for path, count in page_counts.most_common(10)],
        "top_sources": [{"source": source, "views": count} for source, count in source_counts.most_common(10)],
        "funnel": {
            "visitors": visitors,
            "wallet_connect_open": event_counts.get("wallet_connect_open", 0),
            "wallet_connect_success": event_counts.get("wallet_connect_success", 0),
            "checkout_start": event_counts.get("checkout_start", 0),
            "payment_submitted": event_counts.get("payment_submitted", 0),
            "payment_confirmed": event_counts.get("payment_confirmed", 0),
        },
        "revenue_usd": revenue_usd,
        "revenue_goal_usd": 1000,
        "revenue_remaining_usd": max(0, round(1000 - revenue_usd, 2)),
    }


@router.get("/users")
def admin_users(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    _=Depends(verify_admin),
):
    """List all users with pagination."""
    users = db.query(User).order_by(desc(User.created_at)).offset(skip).limit(limit).all()
    return [
        {
            "id": u.id,
            "email": u.email,
            "wallet_address": u.wallet_address,
            "username": u.username,
            "plan": u.plan,
            "is_active": u.is_active,
            "invite_code": u.invite_code,
            "referred_by_code": u.referred_by_code,
            "referral_bonus_days": u.referral_bonus_days,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "strategy_count": len(u.strategies) if u.strategies else 0,
            "bot_count": len(u.bots) if u.bots else 0,
            "payment_count": len(u.payments) if u.payments else 0,
        }
        for u in users
    ]


@router.get("/payments")
def admin_payments(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    _=Depends(verify_admin),
):
    """List all payments with user info."""
    payments = db.query(Payment).order_by(desc(Payment.created_at)).offset(skip).limit(limit).all()
    return [
        {
            "id": p.id,
            "user_id": p.user_id,
            "username": p.user.username if p.user else "",
            "email": p.user.email if p.user else "",
            "amount": p.amount,
            "currency": p.currency,
            "tx_hash": p.tx_hash,
            "status": p.status,
            "plan": p.plan,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }
        for p in payments
    ]


@router.get("/strategies")
def admin_strategies(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    _=Depends(verify_admin),
):
    """List all strategies."""
    strategies = db.query(Strategy).order_by(desc(Strategy.created_at)).offset(skip).limit(limit).all()
    return [
        {
            "id": s.id,
            "user_id": s.user_id,
            "owner": s.owner.username if s.owner else "",
            "name": s.name,
            "category": s.category,
            "language": s.language,
            "is_public": s.is_public,
            "is_published": s.is_published,
            "price_monthly": s.price_monthly,
            "roi": s.roi,
            "sharpe_ratio": s.sharpe_ratio,
            "max_drawdown": s.max_drawdown,
            "subscribers_count": s.subscribers_count,
            "coins": s.coins,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }
        for s in strategies
    ]


@router.get("/bots")
def admin_bots(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    _=Depends(verify_admin),
):
    """List all bots."""
    bots = db.query(Bot).order_by(desc(Bot.created_at)).offset(skip).limit(limit).all()
    return [
        {
            "id": b.id,
            "user_id": b.user_id,
            "owner": b.user.username if b.user else "",
            "name": b.name,
            "bot_type": b.bot_type,
            "exchange": b.exchange,
            "status": b.status,
            "created_at": b.created_at.isoformat() if b.created_at else None,
        }
        for b in bots
    ]


@router.get("/backtests")
def admin_backtests(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    _=Depends(verify_admin),
):
    """List all backtests."""
    backtests = db.query(Backtest).order_by(desc(Backtest.created_at)).offset(skip).limit(limit).all()
    return [
        {
            "id": bt.id,
            "user_id": bt.user_id,
            "owner": bt.user.username if bt.user else "",
            "strategy_id": bt.strategy_id,
            "status": bt.status,
            "created_at": bt.created_at.isoformat() if bt.created_at else None,
        }
        for bt in backtests
    ]


@router.get("/referrals")
def admin_referrals(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    _=Depends(verify_admin),
):
    """List all referrals."""
    refs = db.query(Referral).order_by(desc(Referral.created_at)).offset(skip).limit(limit).all()
    return [
        {
            "id": r.id,
            "referrer_id": r.referrer_id,
            "referrer": r.referrer.username if r.referrer else "",
            "referred_id": r.referred_id,
            "referred": r.referred.username if r.referred else "",
            "invite_code_used": r.invite_code_used,
            "reward_days_referrer": r.reward_days_referrer,
            "reward_days_referred": r.reward_days_referred,
            "status": r.status,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in refs
    ]

"""
Subscriptions router: subscribe to strategies, list, and cancel.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db
from deps import get_current_user
from billing import get_plan_limits
from models import Strategy, Subscription, User
from schemas import MessageResponse, SubscriptionCreate, SubscriptionOut

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


@router.get("/plan-limits")
def plan_limits(current_user: User = Depends(get_current_user)):
    """Return the limits that are currently enforced for the signed-in user."""
    return {"plan": current_user.plan, "limits": get_plan_limits(current_user.plan)}


@router.get("/", response_model=list[SubscriptionOut])
def list_subscriptions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List the current user's active subscriptions."""
    subs = (
        db.query(Subscription)
        .filter(
            Subscription.user_id == current_user.id,
            Subscription.status == "active",
        )
        .order_by(Subscription.started_at.desc())
        .all()
    )
    return subs


@router.post("/", response_model=SubscriptionOut, status_code=status.HTTP_201_CREATED)
def create_subscription(
    payload: SubscriptionCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Subscribe to a strategy."""
    strategy = db.query(Strategy).filter(Strategy.id == payload.strategy_id).first()
    if not strategy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Strategy not found",
        )
    if not strategy.is_public and not strategy.is_published and strategy.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Strategy is not available for subscription",
        )

    # Check for existing active subscription
    existing = (
        db.query(Subscription)
        .filter(
            Subscription.user_id == current_user.id,
            Subscription.strategy_id == payload.strategy_id,
            Subscription.status == "active",
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Already subscribed to this strategy",
        )

    # Create subscription with 30-day expiry
    now = datetime.now(timezone.utc)
    sub = Subscription(
        user_id=current_user.id,
        strategy_id=payload.strategy_id,
        status="active",
        started_at=now,
        expires_at=now + timedelta(days=30),
    )
    db.add(sub)

    # Increment strategy subscriber count
    strategy.subscribers_count = (strategy.subscribers_count or 0) + 1

    db.commit()
    db.refresh(sub)
    return sub


@router.delete("/{subscription_id}", response_model=MessageResponse)
def cancel_subscription(
    subscription_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cancel an active subscription."""
    sub = db.query(Subscription).filter(Subscription.id == subscription_id).first()
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found",
        )
    if sub.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only cancel your own subscriptions",
        )
    if sub.status == "cancelled":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Subscription is already cancelled",
        )

    sub.status = "cancelled"

    # Decrement strategy subscriber count
    strategy = db.query(Strategy).filter(Strategy.id == sub.strategy_id).first()
    if strategy and strategy.subscribers_count and strategy.subscribers_count > 0:
        strategy.subscribers_count -= 1

    db.commit()
    return MessageResponse(message="Subscription cancelled", detail={"id": subscription_id})

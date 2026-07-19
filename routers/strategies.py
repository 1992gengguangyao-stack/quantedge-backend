"""
Strategies router: CRUD operations and marketplace.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from database import get_db
from deps import get_current_user
from billing import get_plan_limits
from models import Strategy, User
from schemas import (
    MessageResponse,
    StrategyCreate,
    StrategyOut,
    StrategyUpdate,
)

router = APIRouter(prefix="/strategies", tags=["strategies"])


@router.get("/", response_model=list[StrategyOut])
def list_my_strategies(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List the current user's strategies."""
    strategies = (
        db.query(Strategy)
        .filter(Strategy.user_id == current_user.id)
        .order_by(Strategy.created_at.desc())
        .all()
    )
    return strategies


@router.post("/", response_model=StrategyOut, status_code=status.HTTP_201_CREATED)
def create_strategy(
    payload: StrategyCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new strategy."""
    limit = get_plan_limits(current_user.plan)["saved_strategies"]
    current_count = db.query(Strategy).filter(Strategy.user_id == current_user.id).count()
    if current_count >= limit:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Your {current_user.plan} plan allows {limit} saved strategies. Upgrade or delete one to continue.",
        )
    strategy = Strategy(
        user_id=current_user.id,
        name=payload.name,
        description=payload.description,
        code=payload.code,
        language=payload.language,
        category=payload.category,
        coins=payload.coins,
        is_public=payload.is_public,
        is_published=False,
        price_monthly=payload.price_monthly,
        roi=payload.roi,
        sharpe_ratio=payload.sharpe_ratio,
        max_drawdown=payload.max_drawdown,
    )
    db.add(strategy)
    db.commit()
    db.refresh(strategy)
    return strategy


@router.get("/marketplace", response_model=list[StrategyOut])
def marketplace(
    category: Optional[str] = Query(None, description="Filter by category"),
    sort: str = Query("subscribers", description="Sort by: roi / subscribers / price / created"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """
    Browse the strategy marketplace.

    Lists strategies that have been published (is_published=True).
    Supports filtering by category and sorting by roi / subscribers / price.
    """
    query = db.query(Strategy).filter(Strategy.is_published == True)

    if category:
        query = query.filter(Strategy.category == category)

    # Sorting
    sort_map = {
        "roi": Strategy.roi.desc(),
        "subscribers": Strategy.subscribers_count.desc(),
        "price": Strategy.price_monthly.asc(),
        "created": Strategy.created_at.desc(),
    }
    order_col = sort_map.get(sort, Strategy.subscribers_count.desc())
    query = query.order_by(order_col)

    strategies = query.offset(offset).limit(limit).all()
    return strategies


@router.get("/{strategy_id}", response_model=StrategyOut)
def get_strategy(
    strategy_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get a strategy by ID. The user must be the owner or the strategy must be public."""
    strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
    if not strategy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Strategy not found",
        )
    # Access control: owner can always see; others can see public or published
    if strategy.user_id != current_user.id and not strategy.is_public and not strategy.is_published:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this strategy",
        )
    return strategy


@router.put("/{strategy_id}", response_model=StrategyOut)
def update_strategy(
    strategy_id: int,
    payload: StrategyUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update a strategy (owner only)."""
    strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
    if not strategy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Strategy not found",
        )
    if strategy.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the owner can update this strategy",
        )

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(strategy, field, value)

    strategy.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(strategy)
    return strategy


@router.delete("/{strategy_id}", response_model=MessageResponse)
def delete_strategy(
    strategy_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a strategy (owner only)."""
    strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
    if not strategy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Strategy not found",
        )
    if strategy.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the owner can delete this strategy",
        )

    db.delete(strategy)
    db.commit()
    return MessageResponse(message="Strategy deleted", detail={"id": strategy_id})


@router.post("/{strategy_id}/publish", response_model=StrategyOut)
def publish_strategy(
    strategy_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Publish a strategy to the marketplace (owner only)."""
    strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
    if not strategy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Strategy not found",
        )
    if strategy.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the owner can publish this strategy",
        )

    strategy.is_published = True
    strategy.is_public = True
    strategy.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(strategy)
    return strategy

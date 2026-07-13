"""
Referrals router: invite codes, referral stats, and referral history.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db
from deps import get_current_user
from models import User, Referral
from schemas import ReferralStatsOut, ReferralOut, MessageResponse

router = APIRouter(prefix="/referrals", tags=["referrals"])

# Reward configuration
REWARD_DAYS_REFERRER = 7   # inviter gets 7 days Pro
REWARD_DAYS_REFERRED = 3   # invitee gets 3 days Pro


@router.get("/stats", response_model=ReferralStatsOut)
def get_referral_stats(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get the current user's referral stats: invite code, invite link,
    total invited count, accumulated bonus days, and recent referrals.
    """
    # Ensure user has an invite code (for legacy users)
    if not current_user.invite_code:
        from routers.auth import _generate_invite_code
        current_user.invite_code = _generate_invite_code(db)
        db.commit()
        db.refresh(current_user)

    # Fetch all referrals made by this user
    referrals = (
        db.query(Referral)
        .filter(Referral.referrer_id == current_user.id)
        .order_by(Referral.created_at.desc())
        .all()
    )

    # Build referral list with referred username
    referral_list = []
    for r in referrals:
        referred_user = db.query(User).filter(User.id == r.referred_id).first()
        referral_list.append(
            ReferralOut(
                id=r.id,
                referrer_id=r.referrer_id,
                referred_id=r.referred_id,
                referred_username=referred_user.username if referred_user else "unknown",
                reward_days_referrer=r.reward_days_referrer,
                reward_days_referred=r.reward_days_referred,
                status=r.status,
                created_at=r.created_at,
            )
        )

    total_bonus = sum(r.reward_days_referrer for r in referrals)

    return ReferralStatsOut(
        invite_code=current_user.invite_code,
        invite_link=f"https://quantedge.io/register?ref={current_user.invite_code}",
        total_invited=len(referrals),
        total_bonus_days=total_bonus,
        referral_bonus_days=current_user.referral_bonus_days or 0,
        recent_referrals=referral_list[:20],
    )


@router.get("/history", response_model=list[ReferralOut])
def get_referral_history(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get the full referral history for the current user."""
    referrals = (
        db.query(Referral)
        .filter(Referral.referrer_id == current_user.id)
        .order_by(Referral.created_at.desc())
        .all()
    )

    result = []
    for r in referrals:
        referred_user = db.query(User).filter(User.id == r.referred_id).first()
        result.append(
            ReferralOut(
                id=r.id,
                referrer_id=r.referrer_id,
                referred_id=r.referred_id,
                referred_username=referred_user.username if referred_user else "unknown",
                reward_days_referrer=r.reward_days_referrer,
                reward_days_referred=r.reward_days_referred,
                status=r.status,
                created_at=r.created_at,
            )
        )
    return result


@router.post("/regenerate-code", response_model=MessageResponse)
def regenerate_invite_code(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Generate a new invite code (replaces the old one)."""
    from routers.auth import _generate_invite_code
    current_user.invite_code = _generate_invite_code(db)
    db.commit()
    return MessageResponse(
        message="Invite code regenerated",
        detail={"invite_code": current_user.invite_code},
    )


@router.get("/validate/{code}", response_model=MessageResponse)
def validate_invite_code(
    code: str,
    db: Session = Depends(get_db),
):
    """Check if an invite code is valid (public, no auth required)."""
    referrer = db.query(User).filter(User.invite_code == code.upper().strip()).first()
    if not referrer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid invite code",
        )
    return MessageResponse(
        message="Valid invite code",
        detail={"referrer": referrer.username},
    )

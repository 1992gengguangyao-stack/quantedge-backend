"""
Auth router: wallet-only login (SIWE) and current user info.
"""

import secrets
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from auth import (
    create_access_token,
    generate_nonce,
    parse_siwe_message,
    verify_siwe_message,
)
from billing import expire_user_plan_if_needed
from database import get_db
from deps import get_current_user
from models import User, Referral, SiweNonce
from schemas import (
    Token,
    UserOut,
    WalletLogin,
    MessageResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _allowed_siwe_domain(domain: str) -> bool:
    if domain in {"aiquantbtc.com", "www.aiquantbtc.com"}:
        return True
    if re.fullmatch(r"[a-z0-9-]+\.aiquantbtc\.pages\.dev", domain or ""):
        return True
    return bool(re.fullmatch(r"(?:localhost|127\.0\.0\.1)(?::\d+)?", domain or ""))


def _siwe_uri_matches(domain: str, uri) -> bool:
    """Require HTTPS for public origins while keeping local development usable."""
    if uri.netloc != domain:
        return False
    local_domain = bool(re.fullmatch(r"(?:localhost|127\.0\.0\.1)(?::\d+)?", domain or ""))
    return uri.scheme in ({"http", "https"} if local_domain else {"https"})


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _generate_invite_code(db: Session) -> str:
    """Generate a unique 8-char alphanumeric invite code."""
    while True:
        code = secrets.token_urlsafe(6)[:8].upper().replace("-", "0").replace("_", "X")
        existing = db.query(User).filter(User.invite_code == code).first()
        if not existing:
            return code


@router.post("/wallet-login", response_model=Token)
def wallet_login(payload: WalletLogin, db: Session = Depends(get_db)):
    """
    Sign-In with Ethereum (EIP-4361 / SIWE).

    The client constructs a SIWE message, signs it with their wallet, and
    sends both {message, signature}. The server recovers the address from the
    signature, verifies it matches the message, and finds-or-creates the user.
    """
    try:
        fields = parse_siwe_message(payload.message)
        domain = fields.get("domain", "")
        uri = urlparse(fields.get("uri", ""))
        if not _allowed_siwe_domain(domain):
            raise ValueError("SIWE domain is not allowed")
        if not _siwe_uri_matches(domain, uri):
            raise ValueError("SIWE URI does not match its domain")

        nonce_record = (
            db.query(SiweNonce)
            .filter(SiweNonce.nonce == fields.get("nonce", ""))
            .with_for_update()
            .first()
        )
        if not nonce_record or nonce_record.used_at is not None:
            raise ValueError("SIWE nonce is invalid or has already been used")
        if _aware(nonce_record.created_at) < datetime.now(timezone.utc) - timedelta(minutes=15):
            raise ValueError("SIWE nonce has expired")

        wallet_address = verify_siwe_message(payload.message, payload.signature)
        nonce_record.used_at = datetime.now(timezone.utc)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"SIWE verification failed: {exc}",
        )

    # Find or create user by wallet address
    user = db.query(User).filter(User.wallet_address == wallet_address).first()
    if not user:
        # Validate invite code if provided
        referrer = None
        if payload.invite_code:
            referrer = db.query(User).filter(User.invite_code == payload.invite_code.upper().strip()).first()
            if not referrer:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid invite code",
                )

        invite_code = _generate_invite_code(db)

        user = User(
            wallet_address=wallet_address,
            username=f"wallet_{wallet_address[:8]}",
            email=None,
            hashed_password=None,
            plan="free",
            is_active=True,
            invite_code=invite_code,
            referred_by_code=payload.invite_code.upper().strip() if payload.invite_code else None,
            referral_bonus_days=3 if referrer else 0,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        # Process referral reward for wallet users
        if referrer:
            referrer.referral_bonus_days = (referrer.referral_bonus_days or 0) + 7
            referral = Referral(
                referrer_id=referrer.id,
                referred_id=user.id,
                invite_code_used=payload.invite_code.upper().strip(),
                reward_days_referrer=7,
                reward_days_referred=3,
                status="completed",
            )
            db.add(referral)
            db.commit()

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive",
        )

    if expire_user_plan_if_needed(user):
        db.commit()
        db.refresh(user)

    token = create_access_token(data={"sub": str(user.id)})
    return Token(access_token=token, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)):
    """Return the current authenticated user's info."""
    return current_user


@router.get("/nonce", response_model=MessageResponse)
def get_nonce(db: Session = Depends(get_db)):
    """Generate a random nonce for SIWE message construction."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    db.query(SiweNonce).filter(SiweNonce.created_at < cutoff).delete(synchronize_session=False)
    nonce = generate_nonce()
    db.add(SiweNonce(nonce=nonce))
    db.commit()
    return MessageResponse(message="nonce", detail={"nonce": nonce})

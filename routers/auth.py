"""
Auth router: register, login, wallet-login (SIWE), and current user info.
"""

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from auth import (
    create_access_token,
    generate_nonce,
    hash_password,
    verify_password,
    verify_siwe_message,
)
from database import get_db
from deps import get_current_user
from models import User, Referral
from schemas import (
    Token,
    UserLogin,
    UserOut,
    UserRegister,
    WalletLogin,
    MessageResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _generate_invite_code(db: Session) -> str:
    """Generate a unique 8-char alphanumeric invite code."""
    while True:
        code = secrets.token_urlsafe(6)[:8].upper().replace("-", "0").replace("_", "X")
        existing = db.query(User).filter(User.invite_code == code).first()
        if not existing:
            return code


@router.post("/register", response_model=Token, status_code=status.HTTP_201_CREATED)
def register(payload: UserRegister, db: Session = Depends(get_db)):
    """Register a new user with email and password. Supports invite codes."""
    # Check if email already exists
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    # Validate invite code if provided
    referrer = None
    if payload.invite_code:
        referrer = db.query(User).filter(User.invite_code == payload.invite_code.upper().strip()).first()
        if not referrer:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid invite code",
            )

    # Generate invite code for the new user
    invite_code = _generate_invite_code(db)

    user = User(
        email=payload.email,
        username=payload.username,
        hashed_password=hash_password(payload.password),
        plan="free",
        is_active=True,
        invite_code=invite_code,
        referred_by_code=payload.invite_code.upper().strip() if payload.invite_code else None,
        referral_bonus_days=3 if referrer else 0,  # invited user gets 3 days Pro bonus
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Process referral reward
    if referrer:
        # Give referrer 7 days Pro bonus
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

    token = create_access_token(data={"sub": str(user.id)})
    return Token(access_token=token, user=UserOut.model_validate(user))


@router.post("/login", response_model=Token)
def login(payload: UserLogin, db: Session = Depends(get_db)):
    """Login with email and password, returns a JWT."""
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not user.hashed_password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    if not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive",
        )

    token = create_access_token(data={"sub": str(user.id)})
    return Token(access_token=token, user=UserOut.model_validate(user))


@router.post("/wallet-login", response_model=Token)
def wallet_login(payload: WalletLogin, db: Session = Depends(get_db)):
    """
    Sign-In with Ethereum (EIP-4361 / SIWE).

    The client constructs a SIWE message, signs it with their wallet, and
    sends both {message, signature}. The server recovers the address from the
    signature, verifies it matches the message, and finds-or-creates the user.
    """
    try:
        wallet_address = verify_siwe_message(payload.message, payload.signature)
    except ValueError as exc:
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

    token = create_access_token(data={"sub": str(user.id)})
    return Token(access_token=token, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)):
    """Return the current authenticated user's info."""
    return current_user


@router.get("/nonce", response_model=MessageResponse)
def get_nonce():
    """Generate a random nonce for SIWE message construction."""
    nonce = generate_nonce()
    return MessageResponse(message="nonce", detail={"nonce": nonce})

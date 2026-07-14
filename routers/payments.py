"""
Payments router: create payment intents, verify on-chain crypto transactions, and history.
Uses the real PaymentVerifier with web3.py for Ethereum/BSC/Polygon/Arbitrum
and Blockstream API for Bitcoin.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from deps import get_current_user
from models import Payment, User
from schemas import MessageResponse, PaymentCreate, PaymentOut, PaymentVerify
from quant.payment_verifier import PaymentVerifier, PLAN_PRICES, CHAIN_CONFIG

router = APIRouter(prefix="/payments", tags=["payments"])


@router.post("/create")
def create_payment(
    payload: PaymentCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Create a payment intent for a subscription plan.

    Returns a Payment record with status 'pending', the amount to pay
    in the requested crypto currency, and the receiving wallet address.
    """
    if payload.plan not in PLAN_PRICES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid plan. Choose from: {list(PLAN_PRICES.keys())}",
        )

    verifier = PaymentVerifier()
    crypto_amount = verifier.get_plan_price(payload.plan, payload.currency)

    if crypto_amount <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to get price for the specified currency",
        )

    if payload.currency.lower() == "usdt" and payload.chain_id not in ("trx", "trc20"):
        raise HTTPException(status_code=400, detail="USDT payments only support TRC-20 (Tron)")

    # Determine receiving address based on currency and chain
    currency = payload.currency.lower()
    receiving_address = ""

    # TRC-20 USDT on Tron
    if currency == "usdt" and payload.chain_id in ("trx", "trc20"):
        receiving_address = settings.TRX_PAYMENT_ADDRESS
    elif currency in ("usdt", "usdc", "eth"):
        receiving_address = settings.PAYMENT_WALLET_ADDRESS
    elif currency == "btc":
        receiving_address = settings.BTC_PAYMENT_ADDRESS
    if not receiving_address:
        raise HTTPException(status_code=503, detail="Payment receiving address is not configured")

    payment = Payment(
        user_id=current_user.id,
        amount=crypto_amount,
        currency=currency,
        tx_hash=None,
        status="pending",
        plan=payload.plan,
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)

    # Return payment with receiving address info (not stored in DB, just for API response)
    payment_dict = PaymentOut.model_validate(payment).model_dump(mode="json")
    payment_dict["receiving_address"] = receiving_address
    payment_dict["usd_price"] = PLAN_PRICES.get(payload.plan, 0)

    return payment_dict


@router.post("/verify", response_model=PaymentOut)
def verify_payment(
    payload: PaymentVerify,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Verify a crypto payment transaction on-chain.

    Uses web3.py to verify Ethereum/ERC-20 transactions and Blockstream API for Bitcoin.
    Checks transaction status, amount, and recipient address.
    """
    payload.tx_hash = payload.tx_hash.strip().lower()
    # Find the user's most recent pending payment in the given currency
    payment = (
        db.query(Payment)
        .filter(
            Payment.user_id == current_user.id,
            Payment.currency == payload.currency.lower(),
            Payment.status == "pending",
        )
        .order_by(Payment.created_at.desc())
        .first()
    )
    if not payment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No pending payment found for this currency",
        )

    if payload.currency.lower() == "usdt" and payload.chain_id not in ("trx", "trc20"):
        raise HTTPException(status_code=400, detail="USDT payments only support TRC-20 (Tron)")
    reused = db.query(Payment).filter(Payment.tx_hash == payload.tx_hash, Payment.id != payment.id).first()
    if reused:
        raise HTTPException(status_code=409, detail="Transaction hash has already been used")

    # Determine expected recipient address
    currency = payload.currency.lower()
    recipient_address = None
    if currency == "usdt" and payload.chain_id in ("trx", "trc20"):
        recipient_address = settings.TRX_PAYMENT_ADDRESS or None
    elif currency in ("usdt", "usdc", "eth"):
        recipient_address = settings.PAYMENT_WALLET_ADDRESS or None
    elif currency == "btc":
        recipient_address = settings.BTC_PAYMENT_ADDRESS or None

    # Verify on-chain using PaymentVerifier
    chain_id = payload.chain_id
    verifier = PaymentVerifier()

    result = verifier.verify_payment(
        tx_hash=payload.tx_hash,
        currency=payload.currency,
        expected_amount=payment.amount,
        recipient_address=recipient_address,
        chain_id=chain_id,
    )

    if not result.get("verified"):
        # Verification failed
        error_msg = result.get("error", "Verification failed")
        issues = result.get("issues", [])
        detail = error_msg
        if issues:
            detail += ": " + "; ".join(issues)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
        )

    # Mark payment as confirmed
    payment.tx_hash = payload.tx_hash
    payment.status = "confirmed"

    # Upgrade user's plan
    current_user.plan = payment.plan

    db.commit()
    db.refresh(payment)
    return payment


@router.get("/history", response_model=list[PaymentOut])
def payment_history(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List the current user's payment history."""
    payments = (
        db.query(Payment)
        .filter(Payment.user_id == current_user.id)
        .order_by(Payment.created_at.desc())
        .all()
    )
    return payments


@router.get("/status/{payment_id}")
def payment_status(
    payment_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Check the real-time status of a payment.

    The auto-verifier scans the blockchain every 30 seconds and auto-confirms
    payments. Frontend polls this endpoint to detect when the payment is confirmed.
    """
    payment = (
        db.query(Payment)
        .filter(Payment.id == payment_id, Payment.user_id == current_user.id)
        .first()
    )
    if not payment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment not found",
        )

    return {
        "id": payment.id,
        "status": payment.status,
        "currency": payment.currency,
        "amount": payment.amount,
        "plan": payment.plan,
        "tx_hash": payment.tx_hash,
        "created_at": payment.created_at.isoformat() if payment.created_at else None,
        "auto_verify": True,
        "poll_interval_seconds": 30,
    }

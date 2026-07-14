"""Hyperliquid read APIs and a private-key-free signed exchange relay."""

import logging
import time
from typing import Any, Literal, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from config import settings
from deps import get_current_user
from models import User
from quant.hyperliquid_trader import HyperliquidTrader

logger = logging.getLogger("quantedge.dex")
router = APIRouter(prefix="/dex", tags=["dex"])

ALLOWED_ACTION_TYPES = {"order", "cancel", "cancelByCloid"}


class Signature(BaseModel):
    r: str = Field(pattern=r"^0x[0-9a-fA-F]{64}$")
    s: str = Field(pattern=r"^0x[0-9a-fA-F]{64}$")
    v: Literal[27, 28]


class SignedExchangeRequest(BaseModel):
    action: dict[str, Any]
    nonce: int
    signature: Signature
    vaultAddress: Optional[str] = Field(None, pattern=r"^0x[0-9a-fA-F]{40}$")
    expiresAfter: Optional[int] = None

    @field_validator("action")
    @classmethod
    def validate_action(cls, action: dict[str, Any]) -> dict[str, Any]:
        if action.get("type") not in ALLOWED_ACTION_TYPES:
            raise ValueError("Only order and cancel actions are supported")
        return action


def _trader(wallet_address: str) -> HyperliquidTrader:
    return HyperliquidTrader(wallet_address=wallet_address, testnet=settings.HYPERLIQUID_TESTNET)


@router.get("/account/{wallet_address}")
def get_account_state(wallet_address: str, current_user: User = Depends(get_current_user)):
    try:
        return {"wallet_address": wallet_address, "account_state": _trader(wallet_address).get_account_state()}
    except Exception as exc:
        logger.error("Hyperliquid account request failed: %s", exc)
        raise HTTPException(status_code=502, detail="Failed to fetch account state") from exc


@router.get("/orders/{wallet_address}")
def get_open_orders(wallet_address: str, current_user: User = Depends(get_current_user)):
    try:
        return {"wallet_address": wallet_address, "orders": _trader(wallet_address).get_open_orders()}
    except Exception as exc:
        logger.error("Hyperliquid orders request failed: %s", exc)
        raise HTTPException(status_code=502, detail="Failed to fetch open orders") from exc


@router.get("/fills/{wallet_address}")
def get_fills(wallet_address: str, current_user: User = Depends(get_current_user)):
    try:
        return {"wallet_address": wallet_address, "fills": _trader(wallet_address).get_user_fills()}
    except Exception as exc:
        logger.error("Hyperliquid fills request failed: %s", exc)
        raise HTTPException(status_code=502, detail="Failed to fetch fills") from exc


@router.get("/prices")
def get_all_prices():
    try:
        mids = _trader("0x0000000000000000000000000000000000000000").get_all_mids()
        return {"source": "hyperliquid", "network": "testnet" if settings.HYPERLIQUID_TESTNET else "mainnet", "prices": mids}
    except Exception as exc:
        logger.error("Hyperliquid prices request failed: %s", exc)
        raise HTTPException(status_code=502, detail="Failed to fetch prices") from exc


@router.post("/exchange")
def relay_signed_exchange(req: SignedExchangeRequest, current_user: User = Depends(get_current_user)):
    """Forward a browser-signed Hyperliquid action; no private key crosses the network."""
    now_ms = int(time.time() * 1000)
    if abs(req.nonce - now_ms) > 5 * 60 * 1000:
        raise HTTPException(status_code=400, detail="Signature nonce is outside the 5-minute window")
    if req.expiresAfter is not None and req.expiresAfter <= now_ms:
        raise HTTPException(status_code=400, detail="Signed action has expired")

    payload = req.model_dump(exclude_none=True)
    payload["signature"] = req.signature.model_dump()
    base_url = settings.HYPERLIQUID_TESTNET_URL if settings.HYPERLIQUID_TESTNET else settings.HYPERLIQUID_API_URL
    try:
        response = requests.post(f"{base_url}/exchange", json=payload, timeout=15)
    except requests.RequestException as exc:
        logger.error("Hyperliquid exchange relay failed: %s", exc)
        raise HTTPException(status_code=502, detail="Hyperliquid is unavailable") from exc
    try:
        body = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Invalid response from Hyperliquid") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=body)
    return body


@router.post("/order", status_code=status.HTTP_410_GONE)
def retired_legacy_order(current_user: User = Depends(get_current_user)):
    raise HTTPException(status_code=410, detail="Private-key order API retired; use /api/dex/exchange with a locally signed action")


@router.delete("/order", status_code=status.HTTP_410_GONE)
def retired_legacy_cancel(current_user: User = Depends(get_current_user)):
    raise HTTPException(status_code=410, detail="Private-key cancel API retired; use /api/dex/exchange with a locally signed action")


@router.get("/funding/{wallet_address}")
def get_funding(wallet_address: str, current_user: User = Depends(get_current_user)):
    try:
        return {"wallet_address": wallet_address, "funding": _trader(wallet_address).get_user_funding()}
    except Exception as exc:
        logger.error("Hyperliquid funding request failed: %s", exc)
        raise HTTPException(status_code=502, detail="Failed to fetch funding history") from exc

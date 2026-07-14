"""
DEX router: Hyperliquid decentralized trading endpoints.
All endpoints require JWT authentication.
User provides their wallet address (read-only) or API wallet private key (trading).
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from deps import get_current_user
from models import User
from quant.hyperliquid_trader import HyperliquidTrader

logger = logging.getLogger("quantedge.dex")

router = APIRouter(prefix="/dex", tags=["dex"])


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class OrderRequest(BaseModel):
    """Request body for placing / cancelling an order."""
    wallet_address: str = Field(..., description="User's EVM wallet address (0x...)")
    private_key: str = Field(..., description="API-wallet private key for signing")
    coin: str = Field(..., description="Asset symbol, e.g. BTC")
    is_buy: bool = Field(True, description="True for buy, False for sell")
    size: float = Field(..., gt=0, description="Order size in base asset units")
    limit_px: Optional[float] = Field(None, description="Limit price (for limit orders)")
    order_type: str = Field("trigger", description="Order type: trigger (market) or limit")
    reduce_only: bool = Field(False, description="If true, only reduce existing position")


class CancelRequest(BaseModel):
    """Request body for cancelling an order."""
    wallet_address: str = Field(..., description="User's EVM wallet address (0x...)")
    private_key: str = Field(..., description="API-wallet private key for signing")
    oid: str = Field(..., description="Order ID to cancel")


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _create_trader(wallet_address: str, private_key: str = None) -> HyperliquidTrader:
    """Create a HyperliquidTrader instance."""
    return HyperliquidTrader(
        wallet_address=wallet_address,
        private_key=private_key,
        testnet=False,
    )


# ---------------------------------------------------------------------------
# 1. GET /api/dex/account/{wallet_address}
# ---------------------------------------------------------------------------

@router.get("/account/{wallet_address}")
def get_account_state(
    wallet_address: str,
    current_user: User = Depends(get_current_user),
):
    """Get account state for a Hyperliquid wallet: margin, positions, balance."""
    try:
        trader = _create_trader(wallet_address)
        state = trader.get_account_state()
        return {
            "wallet_address": wallet_address,
            "account_state": state,
        }
    except Exception as e:
        logger.error("Failed to get Hyperliquid account state for %s: %s", wallet_address, e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch account state: {e}",
        )


# ---------------------------------------------------------------------------
# 2. GET /api/dex/orders/{wallet_address}
# ---------------------------------------------------------------------------

@router.get("/orders/{wallet_address}")
def get_open_orders(
    wallet_address: str,
    current_user: User = Depends(get_current_user),
):
    """Get open orders for a Hyperliquid wallet."""
    try:
        trader = _create_trader(wallet_address)
        orders = trader.get_open_orders()
        return {
            "wallet_address": wallet_address,
            "orders": orders,
        }
    except Exception as e:
        logger.error("Failed to get Hyperliquid open orders for %s: %s", wallet_address, e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch open orders: {e}",
        )


# ---------------------------------------------------------------------------
# 3. GET /api/dex/fills/{wallet_address}
# ---------------------------------------------------------------------------

@router.get("/fills/{wallet_address}")
def get_fills(
    wallet_address: str,
    current_user: User = Depends(get_current_user),
):
    """Get trade history (fills) for a Hyperliquid wallet."""
    try:
        trader = _create_trader(wallet_address)
        fills = trader.get_user_fills()
        return {
            "wallet_address": wallet_address,
            "fills": fills,
        }
    except Exception as e:
        logger.error("Failed to get Hyperliquid fills for %s: %s", wallet_address, e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch fills: {e}",
        )


# ---------------------------------------------------------------------------
# 4. GET /api/dex/prices  (public -- no auth)
# ---------------------------------------------------------------------------

@router.get("/prices")
def get_all_prices():
    """Get all mid prices from Hyperliquid. Public endpoint (no auth)."""
    try:
        trader = _create_trader("0x0000000000000000000000000000000000000000")
        mids = trader.get_all_mids()
        return {
            "source": "hyperliquid",
            "prices": mids,
        }
    except Exception as e:
        logger.error("Failed to get Hyperliquid all mids: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch prices: {e}",
        )


# ---------------------------------------------------------------------------
# 5. POST /api/dex/order
# ---------------------------------------------------------------------------

@router.post("/order")
def place_order(
    req: OrderRequest,
    current_user: User = Depends(get_current_user),
):
    """Place an order on Hyperliquid. Requires private_key in the request body."""
    try:
        trader = _create_trader(req.wallet_address, private_key=req.private_key)
        result = trader.place_order(
            coin=req.coin,
            is_buy=req.is_buy,
            sz=req.size,
            limit_px=req.limit_px,
            order_type=req.order_type,
            reduce_only=req.reduce_only,
        )

        if isinstance(result, dict) and result.get("error"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result,
            )

        return {
            "wallet_address": req.wallet_address,
            "order": result,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to place Hyperliquid order: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to place order: {e}",
        )


# ---------------------------------------------------------------------------
# 6. DELETE /api/dex/order
# ---------------------------------------------------------------------------

@router.delete("/order")
def cancel_order(
    req: CancelRequest,
    current_user: User = Depends(get_current_user),
):
    """Cancel an order on Hyperliquid. Requires private_key in the request body."""
    try:
        trader = _create_trader(req.wallet_address, private_key=req.private_key)
        result = trader.cancel_order(oid=req.oid)

        if isinstance(result, dict) and result.get("error"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result,
            )

        return {
            "wallet_address": req.wallet_address,
            "oid": req.oid,
            "result": result,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to cancel Hyperliquid order: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to cancel order: {e}",
        )


# ---------------------------------------------------------------------------
# 7. GET /api/dex/funding/{wallet_address}
# ---------------------------------------------------------------------------

@router.get("/funding/{wallet_address}")
def get_funding(
    wallet_address: str,
    current_user: User = Depends(get_current_user),
):
    """Get funding payment history for a Hyperliquid wallet."""
    try:
        trader = _create_trader(wallet_address)
        funding = trader.get_user_funding()
        return {
            "wallet_address": wallet_address,
            "funding": funding,
        }
    except Exception as e:
        logger.error("Failed to get Hyperliquid funding for %s: %s", wallet_address, e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch funding history: {e}",
        )

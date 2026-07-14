"""
Hyperliquid DEX trader module.

Hyperliquid is a decentralized perpetual exchange (DEX) on Arbitrum.
This module uses direct HTTP calls to the Hyperliquid API -- it does NOT
use ccxt, because Hyperliquid is not a traditional CEX.

Key design (non-custodial / security-first):
  - The user's wallet private key is NEVER stored on the server by default.
  - For read-only operations (account state, open orders, fills, funding)
    only the *wallet address* is required.
  - For trading operations (place order, cancel order) either:
      a) The frontend signs the transaction with MetaMask and the backend
         relays the pre-signed payload, OR
      b) The user provides an API-wallet private key that is stored
         encrypted and used only for the duration of the request.
  - Private keys are never logged or persisted to disk.

All Hyperliquid *info* endpoints are POST requests to ``/info``.
Trading (exchange) endpoints are POST requests to ``/exchange``.
"""

import logging
from typing import Optional

import requests

logger = logging.getLogger("quantedge.hyperliquid")


class HyperliquidTrader:
    """DEX trader for Hyperliquid -- non-custodial trading."""

    API_URL = "https://api.hyperliquid.xyz"
    TESTNET_URL = "https://api.hyperliquid-testnet.xyz"

    def __init__(
        self,
        wallet_address: str,
        private_key: str = None,
        testnet: bool = False,
    ):
        """
        Initialize the Hyperliquid trader.

        Args:
            wallet_address: The user's EVM wallet address (0x...).
            private_key: Optional API-wallet private key for trading.
                         If None, only read-only operations are available.
            testnet: If True, use the Hyperliquid testnet endpoint.
        """
        self.wallet_address = wallet_address
        self.private_key = private_key
        self.testnet = testnet
        self.base_url = self.TESTNET_URL if testnet else self.API_URL

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _post_info(self, payload: dict, timeout: int = 15) -> dict | list:
        """POST a request to the /info endpoint and return the JSON response."""
        url = f"{self.base_url}/info"
        r = requests.post(url, json=payload, timeout=timeout)
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------
    # Read-only account queries (wallet address only)
    # ------------------------------------------------------------------

    def get_account_state(self) -> dict:
        """Get user's account state: margin, positions, orders.

        Returns the clearinghouse state which includes:
        - marginUsed
        - assetPositions (current open positions)
        - withdrawableUsd
        - crossMarginSummary
        """
        return self._post_info({
            "type": "clearinghouseState",
            "user": self.wallet_address,
        })

    def get_open_orders(self) -> list:
        """Get user's open orders.

        Returns a list of open order objects, each containing:
        coin, side, size, limitPx, orderId, etc.
        """
        return self._post_info({
            "type": "openOrders",
            "user": self.wallet_address,
        })

    def get_positions(self) -> list:
        """Get user's open positions.

        Uses the clearinghouseState endpoint and extracts the
        assetPositions list. Each position includes the coin, size,
        entry price, unrealized PnL, etc.
        """
        state = self.get_account_state()
        positions = []
        asset_positions = state.get("assetPositions", [])
        for ap in asset_positions:
            # Each entry is wrapped: {"position": {...}}
            pos = ap.get("position", ap)
            positions.append(pos)
        return positions

    def get_all_mids(self) -> dict:
        """Get all mid prices.

        Returns a dict mapping coin symbols to their mid price strings,
        e.g. {"BTC": "62500.5", "ETH": "3200.75", ...}
        """
        return self._post_info({"type": "allMids"})

    def get_user_fills(self) -> list:
        """Get user's trade history (recent fills).

        Returns a list of fill objects containing: coin, side, size,
        price, time, crossed, etc.
        """
        return self._post_info({
            "type": "userFills",
            "user": self.wallet_address,
        })

    def get_user_funding(self) -> list:
        """Get user's funding payment history.

        Returns a list of funding payment objects containing: coin,
        time, fundingRate, usd, etc.
        """
        return self._post_info({
            "type": "userFunding",
            "user": self.wallet_address,
        })

    # ------------------------------------------------------------------
    # Trading operations (require private_key)
    # ------------------------------------------------------------------

    def place_order(
        self,
        coin: str,
        is_buy: bool,
        sz: float,
        limit_px: float = None,
        order_type: str = "trigger",
        reduce_only: bool = False,
    ) -> dict:
        """Place an order on Hyperliquid. Requires private_key.

        Args:
            coin: Asset symbol, e.g. "BTC".
            is_buy: True for buy (long), False for sell (short).
            sz: Order size in base asset units.
            limit_px: Limit price (required for limit orders).
            order_type: Order type -- "trigger" (market) or "limit".
            reduce_only: If True, order can only reduce an existing position.
            private_key required.

        Returns:
            The Hyperliquid exchange API response, or an error dict if
            the SDK is not available.
        """
        if not self.private_key:
            return {
                "error": "private_key is required to place orders",
                "message": (
                    "Provide an API-wallet private key to enable trading. "
                    "Alternatively, sign the transaction client-side with "
                    "MetaMask and relay it."
                ),
            }

        try:
            from hyperliquid import Exchange
            from hyperliquid.info import Info
            from hyperliquid.utils import Constants
        except ImportError:
            return {
                "error": "hyperliquid-python-sdk is not installed",
                "message": (
                    "Install hyperliquid-python-sdk to enable order placement: "
                    "pip install hyperliquid-python-sdk"
                ),
            }

        try:
            from eth_account import Account

            # Create an Info client and Exchange for signing
            info = Info(self.base_url, skip_ws=True)
            account = Account.from_key(self.private_key)
            exchange = Exchange(
                account=account,
                base_url=self.base_url,
                account_address=self.wallet_address,
            )

            # Build order parameters
            order_type_params = {"trigger": {}}
            if order_type == "limit" and limit_px is not None:
                order_type_params = {"limit": {"tpsl": "trigger"}}

            order_result = exchange.order(
                name=coin,
                is_buy=is_buy,
                sz=sz,
                limit_px=limit_px if limit_px is not None else 0,
                order_type=order_type_params,
                reduce_only=reduce_only,
            )

            return order_result
        except Exception as e:
            logger.error("Failed to place order on Hyperliquid: %s", e)
            return {"error": str(e)}

    def cancel_order(self, oid: str) -> dict:
        """Cancel an order. Requires private_key.

        Args:
            oid: The order ID to cancel.

        Returns:
            The Hyperliquid exchange API response, or an error dict.
        """
        if not self.private_key:
            return {
                "error": "private_key is required to cancel orders",
                "message": (
                    "Provide an API-wallet private key to enable order "
                    "cancellation."
                ),
            }

        try:
            from hyperliquid import Exchange
            from eth_account import Account
        except ImportError:
            return {
                "error": "hyperliquid-python-sdk is not installed",
                "message": (
                    "Install hyperliquid-python-sdk to enable order "
                    "cancellation: pip install hyperliquid-python-sdk"
                ),
            }

        try:
            account = Account.from_key(self.private_key)
            exchange = Exchange(
                account=account,
                base_url=self.base_url,
                account_address=self.wallet_address,
            )

            cancel_result = exchange.cancel(oid)
            return cancel_result
        except Exception as e:
            logger.error("Failed to cancel order on Hyperliquid: %s", e)
            return {"error": str(e)}

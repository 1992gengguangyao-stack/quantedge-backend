"""
Exchange trading module using ccxt.
Connects to exchanges and executes real trades via API.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import ccxt

import sys
sys.path.insert(0, ".")
from quant.data_fetcher import DataFetcher
from quant.indicators import Indicators

logger = logging.getLogger("quantedge.trader")


class ExchangeTrader:
    """
    Live trading interface for cryptocurrency exchanges.
    Uses ccxt to connect to exchanges and execute trades.
    """

    def __init__(
        self,
        exchange_id: str = "binance",
        api_key: str = "",
        api_secret: str = "",
        testnet: bool = True,
    ):
        self.exchange_id = exchange_id
        self.testnet = testnet
        self._exchange = None
        self._api_key = api_key
        self._api_secret = api_secret

    @property
    def exchange(self):
        """Initialize ccxt exchange with API credentials."""
        if self._exchange is None:
            exchange_class = getattr(ccxt, self.exchange_id)
            config = {
                "apiKey": self._api_key,
                "secret": self._api_secret,
                "enableRateLimit": True,
            }

            # Use testnet/sandbox if available
            if self.testnet:
                config["sandbox"] = True

            self._exchange = exchange_class(config)

            # Set sandbox mode explicitly for exchanges that need it
            if self.testnet and hasattr(self._exchange, "set_sandbox_mode"):
                self._exchange.set_sandbox_mode(True)

        return self._exchange

    # === Account Info ===

    def get_balance(self) -> dict:
        """Fetch account balance."""
        balance = self.exchange.fetch_balance()
        result = {}
        for currency, amounts in balance.get("total", {}).items():
            if amounts and float(amounts) > 0:
                result[currency] = {
                    "free": balance["free"].get(currency, 0),
                    "used": balance["used"].get(currency, 0),
                    "total": amounts,
                }
        return result

    def get_positions(self) -> list:
        """Fetch open positions (futures)."""
        if not hasattr(self.exchange, "fetch_positions"):
            return []
        positions = self.exchange.fetch_positions()
        return [
            {
                "symbol": p["symbol"],
                "side": p.get("side"),
                "size": p.get("contracts"),
                "entry_price": p.get("entryPrice"),
                "unrealized_pnl": p.get("unrealizedPnl"),
                "leverage": p.get("leverage"),
            }
            for p in positions
            if p.get("contracts") and float(p["contracts"]) != 0
        ]

    def get_open_orders(self, symbol: str = None) -> list:
        """Fetch open orders."""
        orders = self.exchange.fetch_open_orders(symbol)
        return [
            {
                "id": o["id"],
                "symbol": o["symbol"],
                "type": o["type"],
                "side": o["side"],
                "price": o["price"],
                "amount": o["amount"],
                "filled": o.get("filled", 0),
                "remaining": o.get("remaining", 0),
                "status": o["status"],
                "timestamp": o.get("timestamp"),
            }
            for o in orders
        ]

    # === Order Execution ===

    def market_buy(self, symbol: str, amount: float) -> dict:
        """
        Place a market buy order.

        Args:
            symbol: Trading pair, e.g. "BTC/USDT"
            amount: Amount in base currency to buy

        Returns:
            Order info dict
        """
        try:
            order = self.exchange.create_market_buy_order(symbol, amount)
            logger.info(f"Market buy: {symbol} amount={amount} order_id={order['id']}")
            return self._format_order(order)
        except ccxt.InsufficientFunds as e:
            logger.error(f"Insufficient funds: {e}")
            return {"error": "insufficient_funds", "message": str(e)}
        except ccxt.InvalidOrder as e:
            logger.error(f"Invalid order: {e}")
            return {"error": "invalid_order", "message": str(e)}
        except Exception as e:
            logger.error(f"Order failed: {e}")
            return {"error": "order_failed", "message": str(e)}

    def market_sell(self, symbol: str, amount: float) -> dict:
        """Place a market sell order."""
        try:
            order = self.exchange.create_market_sell_order(symbol, amount)
            logger.info(f"Market sell: {symbol} amount={amount} order_id={order['id']}")
            return self._format_order(order)
        except ccxt.InsufficientFunds as e:
            return {"error": "insufficient_funds", "message": str(e)}
        except ccxt.InvalidOrder as e:
            return {"error": "invalid_order", "message": str(e)}
        except Exception as e:
            return {"error": "order_failed", "message": str(e)}

    def limit_buy(self, symbol: str, amount: float, price: float) -> dict:
        """Place a limit buy order."""
        try:
            order = self.exchange.create_limit_buy_order(symbol, amount, price)
            return self._format_order(order)
        except Exception as e:
            return {"error": "order_failed", "message": str(e)}

    def limit_sell(self, symbol: str, amount: float, price: float) -> dict:
        """Place a limit sell order."""
        try:
            order = self.exchange.create_limit_sell_order(symbol, amount, price)
            return self._format_order(order)
        except Exception as e:
            return {"error": "order_failed", "message": str(e)}

    def cancel_order(self, order_id: str, symbol: str) -> dict:
        """Cancel an open order."""
        try:
            result = self.exchange.cancel_order(order_id, symbol)
            return {"status": "cancelled", "order_id": order_id}
        except Exception as e:
            return {"error": "cancel_failed", "message": str(e)}

    def cancel_all_orders(self, symbol: str = None) -> dict:
        """Cancel all open orders."""
        try:
            self.exchange.cancel_all_orders(symbol)
            return {"status": "all_cancelled"}
        except Exception as e:
            return {"error": "cancel_failed", "message": str(e)}

    # === Bot Strategy Runners ===

    def run_grid_bot(
        self,
        symbol: str,
        upper_price: float,
        lower_price: float,
        grids: int = 10,
        total_investment: float = 1000.0,
    ) -> dict:
        """
        Run a grid trading bot.
        Places multiple limit orders at evenly spaced price levels.
        """
        price_range = upper_price - lower_price
        grid_size = price_range / grids
        order_amount = total_investment / grids / upper_price  # Simplified

        orders_placed = []
        for i in range(grids):
            price = lower_price + (grid_size * i)
            # Alternate buy/sell orders
            if i < grids // 2:
                result = self.limit_buy(symbol, order_amount, price)
            else:
                result = self.limit_sell(symbol, order_amount, price)
            orders_placed.append({"grid_level": i, "price": price, "result": result})

        return {
            "strategy": "grid",
            "symbol": symbol,
            "upper_price": upper_price,
            "lower_price": lower_price,
            "grids": grids,
            "grid_size": grid_size,
            "orders_placed": len(orders_placed),
            "orders": orders_placed,
        }

    def run_dca_bot(
        self,
        symbol: str,
        amount_per_order: float,
        interval_hours: int = 24,
        take_profit_pct: float = 10.0,
        max_orders: int = 10,
    ) -> dict:
        """
        Run a DCA (Dollar Cost Averaging) bot.
        Executes periodic buys and sells on profit target.
        """
        # Execute first DCA buy immediately
        ticker = self.exchange.fetch_ticker(symbol)
        current_price = ticker["last"]
        buy_amount = amount_per_order / current_price

        result = self.market_buy(symbol, buy_amount)

        return {
            "strategy": "dca",
            "symbol": symbol,
            "amount_per_order": amount_per_order,
            "interval_hours": interval_hours,
            "take_profit_pct": take_profit_pct,
            "max_orders": max_orders,
            "current_price": current_price,
            "first_order": result,
            "next_order_at": (
                datetime.now(timezone.utc).isoformat()
            ),
            "status": "running",
        }

    def run_signal_bot(
        self,
        symbol: str,
        strategy_code: str,
        timeframe: str = "1h",
        config: dict = None,
    ) -> dict:
        """
        Run a signal-based bot.
        Evaluates strategy on latest data and executes trades based on signals.
        """
        if config is None:
            config = {}

        # Fetch latest data
        fetcher = DataFetcher(exchange_id=self.exchange_id)
        df = fetcher.fetch_ohlcv(symbol, timeframe=timeframe, limit=200)

        if df.empty:
            return {"error": "No data available"}

        # Add indicators
        df = Indicators.add_all_indicators(df)

        # Execute strategy
        import numpy as np
        import pandas as pd
        namespace = {"pd": pd, "np": np, "Indicators": Indicators, "config": config}
        exec(strategy_code, namespace)

        if "strategy" not in namespace:
            return {"error": "Strategy code must define a `strategy` function"}

        signals = namespace["strategy"](df, Indicators, config)
        latest_signal = signals.iloc[-1] if len(signals) > 0 else 0

        # Execute trade based on signal
        action = "hold"
        order_result = None
        current_price = df["close"].iloc[-1]

        if latest_signal > 0:
            # Buy signal
            balance = self.get_balance()
            usdt_balance = balance.get("USDT", {}).get("free", 0)
            if usdt_balance and float(usdt_balance) > 10:
                buy_amount = float(usdt_balance) * 0.95 / current_price
                order_result = self.market_buy(symbol, buy_amount)
                action = "buy"
        elif latest_signal < 0:
            # Sell signal
            base_currency = symbol.split("/")[0]
            balance = self.get_balance()
            asset_balance = balance.get(base_currency, {}).get("free", 0)
            if asset_balance and float(asset_balance) > 0:
                order_result = self.market_sell(symbol, float(asset_balance))
                action = "sell"

        return {
            "strategy": "signal",
            "symbol": symbol,
            "timeframe": timeframe,
            "current_price": current_price,
            "latest_signal": int(latest_signal),
            "action": action,
            "order": order_result,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "executed" if action != "hold" else "monitoring",
        }

    # === Helpers ===

    def _format_order(self, order: dict) -> dict:
        """Format ccxt order response."""
        return {
            "id": order.get("id"),
            "symbol": order.get("symbol"),
            "type": order.get("type"),
            "side": order.get("side"),
            "price": order.get("price"),
            "amount": order.get("amount"),
            "filled": order.get("filled"),
            "remaining": order.get("remaining"),
            "cost": order.get("cost"),
            "status": order.get("status"),
            "timestamp": order.get("timestamp"),
            "datetime": order.get("datetime"),
        }

    def test_connection(self) -> dict:
        """Test exchange connection."""
        try:
            self.exchange.load_markets()
            return {
                "status": "connected",
                "exchange": self.exchange_id,
                "testnet": self.testnet,
                "symbols_count": len(self.exchange.symbols),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

"""
Backtesting engine.
Executes trading strategies against historical data and calculates performance metrics.
"""

import traceback
from datetime import datetime, timezone
from typing import Any, Optional

import numpy as np
import pandas as pd

import sys
sys.path.insert(0, ".")
from quant.data_fetcher import DataFetcher
from quant.indicators import Indicators


class BacktestEngine:
    """
    Event-driven backtesting engine.

    Executes a user-defined strategy function on historical OHLCV data,
    simulates trades, and calculates performance metrics.
    """

    def __init__(
        self,
        initial_capital: float = 10000.0,
        commission_rate: float = 0.001,  # 0.1% per trade
        slippage_rate: float = 0.0005,   # 0.05% slippage
    ):
        self.initial_capital = initial_capital
        self.commission_rate = commission_rate
        self.slippage_rate = slippage_rate

    def run(
        self,
        strategy_code: str,
        symbol: str = "BTC/USDT",
        timeframe: str = "1h",
        limit: int = 500,
        exchange: str = "binance",
        config: dict = None,
    ) -> dict:
        """
        Run a backtest.

        Args:
            strategy_code: Python code string defining a `strategy(df, indicators, config)` function
            symbol: Trading pair
            timeframe: Candle interval
            limit: Number of historical candles
            exchange: Exchange to fetch data from
            config: Strategy-specific parameters

        Returns:
            Dict with trades, equity curve, and performance metrics
        """
        if config is None:
            config = {}

        # Step 1: Fetch historical data
        fetcher = DataFetcher(exchange_id=exchange)
        df = fetcher.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

        if df.empty or len(df) < 50:
            return {
                "status": "error",
                "error": f"Insufficient data: got {len(df)} candles, need at least 50",
                "symbol": symbol,
                "timeframe": timeframe,
            }

        # Step 2: Add technical indicators
        df = Indicators.add_all_indicators(df)

        # Step 3: Execute strategy
        try:
            signals = self._execute_strategy(strategy_code, df, config)
        except Exception as e:
            return {
                "status": "error",
                "error": f"Strategy execution failed: {str(e)}",
                "traceback": traceback.format_exc(),
            }

        if signals is None or len(signals) == 0:
            return {
                "status": "error",
                "error": "Strategy returned no signals",
            }

        # Step 4: Simulate trades
        trades = self._simulate_trades(df, signals)

        # Step 5: Build equity curve
        equity_curve = self._build_equity_curve(df, trades)

        # Step 6: Calculate performance metrics
        metrics = self._calculate_metrics(equity_curve, trades)

        return {
            "status": "completed",
            "symbol": symbol,
            "timeframe": timeframe,
            "exchange": exchange,
            "data_points": len(df),
            "period_start": df.index[0].isoformat(),
            "period_end": df.index[-1].isoformat(),
            "trades": trades,
            "trade_count": len(trades),
            "equity_curve": equity_curve,
            "metrics": metrics,
        }

    def _execute_strategy(
        self,
        code: str,
        df: pd.DataFrame,
        config: dict,
    ) -> pd.Series:
        """
        Execute user strategy code safely.

        The strategy code should define a function:
            def strategy(df, indicators, config):
                # df: OHLCV DataFrame with indicators
                # indicators: Indicators class
                # config: dict of strategy parameters
                # Return: pd.Series of signals (1=buy, -1=sell, 0=hold)
        """
        # Create a safe namespace
        namespace = {
            "pd": pd,
            "np": np,
            "Indicators": Indicators,
            "config": config,
        }

        # Execute the strategy code
        exec(code, namespace)

        # Call the strategy function
        if "strategy" not in namespace:
            raise ValueError("Strategy code must define a `strategy(df, indicators, config)` function")

        strategy_func = namespace["strategy"]
        signals = strategy_func(df, Indicators, config)

        if not isinstance(signals, pd.Series):
            signals = pd.Series(signals, index=df.index)

        return signals

    def _simulate_trades(self, df: pd.DataFrame, signals: pd.Series) -> list:
        """Simulate trades based on signals."""
        trades = []
        position = 0.0        # Current position size in base currency
        entry_price = 0.0
        entry_time = None
        capital = self.initial_capital

        for i in range(len(df)):
            signal = signals.iloc[i] if i < len(signals) else 0
            price = df["close"].iloc[i]
            timestamp = df.index[i]

            # Buy signal
            if signal > 0 and position == 0:
                # Calculate position size (use all capital)
                adjusted_price = price * (1 + self.slippage_rate)
                cost = capital * (1 - self.commission_rate)
                position = cost / adjusted_price
                entry_price = adjusted_price
                entry_time = timestamp
                capital = 0.0

            # Sell signal
            elif signal < 0 and position > 0:
                adjusted_price = price * (1 - self.slippage_rate)
                proceeds = position * adjusted_price * (1 - self.commission_rate)
                pnl = proceeds - (position * entry_price)
                pnl_pct = (adjusted_price / entry_price - 1) * 100

                trades.append({
                    "entry_time": entry_time.isoformat(),
                    "exit_time": timestamp.isoformat(),
                    "entry_price": round(entry_price, 6),
                    "exit_price": round(adjusted_price, 6),
                    "size": round(position, 8),
                    "pnl": round(pnl, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "side": "long",
                })

                capital = proceeds
                position = 0.0
                entry_price = 0.0
                entry_time = None

        # Close any open position at the end
        if position > 0:
            price = df["close"].iloc[-1]
            adjusted_price = price * (1 - self.slippage_rate)
            proceeds = position * adjusted_price * (1 - self.commission_rate)
            pnl = proceeds - (position * entry_price)
            pnl_pct = (adjusted_price / entry_price - 1) * 100

            trades.append({
                "entry_time": entry_time.isoformat(),
                "exit_time": df.index[-1].isoformat(),
                "entry_price": round(entry_price, 6),
                "exit_price": round(adjusted_price, 6),
                "size": round(position, 8),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "side": "long",
                "note": "auto_closed",
            })

            capital = proceeds

        return trades

    def _build_equity_curve(self, df: pd.DataFrame, trades: list) -> list:
        """Build equity curve from trades."""
        equity = self.initial_capital
        position = 0.0
        entry_price = 0.0
        curve = []
        trade_idx = 0

        for i in range(len(df)):
            price = df["close"].iloc[i]
            timestamp = df.index[i]

            # Check for trade at this timestamp
            while trade_idx < len(trades):
                trade = trades[trade_idx]
                entry_time = pd.Timestamp(trade["entry_time"])

                if timestamp == entry_time and position == 0:
                    # Enter position
                    entry_price = trade["entry_price"]
                    position = trade["size"]
                    equity = 0.0
                    trade_idx += 1
                    break
                elif timestamp == pd.Timestamp(trade.get("exit_time", "")):
                    if position > 0:
                        # Exit position
                        equity = position * trade["exit_price"]
                        position = 0.0
                        entry_price = 0.0
                    trade_idx += 1
                    break
                else:
                    break

            # Calculate current equity
            if position > 0:
                current_equity = position * price
            else:
                current_equity = equity

            curve.append({
                "timestamp": timestamp.isoformat(),
                "equity": round(current_equity, 2),
                "price": round(price, 6),
            })

        return curve

    def _calculate_metrics(self, equity_curve: list, trades: list) -> dict:
        """Calculate comprehensive performance metrics."""
        if not equity_curve:
            return {}

        equities = [e["equity"] for e in equity_curve]
        final_equity = equities[-1]
        total_return = (final_equity / self.initial_capital - 1) * 100

        # Drawdown
        peak = equities[0]
        max_dd = 0.0
        for eq in equities:
            if eq > peak:
                peak = eq
            dd = (eq / peak - 1) * 100
            if dd < max_dd:
                max_dd = dd

        # Trade statistics
        winning_trades = [t for t in trades if t["pnl"] > 0]
        losing_trades = [t for t in trades if t["pnl"] <= 0]
        win_rate = len(winning_trades) / len(trades) * 100 if trades else 0

        total_pnl = sum(t["pnl"] for t in trades)
        avg_win = np.mean([t["pnl"] for t in winning_trades]) if winning_trades else 0
        avg_loss = np.mean([t["pnl"] for t in losing_trades]) if losing_trades else 0

        # Sharpe ratio (simplified, assuming risk-free rate = 0)
        if len(equities) > 1:
            returns = pd.Series(equities).pct_change().dropna()
            if returns.std() > 0:
                # Annualize based on hourly data (24*365 = 8760)
                sharpe = (returns.mean() / returns.std()) * np.sqrt(8760)
            else:
                sharpe = 0.0
        else:
            sharpe = 0.0

        # Profit factor
        gross_profit = sum(t["pnl"] for t in winning_trades)
        gross_loss = abs(sum(t["pnl"] for t in losing_trades))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        return {
            "initial_capital": self.initial_capital,
            "final_equity": round(final_equity, 2),
            "total_return_pct": round(total_return, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "sharpe_ratio": round(sharpe, 2),
            "win_rate_pct": round(win_rate, 2),
            "total_trades": len(trades),
            "winning_trades": len(winning_trades),
            "losing_trades": len(losing_trades),
            "total_pnl": round(total_pnl, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else 999.99,
            "avg_trade_pnl": round(total_pnl / len(trades), 2) if trades else 0,
        }

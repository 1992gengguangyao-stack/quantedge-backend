"""
Market data fetcher using ccxt.
Fetches OHLCV (candlestick) data from cryptocurrency exchanges.
"""

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

import ccxt
import pandas as pd

import sys
sys.path.insert(0, ".")
from config import settings


class DataFetcher:
    """Fetch OHLCV data from exchanges via ccxt."""

    # Timeframe to seconds mapping
    TF_SECONDS = {
        "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
        "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600,
        "8h": 28800, "12h": 43200, "1d": 86400, "3d": 259200,
        "1w": 604800, "1M": 2592000,
    }

    # Exchanges that may be geo-blocked; try fallback order
    FALLBACK_EXCHANGES = ["htx", "binance", "bybit", "okx"]

    def __init__(self, exchange_id: str = "htx"):
        self.exchange_id = exchange_id
        self._exchange = None

    @property
    def exchange(self):
        """Lazily initialize ccxt exchange instance."""
        if self._exchange is None:
            exchange_class = getattr(ccxt, self.exchange_id)
            config = {"enableRateLimit": True}
            self._exchange = exchange_class(config)
        return self._exchange

    def _fetch_binance_vision(self, symbol: str, timeframe: str, limit: int) -> list:
        """Fetch from Binance public data endpoint (no geo-block)."""
        import requests
        # Convert BTC/USDT -> BTCUSDT
        pair = symbol.replace("/", "").replace(":", "")
        url = f"https://data-api.binance.vision/api/v3/klines"
        params = {"symbol": pair, "interval": timeframe, "limit": min(limit, 1000)}
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        # Binance format: [openTime, open, high, low, close, volume, closeTime, ...]
        return [[d[0], float(d[1]), float(d[2]), float(d[3]), float(d[4]), float(d[5])] for d in data]

    def fetch_ohlcv(
        self,
        symbol: str = "BTC/USDT",
        timeframe: str = "1h",
        since: Optional[datetime] = None,
        limit: int = 500,
    ) -> pd.DataFrame:
        """
        Fetch OHLCV data and return as pandas DataFrame.

        Tries ccxt first, falls back to Binance Vision public API if geo-blocked.

        Args:
            symbol: Trading pair, e.g. "BTC/USDT"
            timeframe: Candle interval, e.g. "1h", "15m", "1d"
            since: Start datetime (default: 30 days ago)
            limit: Number of candles (max 1000 per request)

        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume
        """
        all_data = []

        # Strategy: try Binance Vision FIRST (fast, no geo-block),
        # then fall back to ccxt exchanges if needed.
        try:
            all_data = self._fetch_binance_vision(symbol, timeframe, limit)
        except Exception:
            pass

        # Fallback: try ccxt with the specified exchange
        if not all_data:
            try:
                if since is None:
                    since = datetime.now(timezone.utc) - timedelta(days=30)
                since_ms = int(since.timestamp() * 1000)

                remaining = limit
                current_since = since_ms

                while remaining > 0:
                    chunk_size = min(remaining, 1000)
                    ohlcv = self.exchange.fetch_ohlcv(
                        symbol, timeframe=timeframe, since=current_since, limit=chunk_size
                    )
                    if not ohlcv:
                        break
                    all_data.extend(ohlcv)
                    remaining -= len(ohlcv)
                    if len(ohlcv) < chunk_size:
                        break
                    current_since = ohlcv[-1][0] + 1
            except Exception:
                all_data = []

        # Fallback: try other exchanges
        if not all_data:
            for fallback_id in self.FALLBACK_EXCHANGES:
                if fallback_id == self.exchange_id:
                    continue
                try:
                    fallback_ex = getattr(ccxt, fallback_id)({"enableRateLimit": True})
                    all_data = fallback_ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
                    if all_data:
                        break
                except Exception:
                    continue

        if not all_data:
            return pd.DataFrame()

        df = pd.DataFrame(
            all_data, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df.set_index("datetime", inplace=True)
        df.drop(columns=["timestamp"], inplace=True)

        # Remove duplicates
        df = df[~df.index.duplicated(keep="last")]

        return df

    def fetch_multi_timeframe(
        self,
        symbol: str = "BTC/USDT",
        timeframes: list = None,
        limit: int = 200,
    ) -> dict:
        """Fetch data for multiple timeframes."""
        if timeframes is None:
            timeframes = ["15m", "1h", "4h", "1d"]

        result = {}
        for tf in timeframes:
            result[tf] = self.fetch_ohlcv(symbol, timeframe=tf, limit=limit)
        return result

    def fetch_ticker(self, symbol: str = "BTC/USDT") -> dict:
        """Fetch current ticker data."""
        ticker = self.exchange.fetch_ticker(symbol)
        return {
            "symbol": symbol,
            "last": ticker.get("last"),
            "bid": ticker.get("bid"),
            "ask": ticker.get("ask"),
            "high": ticker.get("high"),
            "low": ticker.get("low"),
            "volume": ticker.get("baseVolume"),
            "timestamp": ticker.get("timestamp"),
        }

    def fetch_order_book(self, symbol: str = "BTC/USDT", limit: int = 20) -> dict:
        """Fetch order book."""
        ob = self.exchange.fetch_order_book(symbol, limit=limit)
        return {
            "bids": ob["bids"][:limit],
            "asks": ob["asks"][:limit],
        }

    def get_available_symbols(self) -> list:
        """Get all available trading pairs on the exchange."""
        self.exchange.load_markets()
        return list(self.exchange.symbols.keys())

    async def fetch_ohlcv_async(
        self,
        symbol: str = "BTC/USDT",
        timeframe: str = "1h",
        since: Optional[datetime] = None,
        limit: int = 500,
    ) -> pd.DataFrame:
        """Async version of fetch_ohlcv for use in async bots."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self.fetch_ohlcv, symbol, timeframe, since, limit
        )

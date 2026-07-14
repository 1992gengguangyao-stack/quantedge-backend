"""
Market data router: public endpoints for OHLCV, ticker, symbols, and
multi-timeframe overview.

All endpoints are public (no authentication required).
Uses DataFetcher which tries ccxt first and falls back to the Binance Vision
public API when the primary exchange is unavailable or geo-blocked.
"""

import logging
import math
from datetime import datetime, timezone, timedelta
from typing import Optional

import pandas as pd
from fastapi import APIRouter, HTTPException, Query, status

from quant.data_fetcher import DataFetcher

logger = logging.getLogger("quantedge.market")

router = APIRouter(prefix="/market", tags=["market"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUPPORTED_TIMEFRAMES = {"1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"}

# Timeframes used for the multi-timeframe overview
MULTI_TF = ["15m", "1h", "4h", "1d"]

# Preset popular trading pairs (fallback when exchange markets are unavailable)
POPULAR_SYMBOLS = [
    "BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT",
    "ADA/USDT", "DOGE/USDT", "AVAX/USDT", "DOT/USDT", "MATIC/USDT",
    "LINK/USDT", "TRX/USDT", "LTC/USDT", "BCH/USDT", "ATOM/USDT",
    "UNI/USDT", "NEAR/USDT", "APT/USDT", "FIL/USDT", "ARB/USDT",
    "OP/USDT", "INJ/USDT", "SUI/USDT", "SEI/USDT", "TIA/USDT",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(val) -> Optional[float]:
    """Convert a value to float, returning None for NaN / None."""
    if val is None:
        return None
    f = float(val)
    if math.isnan(f):
        return None
    return f


def _df_to_candles(df: pd.DataFrame) -> list:
    """Convert an OHLCV DataFrame to a list of candle dicts with Unix timestamps (seconds)."""
    if df is None or df.empty:
        return []

    df = df.dropna()
    df_reset = df.reset_index()
    datetime_col = df_reset.columns[0]  # "datetime"

    candles = []
    for _, row in df_reset.iterrows():
        ts = row[datetime_col]
        time_val = int(ts.timestamp()) if hasattr(ts, "timestamp") else int(ts)
        candles.append({
            "time": time_val,
            "open": _safe_float(row["open"]),
            "high": _safe_float(row["high"]),
            "low": _safe_float(row["low"]),
            "close": _safe_float(row["close"]),
            "volume": _safe_float(row["volume"]),
        })
    return candles


def _compute_since(timeframe: str, limit: int) -> datetime:
    """
    Calculate a `since` datetime so that fetch_ohlcv returns the most *recent*
    candles rather than data from 30 days ago (the DataFetcher default).
    """
    tf_seconds = DataFetcher.TF_SECONDS.get(timeframe, 3600)
    # Add a small buffer so we don't miss the latest in-progress candle
    return datetime.now(timezone.utc) - timedelta(seconds=tf_seconds * (limit + 5))


def _fetch_binance_ticker_24hr(symbol: str) -> dict:
    """Fetch 24h ticker stats from Binance Vision public API (no geo-block)."""
    import requests

    pair = symbol.replace("/", "").replace(":", "")
    url = "https://data-api.binance.vision/api/v3/ticker/24hr"
    r = requests.get(url, params={"symbol": pair}, timeout=15)
    r.raise_for_status()
    data = r.json()
    return {
        "symbol": symbol,
        "last_price": float(data["lastPrice"]),
        "change_pct": round(float(data["priceChangePercent"]), 2),
        "high": float(data["highPrice"]),
        "low": float(data["lowPrice"]),
        "volume": float(data["volume"]),
        "timestamp": int(data["closeTime"] // 1000),
    }


def _fetch_hyperliquid_ticker(symbol: str) -> dict:
    """Fetch ticker from Hyperliquid DEX public API (no geo-block, no auth).

    Uses the ``allMids`` endpoint for a quick mid price and the
    ``metaAndAssetCtxs`` endpoint for 24h volume / previous-day price.
    """
    import time
    import requests

    # Convert "BTC/USDT" -> "BTC"
    coin = symbol.split("/")[0]
    url = "https://api.hyperliquid.xyz/info"

    # --- Step 1: metaAndAssetCtxs for 24h stats ---------------------------
    mark_px = None
    prev_day_px = None
    day_ntl_vlm = None

    try:
        r = requests.post(
            url, json={"type": "metaAndAssetCtxs"}, timeout=15
        )
        r.raise_for_status()
        meta_data = r.json()
        # meta_data is [metadata, assetCtxs]
        if isinstance(meta_data, list) and len(meta_data) == 2:
            metadata = meta_data[0]
            asset_ctxs = meta_data[1]
            universe = metadata.get("universe", [])
            # Find the index of our coin in the universe
            coin_idx = None
            for idx, asset in enumerate(universe):
                if asset.get("name") == coin:
                    coin_idx = idx
                    break
            if coin_idx is not None and coin_idx < len(asset_ctxs):
                ctx = asset_ctxs[coin_idx]
                mark_px = ctx.get("markPx")
                prev_day_px = ctx.get("prevDayPx")
                day_ntl_vlm = ctx.get("dayNtlVlm")
    except Exception:
        pass

    # --- Step 2: allMids fallback for price -------------------------------
    if mark_px is None:
        r = requests.post(url, json={"type": "allMids"}, timeout=15)
        r.raise_for_status()
        mids = r.json()
        mark_px = mids.get(coin)

    if mark_px is None:
        raise ValueError(f"Coin '{coin}' not found on Hyperliquid")

    last_price = float(mark_px)

    # Calculate 24h change percentage from prevDayPx
    change_pct = None
    if prev_day_px is not None:
        prev = float(prev_day_px)
        if prev > 0:
            change_pct = round((last_price - prev) / prev * 100, 2)

    volume = float(day_ntl_vlm) if day_ntl_vlm is not None else None

    return {
        "symbol": symbol,
        "last_price": last_price,
        "change_pct": change_pct,
        "high": None,
        "low": None,
        "volume": volume,
        "timestamp": int(time.time()),
    }


# ---------------------------------------------------------------------------
# 1. GET /api/market/ohlcv
# ---------------------------------------------------------------------------

@router.get("/ohlcv")
def get_ohlcv(
    symbol: str = Query("BTC/USDT", description="Trading pair, e.g. BTC/USDT"),
    timeframe: str = Query("1h", description="Candle interval: 1m/5m/15m/30m/1h/4h/1d/1w"),
    limit: int = Query(200, ge=1, le=1000, description="Number of candles (max 1000)"),
):
    """
    Fetch OHLCV (candlestick) data from the exchange.

    Returns ``time`` as a Unix timestamp (seconds) for frontend chart rendering.
    Public endpoint -- no authentication required.
    """
    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported timeframe '{timeframe}'. "
                f"Supported values: {sorted(SUPPORTED_TIMEFRAMES)}"
            ),
        )

    fetcher = DataFetcher()
    since = _compute_since(timeframe, limit)

    try:
        df = fetcher.fetch_ohlcv(
            symbol=symbol, timeframe=timeframe, since=since, limit=limit
        )
    except Exception as e:
        logger.error("OHLCV fetch error for %s %s: %s", symbol, timeframe, e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch OHLCV data: {e}",
        )

    candles = _df_to_candles(df)
    if not candles:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"No data returned for '{symbol}' on timeframe '{timeframe}'. "
                f"The exchange may be unavailable or the symbol may be invalid."
            ),
        )

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "data": candles,
    }


# ---------------------------------------------------------------------------
# 2. GET /api/market/ticker
# ---------------------------------------------------------------------------

@router.get("/ticker")
def get_ticker(
    symbol: str = Query("BTC/USDT", description="Trading pair, e.g. BTC/USDT"),
):
    """
    Fetch current ticker data: last price, 24h change %, high, low, volume.

    Public endpoint -- no authentication required.
    """
    # --- Attempt 1: Binance Vision 24hr ticker (fast, no geo-block) ------
    try:
        return _fetch_binance_ticker_24hr(symbol)
    except Exception as e:
        logger.warning("Binance Vision ticker failed for %s: %s", symbol, e)

    # --- Attempt 2: Hyperliquid DEX (no geo-block, no auth) --------------
    try:
        return _fetch_hyperliquid_ticker(symbol)
    except Exception as e:
        logger.warning("Hyperliquid ticker failed for %s: %s", symbol, e)

    # --- Attempt 3: DataFetcher.fetch_ticker (ccxt fallback) --------------
    fetcher = DataFetcher()
    try:
        raw = fetcher.fetch_ticker(symbol)
        if raw and raw.get("last") is not None:
            change_pct = None
            try:
                since = _compute_since("1d", 2)
                df_d = fetcher.fetch_ohlcv(
                    symbol=symbol, timeframe="1d", since=since, limit=2
                )
                if df_d is not None and not df_d.empty and len(df_d) >= 2:
                    prev_close = float(df_d.iloc[-2]["close"])
                    last_close = float(df_d.iloc[-1]["close"])
                    if prev_close > 0:
                        change_pct = round(
                            (last_close - prev_close) / prev_close * 100, 2
                        )
                elif df_d is not None and not df_d.empty:
                    row = df_d.iloc[-1]
                    open_price = float(row["open"])
                    if open_price > 0:
                        change_pct = round(
                            (float(row["close"]) - open_price) / open_price * 100, 2
                        )
            except Exception as e:
                logger.warning("change_pct calculation failed for %s: %s", symbol, e)

            raw_ts = raw.get("timestamp")
            ts_seconds = int(raw_ts // 1000) if raw_ts is not None else None

            return {
                "symbol": symbol,
                "last_price": _safe_float(raw.get("last")),
                "change_pct": change_pct,
                "high": _safe_float(raw.get("high")),
                "low": _safe_float(raw.get("low")),
                "volume": _safe_float(raw.get("volume")),
                "timestamp": ts_seconds,
            }
    except Exception as e:
        logger.error("All ticker sources failed for %s: %s", symbol, e)

    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"Failed to fetch ticker data for '{symbol}'",
    )


# ---------------------------------------------------------------------------
# 3. GET /api/market/symbols
# ---------------------------------------------------------------------------

@router.get("/symbols")
def get_symbols(
    popular_only: bool = Query(
        False, description="If true, return only the preset popular list"
    ),
):
    """
    Get available trading pairs.

    Attempts to load markets from the exchange; falls back to a preset
    popular list on failure.

    Public endpoint -- no authentication required.
    """
    if popular_only:
        return {"symbols": POPULAR_SYMBOLS, "source": "preset"}

    fetcher = DataFetcher()
    try:
        symbols = fetcher.get_available_symbols()
        if symbols:
            # Prefer USDT-quoted pairs for relevance
            usdt_pairs = sorted(s for s in symbols if s.endswith("/USDT"))
            if len(usdt_pairs) > 10:
                return {
                    "symbols": usdt_pairs[:200],
                    "source": "exchange",
                    "total": len(symbols),
                }
            return {
                "symbols": sorted(symbols)[:200],
                "source": "exchange",
                "total": len(symbols),
            }
    except Exception as e:
        logger.warning("Failed to fetch symbols from exchange: %s", e)

    # Fallback to preset list
    return {"symbols": POPULAR_SYMBOLS, "source": "preset"}


# ---------------------------------------------------------------------------
# 4. GET /api/market/multi-timeframe
# ---------------------------------------------------------------------------

@router.get("/multi-timeframe")
def get_multi_timeframe(
    symbol: str = Query("BTC/USDT", description="Trading pair, e.g. BTC/USDT"),
):
    """
    Get a multi-timeframe overview (15m / 1h / 4h / 1d).

    Each timeframe returns the most recent few candles plus a summary
    (last price, change %, window high / low / volume).

    Public endpoint -- no authentication required.
    """
    fetcher = DataFetcher()
    candle_limit = 5

    result = {}
    errors = []

    for tf in MULTI_TF:
        try:
            since = _compute_since(tf, candle_limit)
            df = fetcher.fetch_ohlcv(
                symbol=symbol, timeframe=tf, since=since, limit=candle_limit
            )
            candles = _df_to_candles(df)

            if not candles:
                errors.append(f"{tf}: no data returned")
                result[tf] = None
                continue

            latest = candles[-1]

            # Change % of the latest candle (close vs previous close)
            if len(candles) >= 2:
                prev_close = candles[-2]["close"]
                change_pct = (
                    round((latest["close"] - prev_close) / prev_close * 100, 2)
                    if prev_close
                    else 0.0
                )
            elif latest["open"]:
                change_pct = round(
                    (latest["close"] - latest["open"]) / latest["open"] * 100, 2
                )
            else:
                change_pct = 0.0

            # Window high / low / volume across fetched candles
            highs = [c["high"] for c in candles if c["high"] is not None]
            lows = [c["low"] for c in candles if c["low"] is not None]
            window_high = max(highs) if highs else None
            window_low = min(lows) if lows else None
            window_volume = sum(c["volume"] or 0 for c in candles)

            result[tf] = {
                "last_price": latest["close"],
                "change_pct": change_pct,
                "high": window_high,
                "low": window_low,
                "volume": window_volume,
                "candle_count": len(candles),
                "latest_time": latest["time"],
                "candles": candles,
            }
        except Exception as e:
            logger.warning("Multi-TF fetch failed for %s %s: %s", symbol, tf, e)
            errors.append(f"{tf}: {e}")
            result[tf] = None

    # If every timeframe failed, return an error
    if all(v is None for v in result.values()):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"Failed to fetch multi-timeframe data for '{symbol}'. "
                f"Errors: {'; '.join(errors)}"
            ),
        )

    return {
        "symbol": symbol,
        "timeframes": result,
        "errors": errors or None,
    }

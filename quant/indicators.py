"""
Technical indicators library.
Pure numpy/pandas implementation, no external TA library dependency.
"""

import numpy as np
import pandas as pd


class Indicators:
    """Calculate technical indicators on OHLCV data."""

    @staticmethod
    def sma(series: pd.Series, period: int = 20) -> pd.Series:
        """Simple Moving Average."""
        return series.rolling(window=period).mean()

    @staticmethod
    def ema(series: pd.Series, period: int = 20) -> pd.Series:
        """Exponential Moving Average."""
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def rsi(series: pd.Series, period: int = 14) -> pd.Series:
        """Relative Strength Index."""
        delta = series.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)

        avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period).mean()
        avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period).mean()

        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return rsi

    @staticmethod
    def macd(
        series: pd.Series,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
    ) -> tuple:
        """
        MACD indicator.
        Returns (macd_line, signal_line, histogram).
        """
        ema_fast = series.ewm(span=fast, adjust=False).mean()
        ema_slow = series.ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram

    @staticmethod
    def bollinger_bands(
        series: pd.Series,
        period: int = 20,
        std_dev: float = 2.0,
    ) -> tuple:
        """
        Bollinger Bands.
        Returns (upper_band, middle_band, lower_band).
        """
        middle = series.rolling(window=period).mean()
        std = series.rolling(window=period).std()
        upper = middle + (std * std_dev)
        lower = middle - (std * std_dev)
        return upper, middle, lower

    @staticmethod
    def atr(
        high: pd.Series,
        low: pd.Series,
        close: pd.Series,
        period: int = 14,
    ) -> pd.Series:
        """Average True Range."""
        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low - close.shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1.0 / period, min_periods=period).mean()
        return atr

    @staticmethod
    def stochastic(
        high: pd.Series,
        low: pd.Series,
        close: pd.Series,
        k_period: int = 14,
        d_period: int = 3,
    ) -> tuple:
        """
        Stochastic Oscillator.
        Returns (k_line, d_line).
        """
        lowest_low = low.rolling(window=k_period).min()
        highest_high = high.rolling(window=k_period).max()
        k = 100.0 * (close - lowest_low) / (highest_high - lowest_low)
        d = k.rolling(window=d_period).mean()
        return k, d

    @staticmethod
    def adx(
        high: pd.Series,
        low: pd.Series,
        close: pd.Series,
        period: int = 14,
    ) -> pd.Series:
        """Average Directional Index."""
        plus_dm = high.diff()
        minus_dm = -low.diff()

        plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low - close.shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        atr = tr.ewm(alpha=1.0 / period, min_periods=period).mean()
        plus_di = 100.0 * (plus_dm.ewm(alpha=1.0 / period, min_periods=period).mean() / atr)
        minus_di = 100.0 * (minus_dm.ewm(alpha=1.0 / period, min_periods=period).mean() / atr)

        dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di)
        adx = dx.ewm(alpha=1.0 / period, min_periods=period).mean()
        return adx

    @staticmethod
    def vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
        """Volume Weighted Average Price."""
        typical_price = (high + low + close) / 3.0
        return (typical_price * volume).cumsum() / volume.cumsum()

    @staticmethod
    def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
        """On Balance Volume."""
        obv = (np.sign(close.diff()) * volume).cumsum()
        return obv

    @staticmethod
    def williams_r(
        high: pd.Series,
        low: pd.Series,
        close: pd.Series,
        period: int = 14,
    ) -> pd.Series:
        """Williams %R."""
        highest_high = high.rolling(window=period).max()
        lowest_low = low.rolling(window=period).min()
        wr = -100.0 * (highest_high - close) / (highest_high - lowest_low)
        return wr

    @staticmethod
    def cci(
        high: pd.Series,
        low: pd.Series,
        close: pd.Series,
        period: int = 20,
    ) -> pd.Series:
        """Commodity Channel Index."""
        typical_price = (high + low + close) / 3.0
        sma_tp = typical_price.rolling(window=period).mean()
        mean_dev = typical_price.rolling(window=period).apply(
            lambda x: np.abs(x - x.mean()).mean(), raw=True
        )
        cci = (typical_price - sma_tp) / (0.015 * mean_dev)
        return cci

    @staticmethod
    def supertrend(
        high: pd.Series,
        low: pd.Series,
        close: pd.Series,
        period: int = 10,
        multiplier: float = 3.0,
    ) -> pd.DataFrame:
        """
        Supertrend indicator.
        Returns DataFrame with 'supertrend' and 'direction' columns (1=up, -1=down).
        """
        atr = Indicators.atr(high, low, close, period)
        hl2 = (high + low) / 2.0

        upper_band = hl2 + (multiplier * atr)
        lower_band = hl2 - (multiplier * atr)

        supertrend = pd.Series(index=close.index, dtype=float)
        direction = pd.Series(index=close.index, dtype=int)

        for i in range(len(close)):
            if i == 0:
                supertrend.iloc[i] = upper_band.iloc[i]
                direction.iloc[i] = -1
                continue

            if close.iloc[i] <= supertrend.iloc[i - 1]:
                direction.iloc[i] = -1
                supertrend.iloc[i] = (
                    upper_band.iloc[i]
                    if upper_band.iloc[i] < supertrend.iloc[i - 1]
                    else supertrend.iloc[i - 1]
                )
            else:
                direction.iloc[i] = 1
                supertrend.iloc[i] = (
                    lower_band.iloc[i]
                    if lower_band.iloc[i] > supertrend.iloc[i - 1]
                    else supertrend.iloc[i - 1]
                )

        return pd.DataFrame({"supertrend": supertrend, "direction": direction})

    @staticmethod
    def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
        """Add all common indicators to an OHLCV DataFrame."""
        result = df.copy()
        h, l, c, v = result["high"], result["low"], result["close"], result["volume"]

        # Moving averages
        result["sma_20"] = Indicators.sma(c, 20)
        result["sma_50"] = Indicators.sma(c, 50)
        result["sma_200"] = Indicators.sma(c, 200)
        result["ema_12"] = Indicators.ema(c, 12)
        result["ema_26"] = Indicators.ema(c, 26)

        # RSI
        result["rsi_14"] = Indicators.rsi(c, 14)

        # MACD
        macd_line, signal_line, hist = Indicators.macd(c)
        result["macd"] = macd_line
        result["macd_signal"] = signal_line
        result["macd_hist"] = hist

        # Bollinger Bands
        bb_upper, bb_middle, bb_lower = Indicators.bollinger_bands(c)
        result["bb_upper"] = bb_upper
        result["bb_middle"] = bb_middle
        result["bb_lower"] = bb_lower

        # ATR
        result["atr_14"] = Indicators.atr(h, l, c, 14)

        # Stochastic
        k, d = Indicators.stochastic(h, l, c)
        result["stoch_k"] = k
        result["stoch_d"] = d

        # ADX
        result["adx_14"] = Indicators.adx(h, l, c, 14)

        # OBV
        result["obv"] = Indicators.obv(c, v)

        # VWAP
        result["vwap"] = Indicators.vwap(h, l, c, v)

        # Williams %R
        result["williams_r"] = Indicators.williams_r(h, l, c, 14)

        # CCI
        result["cci_20"] = Indicators.cci(h, l, c, 20)

        # Supertrend
        st = Indicators.supertrend(h, l, c)
        result["supertrend"] = st["supertrend"]
        result["supertrend_dir"] = st["direction"]

        return result

"""
AI Strategy Generator.

Uses DeepSeek API to convert natural language descriptions into executable
Python trading strategy code that conforms to the platform's strategy interface.
"""

import logging
import re
import time

import requests

from config import settings

logger = logging.getLogger("quantedge")

SYSTEM_PROMPT = """You are an expert quantitative trading strategy developer.

You write Python trading strategies for a crypto quantitative trading platform.

## Strategy Interface

The strategy must define a function with this exact signature:

```python
def strategy(df, indicators, config):
    signals = pd.Series(0, index=df.index)
    return signals
```

## Available DataFrame Columns

The `df` DataFrame already contains these columns:
- OHLCV: open, high, low, close, volume
- Moving Averages: sma_20, sma_50, sma_200, ema_12, ema_26
- RSI: rsi_14 (0-100)
- MACD: macd, macd_signal, macd_hist
- Bollinger Bands: bb_upper, bb_middle, bb_lower
- ATR: atr_14
- Stochastic: stoch_k, stoch_d (0-100)
- ADX: adx_14 (trend strength)
- OBV: obv (On Balance Volume)
- VWAP: vwap
- Williams %R: williams_r (-100 to 0)
- CCI: cci_20
- Supertrend: supertrend, supertrend_dir (1=up, -1=down)

## Rules

1. ALWAYS use `pd.Series(0, index=df.index)` to initialize signals
2. Set `signals[condition] = 1` for buy signals
3. Set `signals[condition] = -1` for sell signals
4. Use `df['column_name']` to access indicator values
5. Only use `pd` (pandas) and `np` (numpy) - they are pre-imported
6. Do NOT import any libraries
7. Do NOT use loops unless absolutely necessary - use vectorized operations
8. Handle NaN values with `.fillna()` when needed
9. The function must return a pd.Series

Write ONLY the strategy function code. No imports, no explanations, no markdown fences."""

EXAMPLES = [
    {
        "role": "user",
        "content": "When the MACD line crosses above the signal line, buy. When it crosses below, sell.",
    },
    {
        "role": "assistant",
        "content": "def strategy(df, indicators, config):\n    signals = pd.Series(0, index=df.index)\n    macd = df['macd']\n    signal = df['macd_signal']\n    bullish_cross = (macd > signal) & (macd.shift(1) <= signal.shift(1))\n    signals[bullish_cross] = 1\n    bearish_cross = (macd < signal) & (macd.shift(1) >= signal.shift(1))\n    signals[bearish_cross] = -1\n    return signals",
    },
    {
        "role": "user",
        "content": "Buy when price touches the lower Bollinger Band, sell when it touches the upper band.",
    },
    {
        "role": "assistant",
        "content": "def strategy(df, indicators, config):\n    signals = pd.Series(0, index=df.index)\n    close = df['close']\n    lower = df['bb_lower']\n    upper = df['bb_upper']\n    signals[close <= lower] = 1\n    signals[close >= upper] = -1\n    return signals",
    },
]


class AIStrategyGenerator:
    """Generate trading strategy code from natural language using DeepSeek API."""

    def __init__(self):
        self.api_key = settings.DEEPSEEK_API_KEY
        self.api_url = settings.DEEPSEEK_API_URL
        self.model = settings.DEEPSEEK_MODEL

    def generate(self, description: str, temperature: float = 0.3) -> dict:
        if not self.api_key:
            raise ValueError("DeepSeek API key not configured. Set DEEPSEEK_API_KEY in .env")

        start_time = time.time()

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *EXAMPLES,
            {"role": "user", "content": description},
        ]

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 1024,
            "stream": False,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            resp = requests.post(self.api_url, headers=headers, json=payload, timeout=30)
            resp.raise_for_status()
        except requests.exceptions.Timeout:
            raise RuntimeError("DeepSeek API request timed out (30s)")
        except requests.exceptions.HTTPError:
            error_detail = ""
            try:
                error_body = resp.json()
                error_detail = error_body.get("error", {}).get("message", str(resp.text[:200]))
            except Exception:
                error_detail = str(resp.text[:200])
            raise RuntimeError(f"DeepSeek API error: {resp.status_code} - {error_detail}")

        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        tokens_used = data.get("usage", {}).get("total_tokens", 0)

        code = self._extract_code(content)
        validation = self._validate_code(code)
        latency_ms = int((time.time() - start_time) * 1000)

        return {
            "code": code,
            "explanation": validation.get("explanation", ""),
            "valid": validation["valid"],
            "error": validation.get("error", ""),
            "tokens_used": tokens_used,
            "latency_ms": latency_ms,
            "model": self.model,
        }

    def _extract_code(self, content: str) -> str:
        match = re.search(r"```(?:python)?\s*\n(.*?)```", content, re.DOTALL)
        if match:
            return match.group(1).strip()
        if "def strategy" in content:
            start = content.index("def strategy")
            return content[start:].strip()
        return content.strip()

    def _validate_code(self, code: str) -> dict:
        if "def strategy" not in code:
            return {"valid": False, "error": "Generated code does not define a 'strategy' function"}
        try:
            compile(code, "<strategy>", "exec")
        except SyntaxError as e:
            return {"valid": False, "error": f"Syntax error: {e.msg} (line {e.lineno})"}
        return {"valid": True, "explanation": "Strategy code generated successfully"}

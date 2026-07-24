"""
Voice API — AI voice assistant backend for BTCquant.

Provides endpoints for:
- Voice chat (text-based AI conversation)
- Voice command parsing (intent recognition)
- TTS (text-to-speech via user's API relay)
"""

import logging
from typing import Optional
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse

logger = logging.getLogger("quantedge")

router = APIRouter(prefix="/api/voice", tags=["voice"])

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class VoiceChatRequest(BaseModel):
    message: str
    context: Optional[str] = ""
    lang: Optional[str] = "en"

class VoiceChatResponse(BaseModel):
    reply: str
    intent: Optional[str] = None
    confidence: float = 1.0

class VoiceCommandRequest(BaseModel):
    command: str
    lang: Optional[str] = "en"

class VoiceCommandResponse(BaseModel):
    action: str
    params: dict
    confidence: float
    original_text: str

class TTSRequest(BaseModel):
    text: str
    voice: Optional[str] = "female"
    lang: Optional[str] = "en"

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are BTCquant's AI voice assistant. You help users with:
1. Searching and summarizing cryptocurrency news
2. Building and explaining BTC trading strategies
3. Navigating the BTCquant platform
4. Answering questions about Bitcoin and crypto markets

Respond concisely and conversationally (2-3 sentences max). Use technical terms when appropriate.
Always include risk disclaimers when discussing trading strategies.
Current date: 2026-07-23.
"""

# ---------------------------------------------------------------------------
# Intent recognition keywords
# ---------------------------------------------------------------------------

INTENT_PATTERNS = {
    "news_search": {
        "keywords": ["news", "latest", "bullish", "bearish", "bitcoin", "btc", "ethereum", "eth", "crypto"],
        "action": "filter_news",
    },
    "strategy_build": {
        "keywords": ["strategy", "create", "build", "generate", "rsi", "macd", "bollinger", "moving average", "trend"],
        "action": "generate_strategy",
    },
    "backtest_run": {
        "keywords": ["backtest", "test", "run", "simulate"],
        "action": "run_backtest",
    },
    "market_data": {
        "keywords": ["price", "market", "chart", "trend", "volume", "volatility"],
        "action": "get_market_data",
    },
    "help": {
        "keywords": ["help", "how to", "what can", "assist", "support"],
        "action": "show_help",
    },
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def recognize_intent(text: str) -> tuple:
    """Simple keyword-based intent recognition."""
    text_lower = text.lower()
    best_intent = "general_chat"
    best_score = 0
    best_action = "chat"
    best_params = {}

    for intent_name, pattern in INTENT_PATTERNS.items():
        score = sum(1 for kw in pattern["keywords"] if kw in text_lower)
        if score > best_score:
            best_score = score
            best_intent = intent_name
            best_action = pattern["action"]

    # Extract parameters
    if best_intent == "news_search":
        if "bullish" in text_lower:
            best_params["sentiment"] = "bullish"
        elif "bearish" in text_lower:
            best_params["sentiment"] = "bearish"
        elif "neutral" in text_lower:
            best_params["sentiment"] = "neutral"

        for coin in ["bitcoin", "btc", "ethereum", "eth", "solana", "sol"]:
            if coin in text_lower:
                best_params["currency"] = coin.upper().replace("BITCOIN", "BTC").replace("ETHEREUM", "ETH").replace("SOLANA", "SOL")
                break

    elif best_intent == "strategy_build":
        for strategy in ["rsi", "macd", "bollinger", "moving average", "trend"]:
            if strategy in text_lower:
                best_params["strategy_type"] = strategy
                break

    confidence = min(best_score / 3, 1.0) if best_score > 0 else 0.3
    return best_intent, best_action, best_params, confidence

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/chat", response_model=VoiceChatResponse)
async def voice_chat(payload: VoiceChatRequest):
    """
    Receive text from voice recognition, return AI reply.
    This endpoint bridges to the user's API relay for AI responses.
    """
    try:
        # Recognize intent
        intent, action, params, confidence = recognize_intent(payload.message)

        # Build contextual reply based on intent
        reply = ""
        if intent == "news_search":
            sentiment = params.get("sentiment", "")
            currency = params.get("currency", "")
            if sentiment:
                reply = f"I'll show you {sentiment} crypto news"
                if currency:
                    reply += f" about {currency}"
                reply += ". Opening news page now."
            elif currency:
                reply = f"Here are the latest {currency} news updates."
            else:
                reply = "Here are the latest cryptocurrency news updates."

        elif intent == "strategy_build":
            strategy_type = params.get("strategy_type", "custom")
            reply = f"I'll help you build a {strategy_type} trading strategy. Describe your requirements and I'll generate the code."

        elif intent == "backtest_run":
            reply = "Running backtest on your current strategy. Please wait for the results."

        elif intent == "market_data":
            reply = "Current BTC price is available on the dashboard. Would you like me to navigate there?"

        elif intent == "help":
            reply = "I can help you search crypto news, build trading strategies, run backtests, or answer questions about Bitcoin. Just say what you need."

        else:
            reply = "I'm your BTCquant AI assistant. I can help with crypto news, strategy building, and market analysis. What would you like to do?"

        return VoiceChatResponse(
            reply=reply,
            intent=intent,
            confidence=confidence,
        )

    except Exception as e:
        logger.error(f"[voice_chat] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/command", response_model=VoiceCommandResponse)
async def voice_command(payload: VoiceCommandRequest):
    """
    Parse voice command into structured action.
    Returns action + params for frontend execution.
    """
    try:
        intent, action, params, confidence = recognize_intent(payload.command)

        return VoiceCommandResponse(
            action=action,
            params=params,
            confidence=confidence,
            original_text=payload.command,
        )

    except Exception as e:
        logger.error(f"[voice_command] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/tts")
async def voice_tts(payload: TTSRequest):
    """
    Text-to-speech endpoint.
    Returns TTS metadata (actual audio is handled by frontend Web Speech API).
    """
    return JSONResponse({
        "text": payload.text,
        "voice": payload.voice,
        "lang": payload.lang,
        "note": "TTS is handled client-side via Web Speech API for zero cost",
    })

@router.get("/health")
async def voice_health():
    """Voice system health check."""
    return {
        "status": "ok",
        "voice_chat": True,
        "voice_command": True,
        "tts": True,
        "version": "20260723",
    }

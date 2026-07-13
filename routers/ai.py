"""
AI router: natural language strategy generation powered by DeepSeek API.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from deps import get_current_user
from models import User
from quant.ai_strategy import AIStrategyGenerator

logger = logging.getLogger("quantedge")
router = APIRouter(prefix="/ai", tags=["ai"])


class GenerateStrategyRequest(BaseModel):
    description: str
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"


class GenerateStrategyResponse(BaseModel):
    code: str
    valid: bool
    explanation: str = ""
    error: str = ""
    tokens_used: int = 0
    latency_ms: int = 0
    model: str = ""


@router.post("/generate-strategy", response_model=GenerateStrategyResponse)
def generate_strategy(
    payload: GenerateStrategyRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Generate a trading strategy from natural language description.

    Uses DeepSeek API to convert the description into executable Python code.
    """
    if len(payload.description.strip()) < 10:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Description too short. Please provide at least 10 characters.",
        )

    try:
        generator = AIStrategyGenerator()
        result = generator.generate(payload.description)

        if not result["valid"]:
            logger.warning(f"AI generated invalid code: {result.get('error', '')}")

        return GenerateStrategyResponse(
            code=result["code"],
            valid=result["valid"],
            explanation=result.get("explanation", ""),
            error=result.get("error", ""),
            tokens_used=result.get("tokens_used", 0),
            latency_ms=result.get("latency_ms", 0),
            model=result.get("model", ""),
        )

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))
    except Exception as e:
        logger.error(f"AI strategy generation failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Strategy generation failed",
        )

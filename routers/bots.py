"""
Bots router: create, list, start, stop, and delete trading bots.
Uses the real ExchangeTrader via ccxt for live trading.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db
from deps import get_current_user
from models import Bot, Strategy, User
from schemas import BotCreate, BotOut, BotUpdate, MessageResponse
from quant.exchange_trader import ExchangeTrader

router = APIRouter(prefix="/bots", tags=["bots"])


@router.get("/", response_model=list[BotOut])
def list_bots(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List the current user's bots."""
    bots = (
        db.query(Bot)
        .filter(Bot.user_id == current_user.id)
        .order_by(Bot.created_at.desc())
        .all()
    )
    return bots


@router.post("/", response_model=BotOut, status_code=status.HTTP_201_CREATED)
def create_bot(
    payload: BotCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new trading bot."""
    if payload.strategy_id:
        strategy = db.query(Strategy).filter(Strategy.id == payload.strategy_id).first()
        if not strategy:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Strategy not found",
            )
        if strategy.user_id != current_user.id and not strategy.is_public:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this strategy",
            )

    bot = Bot(
        user_id=current_user.id,
        strategy_id=payload.strategy_id,
        name=payload.name,
        bot_type=payload.bot_type,
        exchange=payload.exchange,
        status="stopped",
        config=payload.config,
    )
    db.add(bot)
    db.commit()
    db.refresh(bot)
    return bot


@router.put("/{bot_id}/start", response_model=BotOut)
def start_bot(
    bot_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Start a trading bot.

    Connects to the exchange via ccxt and executes the bot strategy.
    Requires API keys in the bot config.
    """
    bot = db.query(Bot).filter(Bot.id == bot_id).first()
    if not bot:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bot not found",
        )
    if bot.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only control your own bots",
        )
    if bot.status == "running":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bot is already running",
        )

    # Get API keys from config
    api_key = bot.config.get("api_key", "")
    api_secret = bot.config.get("api_secret", "")
    testnet = bot.config.get("testnet", True)
    if not api_key or not api_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Exchange API credentials are required before a bot can start",
        )

    # Execute the bot based on type
    execution_result = None
    try:
        trader = ExchangeTrader(
            exchange_id=bot.exchange,
            api_key=api_key,
            api_secret=api_secret,
            testnet=testnet,
        )

        if bot.bot_type == "grid":
            execution_result = trader.run_grid_bot(
                symbol=bot.config.get("symbol", "BTC/USDT"),
                upper_price=bot.config.get("upper_price", 70000),
                lower_price=bot.config.get("lower_price", 60000),
                grids=bot.config.get("grids", 10),
                total_investment=bot.config.get("total_investment", 1000),
            )
        elif bot.bot_type == "dca":
            execution_result = trader.run_dca_bot(
                symbol=bot.config.get("symbol", "BTC/USDT"),
                amount_per_order=bot.config.get("amount_per_order", 100),
                interval_hours=bot.config.get("interval_hours", 24),
                take_profit_pct=bot.config.get("take_profit_pct", 10),
                max_orders=bot.config.get("max_orders", 10),
            )
        elif bot.bot_type == "signal":
            # Get strategy code
            strategy_code = ""
            if bot.strategy_id:
                strategy = db.query(Strategy).filter(Strategy.id == bot.strategy_id).first()
                if strategy:
                    strategy_code = strategy.code
            if not strategy_code:
                strategy_code = bot.config.get("code", "")

            execution_result = trader.run_signal_bot(
                symbol=bot.config.get("symbol", "BTC/USDT"),
                strategy_code=strategy_code,
                timeframe=bot.config.get("timeframe", "1h"),
                config=bot.config,
            )
        else:
            # Custom bot: just test connection
            execution_result = trader.test_connection()

    except Exception:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The exchange rejected the bot startup request",
        )

    # Update bot status
    bot.status = "running"
    # Store execution result in config
    config = dict(bot.config) if bot.config else {}
    config["last_execution"] = execution_result
    config["started_at"] = datetime.now(timezone.utc).isoformat()
    bot.config = config

    db.commit()
    db.refresh(bot)
    return bot


@router.put("/{bot_id}/stop", response_model=BotOut)
def stop_bot(
    bot_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Stop a trading bot.

    Attempts to cancel all open orders on the exchange before stopping.
    """
    bot = db.query(Bot).filter(Bot.id == bot_id).first()
    if not bot:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bot not found",
        )
    if bot.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only control your own bots",
        )
    if bot.status == "stopped":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bot is already stopped",
        )

    # Try to cancel open orders
    api_key = bot.config.get("api_key", "")
    api_secret = bot.config.get("api_secret", "")
    testnet = bot.config.get("testnet", True)

    cancel_result = None
    try:
        trader = ExchangeTrader(
            exchange_id=bot.exchange,
            api_key=api_key,
            api_secret=api_secret,
            testnet=testnet,
        )
        cancel_result = trader.cancel_all_orders(bot.config.get("symbol"))
    except Exception as e:
        cancel_result = {"error": str(e)}

    bot.status = "stopped"
    config = dict(bot.config) if bot.config else {}
    config["stopped_at"] = datetime.now(timezone.utc).isoformat()
    config["cancel_result"] = cancel_result
    bot.config = config

    db.commit()
    db.refresh(bot)
    return bot


@router.put("/{bot_id}", response_model=BotOut)
def update_bot(
    bot_id: int,
    payload: BotUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update a bot's configuration."""
    bot = db.query(Bot).filter(Bot.id == bot_id).first()
    if not bot:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bot not found",
        )
    if bot.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only update your own bots",
        )

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(bot, field, value)

    db.commit()
    db.refresh(bot)
    return bot


@router.delete("/{bot_id}", response_model=MessageResponse)
def delete_bot(
    bot_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a trading bot."""
    bot = db.query(Bot).filter(Bot.id == bot_id).first()
    if not bot:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bot not found",
        )
    if bot.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only delete your own bots",
        )

    # Stop bot if running before deleting
    if bot.status == "running":
        bot.status = "stopped"
        db.commit()

    db.delete(bot)
    db.commit()
    return MessageResponse(message="Bot deleted", detail={"id": bot_id})

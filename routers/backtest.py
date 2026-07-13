"""
Backtest router: submit backtest jobs and retrieve results.
Uses the real BacktestEngine with ccxt historical data.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db
from deps import get_current_user
from models import Backtest, Strategy, User
from schemas import BacktestCreate, BacktestOut
from quant.backtest_engine import BacktestEngine

router = APIRouter(prefix="/backtest", tags=["backtest"])

# Sample strategy code for users who don't have one
SAMPLE_STRATEGY = """
def strategy(df, indicators, config):
    signals = pd.Series(0, index=df.index)
    rsi = df['rsi_14']
    # Buy when RSI < 30 (oversold)
    signals[rsi < 30] = 1
    # Sell when RSI > 70 (overbought)
    signals[rsi > 70] = -1
    return signals
"""


@router.post("/", response_model=BacktestOut, status_code=status.HTTP_201_CREATED)
def submit_backtest(
    payload: BacktestCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Submit and run a backtest.

    Fetches real historical data via ccxt, executes the user's strategy code,
    simulates trades with commission and slippage, and calculates performance metrics.
    """
    # Get strategy code
    strategy_code = ""
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
        strategy_code = strategy.code

    # Use config-provided code or sample strategy
    if not strategy_code:
        strategy_code = payload.config.get("code", SAMPLE_STRATEGY)

    # Extract backtest parameters from config
    symbol = payload.config.get("symbol", "BTC/USDT")
    timeframe = payload.config.get("timeframe", "1h")
    limit = payload.config.get("limit", 500)
    exchange = payload.config.get("exchange", "binance")
    initial_capital = payload.config.get("initial_capital", 10000.0)

    # Create backtest record with 'running' status
    backtest = Backtest(
        user_id=current_user.id,
        strategy_id=payload.strategy_id,
        status="running",
        config=payload.config,
        result={},
    )
    db.add(backtest)
    db.commit()
    db.refresh(backtest)

    # Run the real backtest
    try:
        engine = BacktestEngine(initial_capital=initial_capital)
        result = engine.run(
            strategy_code=strategy_code,
            symbol=symbol,
            timeframe=timeframe,
            limit=limit,
            exchange=exchange,
            config=payload.config,
        )

        # Update strategy metrics if backtest was successful
        if result.get("status") == "completed" and payload.strategy_id:
            metrics = result.get("metrics", {})
            strategy = db.query(Strategy).filter(Strategy.id == payload.strategy_id).first()
            if strategy:
                strategy.roi = metrics.get("total_return_pct", 0)
                strategy.sharpe_ratio = metrics.get("sharpe_ratio", 0)
                strategy.max_drawdown = metrics.get("max_drawdown_pct", 0)

        backtest.status = result.get("status", "failed")
        backtest.result = result
        db.commit()
        db.refresh(backtest)
        return backtest

    except Exception as e:
        backtest.status = "failed"
        backtest.result = {"status": "error", "error": str(e)}
        db.commit()
        db.refresh(backtest)
        return backtest


@router.get("/{backtest_id}", response_model=BacktestOut)
def get_backtest(
    backtest_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get a backtest result by ID."""
    backtest = db.query(Backtest).filter(Backtest.id == backtest_id).first()
    if not backtest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Backtest not found",
        )
    if backtest.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view your own backtests",
        )
    return backtest

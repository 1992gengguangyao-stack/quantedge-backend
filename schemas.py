"""
Pydantic v2 schemas for request/response validation.
"""

from datetime import datetime
from typing import Any, Optional, Union

from pydantic import BaseModel, Field, ConfigDict


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class WalletLogin(BaseModel):
    message: str
    signature: str
    invite_code: Optional[str] = None  # optional referral code for new wallet users


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserOut"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: Optional[str] = None
    wallet_address: Optional[str] = None
    username: str
    plan: str
    is_active: bool
    created_at: datetime
    invite_code: Optional[str] = None
    referred_by_code: Optional[str] = None
    referral_bonus_days: int = 0


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------

class StrategyBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    description: str = ""
    code: str = ""
    language: str = "python"
    category: str = "trend"
    coins: list[str] = Field(default_factory=list)
    is_public: bool = False
    price_monthly: float = 0.0
    roi: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0


class StrategyCreate(StrategyBase):
    pass


class StrategyUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    code: Optional[str] = None
    language: Optional[str] = None
    category: Optional[str] = None
    coins: Optional[list[str]] = None
    is_public: Optional[bool] = None
    price_monthly: Optional[float] = None
    roi: Optional[float] = None
    sharpe_ratio: Optional[float] = None
    max_drawdown: Optional[float] = None


class StrategyOut(StrategyBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    is_published: bool
    subscribers_count: int
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Subscription
# ---------------------------------------------------------------------------

class SubscriptionCreate(BaseModel):
    strategy_id: int


class SubscriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    strategy_id: int
    status: str
    started_at: datetime
    expires_at: Optional[datetime] = None
    strategy: Optional[StrategyOut] = None


# ---------------------------------------------------------------------------
# Payment
# ---------------------------------------------------------------------------

class PaymentCreate(BaseModel):
    plan: str  # free/starter/pro/expert
    currency: str  # usdt/usdc/btc/eth/fiat
    chain_id: Optional[Union[int, str]] = None  # int=EVM chain, "trx"=Tron


class PaymentVerify(BaseModel):
    tx_hash: str
    currency: str
    amount: Optional[float] = None
    chain_id: Optional[Union[int, str]] = None  # 1=Ethereum, 56=BSC, 137=Polygon, 42161=Arbitrum, "trx"=Tron


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    amount: float
    currency: str
    tx_hash: Optional[str] = None
    status: str
    plan: str
    created_at: datetime


# ---------------------------------------------------------------------------
# Bot
# ---------------------------------------------------------------------------

class BotCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    strategy_id: Optional[int] = None
    bot_type: str = "custom"  # dca/grid/signal/custom
    exchange: str = "binance"
    config: dict[str, Any] = Field(default_factory=dict)


class BotUpdate(BaseModel):
    name: Optional[str] = None
    config: Optional[dict[str, Any]] = None
    bot_type: Optional[str] = None
    exchange: Optional[str] = None


class BotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    strategy_id: Optional[int] = None
    name: str
    bot_type: str
    exchange: str
    status: str
    config: dict[str, Any]
    created_at: datetime


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------

class BacktestCreate(BaseModel):
    strategy_id: Optional[int] = None
    config: dict[str, Any] = Field(default_factory=dict)


class BacktestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    strategy_id: Optional[int] = None
    status: str
    config: dict[str, Any]
    result: dict[str, Any]
    created_at: datetime


# ---------------------------------------------------------------------------
# Generic message response
# ---------------------------------------------------------------------------

class MessageResponse(BaseModel):
    message: str
    detail: Optional[Any] = None


# ---------------------------------------------------------------------------
# Referral
# ---------------------------------------------------------------------------

class ReferralOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    referrer_id: int
    referred_id: int
    referred_username: Optional[str] = None
    reward_days_referrer: int
    reward_days_referred: int
    status: str
    created_at: datetime


class ReferralStatsOut(BaseModel):
    invite_code: Optional[str] = None
    invite_link: Optional[str] = None
    total_invited: int = 0
    total_bonus_days: int = 0
    referral_bonus_days: int = 0
    recent_referrals: list[ReferralOut] = Field(default_factory=list)


# Forward references
Token.model_rebuild()
SubscriptionOut.model_rebuild()

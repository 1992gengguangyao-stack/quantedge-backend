"""
SQLAlchemy ORM models for QuantEdge.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    Float,
    Boolean,
    DateTime,
    ForeignKey,
    JSON,
)
from sqlalchemy.orm import relationship

from database import Base


def _utcnow() -> datetime:
    """Return timezone-aware UTC now."""
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=True)
    wallet_address = Column(String(64), unique=True, index=True, nullable=True)
    username = Column(String(128), nullable=False)
    hashed_password = Column(String(255), nullable=True)
    plan = Column(String(32), default="free", nullable=False)  # free/starter/pro/expert
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    # Referral system fields
    invite_code = Column(String(16), unique=True, index=True, nullable=True)
    referred_by_code = Column(String(16), nullable=True)  # invite code of the referrer
    referral_bonus_days = Column(Integer, default=0, nullable=False)  # accumulated Pro bonus days

    # Relationships
    strategies = relationship("Strategy", back_populates="owner", cascade="all, delete-orphan")
    subscriptions = relationship("Subscription", back_populates="user", cascade="all, delete-orphan")
    payments = relationship("Payment", back_populates="user", cascade="all, delete-orphan")
    bots = relationship("Bot", back_populates="user", cascade="all, delete-orphan")
    backtests = relationship("Backtest", back_populates="user", cascade="all, delete-orphan")
    referrals_made = relationship("Referral", back_populates="referrer", foreign_keys="Referral.referrer_id", cascade="all, delete-orphan")


class SiweNonce(Base):
    """Short-lived, one-time challenge used by wallet sign-in."""

    __tablename__ = "siwe_nonces"

    id = Column(Integer, primary_key=True, index=True)
    nonce = Column(String(128), unique=True, index=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)


class Strategy(Base):
    __tablename__ = "strategies"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(256), nullable=False)
    description = Column(Text, default="")
    code = Column(Text, default="")
    language = Column(String(32), default="python")  # python/visual
    category = Column(String(32), default="trend")    # trend/grid/dca/arb/scalp/mean
    coins = Column(JSON, default=list)                 # ["BTC", "ETH", ...]
    is_public = Column(Boolean, default=False)
    is_published = Column(Boolean, default=False)      # listed on marketplace
    price_monthly = Column(Float, default=0.0)
    roi = Column(Float, default=0.0)
    sharpe_ratio = Column(Float, default=0.0)
    max_drawdown = Column(Float, default=0.0)
    subscribers_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    # Relationships
    owner = relationship("User", back_populates="strategies")
    subscriptions = relationship("Subscription", back_populates="strategy", cascade="all, delete-orphan")
    bots = relationship("Bot", back_populates="strategy")
    backtests = relationship("Backtest", back_populates="strategy")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    strategy_id = Column(Integer, ForeignKey("strategies.id"), nullable=False, index=True)
    status = Column(String(32), default="active")  # active/cancelled
    started_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    user = relationship("User", back_populates="subscriptions")
    strategy = relationship("Strategy", back_populates="subscriptions")


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    amount = Column(Float, nullable=False, default=0.0)
    currency = Column(String(16), nullable=False)  # usdt/usdc/btc/eth/fiat
    tx_hash = Column(String(128), nullable=True, index=True)
    status = Column(String(32), default="pending")  # pending/confirmed/failed
    plan = Column(String(32), default="free")
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    # Relationships
    user = relationship("User", back_populates="payments")


class AnalyticsEvent(Base):
    """Privacy-first first-party product analytics event.

    Anonymous browser identifiers are HMAC-hashed before storage.  We do not
    store wallet addresses, IP addresses, or full referrer URLs here.
    """

    __tablename__ = "analytics_events"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(String(64), unique=True, index=True, nullable=False)
    visitor_id_hash = Column(String(64), index=True, nullable=False)
    session_id_hash = Column(String(64), index=True, nullable=False)
    event_name = Column(String(64), index=True, nullable=False)
    path = Column(String(512), default="/", nullable=False)
    referrer_host = Column(String(255), default="", nullable=False)
    source = Column(String(128), default="direct", nullable=False)
    medium = Column(String(128), default="", nullable=False)
    campaign = Column(String(128), default="", nullable=False)
    plan = Column(String(32), default="", nullable=False)
    properties = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, index=True, nullable=False)


class Bot(Base):
    __tablename__ = "bots"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    strategy_id = Column(Integer, ForeignKey("strategies.id"), nullable=True, index=True)
    name = Column(String(256), nullable=False)
    bot_type = Column(String(32), default="custom")  # dca/grid/signal/custom
    exchange = Column(String(64), default="binance")
    status = Column(String(32), default="stopped")   # running/paused/stopped
    config = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    # Relationships
    user = relationship("User", back_populates="bots")
    strategy = relationship("Strategy", back_populates="bots")


class Backtest(Base):
    __tablename__ = "backtests"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    strategy_id = Column(Integer, ForeignKey("strategies.id"), nullable=True, index=True)
    status = Column(String(32), default="pending")  # pending/running/completed/failed
    config = Column(JSON, default=dict)
    result = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    # Relationships
    user = relationship("User", back_populates="backtests")
    strategy = relationship("Strategy", back_populates="backtests")


class Referral(Base):
    """Tracks invite relationships: who invited whom, reward status, etc."""
    __tablename__ = "referrals"

    id = Column(Integer, primary_key=True, index=True)
    referrer_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    referred_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    invite_code_used = Column(String(16), nullable=True)
    reward_days_referrer = Column(Integer, default=7, nullable=False)   # days given to referrer
    reward_days_referred = Column(Integer, default=3, nullable=False)   # days given to referred user
    status = Column(String(32), default="completed")  # completed/pending
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    # Relationships
    referrer = relationship("User", back_populates="referrals_made", foreign_keys=[referrer_id])
    referred = relationship("User", foreign_keys=[referred_id])

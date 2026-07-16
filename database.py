"""
SQLAlchemy database engine and session configuration.
"""

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base

from config import settings

# SQLite requires check_same_thread=False for use with FastAPI
connect_args = (
    {"check_same_thread": False}
    if settings.DATABASE_URL.startswith("sqlite")
    else {}
)

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,
    echo=False,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

Base = declarative_base()


def get_db():
    """FastAPI dependency: yield a database session and ensure it is closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_compat_schema() -> None:
    """Add small backward-compatible columns that create_all cannot add."""
    with engine.begin() as connection:
        inspector = inspect(connection)
        user_columns = {column["name"] for column in inspector.get_columns("users")}
        payment_columns = {column["name"] for column in inspector.get_columns("payments")}
        if "plan_expires_at" not in user_columns:
            timestamp_type = "TIMESTAMP WITH TIME ZONE" if engine.dialect.name == "postgresql" else "DATETIME"
            connection.execute(text(f"ALTER TABLE users ADD COLUMN plan_expires_at {timestamp_type}"))
        if "billing_period" not in payment_columns:
            connection.execute(text(
                "ALTER TABLE payments ADD COLUMN billing_period VARCHAR(16) NOT NULL DEFAULT 'monthly'"
            ))

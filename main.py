"""
QuantEdge API — Main FastAPI application entrypoint.

Crypto Quantitative Trading Platform backend.
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import Base, engine
from routers import auth, strategies, subscriptions, payments, bots, backtest, market, referrals, ai, admin, analytics, dex as dex_router

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger("quantedge")

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="QuantEdge API",
    description="Crypto quantitative trading platform backend.",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# CORS — allow all origins for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(auth.router, prefix="/api")
app.include_router(strategies.router, prefix="/api")
app.include_router(subscriptions.router, prefix="/api")
app.include_router(payments.router, prefix="/api")
app.include_router(bots.router, prefix="/api")
app.include_router(backtest.router, prefix="/api")
app.include_router(market.router, prefix="/api")
app.include_router(referrals.router, prefix="/api")
app.include_router(ai.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
app.include_router(analytics.router, prefix="/api")
app.include_router(dex_router.router, prefix="/api")

# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
def on_startup():
    """Create database tables on startup and launch background tasks."""
    try:
        logger.info("Creating database tables...")
        Base.metadata.create_all(bind=engine)
        logger.info("QuantEdge API started successfully.")
    except Exception as e:
        logger.error(f"Database init error (non-fatal): {e}")

    # Start auto-payment verifier background task (non-fatal if it fails)
    try:
        from quant.auto_verifier import start_auto_verifier
        start_auto_verifier()
    except Exception as e:
        logger.error(f"Auto-verifier start error (non-fatal): {e}")


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health", tags=["health"])
def health_check():
    """Health check endpoint."""
    return {"status": "ok", "service": "QuantEdge API", "version": "1.0.0"}


@app.get("/", tags=["root"])
def root():
    """Root endpoint with API info."""
    return {
        "service": "QuantEdge API",
        "version": "1.0.0",
        "docs": "/api/docs",
        "health": "/health",
    }

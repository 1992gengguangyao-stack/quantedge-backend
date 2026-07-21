"""
QuantEdge API — Main FastAPI application entrypoint.

Crypto Quantitative Trading Platform backend.
"""

import logging
import time

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from fastapi.middleware.cors import CORSMiddleware

from database import Base, engine, ensure_compat_schema
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


@app.middleware("http")
async def log_critical_flows(request: Request, call_next):
    """Emit concise, secret-free runtime logs for auth and payment support."""
    started = time.perf_counter()
    path = request.url.path
    should_log = path.startswith((
        "/api/auth/",
        "/api/payments/",
        "/api/analytics/",
        "/api/ai/",
        "/api/backtest",
        "/api/strategies",
        "/api/subscriptions",
        "/api/bots",
        "/api/referrals/",
        "/api/dex/exchange",
    ))
    try:
        response = await call_next(request)
    except Exception:
        if should_log:
            logger.exception("HTTP %s %s failed", request.method, path)
        raise
    if should_log:
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "HTTP %s %s -> %d (%.0fms)",
            request.method,
            path,
            response.status_code,
            elapsed_ms,
        )
    return response

# CORS — production site, Cloudflare previews, and local development only.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://aiquantbtc.com",
        "https://www.aiquantbtc.com",
        "http://localhost:3000",
        "http://localhost:8000",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:8000",
    ],
    allow_origin_regex=r"(?:https://[a-z0-9-]+\.aiquantbtc\.pages\.dev|http://(?:localhost|127\.0\.0\.1)(?::\d+)?)",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Performance & Security Middleware (auto-fix: cache + gzip + security)
# ---------------------------------------------------------------------------

app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.middleware("http")
async def add_security_and_cache_headers(request: Request, call_next):
    """Add security headers and Cache-Control to all responses."""
    response = await call_next(request)
    # Security headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # Cache-Control headers
    if request.method == "GET" and response.status_code == 200:
        path = request.url.path
        if path.startswith(("/api/docs", "/api/redoc", "/api/openapi.json")):
            response.headers["Cache-Control"] = "public, max-age=3600"
        elif path in ("/health", "/"):
            response.headers["Cache-Control"] = "no-cache"
        else:
            response.headers["Cache-Control"] = "public, max-age=300"
    return response


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
    logger.info("Creating database tables...")
    Base.metadata.create_all(bind=engine)
    ensure_compat_schema()
    logger.info("QuantEdge API started successfully.")

    # Start auto-payment verifier background task (non-fatal if it fails)
    try:
        from quant.auto_verifier import start_auto_verifier
        start_auto_verifier()
    except Exception as e:
        logger.error(f"Auto-verifier start error (non-fatal): {e}")


# ---------------------------------------------------------------------------
# Error handlers (auto-fix: reduce 404 response size)
# ---------------------------------------------------------------------------

@app.exception_handler(404)
async def not_found_handler(request: Request, exc: HTTPException):
    """Return compact JSON 404 instead of default HTML to reduce bandwidth."""
    return JSONResponse(
        status_code=404,
        content={"detail": "Not Found", "path": request.url.path}
    )


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health", tags=["health"])
def health_check():
    """Health check endpoint."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        logger.error("Database health check failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable",
        ) from exc
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

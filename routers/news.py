"""
Crypto News Router — RSS aggregation + sentiment classification.

Provides AI/agent-friendly JSON feed of crypto news from multiple sources,
with automatic bullish/bearish/neutral classification based on keyword matching.
"""

import asyncio
import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Optional
from xml.etree import ElementTree as ET

import requests
from fastapi import APIRouter, Query

router = APIRouter(prefix="/news", tags=["news"])
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

RSS_SOURCES = [
    {
        "name": "CoinTelegraph",
        "url": "https://cointelegraph.com/rss",
        "id_prefix": "ct",
    },
    {
        "name": "Bitcoin Magazine",
        "url": "https://bitcoinmagazine.com/.rss/full/",
        "id_prefix": "bm",
    },
]

CACHE_TTL_SECONDS = 3600  # 1 hour
FETCH_TIMEOUT = 15

BULLISH_KEYWORDS = [
    "surge", "rally", "bull", "breakout", "pump", "ath", "all-time high",
    "adoption", "approve", "approved", "etf", "institutional", "accumulate",
    "buy", "upgrade", "partnership", "integration", "bullish", "rocket",
    "moon", "soar", "jump", "gain", "rise", "climb", "support", "positive",
    "outperform", "rally", "boost", "rise", "recover", "optimism",
    "inflow", "demand", "growth", "milestone", "record", "high",
]

BEARISH_KEYWORDS = [
    "crash", "dump", "bear", "breakdown", "plunge", "hack", "exploit",
    "ban", "reject", "rejected", "lawsuit", "sec", "regulation",
    "sell-off", "selloff", "liquidation", "bearish", "collapse", "drop",
    "fall", "decline", "warning", "risk", "fraud", "scam", "halt",
    "suspend", "negative", "fear", "panic", "outflow", "fud",
    "delist", "shut down", "probe", "investigation", "charge",
    "plunge", "slump", "tumble", "bleed", "correction",
]

CURRENCY_PATTERNS = {
    "BTC": ["bitcoin", "btc", "bitcoin etf"],
    "ETH": ["ethereum", "eth", "ether"],
    "SOL": ["solana", "sol"],
    "XRP": ["ripple", "xrp"],
    "USDT": ["tether", "usdt"],
    "USDC": ["usdc", "circle"],
    "BNB": ["binance coin", "bnb"],
    "DOGE": ["dogecoin", "doge"],
    "ADA": ["cardano", "ada"],
    "AVAX": ["avalanche", "avax"],
}

# ---------------------------------------------------------------------------
# In-memory cache
# ---------------------------------------------------------------------------

_cache: dict = {
    "items": [],
    "updated_at": None,
    "fetch_time": 0,
}
_refresh_task: Optional[asyncio.Task] = None
_initial_fetch_done = False


# ---------------------------------------------------------------------------
# Sentiment classification
# ---------------------------------------------------------------------------

def classify_sentiment(title: str, summary: str) -> tuple:
    """Classify news sentiment based on keyword matching.

    Returns (sentiment, score) where sentiment is 'bullish'/'bearish'/'neutral'
    and score is a float in [-1.0, 1.0].
    """
    text = (title + " " + summary).lower()

    bullish_count = sum(1 for kw in BULLISH_KEYWORDS if kw in text)
    bearish_count = sum(1 for kw in BEARISH_KEYWORDS if kw in text)

    total = bullish_count + bearish_count
    if total == 0:
        return "neutral", 0.0

    # Score: (bullish - bearish) / total, range [-1.0, 1.0]
    score = (bullish_count - bearish_count) / total

    if score > 0.15:
        return "bullish", round(score, 2)
    elif score < -0.15:
        return "bearish", round(score, 2)
    else:
        return "neutral", round(score, 2)


def extract_currencies(title: str, summary: str) -> list:
    """Extract mentioned currency codes from text."""
    text = (title + " " + summary).lower()
    currencies = []
    for code, patterns in CURRENCY_PATTERNS.items():
        if any(p in text for p in patterns):
            currencies.append(code)
    return currencies if currencies else ["BTC"]  # default


# ---------------------------------------------------------------------------
# RSS fetching
# ---------------------------------------------------------------------------

def _parse_date(date_str: str) -> Optional[str]:
    """Parse various RSS date formats to ISO 8601."""
    if not date_str:
        return None
    # Try RFC 822 (most common in RSS): "Wed, 23 Jul 2026 10:30:00 +0000"
    for fmt in [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S GMT",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
    ]:
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            return dt.isoformat()
        except (ValueError, TypeError):
            continue
    return date_str  # fallback to raw string


def _fetch_single_source(source: dict) -> list:
    """Fetch and parse a single RSS source. Returns list of news items."""
    try:
        resp = requests.get(
            source["url"],
            timeout=FETCH_TIMEOUT,
            headers={"User-Agent": "BTCquant-NewsBot/1.0"},
        )
        resp.raise_for_status()

        root = ET.fromstring(resp.content)
        items = []

        # RSS 2.0: channel/item
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            description = (item.findtext("description") or "").strip()
            pub_date = item.findtext("pubDate") or ""

            if not title:
                continue

            # Strip HTML from description
            summary = description
            if "<" in summary:
                # Simple HTML tag removal
                import re
                summary = re.sub(r"<[^>]+>", "", summary).strip()
            if len(summary) > 300:
                summary = summary[:297] + "..."

            sentiment, score = classify_sentiment(title, summary)
            currencies = extract_currencies(title, summary)

            # Generate stable ID
            raw_id = f'{source["id_prefix"]}:{hashlib.md5(title.encode()).hexdigest()[:12]}'

            items.append({
                "id": raw_id,
                "title": title,
                "url": link,
                "source": source["name"],
                "published_at": _parse_date(pub_date),
                "sentiment": sentiment,
                "sentiment_score": score,
                "currencies": currencies,
                "summary": summary,
            })

        logger.info(f"[news] Fetched {len(items)} items from {source['name']}")
        return items

    except Exception as e:
        logger.warning(f"[news] Failed to fetch {source['name']}: {e}")
        return []


def _fetch_all_sources_sync() -> list:
    """Fetch all RSS sources synchronously. Returns merged, sorted items."""
    all_items = []
    for source in RSS_SOURCES:
        items = _fetch_single_source(source)
        all_items.extend(items)

    # Sort by published_at descending (newest first)
    all_items.sort(
        key=lambda x: x.get("published_at") or "",
        reverse=True,
    )

    # Deduplicate by title
    seen = set()
    unique = []
    for item in all_items:
        key = item["title"].lower()
        if key not in seen:
            seen.add(key)
            unique.append(item)

    return unique


async def _fetch_all_news() -> list:
    """Async wrapper for fetching all news sources."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _fetch_all_sources_sync)


async def _ensure_fresh_cache():
    """Ensure cache is fresh; fetch if stale."""
    global _cache, _initial_fetch_done

    now = time.time()
    cache_age = now - _cache.get("fetch_time", 0)

    if _initial_fetch_done and cache_age < CACHE_TTL_SECONDS:
        return  # cache still fresh

    # Fetch new data
    try:
        items = await _fetch_all_news()
        if items:
            _cache["items"] = items
            _cache["updated_at"] = datetime.now(timezone.utc).isoformat()
            _cache["fetch_time"] = now
            _initial_fetch_done = True
            logger.info(f"[news] Cache refreshed: {len(items)} items")
    except Exception as e:
        logger.error(f"[news] Cache refresh failed: {e}")


async def _refresh_loop():
    """Background task to refresh cache every hour."""
    while True:
        try:
            await _fetch_all_news()
        except Exception as e:
            logger.error(f"[news] Refresh loop error: {e}")
        await asyncio.sleep(CACHE_TTL_SECONDS)


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@router.get("/")
async def get_news(
    sentiment: Optional[str] = Query(None, regex="^(bullish|bearish|neutral)$"),
    currency: Optional[str] = Query(None, max_length=10),
    limit: int = Query(20, ge=1, le=100),
):
    """Get latest crypto news with sentiment classification.

    - **sentiment**: Filter by 'bullish', 'bearish', or 'neutral'
    - **currency**: Filter by currency code (e.g., 'BTC', 'ETH')
    - **limit**: Number of items (1-100, default 20)
    """
    await _ensure_fresh_cache()

    items = _cache["items"]

    # Filter by sentiment
    if sentiment:
        items = [x for x in items if x["sentiment"] == sentiment]

    # Filter by currency
    if currency:
        currency_upper = currency.upper()
        items = [x for x in items if currency_upper in x["currencies"]]

    # Apply limit
    items = items[:limit]

    # Compute metadata
    all_items = _cache["items"]
    stats = {"bullish": 0, "bearish": 0, "neutral": 0}
    for x in all_items:
        stats[x["sentiment"]] = stats.get(x["sentiment"], 0) + 1

    cache_age = int(time.time() - _cache.get("fetch_time", 0))

    return {
        "version": "2.0",
        "title": "BTCquant Crypto News Feed",
        "description": "AI-readable crypto news with sentiment classification",
        "home_page_url": "https://aiquantbtc.com/news",
        "feed_url": "https://quantedge-backend-production.up.railway.app/api/news/",
        "updated_at": _cache.get("updated_at"),
        "items": items,
        "metadata": {
            "total": len(all_items),
            "returned": len(items),
            "bullish": stats["bullish"],
            "bearish": stats["bearish"],
            "neutral": stats["neutral"],
            "cache_age_seconds": cache_age,
            "sources": [s["name"] for s in RSS_SOURCES],
        },
    }


@router.get("/feed")
async def get_news_feed(
    limit: int = Query(50, ge=1, le=100),
):
    """Get news in JSON Feed 1.1 format (compatible with feed readers)."""
    await _ensure_fresh_cache()

    items = _cache["items"][:limit]

    return {
        "version": "https://jsonfeed.org/version/1.1",
        "title": "BTCquant Crypto News Feed",
        "description": "AI-readable crypto news with sentiment classification",
        "home_page_url": "https://aiquantbtc.com/news",
        "feed_url": "https://quantedge-backend-production.up.railway.app/api/news/feed",
        "items": [
            {
                "id": item["id"],
                "url": item["url"],
                "title": item["title"],
                "content_text": item["summary"],
                "date_published": item["published_at"],
                "authors": [{"name": item["source"]}],
                "tags": [item["sentiment"]] + item["currencies"],
            }
            for item in items
        ],
    }


@router.get("/health")
async def news_health():
    """Health check for news service."""
    cache_age = int(time.time() - _cache.get("fetch_time", 0))
    return {
        "module": "news",
        "status": "ok" if _cache["items"] else "warming_up",
        "items_cached": len(_cache["items"]),
        "last_updated": _cache.get("updated_at"),
        "cache_age_seconds": cache_age,
        "sources": RSS_SOURCES,
    }


# ---------------------------------------------------------------------------
# Startup: start background refresh task
# ---------------------------------------------------------------------------

@router.on_event("startup")
async def _start_refresh_task():
    """Start background refresh loop on router startup."""
    global _refresh_task
    if _refresh_task is None:
        _refresh_task = asyncio.create_task(_refresh_loop())
        logger.info("[news] Background refresh task started")

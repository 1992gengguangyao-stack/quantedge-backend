"""
QuantEdge API — End-to-End Test Script
Tests all major API endpoints including real backtest with Binance data.

Usage:
    cd d:\\bitcoin\\crypto-quant-web\\backend
    python run_test.py
"""

import sys
import os
import time
import requests

BASE_URL = "http://127.0.0.1:8000"
TIMEOUT = 30

passed = 0
failed = 0
errors = []


def log_pass(name):
    global passed
    passed += 1
    print(f"  [PASS] {name}")


def log_fail(name, detail=""):
    global failed
    failed += 1
    errors.append(f"{name}: {detail}")
    print(f"  [FAIL] {name} - {detail}")


def test_health():
    print("\n=== Test 1: Health Check ===")
    try:
        r = requests.get(f"{BASE_URL}/health", timeout=TIMEOUT)
        if r.status_code == 200 and r.json().get("status") == "ok":
            log_pass("Health check")
        else:
            log_fail("Health check", f"Status {r.status_code}")
    except Exception as e:
        log_fail("Health check", str(e))


def test_register_and_login():
    print("\n=== Test 2: Register & Login ===")
    email = f"test_{int(time.time())}@quantedge.io"
    password = "TestPass123!"
    token = None

    try:
        r = requests.post(
            f"{BASE_URL}/api/auth/register",
            json={"email": email, "username": "testuser", "password": password},
            timeout=TIMEOUT,
        )
        if r.status_code == 201:
            token = r.json()["access_token"]
            log_pass("Register")
        else:
            log_fail("Register", f"Status {r.status_code}: {r.text}")
            return None
    except Exception as e:
        log_fail("Register", str(e))
        return None

    try:
        r = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": email, "password": password},
            timeout=TIMEOUT,
        )
        if r.status_code == 200:
            token = r.json()["access_token"]
            log_pass("Login")
        else:
            log_fail("Login", f"Status {r.status_code}")
    except Exception as e:
        log_fail("Login", str(e))

    return token


def test_auth_me(token):
    print("\n=== Test 3: Auth /me ===")
    try:
        r = requests.get(
            f"{BASE_URL}/api/auth/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=TIMEOUT,
        )
        if r.status_code == 200 and r.json().get("username") == "testuser":
            log_pass("Get current user")
        else:
            log_fail("Get current user", f"Status {r.status_code}")
    except Exception as e:
        log_fail("Get current user", str(e))


def test_strategies_crud(token):
    print("\n=== Test 4: Strategy CRUD ===")
    headers = {"Authorization": f"Bearer {token}"}
    strategy_id = None

    try:
        r = requests.post(
            f"{BASE_URL}/api/strategies/",
            json={
                "name": "RSI Test Strategy",
                "description": "RSI-based mean reversion",
                "code": "def strategy(df, indicators, config):\n    signals = pd.Series(0, index=df.index)\n    rsi = df['rsi_14']\n    signals[rsi < 30] = 1\n    signals[rsi > 70] = -1\n    return signals",
                "language": "python",
                "category": "mean",
                "coins": ["BTC", "ETH"],
                "is_public": True,
                "price_monthly": 9.99,
            },
            headers=headers,
            timeout=TIMEOUT,
        )
        if r.status_code == 201:
            strategy_id = r.json()["id"]
            log_pass("Create strategy")
        else:
            log_fail("Create strategy", f"Status {r.status_code}: {r.text}")
    except Exception as e:
        log_fail("Create strategy", str(e))

    try:
        r = requests.get(f"{BASE_URL}/api/strategies/", headers=headers, timeout=TIMEOUT)
        if r.status_code == 200 and len(r.json()) > 0:
            log_pass("List strategies")
        else:
            log_fail("List strategies", f"Status {r.status_code}")
    except Exception as e:
        log_fail("List strategies", str(e))

    if strategy_id:
        try:
            r = requests.post(
                f"{BASE_URL}/api/strategies/{strategy_id}/publish",
                headers=headers,
                timeout=TIMEOUT,
            )
            if r.status_code == 200 and r.json().get("is_published"):
                log_pass("Publish strategy")
            else:
                log_fail("Publish strategy", f"Status {r.status_code}")
        except Exception as e:
            log_fail("Publish strategy", str(e))

    return strategy_id


def test_marketplace(token):
    print("\n=== Test 5: Marketplace ===")
    headers = {"Authorization": f"Bearer {token}"}
    try:
        r = requests.get(f"{BASE_URL}/api/strategies/marketplace", headers=headers, timeout=TIMEOUT)
        if r.status_code == 200:
            log_pass(f"Marketplace ({len(r.json())} strategies)")
        else:
            log_fail("Marketplace", f"Status {r.status_code}")
    except Exception as e:
        log_fail("Marketplace", str(e))


def test_backtest(token, strategy_id):
    print("\n=== Test 6: Backtest (Real Binance Data) ===")
    headers = {"Authorization": f"Bearer {token}"}
    try:
        print("  Submitting backtest (BTC/USDT 1h, 500 candles)...")
        r = requests.post(
            f"{BASE_URL}/api/backtest/",
            json={
                "strategy_id": strategy_id,
                "config": {
                    "symbol": "BTC/USDT",
                    "timeframe": "1h",
                    "limit": 500,
                    "exchange": "binance",
                    "initial_capital": 10000.0,
                },
            },
            headers=headers,
            timeout=120,
        )
        if r.status_code == 201:
            result = r.json()
            status = result.get("status")
            if status == "completed":
                metrics = result.get("result", {}).get("metrics", {})
                print(f"  Data points: {result.get('result', {}).get('data_points', 0)}")
                print(f"  Trades: {result.get('result', {}).get('trade_count', 0)}")
                print(f"  Total Return: {metrics.get('total_return_pct', 'N/A')}%")
                print(f"  Sharpe: {metrics.get('sharpe_ratio', 'N/A')}")
                print(f"  Max DD: {metrics.get('max_drawdown_pct', 'N/A')}%")
                print(f"  Win Rate: {metrics.get('win_rate_pct', 'N/A')}%")
                log_pass("Backtest with real Binance data")
            else:
                error = result.get("result", {}).get("error", "Unknown")
                log_fail("Backtest", f"Status={status}, Error={error}")
        else:
            log_fail("Backtest", f"HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        log_fail("Backtest", str(e))


def test_subscriptions(token, strategy_id):
    print("\n=== Test 7: Subscriptions ===")
    headers = {"Authorization": f"Bearer {token}"}
    if not strategy_id:
        log_fail("Subscriptions", "No strategy")
        return
    try:
        r = requests.post(
            f"{BASE_URL}/api/subscriptions/",
            json={"strategy_id": strategy_id},
            headers=headers,
            timeout=TIMEOUT,
        )
        if r.status_code == 201:
            log_pass("Subscribe to strategy")
        else:
            log_fail("Subscribe", f"Status {r.status_code}")
    except Exception as e:
        log_fail("Subscribe", str(e))
    try:
        r = requests.get(f"{BASE_URL}/api/subscriptions/", headers=headers, timeout=TIMEOUT)
        if r.status_code == 200 and len(r.json()) > 0:
            log_pass("List subscriptions")
        else:
            log_fail("List subscriptions", f"Status {r.status_code}")
    except Exception as e:
        log_fail("List subscriptions", str(e))


def test_payments(token):
    print("\n=== Test 8: Payment Creation ===")
    headers = {"Authorization": f"Bearer {token}"}
    try:
        r = requests.post(
            f"{BASE_URL}/api/payments/create",
            json={"plan": "pro", "currency": "usdt"},
            headers=headers,
            timeout=TIMEOUT,
        )
        if r.status_code == 200:
            print(f"  Plan: {r.json().get('plan')}, Amount: {r.json().get('amount')} USDT")
            log_pass("Create payment intent")
        else:
            log_fail("Create payment", f"Status {r.status_code}")
    except Exception as e:
        log_fail("Create payment", str(e))
    try:
        r = requests.get(f"{BASE_URL}/api/payments/history", headers=headers, timeout=TIMEOUT)
        if r.status_code == 200:
            log_pass("Payment history")
        else:
            log_fail("Payment history", f"Status {r.status_code}")
    except Exception as e:
        log_fail("Payment history", str(e))


def test_bots(token, strategy_id):
    print("\n=== Test 9: Bot Management ===")
    headers = {"Authorization": f"Bearer {token}"}
    try:
        r = requests.post(
            f"{BASE_URL}/api/bots/",
            json={
                "name": "Test Grid Bot",
                "strategy_id": strategy_id,
                "bot_type": "grid",
                "exchange": "binance",
                "config": {
                    "symbol": "BTC/USDT",
                    "upper_price": 70000,
                    "lower_price": 60000,
                    "grids": 10,
                    "total_investment": 1000,
                    "testnet": True,
                    "api_key": "",
                    "api_secret": "",
                },
            },
            headers=headers,
            timeout=TIMEOUT,
        )
        if r.status_code == 201:
            log_pass("Create bot")
        else:
            log_fail("Create bot", f"Status {r.status_code}: {r.text}")
    except Exception as e:
        log_fail("Create bot", str(e))
    try:
        r = requests.get(f"{BASE_URL}/api/bots/", headers=headers, timeout=TIMEOUT)
        if r.status_code == 200 and len(r.json()) > 0:
            log_pass("List bots")
        else:
            log_fail("List bots", f"Status {r.status_code}")
    except Exception as e:
        log_fail("List bots", str(e))


def test_nonce():
    print("\n=== Test 10: SIWE Nonce ===")
    try:
        r = requests.get(f"{BASE_URL}/api/auth/nonce", timeout=TIMEOUT)
        if r.status_code == 200 and r.json().get("detail", {}).get("nonce"):
            log_pass("Get SIWE nonce")
        else:
            log_fail("Get nonce", f"Status {r.status_code}")
    except Exception as e:
        log_fail("Get nonce", str(e))


def main():
    print("=" * 60)
    print("  QuantEdge API - End-to-End Test Suite")
    print("=" * 60)

    try:
        requests.get(f"{BASE_URL}/health", timeout=5)
    except Exception:
        print(f"\n[ERROR] Server not running at {BASE_URL}")
        print("Start it first:")
        print("  cd d:\\bitcoin\\crypto-quant-web\\backend")
        print("  pip install -r requirements.txt")
        print("  uvicorn main:app --host 0.0.0.0 --port 8000 --reload")
        sys.exit(1)

    test_health()
    test_nonce()
    token = test_register_and_login()
    if not token:
        print("\n[ABORT] Cannot continue without auth token")
        print_summary()
        sys.exit(1)

    test_auth_me(token)
    strategy_id = test_strategies_crud(token)
    test_marketplace(token)
    test_backtest(token, strategy_id)
    test_subscriptions(token, strategy_id)
    test_payments(token)
    test_bots(token, strategy_id)
    print_summary()


def print_summary():
    print("\n" + "=" * 60)
    print(f"  RESULTS: {passed} passed, {failed} failed")
    print("=" * 60)
    if errors:
        print("\nFailures:")
        for e in errors:
            print(f"  - {e}")
    print()


if __name__ == "__main__":
    main()

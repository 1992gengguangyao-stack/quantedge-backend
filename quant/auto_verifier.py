"""
Auto Payment Verifier — Background polling task.

Every 30 seconds, scans all pending payments and checks the receiving
wallet address for incoming transactions on each supported chain.
When a matching transaction is found (correct amount + recipient),
the payment is auto-confirmed and the user's plan is upgraded.

Supported:
  - ETH / USDT / USDC on Ethereum, BSC, Polygon, Arbitrum (via web3.py)
  - BTC on Bitcoin Network (via Blockstream API)
  - USDT TRC-20 on Tron Network (via TronGrid API)

No third-party dependency — just direct RPC polling.
"""

import logging
import time
import threading
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from config import settings
from database import SessionLocal
from models import Payment, User
from quant.payment_verifier import PaymentVerifier, CHAIN_CONFIG, PLAN_PRICES

logger = logging.getLogger("auto_verifier")

# Polling interval (seconds)
POLL_INTERVAL = 30

# Chains to scan for EVM payments
EVM_CHAIN_IDS = [1, 56, 137, 42161]

# TRC-20 USDT contract on Tron
TRX_USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
TRONGRID_API = "https://api.trongrid.io"

# Track already-seen tx hashes to avoid re-processing
_processed_txs: set = set()
_MAX_SEEN = 10000


def _scan_evm_chain(chain_id: int, receiving_address: str, db: Session) -> int:
    """
    Scan one EVM chain for incoming transactions to the receiving address.
    Returns the number of payments confirmed.
    """
    if not receiving_address:
        return 0

    config = CHAIN_CONFIG.get(chain_id, {})
    if not config:
        return 0

    confirmed_count = 0
    verifier = PaymentVerifier(chain_id=chain_id)

    try:
        w3 = verifier.w3
        if not w3:
            return 0

        # Get latest block
        latest_block = w3.eth.block_number
        # Scan last 20 blocks (~4-5 minutes of history)
        start_block = max(0, latest_block - 20)

        # Find pending EVM payments for this chain
        # We don't store chain_id on payment, so we check all pending EVM currency payments
        pending_payments = (
            db.query(Payment)
            .filter(
                Payment.status == "pending",
                Payment.currency.in_(["usdt", "usdc", "eth"]),
            )
            .all()
        )

        if not pending_payments:
            return 0

        addr_lower = receiving_address.lower()

        for block_num in range(start_block, latest_block + 1):
            try:
                block = w3.eth.get_block(block_num, full_transactions=True)
            except Exception:
                continue

            for tx in block.get("transactions", []):
                try:
                    tx_hash = tx.get("hash").hex()
                    if tx_hash in _processed_txs:
                        continue

                    tx_to = (tx.get("to") or "").lower()
                    tx_value = tx.get("value", 0)  # in wei

                    # --- Native ETH transfer ---
                    if tx_to == addr_lower and tx_value > 0:
                        eth_amount = tx_value / 1e18

                        # Match against pending ETH payments
                        for payment in pending_payments:
                            if payment.currency != "eth":
                                continue
                            if _amounts_match(eth_amount, payment.amount):
                                _confirm_payment(db, payment, tx_hash)
                                confirmed_count += 1
                                break

                        _processed_txs.add(tx_hash)
                        continue

                    # --- ERC-20 transfer (USDT/USDC) ---
                    # Check if tx.to is a known token contract
                    token_contracts = {}
                    usdt_addr = config.get("usdt_contract", "").lower()
                    usdc_addr = config.get("usdc_contract", "").lower()
                    if usdt_addr:
                        token_contracts[usdt_addr] = "usdt"
                    if usdc_addr:
                        token_contracts[usdc_addr] = "usdc"

                    if tx_to in token_contracts:
                        currency = token_contracts[tx_to]
                        # Parse Transfer event logs
                        try:
                            receipt = w3.eth.get_transaction_receipt(tx_hash)
                        except Exception:
                            continue

                        # Transfer event topic
                        transfer_topic = (
                            "0xddf252ad1be2c89b69c2b068fc378daa952ba7f1"
                            "63c4a11628f55a4df523b3ef"
                        )

                        for log in receipt.get("logs", []):
                            log_topics = [t.hex() if isinstance(t, bytes) else t for t in log.get("topics", [])]
                            if transfer_topic not in log_topics:
                                continue

                            # Check recipient is our address
                            if len(log.get("topics", [])) >= 3:
                                recipient_topic = log["topics"][2].hex()
                                # Pad address to 32 bytes
                                padded_addr = "0x" + "0" * 24 + receiving_address[2:].lower()
                                if recipient_topic.lower() != padded_addr:
                                    continue

                                # Decode amount from data
                                amount_hex = log.get("data", "0x0")
                                if isinstance(amount_hex, bytes):
                                    amount_hex = amount_hex.hex()
                                if amount_hex.startswith("0x"):
                                    token_amount = int(amount_hex, 16)
                                else:
                                    token_amount = int(amount_hex, 16)

                                # Convert based on decimals
                                decimals = 6  # USDT/USDC use 6 decimals
                                actual_amount = token_amount / (10 ** decimals)

                                # Match against pending payments
                                for payment in pending_payments:
                                    if payment.currency != currency:
                                        continue
                                    if _amounts_match(actual_amount, payment.amount):
                                        _confirm_payment(db, payment, tx_hash)
                                        confirmed_count += 1
                                        break

                        _processed_txs.add(tx_hash)

                except Exception:
                    continue

            # Trim processed set
            if len(_processed_txs) > _MAX_SEEN:
                _processed_txs.clear()

    except Exception as e:
        logger.warning("EVM chain %d scan error: %s", chain_id, e)

    return confirmed_count


def _scan_btc(receiving_address: str, db: Session) -> int:
    """
    Scan Bitcoin address for incoming transactions via Blockstream API.
    Returns the number of payments confirmed.
    """
    if not receiving_address:
        return 0

    confirmed_count = 0

    # Find pending BTC payments
    pending_payments = (
        db.query(Payment)
        .filter(Payment.status == "pending", Payment.currency == "btc")
        .all()
    )

    if not pending_payments:
        return 0

    try:
        import requests

        # Get recent transactions for the address
        resp = requests.get(
            f"https://blockstream.info/api/address/{receiving_address}/txs",
            timeout=10,
        )
        if resp.status_code != 200:
            return 0

        txs = resp.json()
        # Only check recent 20 transactions
        for tx in txs[:20]:
            tx_hash = tx.get("txid", "")
            if tx_hash in _processed_txs:
                continue

            # Check outputs to our address
            for vout in tx.get("vout", []):
                script_pubkey = vout.get("scriptpubkey_address", "")
                if script_pubkey.lower() == receiving_address.lower():
                    btc_amount = vout.get("value", 0) / 1e8  # satoshis to BTC

                    for payment in pending_payments:
                        if _amounts_match(btc_amount, payment.amount):
                            _confirm_payment(db, payment, tx_hash)
                            confirmed_count += 1
                            break

            _processed_txs.add(tx_hash)

    except Exception as e:
        logger.warning("BTC scan error: %s", e)

    return confirmed_count


def _scan_tron(receiving_address: str, db: Session) -> int:
    """
    Scan Tron address for incoming USDT TRC-20 transfers via TronGrid API.
    Returns the number of payments confirmed.
    """
    if not receiving_address:
        return 0

    confirmed_count = 0

    pending_payments = (
        db.query(Payment)
        .filter(Payment.status == "pending", Payment.currency == "usdt")
        .all()
    )

    if not pending_payments:
        return 0

    try:
        import requests

        # Get recent TRC-20 transfers to the receiving address
        url = f"{TRONGRID_API}/v1/accounts/{receiving_address}/transactions/trc20"
        params = {
            "limit": 20,
            "order_by": "block_timestamp,desc",
            "contract_address": TRX_USDT_CONTRACT,
        }
        resp = requests.get(url, params=params, timeout=15)

        if resp.status_code != 200:
            return 0

        data = resp.json()
        transfers = data.get("data", [])

        for transfer in transfers[:20]:
            tx_hash = transfer.get("transaction_id", "")
            if not tx_hash or tx_hash in _processed_txs:
                continue

            # Verify it's a USDT transfer to our address
            to_addr = transfer.get("to", "")
            if to_addr.lower() != receiving_address.lower():
                continue

            # Parse amount (USDT TRC-20 has 6 decimals)
            value = transfer.get("value", "0")
            if isinstance(value, str):
                amount = int(value) / 1e6
            else:
                amount = float(value) / 1e6

            # Match against pending USDT payments
            for payment in pending_payments:
                if _amounts_match(amount, payment.amount):
                    _confirm_payment(db, payment, tx_hash)
                    confirmed_count += 1
                    break

            _processed_txs.add(tx_hash)

    except Exception as e:
        logger.warning("Tron scan error: %s", e)

    return confirmed_count


def _amounts_match(actual: float, expected: float, tolerance: float = 0.02) -> bool:
    """Check if two amounts match within a tolerance (default 2%)."""
    if expected <= 0:
        return False
    diff = abs(actual - expected) / expected
    return diff <= tolerance


def _confirm_payment(db: Session, payment: Payment, tx_hash: str) -> None:
    """Mark a payment as confirmed and upgrade the user's plan."""
    payment.tx_hash = tx_hash
    payment.status = "confirmed"

    # Upgrade user plan
    user = db.query(User).filter(User.id == payment.user_id).first()
    if user:
        user.plan = payment.plan
        logger.info(
            "Payment #%d auto-confirmed: %s %s -> user %d upgraded to %s (tx: %s)",
            payment.id,
            payment.amount,
            payment.currency,
            payment.user_id,
            payment.plan,
            tx_hash,
        )

    db.commit()


def run_auto_verify_loop() -> None:
    """
    Main loop — runs in a background thread.
    Scans all chains every POLL_INTERVAL seconds.
    """
    logger.info("Auto-payment verifier started (interval=%ds)", POLL_INTERVAL)

    while True:
        try:
            db = SessionLocal()

            # Get receiving addresses
            evm_address = settings.PAYMENT_WALLET_ADDRESS or ""
            btc_address = settings.BTC_PAYMENT_ADDRESS or ""
            trx_address = settings.TRX_PAYMENT_ADDRESS or ""

            total_confirmed = 0

            # Scan EVM chains
            if evm_address:
                for chain_id in EVM_CHAIN_IDS:
                    try:
                        confirmed = _scan_evm_chain(chain_id, evm_address, db)
                        total_confirmed += confirmed
                    except Exception as e:
                        logger.warning("Chain %d scan failed: %s", chain_id, e)

            # Scan BTC
            if btc_address:
                try:
                    confirmed = _scan_btc(btc_address, db)
                    total_confirmed += confirmed
                except Exception as e:
                    logger.warning("BTC scan failed: %s", e)

            # Scan Tron (TRC-20 USDT)
            if trx_address:
                try:
                    confirmed = _scan_tron(trx_address, db)
                    total_confirmed += confirmed
                except Exception as e:
                    logger.warning("Tron scan failed: %s", e)

            # Count remaining pending
            pending_count = (
                db.query(Payment)
                .filter(Payment.status == "pending")
                .count()
            )

            if total_confirmed > 0:
                logger.info("Auto-verified %d payments, %d still pending", total_confirmed, pending_count)

            db.close()

        except Exception as e:
            logger.error("Auto-verify loop error: %s", e)

        time.sleep(POLL_INTERVAL)


def start_auto_verifier() -> None:
    """Start the auto-verifier in a daemon thread."""
    thread = threading.Thread(target=run_auto_verify_loop, daemon=True)
    thread.start()
    logger.info("Auto-payment verifier thread launched")

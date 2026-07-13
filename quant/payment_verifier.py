"""
On-chain payment verification using web3.py.
Verifies cryptocurrency transactions on Ethereum and other EVM chains.
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Union

import requests as req_lib
from web3 import Web3

import sys
sys.path.insert(0, ".")
from config import settings

logger = logging.getLogger("quantedge.payment")

# TRC-20 USDT contract on Tron
TRX_USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
TRONGRID_API = "https://api.trongrid.io"

# ERC-20 ABI (minimal, for balance/transfer checks)
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "type": "function",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "from", "type": "address"},
            {"indexed": True, "name": "to", "type": "address"},
            {"indexed": False, "name": "value", "type": "uint256"},
        ],
        "name": "Transfer",
        "type": "event",
    },
]

# Chain configurations
CHAIN_CONFIG = {
    1: {
        "name": "Ethereum",
        "rpc_url": "https://eth.llamarpc.com",
        "explorer": "https://etherscan.io/tx/",
        "usdt": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
        "usdc": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "native_symbol": "ETH",
    },
    56: {
        "name": "BSC",
        "rpc_url": "https://bsc-dataseed.binance.org",
        "explorer": "https://bscscan.com/tx/",
        "usdt": "0x55d398326f99059fF775485246999027B3197955",
        "usdc": "0x8AC76A51cc950d9822D68b8FEb1b8C0E3E8AeD41",
        "native_symbol": "BNB",
    },
    137: {
        "name": "Polygon",
        "rpc_url": "https://polygon-rpc.com",
        "explorer": "https://polygonscan.com/tx/",
        "usdt": "0xc2132D05D31c914a87C6611C10748AEb04B58e8F",
        "usdc": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
        "native_symbol": "MATIC",
    },
    42161: {
        "name": "Arbitrum",
        "rpc_url": "https://arb1.arbitrum.io/rpc",
        "explorer": "https://arbiscan.io/tx/",
        "usdt": "0xFd086bC7CD5C481D9C456fC8dE1a49E0e9d9e7E4",
        "usdc": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        "native_symbol": "ETH",
    },
}

# Pricing for plans (in USD)
PLAN_PRICES = {
    "starter": 29.0,
    "pro": 79.0,
    "expert": 199.0,
}


class PaymentVerifier:
    """Verify cryptocurrency payments on-chain."""

    def __init__(self, chain_id: int = 1, rpc_url: str = None):
        self.chain_id = chain_id
        config = CHAIN_CONFIG.get(chain_id, {})

        if rpc_url:
            self.rpc_url = rpc_url
        elif settings.WEB3_RPC_URL:
            self.rpc_url = settings.WEB3_RPC_URL
        else:
            self.rpc_url = config.get("rpc_url", "https://eth.llamarpc.com")

        self._w3 = None
        self._chain_config = config

    @property
    def w3(self):
        """Lazily initialize Web3 connection."""
        if self._w3 is None:
            self._w3 = Web3(Web3.HTTPProvider(self.rpc_url))
            if not self._w3.is_connected():
                raise ConnectionError(f"Failed to connect to RPC: {self.rpc_url}")
        return self._w3

    def verify_eth_payment(
        self,
        tx_hash: str,
        expected_amount: float = None,
        recipient_address: str = None,
    ) -> dict:
        """
        Verify an ETH (native) payment transaction.

        Args:
            tx_hash: Transaction hash
            expected_amount: Expected amount in ETH
            recipient_address: Expected recipient address

        Returns:
            Verification result dict
        """
        try:
            tx = self.w3.eth.get_transaction(tx_hash)
            receipt = self.w3.eth.get_transaction_receipt(tx_hash)

            if receipt is None:
                return {"verified": False, "error": "Transaction not found"}

            if not receipt["status"] == 1:
                return {"verified": False, "error": "Transaction failed on-chain"}

            value_eth = self.w3.from_wei(tx["value"], "ether")
            sender = tx["from"]
            to = tx["to"]

            verified = True
            issues = []

            if expected_amount and abs(value_eth - expected_amount) > 0.001:
                verified = False
                issues.append(f"Amount mismatch: expected {expected_amount} ETH, got {value_eth}")

            if recipient_address and to.lower() != recipient_address.lower():
                verified = False
                issues.append(f"Recipient mismatch: expected {recipient_address}, got {to}")

            return {
                "verified": verified,
                "tx_hash": tx_hash,
                "chain": self._chain_config.get("name", "Ethereum"),
                "sender": sender,
                "recipient": to,
                "amount": float(value_eth),
                "currency": "ETH",
                "block_number": receipt["blockNumber"],
                "gas_used": receipt["gasUsed"],
                "issues": issues if issues else None,
                "explorer_url": self._chain_config.get("explorer", "") + tx_hash,
            }

        except Exception as e:
            logger.error(f"ETH payment verification failed: {e}")
            return {"verified": False, "error": str(e)}

    def verify_erc20_payment(
        self,
        tx_hash: str,
        token: str = "usdt",
        expected_amount: float = None,
        recipient_address: str = None,
    ) -> dict:
        """
        Verify an ERC-20 token payment (USDT/USDC).

        Args:
            tx_hash: Transaction hash
            token: Token symbol ("usdt" or "usdc")
            expected_amount: Expected amount in token units
            recipient_address: Expected recipient address

        Returns:
            Verification result dict
        """
        try:
            tx = self.w3.eth.get_transaction(tx_hash)
            receipt = self.w3.eth.get_transaction_receipt(tx_hash)

            if receipt is None:
                return {"verified": False, "error": "Transaction not found"}

            if not receipt["status"] == 1:
                return {"verified": False, "error": "Transaction failed on-chain"}

            # Get token contract address
            token_addr = self._chain_config.get(token.lower())
            if not token_addr:
                return {"verified": False, "error": f"Token {token} not supported on chain {self.chain_id}"}

            # Parse transfer event from receipt logs
            transfer_amount = None
            transfer_from = None
            transfer_to = None

            token_addr_lower = token_addr.lower()
            transfer_topic = self._w3.keccak(text="Transfer(address,address,uint256)").hex()

            for log in receipt["logs"]:
                # Check if this log is from the token contract
                if log["address"].lower() != token_addr_lower:
                    continue

                # Check if it's a Transfer event
                if len(log["topics"]) < 3:
                    continue

                if log["topics"][0].hex() != transfer_topic:
                    continue

                # Decode Transfer event
                transfer_from = "0x" + log["topics"][1].hex()[26:]
                transfer_to = "0x" + log["topics"][2].hex()[26:]

                # Get token decimals
                contract = self.w3.eth.contract(
                    address=self._w3.to_checksum_address(token_addr),
                    abi=ERC20_ABI,
                )
                decimals = contract.functions.decimals().call()
                transfer_amount = int(log["data"].hex(), 16) / (10 ** decimals)
                break

            if transfer_amount is None:
                return {
                    "verified": False,
                    "error": "No transfer event found in transaction",
                    "tx_hash": tx_hash,
                }

            verified = True
            issues = []

            if expected_amount and abs(transfer_amount - expected_amount) > 0.01:
                verified = False
                issues.append(
                    f"Amount mismatch: expected {expected_amount} {token.upper()}, got {transfer_amount}"
                )

            if recipient_address and transfer_to.lower() != recipient_address.lower():
                verified = False
                issues.append(
                    f"Recipient mismatch: expected {recipient_address}, got {transfer_to}"
                )

            return {
                "verified": verified,
                "tx_hash": tx_hash,
                "chain": self._chain_config.get("name", "Ethereum"),
                "token": token.upper(),
                "sender": transfer_from,
                "recipient": transfer_to,
                "amount": transfer_amount,
                "currency": token.upper(),
                "block_number": receipt["blockNumber"],
                "gas_used": receipt["gasUsed"],
                "issues": issues if issues else None,
                "explorer_url": self._chain_config.get("explorer", "") + tx_hash,
            }

        except Exception as e:
            logger.error(f"ERC20 payment verification failed: {e}")
            return {"verified": False, "error": str(e)}

    def verify_btc_payment(
        self,
        tx_hash: str,
        expected_amount: float = None,
        recipient_address: str = None,
    ) -> dict:
        """
        Verify a Bitcoin payment using a public blockchain API.
        Uses Blockstream API (no API key required).
        """
        import requests

        try:
            # Use Blockstream API
            url = f"https://blockstream.info/api/tx/{tx_hash}"
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            tx_data = resp.json()

            # Find outputs matching recipient
            total_received = 0.0
            sender = tx_data.get("vin", [{}])[0].get("prevout", {}).get("scriptpubkey_address", "")

            for vout in tx_data.get("vout", []):
                addr = vout.get("scriptpubkey_address", "")
                if recipient_address and addr == recipient_address:
                    total_received += vout.get("value", 0) / 1e8  # Satoshis to BTC
                elif not recipient_address:
                    total_received += vout.get("value", 0) / 1e8

            verified = True
            issues = []

            if expected_amount and abs(total_received - expected_amount) > 0.0001:
                verified = False
                issues.append(f"Amount mismatch: expected {expected_amount} BTC, got {total_received}")

            # Check confirmations
            status = tx_data.get("status", {})
            confirmed = status.get("confirmed", False)
            confirmations = 0
            if confirmed:
                # Get latest block height
                latest_block = requests.get("https://blockstream.info/api/blocks/tip/height", timeout=10)
                latest_block.raise_for_status()
                latest_height = int(latest_block.text)
                tx_block = status.get("block_height", 0)
                confirmations = latest_height - tx_block + 1 if tx_block else 0

            if confirmations < 3:
                issues.append(f"Low confirmations: {confirmations} (recommended: 3+)")

            return {
                "verified": verified and confirmed,
                "tx_hash": tx_hash,
                "chain": "Bitcoin",
                "sender": sender,
                "recipient": recipient_address,
                "amount": total_received,
                "currency": "BTC",
                "confirmed": confirmed,
                "confirmations": confirmations,
                "block_height": status.get("block_height"),
                "issues": issues if issues else None,
                "explorer_url": f"https://blockstream.info/tx/{tx_hash}",
            }

        except Exception as e:
            logger.error(f"BTC payment verification failed: {e}")
            return {"verified": False, "error": str(e)}

    def verify_trc20_payment(
        self,
        tx_hash: str,
        expected_amount: float = None,
        recipient_address: str = None,
    ) -> dict:
        """Verify a USDT TRC-20 payment on Tron using TronGrid API."""
        try:
            url = f"{TRONGRID_API}/v1/transactions/{tx_hash}"
            resp = req_lib.get(url, timeout=15)
            if resp.status_code != 200:
                return {"verified": False, "error": f"TronGrid API error: {resp.status_code}"}

            data = resp.json()
            tx_data = data.get("data", [{}])[0] if data.get("data") else {}

            # Check if transaction is confirmed
            ret = tx_data.get("ret", [{}])
            if not ret or ret[0].get("contractRet") != "SUCCESS":
                return {"verified": False, "error": "Transaction not successful or pending"}

            # Parse TRC-20 Transfer event from smart contract logs
            # Look for the Transfer event in the transaction's logs
            transfers = []
            # Check contract data
            for contract in tx_data.get("ret", []):
                pass  # ret only has status

            # Parse from the raw transaction data
            # TRC-20 transfers are in the "log" field of the transaction
            logs = []
            # Try to get from events endpoint
            events_url = f"{TRONGRID_API}/v1/transactions/{tx_hash}/events"
            try:
                events_resp = req_lib.get(events_url, timeout=10)
                if events_resp.status_code == 200:
                    logs = events_resp.json().get("data", [])
            except Exception:
                pass

            # Also check the transaction info endpoint for contract logs
            if not logs:
                info_url = f"{TRONGRID_API}/v1/transactions/{tx_hash}/info"
                try:
                    info_resp = req_lib.get(info_url, timeout=10)
                    if info_resp.status_code == 200:
                        info_data = info_resp.json().get("data", {})
                        logs = info_data.get("log", [])
                        # Also check receipt
                        receipt = info_data.get("receipt", {})
                        if receipt.get("result") != "SUCCESS":
                            return {"verified": False, "error": "Transaction receipt shows failure"}
                except Exception:
                    pass

            # Parse TRC-20 Transfer events
            # Transfer event topic for TRC-20: ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef
            transfer_topic = "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

            for log_entry in logs:
                # TronGrid events format
                if isinstance(log_entry, dict):
                    # Check if it's a TRC-20 Transfer event
                    topics = log_entry.get("topics", [])
                    if not topics:
                        # Try alternative format from events endpoint
                        contract_addr = log_entry.get("contract_address", "")
                        if contract_addr == TRX_USDT_CONTRACT or contract_addr == TRX_USDT_CONTRACT.lower():
                            from_addr = log_entry.get("from", "")
                            to_addr = log_entry.get("to", "")
                            amount_raw = log_entry.get("value", 0)
                            if isinstance(amount_raw, str):
                                amount = int(amount_raw) / 1e6  # USDT has 6 decimals
                            else:
                                amount = amount_raw / 1e6

                            if recipient_address and to_addr.lower() != recipient_address.lower():
                                continue

                            verified = True
                            issues = []
                            if expected_amount and abs(amount - expected_amount) > 0.01:
                                verified = False
                                issues.append(f"Amount mismatch: expected {expected_amount}, got {amount}")

                            return {
                                "verified": verified,
                                "tx_hash": tx_hash,
                                "chain": "Tron (TRC-20)",
                                "token": "USDT",
                                "sender": from_addr,
                                "recipient": to_addr,
                                "amount": amount,
                                "currency": "USDT",
                                "issues": issues if issues else None,
                                "explorer_url": f"https://tronscan.org/#/transaction/{tx_hash}",
                            }

                    # EVM-style log format
                    if topics and len(topics) >= 3 and topics[0].replace("0x", "") == transfer_topic:
                        # Parse from topics
                        from_hex = topics[1].replace("0x", "")
                        to_hex = topics[2].replace("0x", "")
                        from_addr = "0x" + from_hex[-40:]  # Last 20 bytes
                        to_addr = "0x" + to_hex[-40:]

                        # For Tron, addresses are base58 - try data field
                        data_hex = log_entry.get("data", "0x0")
                        if isinstance(data_hex, str) and data_hex.startswith("0x"):
                            token_amount = int(data_hex, 16)
                        else:
                            token_amount = int(str(data_hex), 16) if data_hex else 0

                        amount = token_amount / 1e6  # USDT 6 decimals

                        if recipient_address and to_addr.lower() != recipient_address.lower():
                            continue

                        verified = True
                        issues = []
                        if expected_amount and abs(amount - expected_amount) > 0.01:
                            verified = False
                            issues.append(f"Amount mismatch: expected {expected_amount}, got {amount}")

                        return {
                            "verified": verified,
                            "tx_hash": tx_hash,
                            "chain": "Tron (TRC-20)",
                            "token": "USDT",
                            "sender": from_addr,
                            "recipient": to_addr,
                            "amount": amount,
                            "currency": "USDT",
                            "issues": issues if issues else None,
                            "explorer_url": f"https://tronscan.org/#/transaction/{tx_hash}",
                        }

            return {"verified": False, "error": "No USDT TRC-20 transfer found in transaction"}

        except Exception as e:
            logger.error(f"TRC-20 payment verification failed: {e}")
            return {"verified": False, "error": str(e)}

    def verify_payment(
        self,
        tx_hash: str,
        currency: str = "usdt",
        expected_amount: float = None,
        recipient_address: str = None,
        chain_id: Union[int, str] = None,
    ) -> dict:
        """
        Universal payment verification dispatcher.
        Routes to the correct verifier based on currency and chain.
        """
        currency = currency.lower()

        if currency == "btc":
            return self.verify_btc_payment(tx_hash, expected_amount, recipient_address)

        # TRC-20 USDT on Tron
        if currency in ("usdt", "usdc") and (chain_id == "trx" or chain_id == "trc20"):
            return self.verify_trc20_payment(tx_hash, expected_amount, recipient_address)

        if currency == "eth":
            if chain_id and isinstance(chain_id, int):
                self.chain_id = chain_id
                self._chain_config = CHAIN_CONFIG.get(chain_id, {})
                self._w3 = None
            return self.verify_eth_payment(tx_hash, expected_amount, recipient_address)

        if currency in ("usdt", "usdc"):
            if chain_id and isinstance(chain_id, int):
                self.chain_id = chain_id
                self._chain_config = CHAIN_CONFIG.get(chain_id, {})
                self._w3 = None
            return self.verify_erc20_payment(tx_hash, currency, expected_amount, recipient_address)

        return {"verified": False, "error": f"Unsupported currency: {currency}"}

    def get_plan_price(self, plan: str, currency: str = "usdt") -> float:
        """Get the price of a plan in the specified currency."""
        usd_price = PLAN_PRICES.get(plan.lower(), 0)
        if not usd_price:
            return 0

        currency = currency.lower()
        if currency in ("usdt", "usdc"):
            return usd_price  # 1:1 with USD
        elif currency == "eth":
            # Fetch current ETH price (try Binance first, then Coinbase)
            try:
                import requests
                resp = requests.get("https://data-api.binance.vision/api/v3/ticker/price?symbol=ETHUSDT", timeout=10)
                eth_price = float(resp.json()["price"])
                return round(usd_price / eth_price, 6)
            except Exception:
                pass
            try:
                import requests
                resp = requests.get("https://api.coinbase.com/v2/prices/ETH-USD/spot", timeout=10)
                eth_price = float(resp.json()["data"]["amount"])
                return round(usd_price / eth_price, 6)
            except Exception:
                return 0
        elif currency == "btc":
            try:
                import requests
                resp = requests.get("https://data-api.binance.vision/api/v3/ticker/price?symbol=BTCUSDT", timeout=10)
                btc_price = float(resp.json()["price"])
                return round(usd_price / btc_price, 8)
            except Exception:
                pass
            try:
                import requests
                resp = requests.get("https://api.coinbase.com/v2/prices/BTC-USD/spot", timeout=10)
                btc_price = float(resp.json()["data"]["amount"])
                return round(usd_price / btc_price, 8)
            except Exception:
                return 0
        return usd_price

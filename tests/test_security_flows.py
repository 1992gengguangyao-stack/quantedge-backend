import time
import unittest
from urllib.parse import urlparse
from unittest.mock import Mock, patch

from fastapi import HTTPException

from quant.payment_verifier import PaymentVerifier, TRX_USDT_CONTRACT
from quant.auto_verifier import _amounts_match, _confirm_payment
from routers.auth import _allowed_siwe_domain, _siwe_uri_matches
from routers.dex import SignedExchangeRequest, relay_signed_exchange
from routers.analytics import _hash_identifier, _origin_allowed, _safe_properties
from routers.payments import _allocate_unique_amount


RECIPIENT_HEX = "41" + "11" * 20


class PaymentVerifierTests(unittest.TestCase):
    @patch("quant.payment_verifier.req_lib.get")
    def test_trc20_confirmed_exact_transfer(self, get):
        recipient = PaymentVerifier._normalize_tron_address(RECIPIENT_HEX)
        tx = Mock(status_code=200)
        tx.json.return_value = {"data": [{"ret": [{"contractRet": "SUCCESS"}], "blockNumber": 1}]}
        events = Mock(status_code=200)
        events.json.return_value = {"data": [{
            "event_name": "Transfer", "contract_address": TRX_USDT_CONTRACT,
            "result": {"from": "41" + "22" * 20, "to": RECIPIENT_HEX, "value": "29000000"},
        }]}
        get.side_effect = [tx, events]

        result = PaymentVerifier().verify_trc20_payment("a" * 64, 29.0, recipient)
        self.assertTrue(result["verified"])
        self.assertEqual(result["recipient"], recipient)
        self.assertEqual(result["amount"], 29.0)

    @patch("quant.payment_verifier.req_lib.get")
    def test_trc20_rejects_amount_mismatch(self, get):
        recipient = PaymentVerifier._normalize_tron_address(RECIPIENT_HEX)
        tx = Mock(status_code=200)
        tx.json.return_value = {"data": [{"ret": [{"contractRet": "SUCCESS"}], "blockNumber": 1}]}
        events = Mock(status_code=200)
        events.json.return_value = {"data": [{
            "event_name": "Transfer", "contract_address": TRX_USDT_CONTRACT,
            "result": {"from": "41" + "22" * 20, "to": RECIPIENT_HEX, "value": "28999999"},
        }]}
        get.side_effect = [tx, events]
        result = PaymentVerifier().verify_trc20_payment("b" * 64, 29.0, recipient)
        self.assertFalse(result["verified"])

    def test_auto_match_requires_exact_stablecoin_quote(self):
        self.assertTrue(_amounts_match(29.000001, 29.000001, "usdt"))
        self.assertFalse(_amounts_match(29.01, 29.000001, "usdt"))

    def test_auto_match_requires_exact_bitcoin_satoshis(self):
        self.assertTrue(_amounts_match(0.00081234, 0.00081234, "btc"))
        self.assertFalse(_amounts_match(0.00081235, 0.00081234, "btc"))

    def test_payment_quotes_allocate_distinct_exact_units(self):
        db = Mock()
        db.query.return_value.filter.return_value.all.return_value = [(29.000001,)]
        self.assertEqual(_allocate_unique_amount(db, "usdt", 29.0), 29.000002)

    def test_auto_confirm_rejects_reused_transaction_hash(self):
        db = Mock()
        db.query.return_value.filter.return_value.first.return_value = Mock()
        payment = Mock(
            id=7,
            user_id=3,
            amount=29.000001,
            currency="usdt",
            plan="starter",
            status="pending",
        )
        self.assertFalse(_confirm_payment(db, payment, "reused-hash"))
        self.assertEqual(payment.status, "pending")
        db.commit.assert_not_called()


class WalletOriginTests(unittest.TestCase):
    def test_public_wallet_login_requires_https(self):
        self.assertTrue(_allowed_siwe_domain("aiquantbtc.com"))
        self.assertTrue(_siwe_uri_matches("aiquantbtc.com", urlparse("https://aiquantbtc.com")))
        self.assertFalse(_siwe_uri_matches("aiquantbtc.com", urlparse("http://aiquantbtc.com")))

    def test_preview_and_local_origins_are_scoped(self):
        self.assertTrue(_allowed_siwe_domain("abc123.aiquantbtc.pages.dev"))
        self.assertFalse(_allowed_siwe_domain("aiquantbtc.pages.dev.evil.example"))
        self.assertTrue(_siwe_uri_matches("localhost:8000", urlparse("http://localhost:8000")))
        self.assertFalse(_siwe_uri_matches("localhost:8000", urlparse("http://127.0.0.1:8000")))


class SignedRelayTests(unittest.TestCase):
    def request(self, nonce=None):
        return SignedExchangeRequest(
            action={"type": "cancel", "cancels": [{"a": 0, "o": 1}]},
            nonce=nonce or int(time.time() * 1000),
            signature={"r": "0x" + "1" * 64, "s": "0x" + "2" * 64, "v": 27},
        )

    @patch("routers.dex.requests.post")
    def test_relay_forwards_signature_without_private_key(self, post):
        post.return_value = Mock(status_code=200)
        post.return_value.json.return_value = {"status": "ok"}
        result = relay_signed_exchange(self.request(), current_user=Mock())
        self.assertEqual(result, {"status": "ok"})
        payload = post.call_args.kwargs["json"]
        self.assertNotIn("private_key", payload)
        self.assertIn("signature", payload)

    def test_relay_rejects_stale_nonce(self):
        with self.assertRaises(HTTPException):
            relay_signed_exchange(self.request(1), current_user=Mock())


class AnalyticsPrivacyTests(unittest.TestCase):
    def test_identifiers_are_hashed_before_storage(self):
        raw = "visitor-12345678"
        digest = _hash_identifier(raw)
        self.assertNotEqual(digest, raw)
        self.assertEqual(len(digest), 64)
        self.assertEqual(digest, _hash_identifier(raw))

    def test_properties_are_bounded_and_scalar_only(self):
        result = _safe_properties({
            "plan": "starter",
            "long": "x" * 300,
            "nested": {"wallet": "must-not-be-stored"},
        })
        self.assertEqual(result["plan"], "starter")
        self.assertEqual(len(result["long"]), 160)
        self.assertNotIn("nested", result)

    def test_only_owned_web_origins_are_allowed(self):
        self.assertTrue(_origin_allowed("https://aiquantbtc.com"))
        self.assertTrue(_origin_allowed("https://abc123.aiquantbtc.pages.dev"))
        self.assertFalse(_origin_allowed("https://example.com"))


if __name__ == "__main__":
    unittest.main()

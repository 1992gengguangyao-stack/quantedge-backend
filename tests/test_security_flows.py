import time
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException

from quant.payment_verifier import PaymentVerifier, TRX_USDT_CONTRACT
from routers.dex import SignedExchangeRequest, relay_signed_exchange


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


if __name__ == "__main__":
    unittest.main()

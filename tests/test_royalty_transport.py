"""No network or wallet access: Coinos royalty transport boundary tests."""
import io
import json
import unittest
import urllib.error

from royalty_transport import (
    UncertainRoyaltyTransfer, prepare_internal_royalty_transfer,
    royalty_transfer_fingerprint, submit_internal_royalty_transfer_once,
    validate_coinos_endpoint,
)


class _Response:
    def __init__(self, body):
        self.body = io.BytesIO(body)

    def __enter__(self):
        return self.body

    def __exit__(self, *_):
        return False


class _FakeOpener:
    def __init__(self, body=b'{"id":"remote-123"}', error=None):
        self.calls = []
        self.body = body
        self.error = error

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        if self.error:
            raise self.error
        return _Response(self.body)


class RoyaltyTransportTests(unittest.TestCase):
    def test_destination_is_fixed(self):
        self.assertEqual(prepare_internal_royalty_transfer(17), {
            "username": "bitcoinisfreedombot", "amount": 17
        })
        with self.assertRaises(ValueError):
            prepare_internal_royalty_transfer(17, "attacker@coinos.io")
        for invalid in (0, -1, 1.5, True, "100"):
            with self.assertRaises(ValueError):
                prepare_internal_royalty_transfer(invalid)

    def test_endpoint_cannot_exfiltrate_token(self):
        self.assertEqual(validate_coinos_endpoint("https://coinos.io/api"),
                         "https://coinos.io/api")
        for invalid in ("http://coinos.io/api",
                        "https://coinos.io.attacker.com/api",
                        "https://coinos.io@evil.test/api",
                        "https://coinos.io:8443/api",
                        "https://coinos.io/api?token=x"):
            with self.assertRaises(ValueError):
                validate_coinos_endpoint(invalid)

    def test_one_shot_request_is_correct_and_unverified(self):
        fake = _FakeOpener()
        result = submit_internal_royalty_transfer_once(
            base_url="https://coinos.io/api", token="secret",
            amount_sats=23, opener=fake
        )
        self.assertEqual(result, {
            "payment_id": "remote-123", "verification_required": True
        })
        self.assertEqual(len(fake.calls), 1)
        request, timeout = fake.calls[0]
        self.assertEqual(request.full_url,
                         "https://coinos.io/api/payments/internal")
        self.assertEqual(json.loads(request.data), {
            "username": "bitcoinisfreedombot", "amount": 23
        })
        self.assertEqual(request.get_header("Authorization"), "Bearer secret")
        self.assertEqual(timeout, 60)

    def test_network_error_and_bad_reply_remain_unknown(self):
        for fake in (_FakeOpener(error=urllib.error.URLError("timeout")),
                     _FakeOpener(body=b'{}'),
                     _FakeOpener(body=b'not json')):
            with self.assertRaises(UncertainRoyaltyTransfer):
                submit_internal_royalty_transfer_once(
                    base_url="https://coinos.io/api", token="secret",
                    amount_sats=23, opener=fake
                )
            self.assertEqual(len(fake.calls), 1)

    def test_stable_batch_fingerprint(self):
        self.assertEqual(royalty_transfer_fingerprint(7, 23),
                         royalty_transfer_fingerprint(7, 23))
        self.assertNotEqual(royalty_transfer_fingerprint(7, 23),
                            royalty_transfer_fingerprint(8, 23))


if __name__ == "__main__":
    unittest.main()

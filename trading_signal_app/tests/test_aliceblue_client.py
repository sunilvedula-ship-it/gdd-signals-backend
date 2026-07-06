import os
import unittest
from datetime import date
from unittest.mock import patch

from backend.aliceblue_client import (
    AliceBlueInstrument,
    prepare_order,
    resolve_instrument,
)
from backend.credentials import deobfuscate, obfuscate
from backend.security import SignedTokenError, create_signed_token, verify_signed_token


class AliceBlueContractTests(unittest.TestCase):
    def setUp(self):
        self.future = AliceBlueInstrument(
            exchange="NFO",
            instrument_id="61088",
            symbol="BANKNIFTY",
            trading_symbol="BANKNIFTY28JUL26F",
            instrument_type="FUTIDX",
            option_type="XX",
            strike_price=-1,
            expiry_date=date(2026, 7, 28),
            lot_size=30,
            tick_size=0.2,
        )
        self.option = AliceBlueInstrument(
            exchange="NFO",
            instrument_id="70123",
            symbol="BANKNIFTY",
            trading_symbol="BANKNIFTY28JUL26C58500",
            instrument_type="OPTIDX",
            option_type="CE",
            strike_price=58500,
            expiry_date=date(2026, 7, 28),
            lot_size=30,
            tick_size=0.05,
        )

    @patch("backend.aliceblue_client._ist_today", return_value=date(2026, 7, 6))
    @patch("backend.aliceblue_client._contracts")
    def test_resolves_nearest_future(self, contracts, _today):
        later = AliceBlueInstrument(
            **{**self.future.__dict__, "instrument_id": "68390", "expiry_date": date(2026, 9, 29)}
        )
        contracts.return_value = [later, self.future]
        resolved = resolve_instrument("BANKNIFTY")
        self.assertEqual(resolved.instrument_id, "61088")

    @patch("backend.aliceblue_client._contracts")
    def test_resolves_exact_option_contract(self, contracts):
        contracts.return_value = [self.option]
        resolved = resolve_instrument("BANKNIFTY 28JUL26 58500 CE")
        self.assertEqual(resolved.trading_symbol, "BANKNIFTY28JUL26C58500")

    def test_prepared_order_uses_contract_lot_size_and_tick(self):
        prepared = prepare_order(
            "BANKNIFTY",
            "LONG",
            2,
            58450.11,
            "POSITIONAL",
            instrument=self.future,
        )
        self.assertEqual(prepared["quantity"], 60)
        self.assertEqual(prepared["limit_price"], 58450.2)
        self.assertEqual(prepared["product"], "LONGTERM")


class BrokerSecurityTests(unittest.TestCase):
    def test_credentials_use_environment_backed_encryption(self):
        with patch.dict(os.environ, {"CREDENTIAL_ENCRYPTION_KEY": "test-only-key"}, clear=False):
            encrypted = obfuscate("session-token")
            self.assertTrue(encrypted.startswith("fernet:"))
            self.assertEqual(deobfuscate(encrypted), "session-token")

    def test_signed_token_rejects_tampering(self):
        with patch.dict(os.environ, {"BROKER_AUTH_STATE_SECRET": "test-signing-secret"}, clear=False):
            token = create_signed_token({"purpose": "test"}, 60)
            self.assertEqual(verify_signed_token(token)["purpose"], "test")
            with self.assertRaises(SignedTokenError):
                verify_signed_token(token + "x")


if __name__ == "__main__":
    unittest.main()

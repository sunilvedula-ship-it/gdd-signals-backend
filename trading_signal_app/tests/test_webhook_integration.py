import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


TEST_DATABASE_DIR = tempfile.TemporaryDirectory()
TEST_DATABASE_PATH = Path(TEST_DATABASE_DIR.name) / "webhook-test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DATABASE_PATH.as_posix()}"
os.environ["VALID_SECRETS"] = "integration-test-secret"
os.environ["ALLOW_SANDBOX_AUTH"] = "true"
os.environ["LIVE_TRADING_ENABLED"] = "false"
os.environ.pop("WEBHOOK_AUTO_EXECUTION_MODE", None)

from fastapi.testclient import TestClient

from backend.credentials import AppCredentialsManager
from backend.database import BrokerOrder, Position, SessionLocal, Signal, engine, init_db
from backend.main import app


class BankNiftyWebhookIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()

        db = SessionLocal()
        try:
            AppCredentialsManager(db, user_id=1).save_credentials(
                "flattrade",
                "test-api-key",
                "test-api-secret"
            )
        finally:
            db.close()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        engine.dispose()
        TEST_DATABASE_DIR.cleanup()

    def test_linked_broker_still_creates_positional_paper_trade(self):
        response = self.client.post(
            "/api/signals/webhook",
            json={
                "secret": "integration-test-secret",
                "symbol": "BANKNIFTY",
                "price": 58450,
                "orderId": "positional_index",
                "type": "long",
                "timeframe": "5m"
            }
        )

        self.assertEqual(response.status_code, 200, response.text)

        db = SessionLocal()
        try:
            signal = db.query(Signal).order_by(Signal.id.desc()).first()
            position = db.query(Position).order_by(Position.id.desc()).first()

            self.assertIsNotNone(signal)
            self.assertEqual(signal.trade_type, "POSITIONAL")
            self.assertIsNotNone(position)
            self.assertEqual(position.real_or_paper, "PAPER")
            self.assertEqual(position.trade_type, "POSITIONAL")
            self.assertTrue(position.symbol.startswith("BANKNIFTY "))
        finally:
            db.close()

    def test_database_migration_repairs_old_positional_signal_label(self):
        db = SessionLocal()
        try:
            signal = Signal(
                symbol="BANKNIFTY",
                action="LONG",
                price=58450,
                source="TradingView",
                source_name="positional_index",
                raw_payload="{}",
                timeframe="5m",
                trade_type="INTRADAY"
            )
            db.add(signal)
            db.commit()
            signal_id = signal.id
        finally:
            db.close()

        init_db()

        db = SessionLocal()
        try:
            repaired_signal = db.query(Signal).filter(Signal.id == signal_id).one()
            self.assertEqual(repaired_signal.trade_type, "POSITIONAL")
        finally:
            db.close()

    def test_manual_paper_order_remains_available(self):
        db = SessionLocal()
        try:
            signal = Signal(
                symbol="NIFTY",
                action="LONG",
                price=24414,
                source="TradingView",
                source_name="manual-paper-test",
                raw_payload="{}",
                timeframe="5m",
                trade_type="INTRADAY",
            )
            db.add(signal)
            db.commit()
            signal_id = signal.id
        finally:
            db.close()

        with patch("backend.main.get_live_market_price", return_value=None):
            response = self.client.post(
                "/api/broker/execute",
                json={"signal_id": signal_id, "trade_type": "FUTURE", "mode": "PAPER", "lots": 1},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["order_status"], "FILLED")

    def test_live_order_is_locked_until_server_enablement(self):
        db = SessionLocal()
        try:
            signal = Signal(
                symbol="NIFTY",
                action="LONG",
                price=24414,
                source="TradingView",
                source_name="live-lock-test",
                raw_payload="{}",
                timeframe="5m",
                trade_type="INTRADAY",
            )
            db.add(signal)
            db.commit()
            signal_id = signal.id
        finally:
            db.close()

        response = self.client.post(
            "/api/broker/execute",
            json={"signal_id": signal_id, "trade_type": "FUTURE", "mode": "LIVE", "lots": 1},
        )
        self.assertEqual(response.status_code, 503, response.text)

    def test_aliceblue_live_preview_and_submission_are_audited(self):
        db = SessionLocal()
        try:
            signal = Signal(
                symbol="NIFTY",
                action="LONG",
                price=24414,
                source="TradingView",
                source_name="aliceblue-live-test",
                raw_payload="{}",
                timeframe="5m",
                trade_type="INTRADAY",
            )
            db.add(signal)
            db.commit()
            signal_id = signal.id
        finally:
            db.close()

        prepared = {
            "broker_id": "aliceblue",
            "broker_name": "Alice Blue",
            "symbol": "NIFTY",
            "trading_symbol": "NIFTY28JUL26F",
            "instrument_id": "61093",
            "exchange": "NFO",
            "transaction_type": "BUY",
            "quantity": 65,
            "lot_size": 65,
            "lots": 1,
            "product": "INTRADAY",
            "order_type": "LIMIT",
            "validity": "DAY",
            "limit_price": 24414.0,
            "tick_size": 0.1,
            "instrument": {},
        }

        environment = {
            "LIVE_TRADING_ENABLED": "true",
            "CREDENTIAL_ENCRYPTION_KEY": "integration-encryption-key",
            "BROKER_AUTH_STATE_SECRET": "integration-signing-key",
        }
        with patch.dict(os.environ, environment, clear=False):
            db = SessionLocal()
            try:
                AppCredentialsManager(db, user_id=1).save_credentials(
                    "aliceblue",
                    "AB12345",
                    "alice-session-token",
                )
            finally:
                db.close()

            with patch("backend.main.get_live_market_price", return_value=24414.0), patch(
                "backend.main.prepare_aliceblue_order", return_value=prepared
            ):
                preview_response = self.client.post(
                    "/api/broker/order-preview",
                    json={"signal_id": signal_id, "trade_type": "FUTURE", "mode": "LIVE", "lots": 1},
                )
            self.assertEqual(preview_response.status_code, 200, preview_response.text)
            preview_token = preview_response.json()["preview_token"]

            broker_result = {
                "status": "success",
                "broker_order_id": "260706000000001",
                "broker_response": {"status": "Ok"},
            }
            with patch("backend.main.get_live_market_price", return_value=24414.0), patch(
                "backend.main.place_aliceblue_order", return_value=broker_result
            ):
                execute_response = self.client.post(
                    "/api/broker/execute",
                    json={
                        "signal_id": signal_id,
                        "trade_type": "FUTURE",
                        "mode": "LIVE",
                        "lots": 1,
                        "preview_token": preview_token,
                        "idempotency_key": "aliceblue-live-test-order",
                    },
                )

        self.assertEqual(execute_response.status_code, 200, execute_response.text)
        db = SessionLocal()
        try:
            position = db.query(Position).filter(Position.signal_id == signal_id).one()
            order = db.query(BrokerOrder).filter(BrokerOrder.signal_id == signal_id).one()
            self.assertEqual(position.status, "PENDING")
            self.assertEqual(position.entry_broker_order_id, "260706000000001")
            self.assertEqual(order.status, "SUBMITTED")
            self.assertEqual(order.position_id, position.id)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()

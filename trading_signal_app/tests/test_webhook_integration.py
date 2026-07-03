import os
import tempfile
import unittest
from pathlib import Path


TEST_DATABASE_DIR = tempfile.TemporaryDirectory()
TEST_DATABASE_PATH = Path(TEST_DATABASE_DIR.name) / "webhook-test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DATABASE_PATH.as_posix()}"
os.environ["VALID_SECRETS"] = "integration-test-secret"
os.environ.pop("WEBHOOK_AUTO_EXECUTION_MODE", None)

from fastapi.testclient import TestClient

from backend.credentials import AppCredentialsManager
from backend.database import Position, SessionLocal, Signal, engine, init_db
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


if __name__ == "__main__":
    unittest.main()

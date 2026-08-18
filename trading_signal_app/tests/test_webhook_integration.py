import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


TEST_DATABASE_DIR = tempfile.TemporaryDirectory()
TEST_DATABASE_PATH = Path(TEST_DATABASE_DIR.name) / "webhook-test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DATABASE_PATH.as_posix()}"
os.environ["VALID_SECRETS"] = "integration-test-secret"
os.environ["ALLOW_SANDBOX_AUTH"] = "true"
os.environ["LIVE_TRADING_ENABLED"] = "false"
os.environ["ENABLE_INTRADAY_SQUARE_OFF_WORKER"] = "false"
os.environ.pop("WEBHOOK_AUTO_EXECUTION_MODE", None)

from fastapi.testclient import TestClient

from backend.credentials import AppCredentialsManager
from backend.database import AppAuthSession, BrokerLiveSetting, BrokerOrder, Position, SessionLocal, Signal, User, engine, init_db
from backend.main import app, build_tradingview_option_ticker, get_ist_time, get_tradingview_price, is_administrator, parse_option_symbol, pick_tradingview_price, square_off_expired_intraday_positions


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
                "option_price": 1123.45,
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
                trade_type="POSITIONAL",
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

    def test_tradingview_option_ticker_and_bid_ask_midpoint(self):
        parsed = parse_option_symbol("OPTIDX_NIFTY_28JUL2026_CE_23950")
        self.assertTrue(parsed["is_option"])
        self.assertEqual(parsed["formatted_symbol"], "NIFTY 28JUL26 23950 CE")
        self.assertEqual(
            build_tradingview_option_ticker("NIFTY 07JUL26 24550 PE"),
            "NSE:NIFTY260707P24550",
        )
        self.assertEqual(pick_tradingview_price([None, None, None, 74.5, 75.5]), 75.0)
        self.assertIsNone(get_tradingview_price("NIFTY 24550 PE"))

    def test_exact_optidx_signal_uses_alert_premium_and_exit_side(self):
        entry_response = self.client.post(
            "/api/signals/webhook",
            json={
                "secret": "integration-test-secret",
                "symbol": "OPTIDX_NIFTY_28JUL2026_CE_23950",
                "price": 82.80,
                "orderId": "exact-option-entry-test",
                "action": "long",
                "trade_type": "POSITIONAL",
            },
        )
        self.assertEqual(entry_response.status_code, 200, entry_response.text)

        db = SessionLocal()
        try:
            position = db.query(Position).filter(
                Position.symbol == "NIFTY 28JUL26 23950 CE",
                Position.trade_type == "POSITIONAL",
            ).order_by(Position.id.desc()).first()
            self.assertIsNotNone(position)
            self.assertEqual(position.entry_price, 82.80)
            position_id = position.id
        finally:
            db.close()

        exit_response = self.client.post(
            "/api/signals/webhook",
            json={
                "secret": "integration-test-secret",
                "symbol": "OPTIDX_NIFTY_28JUL2026_CE_23950",
                "price": 74.35,
                "orderId": "exact-option-exit-test",
                "action": "exit",
                "direction": "long",
                "trade_type": "POSITIONAL",
            },
        )
        self.assertEqual(exit_response.status_code, 200, exit_response.text)

        db = SessionLocal()
        try:
            position = db.query(Position).filter(Position.id == position_id).one()
            self.assertEqual(position.status, "CLOSED")
            self.assertEqual(position.exit_price, 74.35)
            self.assertEqual(position.exit_reason, "SIGNAL_EXIT")
        finally:
            db.close()

    def test_manual_option_order_uses_live_option_ltp(self):
        db = SessionLocal()
        try:
            signal = Signal(
                symbol="NIFTY",
                action="SHORT",
                price=24550,
                source="TradingView",
                source_name="option-ltp-test",
                raw_payload="{}",
                timeframe="5m",
                trade_type="POSITIONAL",
            )
            db.add(signal)
            db.commit()
            signal_id = signal.id
        finally:
            db.close()

        def fake_live_price(symbol):
            if " PE" in symbol:
                return 75.0
            if symbol == "NIFTY":
                return 24550.0
            return None

        with patch("backend.main.get_live_market_price", side_effect=fake_live_price):
            response = self.client.post(
                "/api/broker/execute",
                json={"signal_id": signal_id, "trade_type": "OPTION", "mode": "PAPER", "lots": 1},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["entry_price"], 75.0)
        db = SessionLocal()
        try:
            position = db.query(Position).filter(Position.signal_id == signal_id).one()
            self.assertTrue(position.symbol.endswith(" PE"))
            self.assertEqual(position.entry_price, 75.0)
        finally:
            db.close()

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
                trade_type="POSITIONAL",
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

    def test_configured_test_phone_can_login_with_common_password(self):
        response = self.client.post(
            "/api/auth/test-login",
            json={"phone": "+91 9043055445", "password": "123456"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        token = response.json()["access_token"]
        self.assertTrue(token)
        profile = self.client.get("/api/user", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(profile.status_code, 200, profile.text)
        self.assertEqual(profile.json()["phone"], "+919043055445")
        db = SessionLocal()
        try:
            db.query(Position).filter(Position.user_id != 1).delete()
            test_user = db.query(User).filter(User.phone == "+919043055445").first()
            if test_user:
                db.query(AppAuthSession).filter(AppAuthSession.user_id == test_user.id).delete()
                db.delete(test_user)
            db.commit()
        finally:
            db.close()

    def test_configured_phone_is_administrator(self):
        admin = User(id=42, email=None, phone="91 8919859974")
        regular_user = User(id=43, email=None, phone="+919043055445")

        with patch.dict(
            os.environ,
            {
                "ADMIN_PHONE_NUMBERS": "+91 8919859974",
                "ALLOW_SANDBOX_AUTH": "false",
            },
        ):
            self.assertTrue(is_administrator(admin))
            self.assertFalse(is_administrator(regular_user))

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
                trade_type="POSITIONAL",
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
            "LIVE_STATIC_IP_CHECK_REQUIRED": "false",
            "CREDENTIAL_ENCRYPTION_KEY": "integration-encryption-key",
            "BROKER_AUTH_STATE_SECRET": "integration-signing-key",
            "ALICEBLUE_APP_CODE": "integration-app-code",
            "ALICEBLUE_API_SECRET": "integration-api-secret",
        }
        with patch.dict(os.environ, environment, clear=False):
            db = SessionLocal()
            try:
                AppCredentialsManager(db, user_id=1).save_credentials(
                    "aliceblue",
                    "AB12345",
                    "alice-session-token",
                )
                db.add(BrokerLiveSetting(
                    user_id=1,
                    broker_id="aliceblue",
                    static_ip="8.8.8.8",
                    static_ip_registered=True,
                ))
                db.commit()
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

    def test_flattrade_live_preview_and_submission_are_audited(self):
        db = SessionLocal()
        try:
            signal = Signal(
                symbol="NIFTY",
                action="LONG",
                price=24414,
                source="TradingView",
                source_name="flattrade-live-test",
                raw_payload="{}",
                timeframe="5m",
                trade_type="POSITIONAL",
            )
            db.add(signal)
            db.commit()
            signal_id = signal.id
        finally:
            db.close()

        today_str = get_ist_time().date().isoformat()
        environment = {
            "LIVE_TRADING_ENABLED": "true",
            "LIVE_STATIC_IP_CHECK_REQUIRED": "false",
            "CREDENTIAL_ENCRYPTION_KEY": "integration-encryption-key",
            "BROKER_AUTH_STATE_SECRET": "integration-signing-key",
        }
        with patch.dict(os.environ, environment, clear=False):
            db = SessionLocal()
            try:
                AppCredentialsManager(db, user_id=1).save_credentials(
                    "flattrade",
                    "FTAPIKEY",
                    "FTSECRET",
                    extra={
                        "client_id": "FT12345",
                        "token": "flattrade-session-token",
                        "token_date": today_str,
                    },
                )
                db.add(BrokerLiveSetting(
                    user_id=1,
                    broker_id="flattrade",
                    static_ip="8.8.4.4",
                    static_ip_registered=True,
                ))
                db.commit()
            finally:
                db.close()

            with patch("backend.main.get_live_market_price", return_value=24414.0):
                preview_response = self.client.post(
                    "/api/broker/order-preview",
                    json={"signal_id": signal_id, "trade_type": "FUTURE", "mode": "LIVE", "lots": 1},
                )
            self.assertEqual(preview_response.status_code, 200, preview_response.text)
            preview = preview_response.json()
            self.assertEqual(preview["broker_name"], "Flattrade")
            preview_token = preview["preview_token"]

            broker_result = {
                "status": "success",
                "broker_order_id": "FT260707000001",
                "broker_response": {"stat": "Ok"},
            }
            with patch("backend.main.get_live_market_price", return_value=24414.0), patch(
                "backend.main.place_flattrade_prepared_order", return_value=broker_result
            ):
                execute_response = self.client.post(
                    "/api/broker/execute",
                    json={
                        "signal_id": signal_id,
                        "trade_type": "FUTURE",
                        "mode": "LIVE",
                        "lots": 1,
                        "preview_token": preview_token,
                        "idempotency_key": "flattrade-live-test-order",
                    },
                )

        self.assertEqual(execute_response.status_code, 200, execute_response.text)
        db = SessionLocal()
        try:
            position = db.query(Position).filter(Position.signal_id == signal_id).one()
            order = db.query(BrokerOrder).filter(BrokerOrder.signal_id == signal_id).one()
            self.assertEqual(position.status, "PENDING")
            self.assertEqual(position.broker_id, "flattrade")
            self.assertEqual(position.entry_broker_order_id, "FT260707000001")
            self.assertEqual(order.broker_id, "flattrade")
            self.assertEqual(order.status, "SUBMITTED")
            self.assertEqual(order.position_id, position.id)
        finally:
            db.close()

    def test_target_hit_closes_matching_position(self):
        entry_response = self.client.post(
            "/api/signals/webhook",
            json={
                "secret": "integration-test-secret",
                "symbol": "WIPRO",
                "price": 500,
                "orderId": "positional_target_test",
                "action": "long",
                "trade_type": "POSITIONAL",
            },
        )
        self.assertEqual(entry_response.status_code, 200, entry_response.text)

        exit_response = self.client.post(
            "/api/signals/webhook",
            json={
                "secret": "integration-test-secret",
                "symbol": "WIPRO",
                "price": 510,
                "orderId": "positional_target_test",
                "action": "Target Hit",
                "trade_type": "POSITIONAL",
            },
        )
        self.assertEqual(exit_response.status_code, 200, exit_response.text)

        db = SessionLocal()
        try:
            position = db.query(Position).filter(Position.symbol == "WIPRO").order_by(Position.id.desc()).first()
            signal = db.query(Signal).filter(Signal.symbol == "WIPRO").order_by(Signal.id.desc()).first()
            self.assertEqual(position.status, "CLOSED")
            self.assertEqual(position.exit_reason, "TARGET_HIT")
            self.assertEqual(signal.action, "EXIT")
        finally:
            db.close()

    def test_sell_exit_does_not_reopen_a_short_position(self):
        entry_response = self.client.post(
            "/api/signals/webhook",
            json={
                "secret": "integration-test-secret",
                "symbol": "INFY",
                "price": 1500,
                "orderId": "positional_sell_exit_test",
                "action": "long",
                "trade_type": "POSITIONAL",
            },
        )
        self.assertEqual(entry_response.status_code, 200, entry_response.text)

        exit_response = self.client.post(
            "/api/signals/webhook",
            json={
                "secret": "integration-test-secret",
                "symbol": "INFY",
                "price": 1510,
                "orderId": "positional_sell_exit_test",
                "action": "sell",
                "trade_type": "POSITIONAL",
            },
        )
        self.assertEqual(exit_response.status_code, 200, exit_response.text)

        db = SessionLocal()
        try:
            positions = db.query(Position).filter(Position.symbol == "INFY").all()
            self.assertEqual(len(positions), 1)
            self.assertEqual(positions[0].status, "CLOSED")
            self.assertEqual(positions[0].exit_reason, "SIGNAL_EXIT")
        finally:
            db.close()

    def test_exit_short_closes_bought_put_option(self):
        entry_response = self.client.post(
            "/api/signals/webhook",
            json={
                "secret": "integration-test-secret",
                "symbol": "SENSEX",
                "price": 80000,
                "option_price": 240.0,
                "orderId": "positional_short_exit_test",
                "action": "short",
                "trade_type": "POSITIONAL",
            },
        )
        self.assertEqual(entry_response.status_code, 200, entry_response.text)

        db = SessionLocal()
        try:
            position = db.query(Position).filter(
                Position.symbol.like("SENSEX %"),
                Position.trade_type == "POSITIONAL",
            ).order_by(Position.id.desc()).first()
            self.assertTrue(position.symbol.endswith(" PE"))
            position_id = position.id
        finally:
            db.close()

        exit_response = self.client.post(
            "/api/signals/webhook",
            json={
                "secret": "integration-test-secret",
                "symbol": "SENSEX",
                "price": 79900,
                "option_price": 265.0,
                "orderId": "positional_short_exit_test",
                "action": "exit_short",
                "trade_type": "POSITIONAL",
            },
        )
        self.assertEqual(exit_response.status_code, 200, exit_response.text)

        db = SessionLocal()
        try:
            position = db.query(Position).filter(Position.id == position_id).one()
            self.assertEqual(position.status, "CLOSED")
            self.assertEqual(position.exit_reason, "SIGNAL_EXIT")
        finally:
            db.close()

    def test_target_hit_long_does_not_close_bought_put_option(self):
        db = SessionLocal()
        try:
            ce_position = Position(
                user_id=1,
                symbol="NIFTY 28JUL26 23950 CE",
                direction="LONG",
                qty=65,
                entry_price=82.80,
                status="OPEN",
                real_or_paper="PAPER",
                trade_type="POSITIONAL",
            )
            pe_position = Position(
                user_id=1,
                symbol="NIFTY 28JUL26 24000 PE",
                direction="LONG",
                qty=65,
                entry_price=180.60,
                status="OPEN",
                real_or_paper="PAPER",
                trade_type="POSITIONAL",
            )
            db.add_all([ce_position, pe_position])
            db.commit()
            ce_id = ce_position.id
            pe_id = pe_position.id
        finally:
            db.close()

        response = self.client.post(
            "/api/signals/webhook",
            json={
                "secret": "integration-test-secret",
                "symbol": "NIFTY",
                "price": 23967.6,
                "option_price": 74.35,
                "orderId": "target-hit-long-side-test",
                "action": "Target Hit",
                "direction": "long",
                "trade_type": "POSITIONAL",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

        db = SessionLocal()
        try:
            ce_position = db.query(Position).filter(Position.id == ce_id).one()
            pe_position = db.query(Position).filter(Position.id == pe_id).one()
            signal = db.query(Signal).filter(Signal.source_name == "target-hit-long-side-test").order_by(Signal.id.desc()).first()
            self.assertEqual(signal.action, "EXIT_LONG")
            self.assertEqual(ce_position.status, "CLOSED")
            self.assertEqual(ce_position.exit_price, 74.35)
            self.assertEqual(ce_position.exit_reason, "TARGET_HIT")
            self.assertEqual(pe_position.status, "OPEN")
        finally:
            db.close()

    def test_intraday_positions_close_at_cutoff_but_positional_remains_open(self):
        db = SessionLocal()
        try:
            signal = Signal(
                symbol="CUTTEST",
                action="LONG",
                price=100,
                source="test",
                source_name="intraday_cutoff_test",
                raw_payload="{}",
                timestamp=datetime(2026, 7, 6, 14, 0),
                timeframe="5m",
                trade_type="INTRADAY",
            )
            db.add(signal)
            db.flush()
            intraday = Position(
                user_id=1,
                symbol="CUTTEST",
                direction="LONG",
                qty=1,
                entry_price=100,
                entry_time=datetime(2026, 7, 6, 14, 0),
                status="OPEN",
                real_or_paper="PAPER",
                signal_id=signal.id,
                timeframe="5m",
                trade_type="INTRADAY",
            )
            positional = Position(
                user_id=1,
                symbol="CUTTEST-POS",
                direction="LONG",
                qty=1,
                entry_price=100,
                entry_time=datetime(2026, 7, 6, 14, 0),
                status="OPEN",
                real_or_paper="PAPER",
                timeframe="5m",
                trade_type="POSITIONAL",
            )
            db.add_all([intraday, positional])
            db.commit()

            with patch("backend.main.get_live_market_price", return_value=105):
                result = square_off_expired_intraday_positions(
                    db,
                    now=datetime(2026, 7, 6, 15, 15),
                )

            db.refresh(intraday)
            db.refresh(positional)
            self.assertIn(intraday.id, result["closed_position_ids"])
            self.assertEqual(intraday.status, "CLOSED")
            self.assertEqual(intraday.exit_reason, "INTRADAY_CUTOFF")
            self.assertEqual(positional.status, "OPEN")
            cutoff_signal = db.query(Signal).filter(
                Signal.symbol == "CUTTEST",
                Signal.source_name == "SYSTEM_INTRADAY_CUTOFF",
            ).first()
            self.assertIsNotNone(cutoff_signal)
        finally:
            db.close()

    def test_paper_trades_endpoint_survives_malformed_position(self):
        db = SessionLocal()
        try:
            position = Position(
                user_id=1,
                symbol=None,
                direction=None,
                qty=None,
                entry_price=None,
                status="CLOSED",
                real_or_paper=None,
                trade_type=None,
            )
            db.add(position)
            db.commit()
            position_id = position.id
        finally:
            db.close()

        with patch("backend.main.square_off_expired_intraday_positions", return_value={}):
            response = self.client.get("/api/paper-trades")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIn("positions", body)
        row = next(item for item in body["positions"] if item["id"] == position_id)
        self.assertEqual(row["symbol"], "UNKNOWN")
        self.assertEqual(row["status"], "CLOSED")


if __name__ == "__main__":
    unittest.main()

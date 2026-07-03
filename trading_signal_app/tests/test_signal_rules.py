import unittest

from backend.signal_rules import resolve_trade_type, resolve_webhook_execution_mode


class ResolveTradeTypeTests(unittest.TestCase):
    def test_positional_order_id_wins_over_action_like_type(self):
        payload = {"orderId": "positional_index", "type": "long"}

        self.assertEqual(resolve_trade_type(payload, "5m"), "POSITIONAL")

    def test_intraday_order_id_is_intraday(self):
        payload = {"orderId": "intraday_index", "type": "long"}

        self.assertEqual(resolve_trade_type(payload, "5m"), "INTRADAY")

    def test_explicit_trade_type_is_respected(self):
        payload = {"trade_type": "positional", "orderId": "intraday_index"}

        self.assertEqual(resolve_trade_type(payload, "5m"), "POSITIONAL")

    def test_daily_timeframe_is_positional_when_no_hint_exists(self):
        self.assertEqual(resolve_trade_type({}, "1D"), "POSITIONAL")


class ResolveWebhookExecutionModeTests(unittest.TestCase):
    def test_linked_broker_does_not_enable_live_by_itself(self):
        self.assertEqual(resolve_webhook_execution_mode(None, "flattrade"), "PAPER")

    def test_live_requires_both_explicit_setting_and_broker(self):
        self.assertEqual(resolve_webhook_execution_mode("LIVE", "flattrade"), "LIVE")
        self.assertEqual(resolve_webhook_execution_mode("LIVE", None), "PAPER")


if __name__ == "__main__":
    unittest.main()

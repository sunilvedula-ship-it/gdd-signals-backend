from typing import Any, Mapping, Optional


def resolve_trade_type(payload: Mapping[str, Any], timeframe: str) -> str:
    """Classify a webhook without mistaking action fields such as type=long for trade type."""
    explicit_trade_type = str(payload.get("trade_type") or "").strip().upper()
    if "POSITIONAL" in explicit_trade_type:
        return "POSITIONAL"
    if "INTRADAY" in explicit_trade_type:
        return "INTRADAY"

    hint_fields = " ".join(
        str(payload.get(field) or "")
        for field in ("orderId", "signal_type", "source_name", "source", "style", "type")
    ).upper()
    if "POSITIONAL" in hint_fields:
        return "POSITIONAL"
    if "INTRADAY" in hint_fields:
        return "INTRADAY"

    normalized_timeframe = str(timeframe or "").strip().lower()
    if normalized_timeframe in {"1d", "d", "daily", "1w", "w", "weekly", "positional"}:
        return "POSITIONAL"

    return "INTRADAY"


def resolve_webhook_execution_mode(configured_mode: Optional[str], active_broker: Optional[str]) -> str:
    """Keep automatic webhooks on paper unless live mode is deliberately enabled."""
    requested_mode = str(configured_mode or "PAPER").strip().upper()
    if requested_mode == "LIVE" and active_broker:
        return "LIVE"
    return "PAPER"

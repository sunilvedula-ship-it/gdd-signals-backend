import csv
import hashlib
import io
import os
import re
import threading
import time as time_module
from collections import deque
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

import requests
from sqlalchemy.orm import Session


BASE_URL = os.environ.get("ALICEBLUE_BASE_URL", "https://a3.aliceblueonline.com").rstrip("/")
SSO_URL = "https://ant.aliceblueonline.com/"
CONTRACT_BASE_URL = os.environ.get(
    "ALICEBLUE_CONTRACT_BASE_URL",
    "https://v2api.aliceblueonline.com/restpy/static/contract_master",
).rstrip("/")


class AliceBlueError(RuntimeError):
    pass


class AliceBlueOrderStatusUnknown(AliceBlueError):
    pass


@dataclass(frozen=True)
class AliceBlueInstrument:
    exchange: str
    instrument_id: str
    symbol: str
    trading_symbol: str
    instrument_type: str
    option_type: str
    strike_price: float
    expiry_date: Optional[date]
    lot_size: int
    tick_size: float


_contract_cache: Dict[str, Dict[str, Any]] = {}
_contract_cache_lock = threading.Lock()
_order_rate_lock = threading.Lock()
_order_timestamps = deque()


def _ist_today() -> date:
    return datetime.now(timezone.utc).astimezone(
        timezone(timedelta(hours=5, minutes=30))
    ).date()


def _proxy_url() -> Optional[str]:
    return (
        os.environ.get("BROKER_PROXY_URL")
        or os.environ.get("PROXY_URL")
        or os.environ.get("QUOTAGUARDSTATIC_URL")
        or os.environ.get("FIXIE_URL")
    )


def _http_session() -> requests.Session:
    session = requests.Session()
    proxy = _proxy_url()
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    return session


def is_vendor_configured() -> bool:
    return bool(
        os.environ.get("ALICEBLUE_APP_CODE", "").strip()
        and os.environ.get("ALICEBLUE_API_SECRET", "").strip()
    )


def get_vendor_login_url() -> str:
    app_code = os.environ.get("ALICEBLUE_APP_CODE", "").strip()
    if not app_code:
        raise AliceBlueError("Alice Blue vendor App Code is not configured")
    return f"{SSO_URL}?appcode={app_code}"


def exchange_vendor_session(user_id: str, auth_code: str) -> Dict[str, str]:
    api_secret = os.environ.get("ALICEBLUE_API_SECRET", "").strip()
    if not api_secret:
        raise AliceBlueError("Alice Blue vendor API Secret is not configured")

    checksum = hashlib.sha256(
        f"{user_id}{auth_code}{api_secret}".encode("utf-8")
    ).hexdigest()
    try:
        response = _http_session().post(
            f"{BASE_URL}/open-api/od/v1/vendor/getUserDetails",
            json={"checkSum": checksum},
            timeout=20,
        )
        data = response.json()
    except requests.RequestException as exc:
        raise AliceBlueError(f"Alice Blue session request failed: {exc}") from exc
    except ValueError as exc:
        raise AliceBlueError("Alice Blue returned an invalid session response") from exc

    if response.status_code != 200:
        raise AliceBlueError(
            data.get("message") or data.get("emsg") or f"HTTP {response.status_code}"
        )

    session_token = data.get("userSession")
    client_id = data.get("clientId") or user_id
    result = data.get("result")
    if not session_token and isinstance(result, list) and result:
        session_token = result[0].get("accessToken")
        client_id = result[0].get("clientId") or client_id

    if not session_token:
        raise AliceBlueError(
            data.get("message") or data.get("emsg") or "Alice Blue did not return a user session"
        )
    return {"user_session": session_token, "client_id": str(client_id)}


def _parse_contract_row(row: Dict[str, str]) -> Optional[AliceBlueInstrument]:
    try:
        expiry_raw = (row.get("Expiry Date") or "").strip()
        expiry = datetime.strptime(expiry_raw, "%Y-%m-%d").date() if expiry_raw else None
        return AliceBlueInstrument(
            exchange=(row.get("Exch") or "").strip().upper(),
            instrument_id=(row.get("Token") or "").strip(),
            symbol=(row.get("Symbol") or "").strip().upper(),
            trading_symbol=(row.get("Trading Symbol") or "").strip().upper(),
            instrument_type=(row.get("Instrument Type") or "").strip().upper(),
            option_type=(row.get("Option Type") or "").strip().upper(),
            strike_price=float(row.get("Strike Price") or 0),
            expiry_date=expiry,
            lot_size=int(float(row.get("Lot Size") or 1)),
            tick_size=float(row.get("Tick Size") or 0.05),
        )
    except (TypeError, ValueError):
        return None


def _download_contract_rows(exchange: str) -> List[AliceBlueInstrument]:
    url = f"{CONTRACT_BASE_URL}/{exchange.upper()}.csv"
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise AliceBlueError(f"Could not download Alice Blue {exchange} contract master: {exc}") from exc

    rows = []
    for row in csv.DictReader(io.StringIO(response.text)):
        instrument = _parse_contract_row(row)
        if instrument:
            rows.append(instrument)
    if not rows:
        raise AliceBlueError(f"Alice Blue {exchange} contract master was empty")
    return rows


def _contracts(exchange: str) -> List[AliceBlueInstrument]:
    exchange = exchange.upper()
    today = _ist_today()
    with _contract_cache_lock:
        cached = _contract_cache.get(exchange)
        if cached and cached["date"] == today:
            return cached["rows"]
        rows = _download_contract_rows(exchange)
        _contract_cache[exchange] = {"date": today, "rows": rows}
        return rows


def clear_contract_cache() -> None:
    with _contract_cache_lock:
        _contract_cache.clear()


def _normalize_underlying(symbol: str) -> str:
    value = symbol.upper().strip()
    if ":" in value:
        value = value.split(":")[-1]
    value = re.sub(r"1!$|!$", "", value)
    if value in {"BNF", "NIFTYBANK"} or "BANKNIFTY" in value:
        return "BANKNIFTY"
    if value in {"BSX", "SENSEX"} or "SENSEX" in value:
        return "SENSEX"
    if "NIFTY" in value:
        return "NIFTY"
    if "CRUDE" in value:
        return "CRUDEOIL"
    return value


def resolve_instrument(symbol: str) -> AliceBlueInstrument:
    value = symbol.upper().strip()
    option_match = re.match(
        r"^([A-Z]+)\s+(\d{2}[A-Z]{3}\d{2})\s+(\d+(?:\.\d+)?)\s+(CE|PE)$",
        value,
    )
    if option_match:
        underlying = _normalize_underlying(option_match.group(1))
        expiry = datetime.strptime(option_match.group(2), "%d%b%y").date()
        strike = float(option_match.group(3))
        option_type = option_match.group(4)
        exchange = "BFO" if underlying == "SENSEX" else "NFO"
        matches = [
            item
            for item in _contracts(exchange)
            if item.symbol == underlying
            and item.expiry_date == expiry
            and abs(item.strike_price - strike) < 0.001
            and item.option_type == option_type
        ]
    else:
        underlying = _normalize_underlying(value)
        exchange = "MCX" if underlying in {"GOLD", "GOLDM", "SILVER", "CRUDEOIL"} else (
            "BFO" if underlying == "SENSEX" else "NFO"
        )
        today = _ist_today()
        matches = [
            item
            for item in _contracts(exchange)
            if item.symbol == underlying
            and item.instrument_type.startswith("FUT")
            and item.expiry_date
            and item.expiry_date >= today
        ]
        matches.sort(key=lambda item: item.expiry_date or date.max)

    if not matches:
        raise AliceBlueError(f"No active Alice Blue contract found for {symbol}")
    return matches[0]


def _round_to_tick(price: float, tick_size: float) -> float:
    if price <= 0:
        raise AliceBlueError("A positive limit price is required")
    tick = Decimal(str(tick_size or 0.05))
    rounded = (Decimal(str(price)) / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick
    return float(rounded)


def _reserve_order_rate_slot() -> None:
    limit = max(1, int(os.environ.get("ALICEBLUE_MAX_ORDERS_PER_SECOND", "9")))
    now = time_module.monotonic()
    with _order_rate_lock:
        while _order_timestamps and now - _order_timestamps[0] >= 1.0:
            _order_timestamps.popleft()
        if len(_order_timestamps) >= limit:
            raise AliceBlueError("Order rate limit reached. Please wait a moment and try again.")
        _order_timestamps.append(now)


def prepare_order(
    symbol: str,
    direction: str,
    lots: float,
    price: float,
    trade_type: str,
    quantity: Optional[int] = None,
    instrument: Optional[AliceBlueInstrument] = None,
) -> Dict[str, Any]:
    if lots <= 0 or int(lots) != lots:
        raise AliceBlueError("Lots must be a positive whole number")
    instrument = instrument or resolve_instrument(symbol)
    qty = int(quantity if quantity is not None else int(lots) * instrument.lot_size)
    if qty <= 0:
        raise AliceBlueError("Order quantity must be positive")

    transaction_type = "BUY" if direction.upper() in {"BUY", "LONG"} else "SELL"
    product = "INTRADAY" if trade_type.upper() == "INTRADAY" else "LONGTERM"
    limit_price = _round_to_tick(price, instrument.tick_size)
    instrument_data = asdict(instrument)
    if instrument.expiry_date:
        instrument_data["expiry_date"] = instrument.expiry_date.isoformat()
    return {
        "broker_id": "aliceblue",
        "broker_name": "Alice Blue",
        "symbol": symbol,
        "trading_symbol": instrument.trading_symbol,
        "instrument_id": instrument.instrument_id,
        "exchange": instrument.exchange,
        "transaction_type": transaction_type,
        "quantity": qty,
        "lot_size": instrument.lot_size,
        "lots": int(lots),
        "product": product,
        "order_type": "LIMIT",
        "validity": "DAY",
        "limit_price": limit_price,
        "tick_size": instrument.tick_size,
        "instrument": instrument_data,
    }


def _load_user_session(user_id: int, db: Session) -> Dict[str, str]:
    from backend.credentials import AppCredentialsManager

    credentials = AppCredentialsManager(db, user_id=user_id).load_credentials("aliceblue")
    if not credentials:
        raise AliceBlueError("Alice Blue account is not connected")
    session_token = credentials.get("api_secret", "").strip()
    alice_user_id = credentials.get("api_key", "").strip()
    if not session_token or not alice_user_id:
        raise AliceBlueError("Alice Blue session is incomplete; reconnect the account")
    return {"session_token": session_token, "alice_user_id": alice_user_id}


def _authorized_request(
    user_id: int,
    db: Session,
    method: str,
    path: str,
    payload: Optional[Any] = None,
    order_request: bool = False,
) -> Dict[str, Any]:
    session = _load_user_session(user_id, db)
    try:
        response = _http_session().request(
            method,
            f"{BASE_URL}{path}",
            headers={
                "Authorization": f"Bearer {session['session_token']}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=20,
        )
        data = response.json()
    except requests.RequestException as exc:
        if order_request:
            raise AliceBlueOrderStatusUnknown(
                "The broker response was not received. Do not retry until the Alice Blue order book is checked."
            ) from exc
        raise AliceBlueError(f"Alice Blue request failed: {exc}") from exc
    except ValueError as exc:
        if order_request:
            raise AliceBlueOrderStatusUnknown(
                "Alice Blue returned an unreadable order response. Check the broker order book before retrying."
            ) from exc
        raise AliceBlueError("Alice Blue returned an invalid response") from exc

    if response.status_code != 200:
        message = data.get("message") or data.get("emsg") or f"HTTP {response.status_code}"
        raise AliceBlueError(message)
    return data


def place_order(user_id: int, db: Session, prepared: Dict[str, Any], order_tag: str) -> Dict[str, Any]:
    _reserve_order_rate_slot()
    payload = [{
        "exchange": prepared["exchange"],
        "instrumentId": prepared["instrument_id"],
        "transactionType": prepared["transaction_type"],
        "quantity": prepared["quantity"],
        "product": prepared["product"],
        "orderComplexity": "REGULAR",
        "orderType": "LIMIT",
        "validity": "DAY",
        "price": f"{prepared['limit_price']:.2f}",
        "slLegPrice": "",
        "targetLegPrice": "",
        "slTriggerPrice": "",
        "disclosedQuantity": "",
        "marketProtectionPercent": "",
        "deviceId": os.environ.get("ALICEBLUE_DEVICE_ID", "GDD-MOBILE"),
        "trailingSlAmount": "",
        "apiOrderSource": os.environ.get("ALICEBLUE_API_ORDER_SOURCE", "API"),
        "algoId": os.environ.get("ALICEBLUE_ALGO_ID", ""),
        "orderTag": order_tag[:40],
    }]
    data = _authorized_request(
        user_id,
        db,
        "POST",
        "/open-api/od/v1/orders/placeorder",
        payload,
        order_request=True,
    )
    result = data.get("result")
    broker_order_id = result[0].get("brokerOrderId") if isinstance(result, list) and result else None
    if data.get("status") != "Ok" or not broker_order_id:
        raise AliceBlueError(data.get("message") or data.get("emsg") or "Alice Blue rejected the order")
    return {
        "status": "success",
        "broker_order_id": str(broker_order_id),
        "prepared": prepared,
        "broker_response": data,
    }


def get_order_history(user_id: int, db: Session, broker_order_id: str) -> Dict[str, Any]:
    data = _authorized_request(
        user_id,
        db,
        "POST",
        "/open-api/od/v1/orders/history",
        {"brokerOrderId": broker_order_id},
    )
    result = data.get("result")
    if data.get("status") != "Ok" or not isinstance(result, list) or not result:
        raise AliceBlueError(
            data.get("message") or data.get("emsg") or "Alice Blue order history was unavailable"
        )
    order = result[0]
    return {
        "status": str(order.get("orderStatus") or "UNKNOWN").upper(),
        "average_price": float(order.get("averageTradedPrice") or 0),
        "filled_quantity": int(float(order.get("filledQuantity") or 0)),
        "rejection_reason": order.get("rejectionReason") or "",
        "raw": data,
    }


def get_funds(user_id: int, db: Session) -> Optional[float]:
    data = _authorized_request(user_id, db, "GET", "/open-api/od/v1/limits/")
    result = data.get("result")
    if data.get("status") != "Ok" or not isinstance(result, list) or not result:
        return None
    values = result[0]
    trading_limit = values.get("tradingLimit")
    opening_cash = values.get("openingCashLimit")
    try:
        return float(trading_limit if trading_limit is not None else opening_cash)
    except (TypeError, ValueError):
        return None

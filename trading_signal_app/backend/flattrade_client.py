import os
import json
import hashlib
import urllib.request
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session

# Flattrade API v2 Endpoints
TOKEN_URL = "https://authapi.flattrade.in/trade/apitoken"
PLACE_ORDER_URL = "https://piconnect.flattrade.in/PiConnectAPI/PlaceOrder"


class FlattradeError(RuntimeError):
    pass


def _proxy_url() -> Optional[str]:
    return (
        os.environ.get("BROKER_PROXY_URL")
        or os.environ.get("PROXY_URL")
        or os.environ.get("QUOTAGUARDSTATIC_URL")
        or os.environ.get("FIXIE_URL")
    )

def send_api_request(url: str, data_dict: dict) -> tuple[int, str]:
    """Helper to send POST request, optionally routing through a proxy if configured."""
    headers = {"Content-Type": "application/json"}
    data_bytes = json.dumps(data_dict).encode("utf-8")
    req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
    
    proxy_url = _proxy_url()
    if proxy_url:
        proxy_handler = urllib.request.ProxyHandler({'http': proxy_url, 'https': proxy_url})
        opener = urllib.request.build_opener(proxy_handler)
        with opener.open(req, timeout=10) as res:
            return res.getcode(), res.read().decode("utf-8")
    else:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.getcode(), res.read().decode("utf-8")

def get_ist_time() -> datetime:
    from datetime import timezone, timedelta
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=5, minutes=30)))

def get_next_monthly_expiry(dt: datetime, weekday: int = 1) -> datetime:
    """Find the last Tuesday (weekday=1) of the current month."""
    import calendar
    year = dt.year
    month = dt.month
    
    # Get last day of month
    last_day = calendar.monthrange(year, month)[1]
    last_dt = datetime(year, month, last_day, 15, 30)
    
    # Backtrack to find the last occurrence of the weekday
    while last_dt.weekday() != weekday:
        last_dt = last_dt - timedelta(days=1)
        
    # If the last Tuesday has already passed today, get last Tuesday of next month
    if last_dt.date() < dt.date():
        if month == 12:
            month = 1
            year += 1
        else:
            month += 1
        last_day = calendar.monthrange(year, month)[1]
        last_dt = datetime(year, month, last_day, 15, 30)
        while last_dt.weekday() != weekday:
            last_dt = last_dt - timedelta(days=1)
            
    return last_dt

def exchange_request_code(api_key: str, api_secret: str, request_code: str) -> str:
    """Exchanges authorization request_code for the daily session token/key via SHA-256."""
    import urllib.error
    
    # Create SHA-256 hash signature
    hash_input = (api_key + request_code + api_secret).encode("utf-8")
    hash_value = hashlib.sha256(hash_input).hexdigest()
    
    payload = {
        "api_key": api_key,
        "request_code": request_code,
        "api_secret": hash_value
    }
    
    try:
        status_code, res_body = send_api_request(TOKEN_URL, payload)
        if status_code == 200:
            data = json.loads(res_body)
            token = data.get("token")
            if token:
                return token
            
            # Check for error msg in response body
            emsg = data.get("emsg")
            stat = data.get("stat")
            if stat == "Not_Ok" or emsg:
                raise ValueError(f"Flattrade returned: {emsg or 'Not_Ok status'}")
            raise ValueError(f"No token field in response: {res_body}")
        else:
            raise ValueError(f"HTTP status {status_code}: {res_body}")
    except urllib.error.HTTPError as he:
        try:
            err_body = he.read().decode("utf-8")
            try:
                data = json.loads(err_body)
                if data.get("emsg"):
                    raise ValueError(f"Flattrade error: {data['emsg']}")
            except Exception:
                pass
            raise ValueError(f"HTTP {he.code}: {err_body}")
        except Exception:
            raise ValueError(f"HTTP {he.code} Error (Failed to read response body)")
    except Exception as e:
        if isinstance(e, ValueError):
            raise e
        raise ValueError(f"Network error: {str(e)}")

def map_to_flattrade_symbol(symbol_str: str) -> str:
    """
    Maps app symbol to Flattrade Pi API trading symbol.
    Options: 'NIFTY 07JUL26 24100 CE' -> 'NIFTY07JUL26C24100'
    Futures: 'NIFTY' -> 'NIFTY28JUL26F'
    """
    s = symbol_str.strip()
    parts = s.split()
    
    # 1. Option contract mapping (e.g. NIFTY 07JUL26 24100 CE)
    if len(parts) >= 4 and parts[-1] in ["CE", "PE"]:
        underlying = parts[0]
        expiry = parts[1]
        strike = parts[2]
        opt_type = "C" if parts[3] == "CE" else "P"
        return f"{underlying}{expiry}{opt_type}{strike}"
        
    # 2. Futures mapping (e.g. NIFTY1! or NIFTY)
    underlying = s
    if underlying.endswith("1!"):
        underlying = underlying[:-2]
    elif underlying.endswith("!"):
        underlying = underlying[:-1]
        
    if "BANKNIFTY" in underlying or "BNF" in underlying:
        underlying = "BANKNIFTY"
    elif "NIFTY" in underlying:
        underlying = "NIFTY"
    elif "SENSEX" in underlying or "BSX" in underlying:
        underlying = "SENSEX"
        
    # Get near-month futures expiry date (last Tuesday of the month for 2026 rules)
    ist_now = get_ist_time()
    expiry_date = get_next_monthly_expiry(ist_now, weekday=1) # Tuesday
    expiry_str = expiry_date.strftime('%d%b%y').upper()
    
    return f"{underlying}{expiry_str}F"


def prepare_order(
    symbol: str,
    direction: str,
    lots: float,
    price: float,
    trade_type: str,
    quantity: Optional[int] = None,
) -> Dict[str, Any]:
    if lots <= 0 or int(lots) != lots:
        raise FlattradeError("Lots must be a positive whole number")
    qty = int(quantity if quantity is not None else lots)
    if qty <= 0:
        raise FlattradeError("Order quantity must be positive")

    parts = symbol.split()
    is_option = len(parts) >= 3 and parts[-1] in ["CE", "PE"]
    normalized = symbol.upper().strip()
    underlying = parts[0].upper() if parts else normalized
    is_future = (
        "1!" in normalized
        or "FUT" in normalized
        or normalized in ["NIFTY", "BANKNIFTY", "SENSEX"]
    )
    exch = ("BFO" if ("SENSEX" in underlying or "BSX" in underlying) else "NFO") if (is_option or is_future) else "NSE"
    transaction_type = "B" if direction.upper() in ["BUY", "LONG"] else "S"
    product = ("M" if trade_type.upper() == "INTRADAY" else "H") if exch == "NFO" else (
        "M" if trade_type.upper() == "INTRADAY" else "C"
    )
    trading_symbol = map_to_flattrade_symbol(symbol)
    return {
        "broker_id": "flattrade",
        "broker_name": "Flattrade",
        "symbol": symbol,
        "trading_symbol": trading_symbol,
        "instrument_id": trading_symbol,
        "exchange": exch,
        "transaction_type": transaction_type,
        "quantity": qty,
        "lot_size": int(qty / lots) if lots else qty,
        "lots": int(lots),
        "product": product,
        "order_type": "LIMIT",
        "validity": "DAY",
        "limit_price": round(float(price), 2),
    }


def _load_user_session(user_id: int, db: Session) -> Dict[str, str]:
    from backend.credentials import AppCredentialsManager

    mgr = AppCredentialsManager(db, user_id=user_id)
    creds = mgr.load_credentials("flattrade")
    if not creds:
        raise FlattradeError("Flattrade credentials are not configured")

    extra = creds.get("extra", {}) or {}
    client_id = extra.get("client_id")
    session_token = extra.get("token")
    token_date = extra.get("token_date")
    today_str = get_ist_time().date().isoformat()

    if not client_id or not creds.get("api_key"):
        raise FlattradeError("Flattrade Client ID and API Key are required")
    if not session_token:
        raise FlattradeError("Authorize today's Flattrade session before placing live orders")
    if token_date != today_str:
        raise FlattradeError("Flattrade live session expired. Re-authorize today's session")

    return {
        "client_id": client_id,
        "api_key": creds.get("api_key"),
        "session_token": session_token,
    }


def place_order(user_id: int, db: Session, prepared: Dict[str, Any], order_tag: str = "") -> Dict[str, Any]:
    session = _load_user_session(user_id, db)
    client_id = session["client_id"]
    jData = {
        "uid": client_id,
        "actid": client_id,
        "exch": prepared["exchange"],
        "tsym": prepared["trading_symbol"],
        "qty": str(int(prepared["quantity"])),
        "trantype": prepared["transaction_type"],
        "prctyp": "LMT",
        "prd": prepared["product"],
        "ret": "DAY",
        "trgprc": "0",
        "prc": f"{float(prepared['limit_price']):.2f}",
    }
    if order_tag:
        jData["remarks"] = order_tag[:50]

    payload = {
        "jData": json.dumps(jData),
        "jKey": session["session_token"],
    }

    try:
        response_code, response_body = send_api_request(PLACE_ORDER_URL, payload)
        data = json.loads(response_body)
    except Exception as exc:
        raise FlattradeError(f"Flattrade request failed: {exc}") from exc

    if response_code != 200:
        raise FlattradeError(f"HTTP status code {response_code}: {response_body}")
    if data.get("stat") != "Ok":
        raise FlattradeError(data.get("emsg") or "Flattrade rejected the order")

    broker_order_id = data.get("norenordno")
    if not broker_order_id:
        raise FlattradeError("Flattrade did not return an order number")

    return {
        "status": "success",
        "broker_order_id": str(broker_order_id),
        "prepared": prepared,
        "broker_response": data,
    }

def place_flattrade_order(user_id: int, symbol: str, direction: str, qty: float, price: float, db: Session, trade_type: str = "INTRADAY") -> Dict[str, Any]:
    """Places a live Limit order with Flattrade REST API."""
    try:
        prepared = prepare_order(
            symbol=symbol,
            direction=direction,
            lots=1,
            quantity=int(qty),
            price=price,
            trade_type=trade_type,
        )
        result = place_order(user_id, db, prepared, order_tag=f"GDD-{symbol}"[:50])
        return {
            "status": "success",
            "order_id": result["broker_order_id"],
            "symbol": prepared["trading_symbol"],
            "qty": qty,
            "price": price,
            "broker_response": result.get("broker_response", {}),
        }
    except Exception as e:
        return {"status": "error", "message": f"Network / API connection error: {e}"}

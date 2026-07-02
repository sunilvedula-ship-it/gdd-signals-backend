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

def send_api_request(url: str, data_dict: dict) -> tuple[int, str]:
    """Helper to send POST request, optionally routing through a proxy if configured."""
    headers = {"Content-Type": "application/json"}
    data_bytes = json.dumps(data_dict).encode("utf-8")
    req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
    
    proxy_url = os.environ.get("PROXY_URL") or os.environ.get("QUOTAGUARDSTATIC_URL") or os.environ.get("FIXIE_URL")
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

def place_flattrade_order(user_id: int, symbol: str, direction: str, qty: float, price: float, db: Session, trade_type: str = "INTRADAY") -> Dict[str, Any]:
    """Places a live Limit order with Flattrade REST API."""
    from backend.credentials import AppCredentialsManager
    mgr = AppCredentialsManager(db, user_id=user_id)
    
    # Load credentials
    creds = mgr.load_credentials("flattrade")
    if not creds:
        return {"status": "error", "message": "Flattrade credentials not configured."}
        
    client_id = creds.get("extra", {}).get("client_id")
    api_key = creds.get("api_key")
    session_token = creds.get("extra", {}).get("token")
    token_date = creds.get("extra", {}).get("token_date")
    
    if not client_id or not api_key or not session_token:
        return {"status": "error", "message": "Live session not authorized. Please log in to Flattrade."}
        
    # Verify token is from today
    today_str = get_ist_time().date().isoformat()
    if token_date != today_str:
        return {"status": "error", "message": "Live session expired. Please re-authorize today's session."}
        
    # Resolve exchange, transaction type and product
    parts = symbol.split()
    is_option = len(parts) >= 3 and parts[-1] in ["CE", "PE"]
    is_future = "1!" in symbol or "FUT" in symbol or symbol in ["NIFTY", "BANKNIFTY", "SENSEX"]
    
    exch = "NFO" if (is_option or is_future) else "NSE"
    trantype = "B" if direction.upper() in ["BUY", "LONG"] else "S"
    
    # Product type: MIS (M) for intraday, NRML (H) for positional options/futures, CNC (C) for stocks
    if exch == "NFO":
        prd = "M" if trade_type.upper() == "INTRADAY" else "H"
    else:
        prd = "M" if trade_type.upper() == "INTRADAY" else "C"
        
    tsym = map_to_flattrade_symbol(symbol)
    
    # Construct Flattrade order JSON payload
    jData = {
        "uid": client_id,
        "actid": client_id,
        "exch": exch,
        "tsym": tsym,
        "qty": str(int(qty)),
        "trantype": trantype,
        "prctyp": "LMT",
        "prd": prd,
        "ret": "DAY",
        "trgprc": "0",
        "prc": f"{price:.2f}"
    }
    
    payload = {
        "jData": json.dumps(jData),
        "jKey": session_token
    }
    
    try:
        response_code, response_body = send_api_request(PLACE_ORDER_URL, payload)
        if response_code == 200:
            data = json.loads(response_body)
            if data.get("stat") == "Ok":
                return {
                    "status": "success",
                    "order_id": data.get("norenordno"),
                    "symbol": tsym,
                    "qty": qty,
                    "price": price
                }
            else:
                return {"status": "error", "message": data.get("emsg", "Flattrade returned failure status")}
        else:
            return {"status": "error", "message": f"HTTP status code {response_code}"}
    except Exception as e:
        return {"status": "error", "message": f"Network / API connection error: {e}"}

import os
import json
import math
import html
import secrets
from urllib.parse import quote
from datetime import datetime, date, time, timedelta
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, status, Query, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError


from backend.database import init_db, get_db, Signal, Position, DailyConsent, User, BrokerOrder, BrokerAuthState
from backend.credentials import AppCredentialsManager, INDIAN_BROKERS, CRYPTO_EXCHANGES
from backend.flattrade_client import place_flattrade_order, exchange_request_code
from backend.aliceblue_client import (
    AliceBlueError,
    AliceBlueOrderStatusUnknown,
    exchange_vendor_session,
    get_funds as get_aliceblue_funds,
    get_order_history as get_aliceblue_order_history,
    get_vendor_login_url,
    is_vendor_configured as is_aliceblue_vendor_configured,
    place_order as place_aliceblue_order,
    prepare_order as prepare_aliceblue_order,
)
from backend.security import SignedTokenError, create_signed_token, env_flag, verify_signed_token
from backend.signal_rules import resolve_trade_type, resolve_webhook_execution_mode
from fastapi.responses import HTMLResponse, RedirectResponse

# Initialize FastAPI App
app = FastAPI(title="GuruDevaDatta Trading App Backend", version="1.0.0")

# Enable CORS for the local web simulator
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Active WebSocket connections
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                # Handle stale connections
                pass

manager = ConnectionManager()

# Webhook payload model
class WebhookPayload(BaseModel):
    auth: str
    symbol: str
    action: str  # buy, sell, long, short, exit, exit_long, exit_short
    price: float
    orderId: Optional[str] = None
    datetime: Optional[str] = None
    telegram: Optional[str] = "disabled"
    discord: Optional[str] = "disabled"
    source: Optional[str] = "TradingView"

# Request models
class ConsentRequest(BaseModel):
    agreement_version: str

class CredentialRequest(BaseModel):
    broker_id: str
    api_key: str
    api_secret: str
    extra: Optional[dict] = None

# On startup, initialize DB
@app.on_event("startup")
def startup_event():
    init_db()
    # Insert a dummy user if not exists
    db = next(get_db())
    if not db.query(User).filter(User.id == 1).first():
        user = User(id=1, email="user@example.com", phone="+919999999999", name="Sunil Vedula")
        # 5 working-days trial setup (mocked as ending 7 days from now to cover weekend)
        user.trial_end = get_ist_time() + timedelta(days=7)
        db.add(user)
        db.commit()
    
    # Local simulator convenience is explicitly opt-in and never enabled in production by default.
    ist_now = get_ist_time()
    today_str = ist_now.date().isoformat()
    if env_flag("ALLOW_SANDBOX_AUTH") and not db.query(DailyConsent).filter(DailyConsent.date == today_str, DailyConsent.user_id == 1).first():
        consent = DailyConsent(
            date=today_str,
            agreement_text_version="v1.0",
            consent_given=True,
            timestamp=ist_now,
            user_id=1
        )
        db.add(consent)
        db.commit()
        
    db.close()

# Supabase Credentials & Token Validation Helpers
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://lgedxrswafjsvjcoduvw.supabase.co")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImxnZWR4cnN3YWZqc3ZqY29kdXZ3Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzk1MjczMDYsImV4cCI6MjA5NTEwMzMwNn0.08tMMK4TGbfeLZKHYteqtU2EYR4K5PwAJmgeA-xqrXk")

def get_user_from_token(token: str) -> Optional[dict]:
    import urllib.request
    import json
    
    url = f"{SUPABASE_URL}/auth/v1/user"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "apikey": SUPABASE_ANON_KEY
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as e:
        print(f"[Auth Error] Failed to validate token with Supabase: {e}")
        return None

def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    auth_header = request.headers.get("Authorization")
    token = None
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
        
    if not token and env_flag("ALLOW_SANDBOX_AUTH"):
        # Explicit local-only fallback for the web simulator.
        user = db.query(User).filter(User.id == 1).first()
        if not user:
            user = User(
                id=1,
                email="sandbox@example.com",
                phone="+919999999999",
                name="Sandbox User",
                subscription_status="active"
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        return user

    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
        
    # Verify token
    user_info = get_user_from_token(token)
    if not user_info:
        raise HTTPException(status_code=401, detail="Invalid session token. Please log in again.")
        
    supabase_uid = user_info.get("id")
    email = user_info.get("email")
    phone = user_info.get("phone")
    name = user_info.get("user_metadata", {}).get("name", email or phone or "User")
    
    user = db.query(User).filter(User.supabase_uid == supabase_uid).first()
    if not user:
        # Check if user with same phone or email exists but has no supabase_uid
        user = db.query(User).filter(
            (User.phone == phone) | (User.email == email)
        ).first()
        if user:
            user.supabase_uid = supabase_uid
        else:
            user = User(
                supabase_uid=supabase_uid,
                email=email,
                phone=phone,
                name=name,
                subscription_status="active"
            )
            db.add(user)
        db.commit()
        db.refresh(user)
        
    return user

def get_base_symbol(symbol: str) -> str:
    s = symbol.upper().strip()
    if ":" in s:
        s = s.split(":")[-1]
    
    # Check if option symbol
    parse_res = parse_option_symbol(s)
    if parse_res.get("is_option"):
        underlying = parse_res["underlying"]
        return get_base_symbol(underlying)
        
    if s.endswith("1!"):
        s = s[:-2]
    elif s.endswith("!"):
        s = s[:-1]
        
    if "XAU" in s or "GOLD" in s:
        return "GOLD"
    if "SILVER" in s:
        return "SILVER"
    if "BANKNIFTY" in s or "BNF" in s:
        return "BANKNIFTY"
    if "NIFTY" in s:
        return "NIFTY"
    if "SENSEX" in s or "BSX" in s:
        return "SENSEX"
    if "CRUDE" in s:
        return "CRUDEOIL"
    return s

def is_symbol_muted(symbol: str, muted_symbols_str: str) -> bool:
    if not muted_symbols_str:
        return False
        
    target_base = get_base_symbol(symbol)
    muted_list = [get_base_symbol(s) for s in muted_symbols_str.split(",") if s.strip()]
    
    return target_base in muted_list

# Settings endpoints
class MuteRequest(BaseModel):
    symbol: str
    mute: bool

@app.get("/api/user/settings")
def get_user_settings(user: User = Depends(get_current_user)):
    muted_list = [s.strip() for s in user.muted_symbols.split(",") if s.strip()] if user.muted_symbols else []
    return {"muted_symbols": muted_list}

@app.post("/api/user/settings/mute")
def toggle_mute_symbol(req: MuteRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    muted_list = [s.strip() for s in user.muted_symbols.split(",") if s.strip()] if user.muted_symbols else []
    # Clean exchange prefixes/suffixes like normalize_symbol does, or use raw input
    sym = req.symbol.upper().strip()
    if ":" in sym:
        sym = sym.split(":")[-1]
    if sym.endswith("1!"):
        sym = sym[:-2]
    elif sym.endswith("!"):
        sym = sym[:-1]
        
    if "XAUUSD" in sym or "XAU" in sym or "GOLD" in sym:
        sym = "GOLD"
    elif "SILVER" in sym:
        sym = "SILVER"
    elif "BANKNIFTY" in sym or "BNF" in sym:
        sym = "BANKNIFTY"
    elif "NIFTY" in sym:
        sym = "NIFTY"
    elif "SENSEX" in sym or "BSX" in sym:
        sym = "SENSEX"
    elif "CRUDE" in sym:
        sym = "CRUDEOIL"
        
    if req.mute:
        if sym not in muted_list:
            muted_list.append(sym)
    else:
        if sym in muted_list:
            muted_list.remove(sym)
            
    user.muted_symbols = ",".join(muted_list)
    db.commit()
    return {"status": "success", "muted_symbols": muted_list}


# WebSocket endpoint for real-time updates
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

def get_ist_time() -> datetime:
    # Indian Standard Time (IST) is UTC + 5:30
    return datetime.utcnow() + timedelta(hours=5, minutes=30)

import re

MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12
}

def parse_option_symbol(symbol: str) -> dict:
    s = symbol.upper().strip()
    if ":" in s:
        s = s.split(":")[-1]
        
    # Format 1: Space separated with explicit expiry e.g. NIFTY 09JUN26 23500 CE
    match_space = re.match(r'^([A-Z]+)\s+([0-9A-Z]{6,7})\s+(\d+)\s+(CE|PE)$', s)
    if match_space:
        underlying = match_space.group(1)
        expiry_str = match_space.group(2)
        strike = int(match_space.group(3))
        opt_type = match_space.group(4)
        
        # Parse expiry date
        expiry_date = None
        try:
            expiry_date = datetime.strptime(expiry_str, "%d%b%y").date()
        except Exception:
            try:
                expiry_date = datetime.strptime(expiry_str, "%y%m%d").date()
            except Exception:
                pass
                
        if underlying in ["BNF", "BANKNIFTY"]:
            underlying = "BANKNIFTY"
        elif underlying in ["NIFTY"]:
            underlying = "NIFTY"
        elif underlying in ["BSX", "SENSEX"]:
            underlying = "SENSEX"
            
        return {
            "is_option": True,
            "underlying": underlying,
            "expiry_date": expiry_date,
            "strike": strike,
            "opt_type": opt_type,
            "formatted_symbol": f"{underlying} {expiry_date.strftime('%d%b%y').upper() if expiry_date else ''} {strike} {opt_type}"
        }
        
    # Format 2: Space separated without expiry (fallback ATM calculation) e.g. BANKNIFTY 54400 CE
    match_no_expiry = re.match(r'^([A-Z]+)\s+(\d+)\s+(CE|PE)$', s)
    if match_no_expiry:
        underlying = match_no_expiry.group(1)
        strike = int(match_no_expiry.group(2))
        opt_type = match_no_expiry.group(3)
        
        if underlying in ["BNF", "BANKNIFTY"]:
            underlying = "BANKNIFTY"
        elif underlying in ["NIFTY"]:
            underlying = "NIFTY"
        elif underlying in ["BSX", "SENSEX"]:
            underlying = "SENSEX"
            
        return {
            "is_option": True,
            "underlying": underlying,
            "expiry_date": None,
            "strike": strike,
            "opt_type": opt_type,
            "formatted_symbol": f"{underlying} {strike} {opt_type}"
        }

    # Format 3: NSE compact format e.g. NIFTY09JUN26C23500 or BANKNIFTY260625C54400
    match_nse = re.match(r'^([A-Z]+)([0-9A-Z]{6,7})([CP])(\d+)$', s)
    if match_nse:
        underlying = match_nse.group(1)
        expiry_str = match_nse.group(2)
        opt_char = match_nse.group(3)
        strike = int(match_nse.group(4))
        
        expiry_date = None
        try:
            expiry_date = datetime.strptime(expiry_str, "%d%b%y").date()
        except Exception:
            try:
                expiry_date = datetime.strptime(expiry_str, "%y%m%d").date()
            except Exception:
                pass
                
        if underlying in ["BNF", "BANKNIFTY"]:
            underlying = "BANKNIFTY"
        elif underlying in ["NIFTY"]:
            underlying = "NIFTY"
        elif underlying in ["BSX", "SENSEX"]:
            underlying = "SENSEX"
            
        opt_type = "CE" if opt_char == "C" else "PE"
        return {
            "is_option": True,
            "underlying": underlying,
            "expiry_date": expiry_date,
            "strike": strike,
            "opt_type": opt_type,
            "formatted_symbol": f"{underlying} {expiry_date.strftime('%d%b%y').upper() if expiry_date else ''} {strike} {opt_type}"
        }
        
    # Format 4: BSE compact format e.g. SENSEX2660474800CE
    match_bse = re.match(r'^([A-Z]+)(\d{2})(10|11|12|[1-9]|[OND])(\d{2})(\d+)(CE|PE)$', s)
    if match_bse:
        underlying = match_bse.group(1)
        year_str = match_bse.group(2)
        month_str = match_bse.group(3)
        day_str = match_bse.group(4)
        strike = int(match_bse.group(5))
        opt_type = match_bse.group(6)
        
        # Parse expiry date
        year = 2000 + int(year_str)
        day = int(day_str)
        
        if month_str.isdigit():
            month = int(month_str)
        else:
            m_char = month_str.upper()
            if m_char == "O": month = 10
            elif m_char == "N": month = 11
            elif m_char == "D": month = 12
            else: month = 1
            
        try:
            expiry_date = date(year, month, day)
        except Exception:
            expiry_date = None
            
        if underlying in ["BNF", "BANKNIFTY"]:
            underlying = "BANKNIFTY"
        elif underlying in ["NIFTY"]:
            underlying = "NIFTY"
        elif underlying in ["BSX", "SENSEX"]:
            underlying = "SENSEX"
            
        return {
            "is_option": True,
            "underlying": underlying,
            "expiry_date": expiry_date,
            "strike": strike,
            "opt_type": opt_type,
            "formatted_symbol": f"{underlying} {expiry_date.strftime('%d%b%y').upper() if expiry_date else ''} {strike} {opt_type}"
        }

    return {"is_option": False}

def get_next_weekly_expiry(ist_now: datetime, expiry_weekday: int) -> date:
    days_ahead = expiry_weekday - ist_now.weekday()
    if days_ahead < 0:
        days_ahead += 7
    elif days_ahead == 0:
        if ist_now.time() > time(15, 30):
            days_ahead = 7
    return (ist_now + timedelta(days=days_ahead)).date()

def get_last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    if month == 12:
        last_day = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(year, month + 1, 1) - timedelta(days=1)
    while last_day.weekday() != weekday:
        last_day -= timedelta(days=1)
    return last_day

def get_next_monthly_expiry(ist_now: datetime, weekday: int = 1) -> date:
    curr_month_expiry = get_last_weekday_of_month(ist_now.year, ist_now.month, weekday)
    is_today_expiry = ist_now.date() == curr_month_expiry
    is_past_expiry = ist_now.date() > curr_month_expiry or (is_today_expiry and ist_now.time() > time(15, 30))
    
    if is_past_expiry:
        if ist_now.month == 12:
            next_year = ist_now.year + 1
            next_month = 1
        else:
            next_year = ist_now.year
            next_month = ist_now.month + 1
        return get_last_weekday_of_month(next_year, next_month, weekday)
    return curr_month_expiry

def get_time_to_expiry_years(underlying: str, expiry_date: Optional[date] = None) -> float:
    ist_now = get_ist_time()
    if expiry_date is None:
        if underlying == "BANKNIFTY":
            expiry_date = get_next_monthly_expiry(ist_now, weekday=1)
        elif underlying == "NIFTY":
            expiry_date = get_next_weekly_expiry(ist_now, 1)
        elif underlying == "SENSEX":
            expiry_date = get_next_weekly_expiry(ist_now, 3)
        else:
            expiry_date = get_next_weekly_expiry(ist_now, 1)
        
    expiry_datetime = datetime.combine(expiry_date, time(15, 30))
    diff = expiry_datetime - ist_now
    diff_seconds = diff.total_seconds()
    if diff_seconds <= 0:
        return 0.0
    return diff_seconds / (365.0 * 24.0 * 3600.0)

def black_scholes_option_price(S: float, K: float, T: float, r: float, sigma: float, option_type: str) -> float:
    if T <= 0:
        if option_type == "CE":
            return max(0.0, S - K)
        else:
            return max(0.0, K - S)
            
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    
    def N(x):
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
        
    if option_type == "CE":
        price = S * N(d1) - K * math.exp(-r * T) * N(d2)
    else:
        price = K * math.exp(-r * T) * N(-d2) - S * N(-d1)
    return price

def calculate_option_price_bs(underlying: str, strike: float, opt_type: str, live_underlying: float, expiry_date: Optional[date] = None) -> float:
    T = get_time_to_expiry_years(underlying, expiry_date)
    r = 0.07
    if underlying == "BANKNIFTY":
        sigma = 0.19
    elif underlying in ["NIFTY", "SENSEX"]:
        sigma = 0.15
    else:
        sigma = 0.15
    price = black_scholes_option_price(S=live_underlying, K=strike, T=T, r=r, sigma=sigma, option_type=opt_type)
    return round(max(1.0, price), 2)

# Helper to normalize symbols
def normalize_symbol(symbol: str) -> str:
    s = symbol.upper().strip()
    
    # Check if option symbol first
    parse_res = parse_option_symbol(s)
    if parse_res.get("is_option"):
        return parse_res["formatted_symbol"]
        
    # Strip TV futures suffixes
    if s.endswith("1!"):
        s = s[:-2]
    elif s.endswith("!"):
        s = s[:-1]
    
    if ":" in s:
        s = s.split(":")[-1]
        
    if "XAUUSD" in s or "XAU" in s or "GOLD" in s:
        return "GOLD"
    if "SILVER" in s:
        return "SILVER"
    if "BANKNIFTY" in s or "BNF" in s:
        return "BANKNIFTY"
    if "NIFTY" in s:
        return "NIFTY"
    if "SENSEX" in s or "BSX" in s:
        return "SENSEX"
    if "CRUDE" in s:
        return "CRUDEOIL"
    return s


def extract_strike_from_symbol(symbol: str) -> Optional[float]:
    parse_res = parse_option_symbol(symbol)
    if parse_res.get("is_option"):
        return float(parse_res["strike"])
    return None

def close_position_entry(pos: Position, index_exit_price: float, db: Session) -> float:
    parse_res = parse_option_symbol(pos.symbol)
    
    if parse_res.get("is_option"):
        underlying = parse_res["underlying"]
        opt_type = parse_res["opt_type"]
        strike = parse_res["strike"]
        expiry_date = parse_res.get("expiry_date")
        
        if index_exit_price > 0 and index_exit_price < 0.2 * strike:
            exit_price = index_exit_price
        else:
            exit_price = calculate_option_price_bs(underlying, strike, opt_type, index_exit_price, expiry_date=expiry_date)
    else:
        exit_price = index_exit_price
        
    if pos.real_or_paper.upper() == "LIVE":
        from backend.credentials import AppCredentialsManager
        mgr = AppCredentialsManager(db, user_id=pos.user_id)
        active_broker = pos.broker_id or mgr.get_active_broker()
        if active_broker == "flattrade":
            opp_direction = "SHORT" if pos.direction == "LONG" else "LONG"
            res = place_flattrade_order(
                user_id=pos.user_id,
                symbol=pos.symbol,
                direction=opp_direction,
                qty=pos.qty,
                price=exit_price,
                db=db,
                trade_type=pos.trade_type or "INTRADAY"
            )
            if res.get("status") == "error":
                raise Exception(res.get("message"))
        elif active_broker == "aliceblue":
            opp_direction = "SHORT" if pos.direction == "LONG" else "LONG"
            existing_exit = db.query(BrokerOrder).filter(
                BrokerOrder.user_id == pos.user_id,
                BrokerOrder.position_id == pos.id,
                BrokerOrder.order_kind == "EXIT",
            ).first()
            if existing_exit and existing_exit.status in {"PENDING", "SUBMITTED", "UNKNOWN"}:
                raise Exception("An exit order is already pending at Alice Blue")

            try:
                prepared = prepare_aliceblue_order(
                    symbol=pos.symbol,
                    direction=opp_direction,
                    lots=float(pos.lot_size or 1),
                    quantity=int(pos.qty),
                    price=exit_price,
                    trade_type=pos.trade_type or "INTRADAY",
                )
            except AliceBlueError as exc:
                raise Exception(str(exc)) from exc

            exit_order = existing_exit or BrokerOrder(
                user_id=pos.user_id,
                signal_id=pos.signal_id,
                position_id=pos.id,
                broker_id="aliceblue",
                idempotency_key=f"exit-{pos.id}",
                order_kind="EXIT",
                symbol=pos.symbol,
                created_at=get_ist_time(),
            )
            exit_order.broker_trading_symbol = prepared["trading_symbol"]
            exit_order.broker_instrument_id = prepared["instrument_id"]
            exit_order.transaction_type = prepared["transaction_type"]
            exit_order.quantity = prepared["quantity"]
            exit_order.limit_price = prepared["limit_price"]
            exit_order.status = "PENDING"
            exit_order.updated_at = get_ist_time()
            if not existing_exit:
                db.add(exit_order)
            db.commit()

            try:
                result = place_aliceblue_order(
                    pos.user_id,
                    db,
                    prepared,
                    order_tag=f"GDD-EXIT-{pos.id}",
                )
            except AliceBlueOrderStatusUnknown as exc:
                exit_order.status = "UNKNOWN"
                exit_order.broker_response = json.dumps({"error": str(exc)})
                exit_order.updated_at = get_ist_time()
                db.commit()
                raise Exception(str(exc)) from exc
            except AliceBlueError as exc:
                exit_order.status = "REJECTED"
                exit_order.broker_response = json.dumps({"error": str(exc)})
                exit_order.updated_at = get_ist_time()
                db.commit()
                raise Exception(str(exc)) from exc

            exit_order.status = "SUBMITTED"
            exit_order.broker_order_id = result["broker_order_id"]
            exit_order.broker_response = json.dumps(result.get("broker_response", {}))
            exit_order.updated_at = get_ist_time()
            pos.exit_broker_order_id = result["broker_order_id"]
            pos.exit_order_status = "SUBMITTED"
            pos.status = "EXIT_PENDING"
            db.commit()
            return pos.pnl
        
    pos.exit_price = round(exit_price, 2)
    pos.exit_time = get_ist_time()
    pos.status = "CLOSED"
    
    if pos.direction == "LONG":
        pos.pnl = round((pos.exit_price - pos.entry_price) * pos.qty, 2)
    else:
        pos.pnl = round((pos.entry_price - pos.exit_price) * pos.qty, 2)
        
    return pos.pnl


def open_position_entry(symbol: str, direction: str, entry_price: float, qty: float, db: Session, 
                        user_id: int = 1, timeframe: str = "5m", real_or_paper: str = "PAPER", trade_type: str = "INTRADAY") -> Position:
    if real_or_paper.upper() == "LIVE":
        from backend.credentials import AppCredentialsManager
        mgr = AppCredentialsManager(db, user_id=user_id)
        active_broker = mgr.get_active_broker()
        if active_broker == "flattrade":
            res = place_flattrade_order(
                user_id=user_id,
                symbol=symbol,
                direction=direction,
                qty=qty,
                price=entry_price,
                db=db,
                trade_type=trade_type
            )
            if res.get("status") == "error":
                raise Exception(res.get("message"))
                
    new_pos = Position(
        user_id=user_id,
        symbol=symbol,
        direction=direction,
        qty=qty,
        entry_price=entry_price,
        entry_time=get_ist_time(),
        status="OPEN",
        timeframe=timeframe,
        real_or_paper=real_or_paper,
        trade_type=trade_type
    )
    db.add(new_pos)
    return new_pos

def safe_open_position_entry(symbol: str, direction: str, entry_price: float, qty: float, db: Session, 
                             user_id: int, timeframe: str, real_or_paper: str, trade_type: str, trade_log: list, success_msg: str):
    try:
        open_position_entry(symbol, direction, entry_price, qty, db, user_id=user_id, timeframe=timeframe, real_or_paper=real_or_paper, trade_type=trade_type)
        trade_log.append(success_msg)
    except Exception as ex:
        trade_log.append(f"User {user_id}: Failed to open {direction} position for {symbol} in {real_or_paper} mode: {ex}")

def safe_close_position_entry(pos: Position, index_exit_price: float, db: Session, trade_log: list, success_msg_template: str):
    try:
        pnl = close_position_entry(pos, index_exit_price, db)
        trade_log.append(success_msg_template.format(pnl=pnl, exit_price=pos.exit_price))
    except Exception as ex:
        trade_log.append(f"User {pos.user_id}: Failed to close {pos.direction} position on {pos.symbol} in {pos.real_or_paper} mode: {ex}")

# Helper to determine qty and lot size based on symbol rules
def calculate_trade_qty(symbol: str) -> float:
    sym = symbol.upper()
    if "BANKNIFTY" in sym:
        return 30.0  # 1 lot Banknifty
    elif "NIFTY" in sym:
        return 65.0  # 1 lot Nifty
    elif "SENSEX" in sym or "BSX" in sym:
        return 20.0  # 1 lot Sensex
    elif any(crypto in sym for crypto in ["BTC", "ETH", "SOL"]):
        return 1.0   # 1 qty for Cryptos
    # Stock futures lot sizes (defaults)
    elif "WIPRO" in sym:
        return 1500.0
    elif "RELIANCE" in sym:
        return 250.0
    elif "TITAN" in sym:
        return 375.0
    elif "BAJFINSERV" in sym:
        return 500.0
    elif "ADANIPORTS" in sym:
        return 625.0
    elif "CRUDE" in sym:
        return 100.0
    elif "GOLD" in sym:
        return 100.0
    else:
        return 100.0  # Default fallback lot size

def calculate_option_premium(symbol: str, index_price: float) -> float:
    sym = symbol.upper().strip()
    # Weekly indices: NIFTY, SENSEX, BSX
    # Monthly indices: BANKNIFTY, BNF
    is_weekly = "NIFTY" in sym or "SENSEX" in sym or "BSX" in sym
    is_banknifty = "BANKNIFTY" in sym or "BNF" in sym
    
    if is_banknifty:
        return round(index_price * 0.021, 2)      # 2.1% of index value standard (matches ~1124 premium at 54400 strike)
    elif is_weekly:
        ist_now = get_ist_time()
        # Nifty expiry typically Thursdays, FinNifty Tuesdays, Sensex Fridays.
        # Check if weekday is Tuesday (1), Thursday (3), or Friday (4).
        is_expiry_day = ist_now.weekday() in [1, 3, 4]
        if is_expiry_day:
            return round(index_price * 0.003, 2)  # 0.3% of index value
        else:
            return round(index_price * 0.006, 2)  # 0.6% of index value
    else:
        return round(index_price * 0.012, 2)      # 1.2% fallback

@app.post("/api/signals/webhook")
async def receive_webhook(request: Request, db: Session = Depends(get_db)):
    body_bytes = await request.body()
    body_str = body_bytes.decode("utf-8", errors="ignore").strip()
    
    print(f"[Webhook Request] Received: Path={request.url.path}, Method={request.method}, QueryParams={dict(request.query_params)}, Body='{body_str}'")
    
    payload = {}
    if body_str:
        try:
            payload = json.loads(body_str)
        except Exception as e:
            # Not JSON. Try parsing as form/query parameters
            try:
                from urllib.parse import parse_qs
                parsed = parse_qs(body_str)
                payload = {k: v[0] for k, v in parsed.items()}
            except Exception:
                pass
    
    # Merge query parameters (gives query params priority or fallback)
    for k, v in request.query_params.items():
        payload[k] = v
        
    # 1. Resolve Auth Secret (Query, Header or Body)
    auth_token = payload.get("secret") or payload.get("auth") or payload.get("auth-token")
    if not auth_token:
        auth_token = request.headers.get("x-webhook-secret")
        
    # Load secrets dynamically from environment (comma-separated) or fallback to defaults
    env_secrets = os.environ.get("VALID_SECRETS")
    if env_secrets:
        VALID_SECRETS = [s.strip() for s in env_secrets.split(",")]
    else:
        VALID_SECRETS = ["TradeSignal2024", "indian_market_5645c3c44e98ddb7ed7aee5f05482e6e9e910031", "8cf895aa0e3387d51d8c6c19f3dea05e02e2839b"]

    if not auth_token and body_str:
        # Check if auth-token or secret is in body_str as substring
        for val_sec in VALID_SECRETS:
            if val_sec in body_str:
                auth_token = val_sec
                break
    if not auth_token or auth_token not in VALID_SECRETS:
        print(f"[Webhook Error] Unauthorized auth token: '{auth_token}' in payload: {payload}")
        raise HTTPException(status_code=401, detail="Unauthorized webhook source")
        
    # 2. Resolve Symbol
    symbol = payload.get("symbol") or payload.get("ticker")
    if not symbol and body_str:
        # Fallback to search inside raw body text
        for sym_cand in ["BTCUSD", "ETHUSD", "BANKNIFTY", "NIFTY", "SENSEX", "CRUDEOIL"]:
            if sym_cand in body_str.upper():
                symbol = sym_cand
                break
    if not symbol:
        # Fallback default to prevent 400 bad request error for empty alerts
        symbol = "BTCUSD"
            
    symbol_norm = normalize_symbol(symbol)
    
    # Resolve Timeframe
    raw_timeframe = payload.get("timeframe") or payload.get("interval")
    if raw_timeframe:
        timeframe_str = str(raw_timeframe).strip()
        if timeframe_str.isdigit():
            timeframe_str = f"{timeframe_str}m"
    else:
        timeframe_str = "5m"
        
    # Resolve Trade Type (INTRADAY or POSITIONAL)
    trade_type_val = resolve_trade_type(payload, timeframe_str)
    
    # 3. Resolve Price
    price = payload.get("price") or payload.get("signal_price")
    price_val = None
    
    if price is not None:
        try:
            price_val = float(price)
        except (ValueError, TypeError):
            pass
            
    is_json = False
    if body_str:
        try:
            json.loads(body_str)
            is_json = True
        except Exception:
            pass
            
    if price_val is None and not is_json and body_str:
        # Search for a decimal/float number in the plain text body
        import re
        numbers = re.findall(r"\b\d+(?:\.\d+)?\b", body_str)
        if numbers:
            try:
                price_val = float(numbers[0])
            except ValueError:
                pass
                
    # If still no price found or resolves to 0.0, fetch from live TradingView price feed
    if price_val is None or price_val == 0.0:
        live_price = get_live_market_price(symbol_norm)
        if live_price is not None:
            price_val = live_price
        else:
            price_val = 0.0
        
    # 4. Resolve Action
    raw_action = payload.get("action")
    raw_key = payload.get("key")
    raw_dir = payload.get("direction")
    
    action_norm = "EXIT" # Default fallback
    
    if raw_action:
        act_lower = str(raw_action).lower()
        if act_lower in ["exit_long", "exitlong"]:
            action_norm = "EXIT_LONG"
        elif act_lower in ["exit_short", "exitshort", "cover"]:
            action_norm = "EXIT_SHORT"
        elif act_lower in ["exit", "close"]:
            action_norm = "EXIT"
        elif act_lower in ["buy", "long"]:
            action_norm = "LONG"
        elif act_lower in ["sell", "short"]:
            action_norm = "SHORT" if act_lower == "short" else "SELL"
        elif act_lower == "entry":
            dir_str = str(raw_dir or "").upper()
            if "LONG" in dir_str or "BUY" in dir_str:
                action_norm = "LONG"
            elif "SHORT" in dir_str or "SELL" in dir_str:
                action_norm = "SHORT"
    elif raw_key:
        key_str = str(raw_key).upper()
        if "EXIT_LONG" in key_str or "SELLALERT" in key_str or "SELL_ALERT" in key_str:
            action_norm = "EXIT_LONG"
        elif "EXIT_SHORT" in key_str or "COVER" in key_str:
            action_norm = "EXIT_SHORT"
        elif "EXIT" in key_str or "CLOSE" in key_str:
            action_norm = "EXIT"
        elif "LONG" in key_str or "BUY" in key_str:
            action_norm = "LONG"
        elif "SHORT" in key_str or "SELL" in key_str:
            action_norm = "SHORT"
    elif raw_dir:
        dir_str = str(raw_dir).upper()
        if dir_str in ["LONG", "BUY"]:
            action_norm = "LONG"
        elif dir_str in ["SHORT", "SELL"]:
            action_norm = "SHORT"
    elif body_str:
        search_text = body_str
        if isinstance(payload, dict):
            if "text" in payload:
                search_text = str(payload["text"])
            elif "message" in payload:
                search_text = str(payload["message"])
                
        if search_text:
            text_upper = search_text.upper()
            import re
            
            # 1. Precise Word Boundary Checks
            if re.search(r"\bEXIT_LONG\b", text_upper) or re.search(r"\bSELLALERT\b", text_upper) or re.search(r"\bSELL_ALERT\b", text_upper) or re.search(r"\bEXIT\s+LONG\b", text_upper) or re.search(r"\bSELL\s+ALERT\b", text_upper):
                action_norm = "EXIT_LONG"
            elif re.search(r"\bEXIT_SHORT\b", text_upper) or re.search(r"\bCOVERALERT\b", text_upper) or re.search(r"\bCOVER_ALERT\b", text_upper) or re.search(r"\bEXIT\s+SHORT\b", text_upper) or re.search(r"\bCOVER\s+ALERT\b", text_upper):
                action_norm = "EXIT_SHORT"
            elif re.search(r"\bEXIT\b", text_upper) or re.search(r"\bCLOSE\b", text_upper):
                action_norm = "EXIT"
            elif re.search(r"\bCOVER\b", text_upper):
                action_norm = "EXIT_SHORT"
            elif re.search(r"\bLONG\b", text_upper) or re.search(r"\bBUY\b", text_upper):
                action_norm = "LONG"
            elif re.search(r"\bSHORT\b", text_upper) or re.search(r"\bSELL\b", text_upper):
                action_norm = "SHORT"
                
            # 2. Fallback to Substring Checks
            else:
                if "EXIT_LONG" in text_upper or "SELLALERT" in text_upper or "SELL_ALERT" in text_upper:
                    action_norm = "EXIT_LONG"
                elif "EXIT_SHORT" in text_upper or "COVER" in text_upper:
                    action_norm = "EXIT_SHORT"
                elif "BUY" in text_upper or "LONG" in text_upper:
                    if "BUY" in text_upper and "OPTIONBUYING" in text_upper and not "BUY " in text_upper and not " BUY" in text_upper:
                        pass
                    else:
                        action_norm = "LONG"
                elif "SELL" in text_upper or "SHORT" in text_upper:
                    action_norm = "SHORT"
                elif "EXIT" in text_upper:
                    action_norm = "EXIT"
            
            
    # Resolve source
    source = payload.get("source") or ("TradingView" if (raw_key or "tradingview" in body_str.lower()) else "Scanner")
    source_name = payload.get("orderId") or payload.get("signal_type") or "Webhook Strategy Alert"

    # Check if daily consent is signed for today (based on IST date)
    ist_now = get_ist_time()

    # 15-second Deduplication check
    fifteen_secs_ago = ist_now - timedelta(seconds=15)
    duplicate = db.query(Signal).filter(
        Signal.symbol == symbol_norm,
        Signal.action == action_norm,
        Signal.source_name == source_name,
        Signal.timestamp >= fifteen_secs_ago
    ).first()
    if duplicate:
        print(f"[Webhook Skipped] Duplicate signal detected for {symbol_norm} with action {action_norm} from {source_name} within last 15 seconds")
        return {"status": "skipped", "reason": "duplicate signal received"}

    today_str = ist_now.date().isoformat()
    consent = db.query(DailyConsent).filter(DailyConsent.date == today_str).first()
    consent_signed = consent is not None and consent.consent_given
    
    # Process Paper Trade
    # Normalize underlying symbol for options checking
    underlying_norm = symbol_norm
    if "BANKNIFTY" in underlying_norm or "BNF" in underlying_norm:
        underlying_norm = "BANKNIFTY"
    elif "NIFTY" in underlying_norm:
        underlying_norm = "NIFTY"
    elif "SENSEX" in underlying_norm or "BSX" in underlying_norm:
        underlying_norm = "SENSEX"
        
    # Check if this is a futures alert using the raw symbol and context
    raw_sym = str(symbol or "").upper().strip()
    raw_id = str(payload.get("orderId") or "").upper()
    raw_type = str(payload.get("signal_type") or "").upper()
    body_upper = str(body_str or "").upper()
    
    is_crypto_or_commodity = (
        raw_sym in ["GOLD", "CRUDEOIL", "BTCUSD", "ETHUSD", "SOLUSD"] or 
        "BTC" in raw_sym or 
        "ETH" in raw_sym or 
        "SOL" in raw_sym
    )
    is_futures_alert = (
        "1!" in raw_sym or 
        "FUT" in raw_sym or 
        "FUTURES" in raw_sym or
        "FUT" in raw_id or
        "FUTURES" in raw_id or
        "FUT" in raw_type or
        "FUTURES" in raw_type or
        "FUT" in body_upper or
        "FUTURES" in body_upper or
        is_crypto_or_commodity
    )
        
    # Calculate Option details (At-The-Money CE/PE option contract)
    has_options = underlying_norm in ["NIFTY", "BANKNIFTY", "SENSEX"]
    opt_strike = None
    opt_symbol = None
    opt_premium = None
    opt_type = None
    
    # Check if the incoming signal is directly on an Option contract
    parse_res = parse_option_symbol(symbol_norm)
    is_option_signal = parse_res.get("is_option", False)
    
    if is_option_signal:
        opt_symbol = parse_res["formatted_symbol"]
        opt_strike = parse_res["strike"]
        opt_type = parse_res["opt_type"]
        expiry_date = parse_res.get("expiry_date")
        
        # If the price in payload is at the scale of an option premium
        if price_val > 0 and price_val < 0.2 * opt_strike:
            opt_premium = price_val
        else:
            # Price is index price or 0. Fetch index price and calculate BS premium
            index_p = price_val if price_val > 0 else (get_live_market_price(underlying_norm) or opt_strike)
            opt_premium = calculate_option_price_bs(underlying_norm, opt_strike, opt_type, index_p, expiry_date=expiry_date)
            price_val = index_p
    elif has_options and price_val > 0:
        step = 50 if underlying_norm == "NIFTY" else 100
        opt_strike = int(round(price_val / step) * step)
        
        if action_norm in ["BUY", "LONG"]:
            opt_type = "CE"
        elif action_norm in ["SELL", "SHORT"]:
            opt_type = "PE"
                
        if opt_type:
            # Determine expiry date
            if underlying_norm == "BANKNIFTY":
                expiry_date = get_next_monthly_expiry(ist_now, weekday=1)
            elif underlying_norm == "NIFTY":
                expiry_date = get_next_weekly_expiry(ist_now, 1)
            elif underlying_norm == "SENSEX":
                expiry_date = get_next_weekly_expiry(ist_now, 3)
            else:
                expiry_date = get_next_weekly_expiry(ist_now, 1)
                
            opt_premium = calculate_option_price_bs(underlying_norm, opt_strike, opt_type, price_val, expiry_date=expiry_date)
            opt_symbol = f"{underlying_norm} {expiry_date.strftime('%d%b%y').upper()} {opt_strike} {opt_type}"
            
    # Check for open positions on this symbol or its options
    # Save signal in database in IST
    signal_entry = Signal(
        symbol=symbol_norm,
        action=action_norm,
        price=price_val,
        source=source,
        source_name=source_name,
        raw_payload=json.dumps(payload),
        timestamp=ist_now,
        timeframe=timeframe_str,
        trade_type=trade_type_val
    )
    db.add(signal_entry)
    db.commit()
    
    trade_log = []
    
    # 5. Process execution for all users
    today_str = ist_now.date().isoformat()
    users = db.query(User).all()
    processed_users_count = 0
    
    for user in users:
        mgr = AppCredentialsManager(db, user_id=user.id)
        active_broker = mgr.get_active_broker()
        mode_val = resolve_webhook_execution_mode(
            os.environ.get("WEBHOOK_AUTO_EXECUTION_MODE", "PAPER"),
            active_broker
        )
        
        # Daily consent is only required for LIVE mode users
        if mode_val == "LIVE":
            consent = db.query(DailyConsent).filter(
                DailyConsent.date == today_str,
                DailyConsent.user_id == user.id,
                DailyConsent.consent_given == True
            ).first()
            if not consent:
                print(f"[Webhook Skipped] User ID: {user.id} ({user.name}) is in LIVE mode but has not signed daily consent for today ({today_str})")
                continue
                
        print(f"[Webhook Processing] User ID: {user.id}, Name: {user.name}, Mode: {mode_val}")
            
        if is_symbol_muted(symbol_norm, user.muted_symbols):
            print(f"[Webhook Skipped] Symbol {symbol_norm} is muted for User {user.id} ({user.name})")
            continue
            
        # Check for open positions on this symbol or its options for this user
        # Scoped to same trade_type so INTRADAY and POSITIONAL coexist independently
        user_open_positions = db.query(Position).filter(
            Position.user_id == user.id,
            ((Position.symbol == symbol_norm) | 
             (Position.symbol == underlying_norm) |
             (Position.symbol.like(f"{symbol_norm} %")) |
             (Position.symbol.like(f"{underlying_norm} %"))),
            Position.status == "OPEN",
            Position.trade_type == trade_type_val
        ).all()
        
        open_pos_future = next((p for p in user_open_positions if p.symbol == symbol_norm), None)
        
        user_action = action_norm
        # Contextual Exit Action Labels (SELL & COVER instead of EXIT)
        if user_action == "EXIT" and open_pos_future:
            if open_pos_future.direction == "LONG":
                user_action = "SELL"
            else:
                user_action = "COVER"
                
        # Execution Logic for this user
        if user_action in ["BUY", "LONG"]:
            is_explicit_long_entry = (
                raw_action is None or
                str(raw_action).lower() in ["long", "entry_long", "entry"] or
                "LONG" in str(raw_key).upper() or
                "LONG" in str(raw_dir).upper() or
                (str(raw_action).lower() == "buy" and not open_pos_future)
            )
            
            if user_open_positions:
                for p in user_open_positions:
                    safe_close_position_entry(p, price_val, db, trade_log, "User " + str(user.id) + ": Closed existing " + p.direction + " position on " + p.symbol + " (P&L: {pnl})")
                
                if not is_explicit_long_entry:
                    trade_log.append(f"User {user.id}: Covered SHORT position on {symbol_norm} (flat)")
                    
            if not user_open_positions or is_explicit_long_entry:
                if is_futures_alert:
                    # Open Future position
                    qty = calculate_trade_qty(symbol_norm)
                    safe_open_position_entry(symbol_norm, "LONG", price_val, qty, db, user.id, timeframe_str, mode_val, trade_type_val, trade_log, f"User {user.id}: Opened Future LONG position for {symbol_norm} (Qty: {qty}) in {mode_val} mode")
                else:
                    if is_option_signal:
                        qty = calculate_trade_qty(underlying_norm)
                        safe_open_position_entry(symbol_norm, "LONG", opt_premium, qty, db, user.id, timeframe_str, mode_val, trade_type_val, trade_log, f"User {user.id}: Opened Option LONG position for {symbol_norm} (Premium: {opt_premium:.2f}, Qty: {qty}) in {mode_val} mode")
                    else:
                        # Standard signal (e.g. NIFTY) -> Trade Option
                        if opt_symbol and opt_premium:
                            qty = calculate_trade_qty(underlying_norm)
                            safe_open_position_entry(opt_symbol, "LONG", opt_premium, qty, db, user.id, timeframe_str, mode_val, trade_type_val, trade_log, f"User {user.id}: Opened Option LONG position for {opt_symbol} (Premium: {opt_premium:.2f}, Qty: {qty}) in {mode_val} mode")
                        else:
                            # Fallback if options are not supported (e.g. stocks)
                            qty = calculate_trade_qty(symbol_norm)
                            safe_open_position_entry(symbol_norm, "LONG", price_val, qty, db, user.id, timeframe_str, mode_val, trade_type_val, trade_log, f"User {user.id}: Opened Future LONG position for {symbol_norm} (Qty: {qty}) in {mode_val} mode")
                    
        elif user_action in ["SELL", "SHORT"]:
            is_explicit_short_entry = (
                raw_action is None or
                str(raw_action).lower() in ["short", "entry_short", "entry"] or
                "SHORT" in str(raw_key).upper() or
                "SHORT" in str(raw_dir).upper() or
                (str(raw_action).lower() == "sell" and not open_pos_future)
            )
            
            if user_open_positions:
                for p in user_open_positions:
                    safe_close_position_entry(p, price_val, db, trade_log, "User " + str(user.id) + ": Closed existing " + p.direction + " position on " + p.symbol + " (P&L: {pnl})")
                
                if not is_explicit_short_entry:
                    trade_log.append(f"User {user.id}: Exited LONG position on {symbol_norm} (flat)")
                    
            if not user_open_positions or is_explicit_short_entry:
                if is_futures_alert:
                    # Open Future position
                    qty = calculate_trade_qty(symbol_norm)
                    safe_open_position_entry(symbol_norm, "SHORT", price_val, qty, db, user.id, timeframe_str, mode_val, trade_type_val, trade_log, f"User {user.id}: Opened Future SHORT position for {symbol_norm} (Qty: {qty}) in {mode_val} mode")
                else:
                    if is_option_signal:
                        qty = calculate_trade_qty(underlying_norm)
                        safe_open_position_entry(symbol_norm, "LONG", opt_premium, qty, db, user.id, timeframe_str, mode_val, trade_type_val, trade_log, f"User {user.id}: Opened Option LONG position for {symbol_norm} (Premium: {opt_premium:.2f}, Qty: {qty}) in {mode_val} mode")
                    else:
                        # Standard signal (e.g. NIFTY) -> Trade Option (PE is bought, direction is LONG)
                        if opt_symbol and opt_premium:
                            qty = calculate_trade_qty(underlying_norm)
                            safe_open_position_entry(opt_symbol, "LONG", opt_premium, qty, db, user.id, timeframe_str, mode_val, trade_type_val, trade_log, f"User {user.id}: Opened Option LONG position for {opt_symbol} (Premium: {opt_premium:.2f}, Qty: {qty}) in {mode_val} mode")
                        else:
                            # Fallback if options are not supported (e.g. stocks)
                            qty = calculate_trade_qty(symbol_norm)
                            safe_open_position_entry(symbol_norm, "SHORT", price_val, qty, db, user.id, timeframe_str, mode_val, trade_type_val, trade_log, f"User {user.id}: Opened Future SHORT position for {symbol_norm} (Qty: {qty}) in {mode_val} mode")
                    
        elif user_action in ["EXIT", "EXIT_LONG", "EXIT_SHORT", "CLOSE", "COVER"]:
            if user_open_positions:
                for p in user_open_positions:
                    # Direction-aware exit check to prevent exit-ordering race conditions
                    should_close = False
                    if user_action in ["EXIT", "CLOSE"]:
                        should_close = True
                    elif user_action == "EXIT_LONG" and p.direction == "LONG":
                        should_close = True
                    elif user_action in ["EXIT_SHORT", "COVER"] and p.direction == "SHORT":
                        should_close = True
                        
                    if should_close:
                        safe_close_position_entry(p, price_val, db, trade_log, "User " + str(user.id) + ": Exited " + p.direction + " position on " + p.symbol + " at {exit_price} (P&L: {pnl})")
            else:
                trade_log.append(f"User {user.id}: Received exit signal for {symbol_norm} but no open position existed")
        processed_users_count += 1
                
    db.commit()
    
    # Broadcast signal and trades to all WS clients
    ws_data = {
        "event": "new_signal",
        "signal": {
            "id": signal_entry.id,
            "timestamp": signal_entry.timestamp.isoformat(),
            "symbol": signal_entry.symbol,
            "action": signal_entry.action,
            "price": signal_entry.price,
            "source": signal_entry.source,
            "source_name": signal_entry.source_name,
            "timeframe": signal_entry.timeframe,
            "trade_type": signal_entry.trade_type or "INTRADAY"
        },
        "consent_signed": True,
        "logs": trade_log
    }
    await manager.broadcast(json.dumps(ws_data))
    
    return {"status": "success", "processed_signals": processed_users_count, "actions": trade_log, "consent_signed": True}


@app.get("/api/signals")
def get_signals(limit: int = Query(50, ge=1, le=100), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    signals = db.query(Signal).order_by(Signal.timestamp.desc()).limit(limit).all()
    filtered_signals = [s for s in signals if not is_symbol_muted(s.symbol, user.muted_symbols)]
    return [{
        "id": s.id,
        "timestamp": s.timestamp.isoformat(),
        "symbol": s.symbol,
        "action": s.action,
        "price": s.price,
        "source": s.source,
        "source_name": s.source_name,
        "timeframe": s.timeframe,
        "trade_type": s.trade_type or "INTRADAY"
    } for s in filtered_signals]

def get_tradingview_price(symbol: str) -> Optional[float]:
    s = symbol.upper().strip()
    
    # 1. Map to TradingView ticker and scanner market
    tv_ticker = None
    market = "global"
    
    if s in ["NIFTY", "NIFTY1!", "NIFTY50", "NIFTY 50", "NSE:NIFTY", "NSE:NIFTY1!"]:
        tv_ticker = "NSE:NIFTY1!"
        market = "futures"
    elif s in ["BANKNIFTY", "BANKNIFTY1!", "NIFTYBANK", "NSE:BANKNIFTY", "NSE:BANKNIFTY1!"]:
        tv_ticker = "NSE:BANKNIFTY1!"
        market = "futures"
    elif s in ["SENSEX", "BSX1!", "BSE:BSX1!", "BSE:SENSEX"]:
        tv_ticker = "BSE:BSX1!"
        market = "futures"
    elif s in ["BTCUSD", "BTC", "BTC-USD", "BINANCE:BTCUSD"]:
        tv_ticker = "BINANCE:BTCUSD"
        market = "crypto"
    elif s in ["GOLDM1!", "GOLD", "GOLDM", "MCX:GOLDM1!"]:
        tv_ticker = "MCX:GOLDM1!"
        market = "futures"
    elif s in ["CRUDE", "CRUDEOIL", "MCX:CRUDEOIL1!"]:
        tv_ticker = "MCX:CRUDEOIL1!"
        market = "futures"
        
    if not tv_ticker:
        # Fallback mappings
        if "BTC" in s:
            tv_ticker = "BINANCE:BTCUSD"
            market = "crypto"
        elif "NIFTY" in s:
            tv_ticker = "NSE:NIFTY1!"
            market = "futures"
        elif "BANK" in s:
            tv_ticker = "NSE:BANKNIFTY1!"
            market = "futures"
        elif "GOLD" in s:
            tv_ticker = "MCX:GOLDM1!"
            market = "futures"
        else:
            tv_ticker = f"NSE:{s}1!" if not ":" in s else s
            market = "global"

    # Send POST request to TradingView Scanner API
    url = f"https://scanner.tradingview.com/{market}/scan"
    payload = {
        "symbols": {
            "tickers": [tv_ticker],
            "query": {"types": []}
        },
        "columns": ["close"]
    }
    
    import urllib.request
    import json
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Content-Type": "application/json"
        },
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            data_list = res_data.get("data", [])
            if data_list:
                return float(data_list[0]["d"][0])
    except Exception as e:
        print(f"Error fetching TradingView price for {tv_ticker} on {market}: {e}")
    return None

def map_symbol_to_google_ticker(symbol: str) -> Optional[str]:
    s_upper = symbol.upper().strip()
    if s_upper in ["NIFTY", "NIFTY1!", "NIFTY50", "NIFTY 50", "NSE:NIFTY", "NSE:NIFTY1!"]:
        return "NIFTY_50:INDEXNSE"
    elif s_upper in ["BANKNIFTY", "BANKNIFTY1!", "NIFTYBANK", "NSE:BANKNIFTY", "NSE:BANKNIFTY1!"]:
        return "NIFTY_BANK:INDEXNSE"
    elif s_upper in ["SENSEX", "BSX1!", "BSE:BSX1!", "BSE:SENSEX"]:
        return "SENSEX:INDEXBOM"
    elif s_upper in ["BTCUSD", "BTC", "BTC-USD"]:
        return "BTC-USD"
    return None

def map_symbol_to_yahoo_ticker(symbol: str) -> str:
    s_upper = symbol.upper().strip()
    if s_upper in ["NIFTY", "NIFTY1!", "NIFTY50", "NIFTY 50", "NSE:NIFTY", "NSE:NIFTY1!"]:
        return "^NSEI"
    elif s_upper in ["BANKNIFTY", "BANKNIFTY1!", "NIFTYBANK", "NSE:BANKNIFTY", "NSE:BANKNIFTY1!"]:
        return "^NSEBANK"
    elif s_upper in ["SENSEX", "BSX1!", "BSE:BSX1!", "BSE:SENSEX"]:
        return "^BSESN"
    elif s_upper in ["BTCUSD", "BTC", "BTC-USD"]:
        return "BTC-USD"
    elif "GOLD" in s_upper:
        return "GOLDBEES.NS"
    elif "SILVER" in s_upper:
        return "SILVERBEES.NS"
    elif "CRUDE" in s_upper:
        return "CL=F"
    
    # Fallback to other cryptos
    if s_upper.endswith("USD"):
        return f"{s_upper[:-3]}-USD"
    if s_upper.endswith("USDT"):
        return f"{s_upper[:-4]}-USD"
        
    return f"{s_upper}.NS"

def get_google_finance_price(ticker: str) -> Optional[float]:
    import urllib.request
    import re
    url = f"https://www.google.com/finance/quote/{ticker}"
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })
        with urllib.request.urlopen(req, timeout=5) as response:
            html = response.read().decode('utf-8')
            match = re.search(r'<div class="gO24Ff">([^<]+)</div>.*?jsname="Pdsbrc"[^>]*><span>([^<]+)</span>', html, re.DOTALL)
            if match:
                price_str = match.group(2).replace(',', '').replace('$', '').replace('₹', '').strip()
                return float(price_str)
    except Exception as e:
        print(f"Error scraping Google Finance for {ticker}: {e}")
    return None

def get_yahoo_finance_price_data(ticker: str) -> dict:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    try:
        import urllib.request
        import json
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read())
            result = data['chart']['result'][0]
            price = result['meta']['regularMarketPrice']
            prev_close = result['meta'].get('previousClose')
            return {"price": float(price), "previous_close": float(prev_close) if prev_close else None}
    except Exception as e:
        print(f"Error fetching live price from Yahoo Finance for {ticker}: {e}")
    return {}

PRICE_CACHE = {}
PRICE_CACHE_TTL = 15 # seconds

def get_live_market_price_data(symbol: str) -> dict:
    s_upper = symbol.upper().strip()
    
    # Check cache
    now = datetime.utcnow()
    if s_upper in PRICE_CACHE:
        cached_val, cached_time = PRICE_CACHE[s_upper]
        if (now - cached_time).total_seconds() < PRICE_CACHE_TTL:
            return cached_val
            
    # Check if known test/mock symbol to prevent long timeouts
    if "TEST" in s_upper or "MOCK" in s_upper or s_upper in ["DUMMY", "XYZ"]:
        res = {}
    else:
        res = _fetch_live_price_no_cache(s_upper)
        
    PRICE_CACHE[s_upper] = (res, now)
    return res

def _fetch_live_price_no_cache(symbol: str) -> dict:
    # 1. Try TradingView first (Primary Source)
    price = get_tradingview_price(symbol)
    if price is not None:
        return {"price": price, "previous_close": None, "source": "TradingView"}
        
    # 2. Try Google Finance (Backup Source)
    g_ticker = map_symbol_to_google_ticker(symbol)
    if g_ticker:
        price = get_google_finance_price(g_ticker)
        if price is not None:
            return {"price": price, "previous_close": None, "source": "GoogleFinance"}
            
    # 3. Fallback to Yahoo Finance
    y_ticker = map_symbol_to_yahoo_ticker(symbol)
    y_data = get_yahoo_finance_price_data(y_ticker)
    if y_data:
        return {"price": y_data["price"], "previous_close": y_data.get("previous_close"), "source": "YahooFinance"}
        
    return {}

def get_live_market_price(symbol: str) -> Optional[float]:
    res = get_live_market_price_data(symbol)
    return res.get("price")

def get_current_price(symbol: str, entry_price: float, db: Session) -> float:
    s_upper = symbol.upper().strip()
    
    # 1. Detect if the symbol is an Option contract
    parse_res = parse_option_symbol(symbol)
    
    if parse_res.get("is_option"):
        underlying = parse_res["underlying"]
        opt_type = parse_res["opt_type"]
        strike = float(parse_res["strike"])
        expiry_date = parse_res.get("expiry_date")
        
        # Get live underlying index price
        live_underlying = get_live_market_price(underlying)
        if live_underlying is None:
            # Fallback to latest signal price in database
            latest_signal = db.query(Signal).filter(Signal.symbol == underlying).order_by(Signal.timestamp.desc()).first()
            live_underlying = latest_signal.price if latest_signal else strike
            
        current_premium = calculate_option_price_bs(underlying, strike, opt_type, live_underlying, expiry_date=expiry_date)
        return round(max(1.0, current_premium), 2)
        
    # 2. Standard future/equity/crypto price resolution
    price_data = get_live_market_price_data(symbol)
    
    if price_data:
        live_price = price_data["price"]
        source = price_data.get("source")
        
        if source == "TradingView":
            return round(live_price, 2)
            
        prev_close = price_data.get("previous_close")
        is_commodity_proxy = s_upper in ["GOLDM1!", "GOLD", "CRUDEOIL", "CRUDE", "SILVER"]
        
        if is_commodity_proxy and prev_close and prev_close > 0:
            # Scale entry price by the proxy ETF/future daily percent change
            pct_change = (live_price - prev_close) / prev_close
            scaled_price = entry_price * (1.0 + pct_change)
            return round(scaled_price, 2)
        else:
            return round(live_price, 2)
            
    # Fallback to latest signal price in database
    latest_signal = db.query(Signal).filter(Signal.symbol == symbol).order_by(Signal.timestamp.desc()).first()
    price = latest_signal.price if latest_signal else entry_price
    
    # Fallback simulated tick fluctuation
    import random
    fluctuation = random.uniform(-0.0015, 0.0015)
    simulated_price = price * (1 + fluctuation)
    return round(simulated_price, 2)

def is_usd_asset(symbol: str) -> bool:
    s = symbol.upper().strip()
    return "USD" in s or "USDT" in s or s in ["BTC", "ETH", "SOL", "ADA", "XRP"]


def reconcile_pending_aliceblue_positions(user_id: int, db: Session) -> None:
    pending = db.query(Position).filter(
        Position.user_id == user_id,
        Position.broker_id == "aliceblue",
        Position.status.in_(["PENDING", "PARTIAL", "EXIT_PENDING", "EXIT_PARTIAL"]),
    ).all()
    changed = False
    for position in pending:
        broker_order_id = (
            position.entry_broker_order_id
            if position.status in {"PENDING", "PARTIAL"}
            else position.exit_broker_order_id
        )
        if not broker_order_id:
            continue
        try:
            history = get_aliceblue_order_history(user_id, db, broker_order_id)
        except AliceBlueError:
            continue

        broker_status = history["status"]
        order_row = db.query(BrokerOrder).filter(
            BrokerOrder.user_id == user_id,
            BrokerOrder.broker_order_id == broker_order_id,
        ).first()
        if order_row:
            order_row.status = broker_status
            order_row.broker_response = json.dumps(history.get("raw", {}))
            order_row.updated_at = get_ist_time()

        filled_quantity = history["filled_quantity"]
        average_price = history["average_price"]
        if position.status in {"PENDING", "PARTIAL"}:
            position.entry_order_status = broker_status
            position.entry_filled_qty = filled_quantity
            if filled_quantity > 0:
                position.qty = filled_quantity
                if average_price > 0:
                    position.entry_price = average_price
            if broker_status in {"COMPLETE", "COMPLETED", "FILLED"}:
                position.status = "OPEN"
            elif broker_status in {"REJECTED", "CANCELLED", "CANCELED"}:
                position.status = "OPEN" if filled_quantity > 0 else "REJECTED"
            elif filled_quantity > 0:
                position.status = "PARTIAL"
        else:
            position.exit_order_status = broker_status
            position.exit_filled_qty = filled_quantity
            if broker_status in {"COMPLETE", "COMPLETED", "FILLED"}:
                position.status = "CLOSED"
                position.exit_time = get_ist_time()
                if average_price > 0:
                    position.exit_price = average_price
                if position.exit_price is not None:
                    if position.direction == "LONG":
                        position.pnl = round(position.pnl + (position.exit_price - position.entry_price) * position.qty, 2)
                    else:
                        position.pnl = round(position.pnl + (position.entry_price - position.exit_price) * position.qty, 2)
            elif broker_status in {"REJECTED", "CANCELLED", "CANCELED"}:
                if filled_quantity > 0 and average_price > 0:
                    if position.direction == "LONG":
                        position.pnl = round(position.pnl + (average_price - position.entry_price) * filled_quantity, 2)
                    else:
                        position.pnl = round(position.pnl + (position.entry_price - average_price) * filled_quantity, 2)
                    position.qty = max(0, position.qty - filled_quantity)
                if position.qty == 0:
                    position.status = "CLOSED"
                    position.exit_price = average_price or position.exit_price
                    position.exit_time = get_ist_time()
                else:
                    position.status = "OPEN"
            elif filled_quantity > 0:
                position.status = "EXIT_PARTIAL"
        changed = True
    if changed:
        db.commit()

@app.get("/api/paper-trades")
def get_paper_trades(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    reconcile_pending_aliceblue_positions(user.id, db)
    positions = db.query(Position).filter(Position.user_id == user.id).order_by(Position.entry_time.desc()).all()
    
    # Calculate stats
    closed_positions = [p for p in positions if p.status == "CLOSED"]
    active_statuses = {"OPEN", "PENDING", "PARTIAL", "EXIT_PENDING", "EXIT_PARTIAL"}
    open_positions = [p for p in positions if p.status in active_statuses]
    
    # Calculate open positions details & accumulate PnL separately
    positions_data = []
    total_pnl_inr = 0.0
    total_pnl_usd = 0.0
    
    # Process open positions
    for p in positions:
        if p.status in active_statuses:
            current_price = p.entry_price if p.status == "PENDING" else get_current_price(p.symbol, p.entry_price, db)
            if p.status == "PENDING":
                pnl = 0.0
            elif p.direction == "LONG":
                pnl = p.pnl + (current_price - p.entry_price) * p.qty
            else:
                pnl = p.pnl + (p.entry_price - current_price) * p.qty
            
            if is_usd_asset(p.symbol):
                total_pnl_usd += pnl
            else:
                total_pnl_inr += pnl
                
            positions_data.append({
                "id": p.id,
                "symbol": p.symbol,
                "direction": p.direction,
                "qty": p.qty,
                "entry_price": p.entry_price,
                "entry_time": p.entry_time.isoformat(),
                "exit_price": None,
                "exit_time": None,
                "status": p.status,
                "current_price": current_price,
                "pnl": round(pnl, 2),
                "real_or_paper": p.real_or_paper,
                "signal_id": p.signal_id,
                "timeframe": p.timeframe,
                "trade_type": p.trade_type or "INTRADAY",
                "broker_id": p.broker_id,
                "broker_order_id": p.entry_broker_order_id,
                "order_status": p.exit_order_status if p.status in {"EXIT_PENDING", "EXIT_PARTIAL"} else p.entry_order_status,
            })
        else:
            pnl = p.pnl
            if is_usd_asset(p.symbol):
                total_pnl_usd += pnl
            else:
                total_pnl_inr += pnl
                
            positions_data.append({
                "id": p.id,
                "symbol": p.symbol,
                "direction": p.direction,
                "qty": p.qty,
                "entry_price": p.entry_price,
                "entry_time": p.entry_time.isoformat(),
                "exit_price": p.exit_price,
                "exit_time": p.exit_time.isoformat() if p.exit_time else None,
                "status": p.status,
                "pnl": p.pnl,
                "real_or_paper": p.real_or_paper,
                "signal_id": p.signal_id,
                "timeframe": p.timeframe,
                "trade_type": p.trade_type or "INTRADAY",
                "broker_id": p.broker_id,
                "broker_order_id": p.entry_broker_order_id,
                "order_status": p.exit_order_status or p.entry_order_status,
            })
            
    total_trades = len(closed_positions)
    winning_trades = sum(1 for p in closed_positions if p.pnl > 0)
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
    
    return {
        "positions": positions_data,
        "stats": {
            "total_pnl": round(total_pnl_inr, 2), # for backward compatibility
            "total_pnl_inr": round(total_pnl_inr, 2),
            "total_pnl_usd": round(total_pnl_usd, 2),
            "total_trades": total_trades,
            "win_rate": win_rate,
            "open_count": len(open_positions)
        }
    }

@app.post("/api/paper-trades/manual-exit/{pos_id}")
def manual_exit_position(pos_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    pos = db.query(Position).filter(Position.id == pos_id, Position.user_id == user.id, Position.status == "OPEN").first()
    if not pos:
        raise HTTPException(status_code=404, detail="Open position not found")
    
    # Resolve index exit price
    parts = pos.symbol.split()
    is_option = len(parts) >= 3 and parts[-1] in ["CE", "PE"]
    underlying = parts[0] if is_option else pos.symbol
    
    index_exit_price = get_live_market_price(underlying)
    if index_exit_price is None:
        latest_signal = db.query(Signal).filter(Signal.symbol == underlying).order_by(Signal.timestamp.desc()).first()
        index_exit_price = latest_signal.price if latest_signal else pos.entry_price
        
    close_position_entry(pos, index_exit_price, db)
    db.commit()
    return {"status": "success", "pnl": pos.pnl}

@app.get("/api/consent")
def check_consent(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ist_now = get_ist_time()
    today_str = ist_now.date().isoformat()
    consent = db.query(DailyConsent).filter(DailyConsent.date == today_str, DailyConsent.user_id == user.id).first()
    return {
        "consent_signed": consent is not None and consent.consent_given,
        "date": today_str,
        "timestamp": consent.timestamp.isoformat() if consent else None
    }

@app.post("/api/consent")
def sign_consent(req: ConsentRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ist_now = get_ist_time()
    today_str = ist_now.date().isoformat()
    consent = db.query(DailyConsent).filter(DailyConsent.date == today_str, DailyConsent.user_id == user.id).first()
    if consent:
        consent.consent_given = True
        consent.timestamp = ist_now
    else:
        consent = DailyConsent(
            date=today_str,
            agreement_text_version=req.agreement_version,
            consent_given=True,
            timestamp=ist_now,
            user_id=user.id
        )
        db.add(consent)
    db.commit()
    return {"status": "success", "signed": True, "date": today_str}

@app.get("/api/credentials")
def get_credentials(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    mgr = AppCredentialsManager(db, user_id=user.id)
    active_broker = mgr.get_active_broker()
    broker_list = []
    for broker in INDIAN_BROKERS:
        has = mgr.has_credentials(broker['id'])
        masked = mgr.get_masked_info(broker['id']) if has else None
        broker_list.append({
            "id": broker["id"],
            "name": broker["name"],
            "api_name": broker["api_name"],
            "fields": broker["fields"],
            "connection_type": broker.get("connection_type", "credentials"),
            "available": is_aliceblue_vendor_configured() if broker["id"] == "aliceblue" else True,
            "configured": has,
            "active": active_broker == broker["id"],
            "info": masked
        })
    
    crypto_list = []
    for crypto in CRYPTO_EXCHANGES:
        has = mgr.has_credentials(crypto['id'])
        masked = mgr.get_masked_info(crypto['id']) if has else None
        crypto_list.append({
            "id": crypto["id"],
            "name": crypto["name"],
            "api_name": crypto["api_name"],
            "fields": crypto["fields"],
            "configured": has,
            "info": masked
        })
        
    return {
        "brokers": broker_list,
        "crypto": crypto_list
    }

@app.post("/api/credentials")
def save_credentials(req: CredentialRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    supported = {broker["id"] for broker in INDIAN_BROKERS + CRYPTO_EXCHANGES}
    if req.broker_id not in supported:
        raise HTTPException(status_code=400, detail="Unsupported broker")
    if req.broker_id == "aliceblue":
        raise HTTPException(
            status_code=400,
            detail="Alice Blue must be connected through the secure broker login flow",
        )
    mgr = AppCredentialsManager(db, user_id=user.id)
    success = mgr.save_credentials(
        broker_id=req.broker_id,
        api_key=req.api_key,
        api_secret=req.api_secret,
        extra=req.extra
    )
    if success:
        return {"status": "success", "broker_id": req.broker_id}
    raise HTTPException(status_code=500, detail="Error saving credentials")

@app.delete("/api/credentials/{broker_id}")
def delete_credentials(broker_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    mgr = AppCredentialsManager(db, user_id=user.id)
    if mgr.delete_credentials(broker_id):
        return {"status": "success", "broker_id": broker_id}
    raise HTTPException(status_code=404, detail="Credentials not found")


@app.post("/api/broker/aliceblue/login-url")
def aliceblue_login_url(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not is_aliceblue_vendor_configured():
        raise HTTPException(status_code=503, detail="Alice Blue Vendor API is not configured yet")
    if not os.environ.get("CREDENTIAL_ENCRYPTION_KEY", "").strip():
        raise HTTPException(status_code=503, detail="Broker credential encryption is not configured")

    try:
        nonce = secrets.token_urlsafe(24)
        state = create_signed_token(
            {"purpose": "aliceblue_sso", "user_id": user.id, "nonce": nonce},
            600,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    db.add(BrokerAuthState(
        nonce=nonce,
        user_id=user.id,
        broker_id="aliceblue",
        expires_at=get_ist_time() + timedelta(minutes=10),
        created_at=get_ist_time(),
    ))
    db.commit()

    public_base = os.environ.get("PUBLIC_BACKEND_URL", "").rstrip("/")
    if public_base:
        start_url = f"{public_base}/api/broker/aliceblue/start?state={quote(state)}"
    else:
        start_url = f"{request.url_for('aliceblue_login_start')}?state={quote(state)}"
    return {"login_url": start_url}


@app.get("/api/broker/aliceblue/start")
def aliceblue_login_start(state: str, db: Session = Depends(get_db)):
    try:
        payload = verify_signed_token(state)
    except (SignedTokenError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload.get("purpose") != "aliceblue_sso":
        raise HTTPException(status_code=400, detail="Invalid broker login state")
    auth_state = db.query(BrokerAuthState).filter(
        BrokerAuthState.nonce == payload.get("nonce"),
        BrokerAuthState.user_id == payload.get("user_id"),
        BrokerAuthState.broker_id == "aliceblue",
        BrokerAuthState.used_at.is_(None),
    ).first()
    if not auth_state or auth_state.expires_at < get_ist_time():
        raise HTTPException(status_code=400, detail="Broker login state has expired")

    try:
        login_url = get_vendor_login_url()
    except AliceBlueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    response = RedirectResponse(url=login_url, status_code=302)
    response.set_cookie(
        "aliceblue_auth_state",
        state,
        max_age=600,
        httponly=True,
        secure=not env_flag("ALLOW_SANDBOX_AUTH"),
        samesite="lax",
        path="/api/broker/aliceblue",
    )
    return response


@app.get("/api/broker/aliceblue/callback")
def aliceblue_callback(request: Request, db: Session = Depends(get_db)):
    auth_code = request.query_params.get("authCode") or request.query_params.get("auth_code")
    alice_user_id = request.query_params.get("userId") or request.query_params.get("user_id")
    state = request.cookies.get("aliceblue_auth_state")
    if not auth_code or not alice_user_id or not state:
        return HTMLResponse(
            content="<h2>Alice Blue login could not be linked. Please return to the app and try again.</h2>",
            status_code=400,
        )

    try:
        state_payload = verify_signed_token(state)
        if state_payload.get("purpose") != "aliceblue_sso":
            raise SignedTokenError("Invalid broker login state")
        user = db.query(User).filter(User.id == int(state_payload["user_id"])).first()
        if not user:
            raise SignedTokenError("Application user was not found")
        auth_state = db.query(BrokerAuthState).filter(
            BrokerAuthState.nonce == state_payload.get("nonce"),
            BrokerAuthState.user_id == user.id,
            BrokerAuthState.broker_id == "aliceblue",
            BrokerAuthState.used_at.is_(None),
        ).first()
        if not auth_state or auth_state.expires_at < get_ist_time():
            raise SignedTokenError("Broker login state has expired or was already used")
        auth_state.used_at = get_ist_time()
        db.commit()
        session = exchange_vendor_session(alice_user_id, auth_code)
        AppCredentialsManager(db, user_id=user.id).save_credentials(
            "aliceblue",
            str(alice_user_id),
            session["user_session"],
            extra={
                "client_id": session["client_id"],
                "authorized_at": get_ist_time().isoformat(),
                "connection_type": "vendor_sso",
            },
        )
    except (AliceBlueError, SignedTokenError, RuntimeError, KeyError, ValueError) as exc:
        response = HTMLResponse(
            content=f"<h2>Alice Blue login failed</h2><p>{html.escape(str(exc))}</p>",
            status_code=400,
        )
        response.delete_cookie("aliceblue_auth_state", path="/api/broker/aliceblue")
        return response

    response = HTMLResponse(content="""
    <html><head><title>Alice Blue Connected</title><meta name="viewport" content="width=device-width,initial-scale=1">
    <style>body{font-family:Arial,sans-serif;background:#0a0e17;color:#fff;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}.panel{max-width:420px;padding:28px;border:1px solid #273244;border-radius:8px;text-align:center}h1{color:#10b981;font-size:24px}p{color:#aab3c2;line-height:1.5}</style></head>
    <body><div class="panel"><h1>Alice Blue connected</h1><p>Your trading account is linked. You can close this page and return to the app.</p></div></body></html>
    """)
    response.delete_cookie("aliceblue_auth_state", path="/api/broker/aliceblue")
    return response

@app.get("/api/broker/callback")
def broker_callback(code: str, client: str = Query(None), db: Session = Depends(get_db)):
    from backend.database import BrokerCredential
    from backend.credentials import deobfuscate
    import json
    
    creds_list = db.query(BrokerCredential).filter(BrokerCredential.broker_id == "flattrade").all()
    target_user_id = None
    
    if client:
        for c in creds_list:
            try:
                extra = json.loads(deobfuscate(c.extra_fields)) if c.extra_fields else {}
                if extra.get("client_id") == client:
                    target_user_id = c.user_id
                    break
            except Exception:
                continue
                
    if not target_user_id:
        target_user_id = creds_list[0].user_id if creds_list else 1
        
    mgr = AppCredentialsManager(db, user_id=target_user_id)
    creds = mgr.load_credentials("flattrade")
    if not creds:
        return HTMLResponse(content="<h2>Error: Flattrade credentials are not configured in the app yet. Please configure them first under settings.</h2>", status_code=400)
    
    api_key = creds.get("api_key")
    api_secret = creds.get("api_secret")
    
    try:
        token = exchange_request_code(api_key, api_secret, code)
    except Exception as e:
        return HTMLResponse(
            content=f"<h2>Error: Failed to exchange request code for session token. Please verify your static IP and credentials.</h2><p style='color: #ef4444; font-family: monospace; font-size: 14px; background: rgba(239, 68, 68, 0.1); padding: 10px; border-radius: 6px;'>Details: {str(e)}</p>",
            status_code=400
        )
        
    today_str = get_ist_time().date().isoformat()
    extra_fields = creds.get("extra", {}) or {}
    extra_fields["token"] = token
    extra_fields["token_date"] = today_str
    
    mgr.save_credentials("flattrade", api_key, api_secret, extra=extra_fields)
    
    html_content = """
    <html>
        <head>
            <title>Auth Success</title>
            <style>
                body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background-color: #111827; color: #f3f4f6; display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; margin: 0; }
                .card { background-color: #1f2937; padding: 2.5rem; border-radius: 12px; border: 1px solid #374151; text-align: center; max-width: 480px; box-shadow: 0 10px 15px -3px rgba(0,0,0,0.3); }
                h1 { color: #10b981; margin-top: 0; }
                p { color: #9ca3af; line-height: 1.5; font-size: 1.1rem; }
            </style>
        </head>
        <body>
            <div class="card">
                <h1>Login Successful!</h1>
                <p>Your Flattrade Pi API daily session has been successfully authorized and linked to SKI Analytics.</p>
                <p>You can close this tab and return to the app.</p>
            </div>
        </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.get("/api/broker/login/{broker_id}")
def broker_login_redirect(broker_id: str, token: str = Query(None), db: Session = Depends(get_db)):
    user = None
    if token:
        user_info = get_user_from_token(token)
        if user_info:
            supabase_uid = user_info.get("id")
            user = db.query(User).filter(User.supabase_uid == supabase_uid).first()
            
    if not user and env_flag("ALLOW_SANDBOX_AUTH"):
        user = db.query(User).filter(User.id == 1).first()
        
    if not user:
        raise HTTPException(status_code=401, detail="User not authenticated")
        
    if broker_id != "flattrade":
        raise HTTPException(status_code=400, detail="Only Flattrade is supported for login redirection")
        
    mgr = AppCredentialsManager(db, user_id=user.id)
    creds = mgr.load_credentials("flattrade")
    if not creds:
        raise HTTPException(status_code=400, detail="Flattrade credentials not configured. Please save them first.")
        
    api_key = creds.get("api_key")
    if not api_key:
        raise HTTPException(status_code=400, detail="API Key not found in Flattrade credentials")
        
    auth_url = f"https://auth.flattrade.in/?app_key={api_key}"
    return RedirectResponse(url=auth_url)

@app.get("/api/user")
def get_user_profile(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    trial_days_left = 5
    if user.trial_end:
        delta = user.trial_end - get_ist_time()
        trial_days_left = max(0, delta.days)
    return {
        "name": user.name,
        "email": user.email,
        "phone": user.phone,
        "subscription_status": user.subscription_status,
        "trial_days_left": trial_days_left
    }

class OrderPreviewRequest(BaseModel):
    signal_id: int
    trade_type: str  # FUTURE or OPTION
    mode: str        # LIVE or PAPER
    lots: float


class ExecuteOrderRequest(OrderPreviewRequest):
    preview_token: Optional[str] = None
    idempotency_key: Optional[str] = None

def get_lot_size(symbol: str) -> int:
    sym = symbol.upper()
    if "BANKNIFTY" in sym:
        return 30
    elif "NIFTY" in sym:
        return 65
    elif "SENSEX" in sym or "BSX" in sym:
        return 20
    elif "CRUDE" in sym:
        return 100
    elif "GOLD" in sym:
        return 100
    elif "WIPRO" in sym:
        return 1500
    elif "RELIANCE" in sym:
        return 250
    elif "TITAN" in sym:
        return 375
    elif "BAJFINSERV" in sym:
        return 500
    elif "ADANIPORTS" in sym:
        return 625
    else:
        return 100


def resolve_manual_trade(signal: Signal, trade_type: str, lots: float) -> dict:
    trade_type = trade_type.upper()
    if trade_type not in {"FUTURE", "OPTION"}:
        raise HTTPException(status_code=400, detail="Trade type must be FUTURE or OPTION")
    if lots <= 0 or int(lots) != lots or lots > 100:
        raise HTTPException(status_code=400, detail="Lots must be a whole number between 1 and 100")

    sym_upper = signal.symbol.upper()
    is_crypto = any(value in sym_upper for value in ["BTC", "ETH", "SOL", "USD", "USDT"])
    qty = lots if is_crypto else lots * get_lot_size(signal.symbol)

    if trade_type == "OPTION":
        step = 50 if "NIFTY" in sym_upper and "BANKNIFTY" not in sym_upper else 100
        underlying_price = get_live_market_price(signal.symbol) or signal.price
        opt_strike = int(round(underlying_price / step) * step)
        opt_type = "CE" if signal.action in ["LONG", "BUY"] else "PE"
        ist_now = get_ist_time()
        if "BANKNIFTY" in sym_upper:
            expiry_date = get_next_monthly_expiry(ist_now, weekday=1)
        elif "NIFTY" in sym_upper:
            expiry_date = get_next_weekly_expiry(ist_now, 1)
        elif "SENSEX" in sym_upper:
            expiry_date = get_next_weekly_expiry(ist_now, 3)
        else:
            expiry_date = get_next_weekly_expiry(ist_now, 1)
        trade_symbol = f"{signal.symbol} {expiry_date.strftime('%d%b%y').upper()} {opt_strike} {opt_type}"
        entry_price = calculate_option_price_bs(
            signal.symbol, opt_strike, opt_type, underlying_price, expiry_date=expiry_date
        )
        direction = "LONG"
    else:
        trade_symbol = signal.symbol
        entry_price = get_live_market_price(signal.symbol) or signal.price
        direction = "LONG" if signal.action in ["LONG", "BUY"] else "SHORT"

    return {
        "trade_symbol": trade_symbol,
        "entry_price": float(entry_price),
        "direction": direction,
        "qty": float(qty),
        "lots": int(lots),
        "contract_type": trade_type,
    }


BROKER_FUNDS_CACHE = {}


def get_cached_broker_balance(user_id: int, broker_id: str, db: Session) -> Optional[float]:
    cache_key = (user_id, broker_id)
    now = datetime.utcnow()
    cached = BROKER_FUNDS_CACHE.get(cache_key)
    if cached and (now - cached[1]).total_seconds() < 30:
        return cached[0]
    balance = None
    if broker_id == "aliceblue":
        try:
            balance = get_aliceblue_funds(user_id, db)
        except AliceBlueError:
            balance = None
    BROKER_FUNDS_CACHE[cache_key] = (balance, now)
    return balance

@app.get("/api/broker/status")
def get_broker_status(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    mgr = AppCredentialsManager(db, user_id=user.id)
    active_id = mgr.get_active_broker()
    
    # Calculate open positions combined live open P&L
    positions = db.query(Position).filter(
        Position.status.in_(["OPEN", "PENDING", "PARTIAL", "EXIT_PENDING", "EXIT_PARTIAL"]),
        Position.user_id == user.id,
    ).all()
    combined_pnl = 0.0
    for p in positions:
        if p.status == "PENDING":
            continue
        current_price = get_current_price(p.symbol, p.entry_price, db)
        if p.direction == "LONG":
            pnl = p.pnl + (current_price - p.entry_price) * p.qty
        else:
            pnl = p.pnl + (p.entry_price - current_price) * p.qty
        combined_pnl += pnl
            
    if active_id:
        masked = mgr.get_masked_info(active_id)
        live_enabled = env_flag("LIVE_TRADING_ENABLED") and active_id == "aliceblue"
        balance = get_cached_broker_balance(user.id, active_id, db)
        return {
            "status": "linked",
            "broker_id": active_id,
            "broker_name": "Alice Blue" if active_id == "aliceblue" else masked["broker_id"].capitalize(),
            "balance": balance or 0.0,
            "balance_available": balance is not None,
            "mode": "LIVE" if live_enabled else "LINKED",
            "live_enabled": live_enabled,
            "combined_open_pnl": round(combined_pnl, 2)
        }
    else:
        return {
            "status": "sandbox",
            "broker_id": "sandbox",
            "broker_name": "Sandbox Broker",
            "balance": 1000000.00,  # 10 Lakh INR sandbox capital
            "mode": "SANDBOX",
            "live_enabled": False,
            "combined_open_pnl": round(combined_pnl, 2)
        }


def require_daily_live_consent(user_id: int, db: Session) -> None:
    today_str = get_ist_time().date().isoformat()
    consent = db.query(DailyConsent).filter(
        DailyConsent.date == today_str,
        DailyConsent.user_id == user_id,
        DailyConsent.consent_given == True,
    ).first()
    if not consent:
        raise HTTPException(
            status_code=403,
            detail="Daily trading consent must be signed before placing a live order",
        )


@app.post("/api/broker/order-preview")
def preview_broker_order(
    req: OrderPreviewRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if req.mode.upper() != "LIVE":
        raise HTTPException(status_code=400, detail="Order preview is only required for live orders")
    if not env_flag("LIVE_TRADING_ENABLED"):
        raise HTTPException(status_code=503, detail="Live trading is not enabled on the server")
    max_live_lots = max(1, int(os.environ.get("MAX_LIVE_LOTS_PER_ORDER", "10")))
    if req.lots > max_live_lots:
        raise HTTPException(status_code=400, detail=f"Live orders are limited to {max_live_lots} lots")
    require_daily_live_consent(user.id, db)

    manager = AppCredentialsManager(db, user_id=user.id)
    if manager.get_active_broker() != "aliceblue":
        raise HTTPException(status_code=400, detail="Connect Alice Blue before previewing a live order")
    signal = db.query(Signal).filter(Signal.id == req.signal_id).first()
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")
    if signal.action.upper() not in {"LONG", "SHORT", "BUY"}:
        raise HTTPException(status_code=400, detail="Only active entry signals can be traded")

    resolved = resolve_manual_trade(signal, req.trade_type, req.lots)
    try:
        prepared = prepare_aliceblue_order(
            symbol=resolved["trade_symbol"],
            direction=resolved["direction"],
            lots=req.lots,
            price=resolved["entry_price"],
            trade_type=signal.trade_type or "INTRADAY",
        )
        preview_token = create_signed_token(
            {
                "purpose": "live_order_preview",
                "user_id": user.id,
                "signal_id": signal.id,
                "trade_type": req.trade_type.upper(),
                "mode": "LIVE",
                "lots": int(req.lots),
                "prepared": prepared,
            },
            90,
        )
    except (AliceBlueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "status": "ready",
        "preview_token": preview_token,
        "broker_name": prepared["broker_name"],
        "symbol": prepared["trading_symbol"],
        "exchange": prepared["exchange"],
        "transaction_type": prepared["transaction_type"],
        "quantity": prepared["quantity"],
        "lots": prepared["lots"],
        "lot_size": prepared["lot_size"],
        "order_type": prepared["order_type"],
        "limit_price": prepared["limit_price"],
        "product": prepared["product"],
        "expires_in_seconds": 90,
    }

@app.post("/api/broker/execute")
def execute_broker_order(req: ExecuteOrderRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    mode = req.mode.upper()
    contract_type = req.trade_type.upper()
    if mode not in {"LIVE", "PAPER"}:
        raise HTTPException(status_code=400, detail="Mode must be LIVE or PAPER")

    signal = db.query(Signal).filter(Signal.id == req.signal_id).first()
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")
        
    # Block manual entry executions on exit signals (Sell, Cover, Exit)
    if signal.action.upper() in ["EXIT", "EXIT_LONG", "EXIT_SHORT", "CLOSE", "COVER", "SELL"]:
        raise HTTPException(
            status_code=400, 
            detail="This is an exit signal (Sell/Cover). Entries are only allowed on Long or Short signals."
        )

    preview_payload = None
    prepared_live_order = None
    active_broker = None
    if mode == "LIVE":
        if not env_flag("LIVE_TRADING_ENABLED"):
            raise HTTPException(status_code=503, detail="Live trading is not enabled on the server")
        require_daily_live_consent(user.id, db)
        mgr = AppCredentialsManager(db, user_id=user.id)
        active_broker = mgr.get_active_broker()
        if active_broker != "aliceblue":
            raise HTTPException(
                status_code=400,
                detail="Connect Alice Blue before executing a live order",
            )
        if not req.preview_token:
            raise HTTPException(status_code=400, detail="A fresh live-order preview is required")
        try:
            preview_payload = verify_signed_token(req.preview_token)
        except (SignedTokenError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        expected = {
            "purpose": "live_order_preview",
            "user_id": user.id,
            "signal_id": signal.id,
            "trade_type": contract_type,
            "mode": "LIVE",
            "lots": int(req.lots),
        }
        if any(preview_payload.get(key) != value for key, value in expected.items()):
            raise HTTPException(status_code=400, detail="Live-order preview no longer matches this order")
        prepared_live_order = preview_payload.get("prepared")
        if not isinstance(prepared_live_order, dict):
            raise HTTPException(status_code=400, detail="Live-order preview is incomplete")

    # Check if the signal is still active (no subsequent exit alert on the symbol matching direction)
    exit_actions = ["EXIT", "CLOSE"]
    if signal.action.upper() in ["LONG", "BUY"]:
        exit_actions.extend(["EXIT_LONG", "SELL"])
    elif signal.action.upper() in ["SHORT", "SELL"]:
        exit_actions.extend(["EXIT_SHORT", "COVER"])

    exit_exists = db.query(Signal).filter(
        Signal.symbol == signal.symbol,
        Signal.action.in_(exit_actions),
        Signal.timestamp > signal.timestamp
    ).first()
    if exit_exists:
        raise HTTPException(status_code=400, detail="This signal is no longer active (an exit signal has already been received)")
        
    # Check duplicate trade prevention per signal and contract type separately
    existing = None
    if contract_type == "OPTION":
        existing = db.query(Position).filter(
            Position.signal_id == req.signal_id,
            Position.user_id == user.id,
            Position.real_or_paper == mode,
            Position.status != "REJECTED",
            (Position.symbol.like("% CE") | Position.symbol.like("% PE") | Position.symbol.like("%CE%") | Position.symbol.like("%PE%"))
        ).first()
    else:
        # FUTURE
        existing = db.query(Position).filter(
            Position.signal_id == req.signal_id,
            Position.user_id == user.id,
            Position.real_or_paper == mode,
            Position.status != "REJECTED",
            ~Position.symbol.like("% CE"),
            ~Position.symbol.like("% PE"),
            ~Position.symbol.like("%CE%"),
            ~Position.symbol.like("%PE%")
        ).first()

    if existing:
        lots_used = existing.lot_size or 1
        raise HTTPException(
            status_code=400, 
            detail=f"A {contract_type} trade is already running on this signal with {lots_used} lots. You cannot place another {contract_type} trade on the same signal."
        )

    resolved = resolve_manual_trade(signal, contract_type, req.lots)
    trade_symbol = resolved["trade_symbol"]
    direction = resolved["direction"]
    qty = resolved["qty"]
    entry_price = resolved["entry_price"]
    broker_order = None
    broker_result = None

    if mode == "LIVE":
        if prepared_live_order.get("symbol") != trade_symbol:
            raise HTTPException(status_code=400, detail="The selected contract changed; preview the order again")
        qty = int(prepared_live_order["quantity"])
        entry_price = float(prepared_live_order["limit_price"])
        idempotency_key = (req.idempotency_key or preview_payload.get("nonce") or "")[:120]
        if not idempotency_key:
            raise HTTPException(status_code=400, detail="Order idempotency key is missing")

        broker_order = BrokerOrder(
            user_id=user.id,
            signal_id=signal.id,
            broker_id="aliceblue",
            idempotency_key=idempotency_key,
            order_kind="ENTRY",
            symbol=trade_symbol,
            broker_trading_symbol=prepared_live_order.get("trading_symbol"),
            broker_instrument_id=prepared_live_order.get("instrument_id"),
            transaction_type=prepared_live_order.get("transaction_type"),
            quantity=qty,
            limit_price=entry_price,
            status="PENDING",
            created_at=get_ist_time(),
            updated_at=get_ist_time(),
        )
        db.add(broker_order)
        try:
            db.commit()
            db.refresh(broker_order)
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail="This live order request was already submitted. Refresh positions before trying again.",
            ) from exc

        try:
            broker_result = place_aliceblue_order(
                user.id,
                db,
                prepared_live_order,
                order_tag=f"GDD-{signal.id}-{contract_type}",
            )
        except AliceBlueOrderStatusUnknown as exc:
            broker_order.status = "UNKNOWN"
            broker_order.broker_response = json.dumps({"error": str(exc)})
            broker_order.updated_at = get_ist_time()
            db.commit()
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except AliceBlueError as exc:
            broker_order.status = "REJECTED"
            broker_order.broker_response = json.dumps({"error": str(exc)})
            broker_order.updated_at = get_ist_time()
            db.commit()
            raise HTTPException(status_code=400, detail=f"Alice Blue order failed: {exc}") from exc

        broker_order.status = "SUBMITTED"
        broker_order.broker_order_id = broker_result["broker_order_id"]
        broker_order.broker_response = json.dumps(broker_result.get("broker_response", {}))
        broker_order.updated_at = get_ist_time()
        db.commit()

    # Create the position
    new_pos = Position(
        user_id=user.id,
        symbol=trade_symbol,
        direction=direction,
        qty=qty,
        lot_size=int(req.lots),
        entry_price=round(entry_price, 2),
        entry_time=get_ist_time(),
        status="PENDING" if mode == "LIVE" else "OPEN",
        real_or_paper=mode,
        signal_id=req.signal_id,
        timeframe=signal.timeframe,
        trade_type=signal.trade_type,
        broker_id="aliceblue" if mode == "LIVE" else None,
        broker_instrument_id=prepared_live_order.get("instrument_id") if prepared_live_order else None,
        broker_trading_symbol=prepared_live_order.get("trading_symbol") if prepared_live_order else None,
        entry_broker_order_id=broker_result.get("broker_order_id") if broker_result else None,
        entry_order_status="SUBMITTED" if broker_result else None,
    )
    db.add(new_pos)
    db.commit()
    db.refresh(new_pos)
    if broker_order:
        broker_order.position_id = new_pos.id
        broker_order.updated_at = get_ist_time()
        db.commit()
    
    return {
        "status": "success",
        "symbol": trade_symbol,
        "qty": qty,
        "entry_price": entry_price,
        "mode": mode,
        "broker_order_id": broker_result.get("broker_order_id") if broker_result else None,
        "order_status": "SUBMITTED" if broker_result else "FILLED",
    }

@app.post("/api/broker/manual-exit/{pos_id}")
def manual_exit_broker_position(pos_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    pos = db.query(Position).filter(Position.id == pos_id, Position.user_id == user.id, Position.status == "OPEN").first()
    if not pos:
        raise HTTPException(status_code=404, detail="Open position not found")
        
    parts = pos.symbol.split()
    is_option = len(parts) >= 3 and parts[-1] in ["CE", "PE"]
    underlying = parts[0] if is_option else pos.symbol
    
    index_exit_price = get_live_market_price(underlying)
    if index_exit_price is None:
        latest_signal = db.query(Signal).filter(Signal.symbol == underlying).order_by(Signal.timestamp.desc()).first()
        index_exit_price = latest_signal.price if latest_signal else pos.entry_price
        
    try:
        close_position_entry(pos, index_exit_price, db)
        db.commit()
    except Exception as ex:
        raise HTTPException(status_code=400, detail=f"Broker Exit Failed: {str(ex)}")
    return {"status": "success", "pnl": pos.pnl}

@app.post("/api/admin/purge-test-data")
def purge_test_data(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    admin_emails = {
        value.strip().lower()
        for value in os.environ.get("ADMIN_EMAILS", "").split(",")
        if value.strip()
    }
    is_local_admin = env_flag("ALLOW_SANDBOX_AUTH") and user.id == 1
    if not is_local_admin and (not user.email or user.email.lower() not in admin_emails):
        raise HTTPException(status_code=403, detail="Administrator access required")
    try:
        num_positions = db.query(Position).filter(Position.user_id == user.id).delete()
        num_signals = db.query(Signal).delete()
        db.commit()
        return {
            "status": "success",
            "purged_positions": num_positions,
            "purged_signals": num_signals,
            "detail": "Successfully cleared all user positions and signals."
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error during purge: {e}")

@app.get("/api/admin/debug-info")
def get_debug_info(secret: str = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    env_secrets = os.environ.get("VALID_SECRETS")
    if not env_secrets:
        raise HTTPException(status_code=503, detail="Debug access is disabled")
    VALID_SECRETS = [s.strip() for s in env_secrets.split(",")]
        
    if not secret or secret not in VALID_SECRETS:
        raise HTTPException(status_code=401, detail="Unauthorized debug request")
        
    from sqlalchemy import inspect
    inspector = inspect(db.bind)
    
    tables_schema = {}
    for table_name in ["users", "signals", "positions", "daily_consents", "broker_credentials"]:
        columns = [{"name": col["name"], "type": str(col["type"])} for col in inspector.get_columns(table_name)]
        tables_schema[table_name] = columns
        
    db_type = "PostgreSQL" if "postgresql" in str(db.bind.url) else "SQLite"
    
    signals = db.query(Signal).order_by(Signal.timestamp.desc()).limit(20).all()
    positions = db.query(Position).order_by(Position.entry_time.desc()).limit(20).all()
    consents = db.query(DailyConsent).order_by(DailyConsent.timestamp.desc()).limit(20).all()
    
    from backend.database import BrokerCredential
    creds = db.query(BrokerCredential).all()
    masked_creds = [{
        "id": c.id,
        "user_id": c.user_id,
        "broker_id": c.broker_id,
        "api_key_len": len(c.api_key) if c.api_key else 0,
        "has_secret": bool(c.api_secret)
    } for c in creds]
    
    return {
        "db_type": db_type,
        "tables_schema": tables_schema,
        "counts": {
            "users": db.query(User).count(),
            "signals": db.query(Signal).count(),
            "positions": db.query(Position).count(),
            "daily_consents": db.query(DailyConsent).count(),
            "broker_credentials": len(creds)
        },
        "recent_signals": [{
            "id": s.id, "symbol": s.symbol, "action": s.action, "price": s.price, "timestamp": s.timestamp.isoformat()
        } for s in signals],
        "recent_positions": [{
            "id": p.id, "user_id": p.user_id, "symbol": p.symbol, "direction": p.direction, "status": p.status, "real_or_paper": p.real_or_paper, "pnl": p.pnl, "entry_time": p.entry_time.isoformat()
        } for p in positions],
        "recent_consents": [{
            "id": c.id, "user_id": c.user_id, "date": c.date, "consent_given": c.consent_given, "timestamp": c.timestamp.isoformat()
        } for c in consents],
        "credentials": masked_creds,
        "all_users": [{
            "id": u.id, "name": u.name, "email": u.email, "phone": u.phone, "supabase_uid": u.supabase_uid
        } for u in db.query(User).all()]
    }

from fastapi.staticfiles import StaticFiles
# Mount static files for the simulator at root
simulator_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "simulator")
if os.path.exists(simulator_dir):
    app.mount("/", StaticFiles(directory=simulator_dir, html=True), name="static")

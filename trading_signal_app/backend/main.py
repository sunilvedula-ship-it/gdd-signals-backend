import os
import json
import math
import hashlib
import secrets
from datetime import datetime, date, time, timedelta
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, status, Query, WebSocket, WebSocketDisconnect, Request, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session


from backend.database import init_db, get_db, Signal, Position, DailyConsent, User, AppAuthSession, BrokerOrder
from backend.credentials import AppCredentialsManager, INDIAN_BROKERS, CRYPTO_EXCHANGES
from backend.flattrade_client import place_flattrade_order, exchange_request_code
from fastapi.responses import HTMLResponse, RedirectResponse

# Initialize FastAPI App
app = FastAPI(title="GuruDevaDatta Trading App Backend", version="1.0.0")
INTRADAY_SQUARE_OFF_TIME = time(15, 15)

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

class TestLoginRequest(BaseModel):
    phone: str
    password: str

class PurgeRequest(BaseModel):
    segment: Optional[str] = "ALL"
    start_date: Optional[str] = None
    end_date: Optional[str] = None

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

    # Auto-insert daily consent for User 1 to make local webhook simulator work out-of-the-box
    ist_now = get_ist_time()
    today_str = ist_now.date().isoformat()
    if not db.query(DailyConsent).filter(DailyConsent.date == today_str, DailyConsent.user_id == 1).first():
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

    if not token:
        # Fallback to sandbox user (User ID 1) for local web simulator
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

    app_session_user = get_app_session_user(token, db)
    if app_session_user:
        return app_session_user

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

def env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

def normalize_phone_number(value: Optional[str]) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) == 10:
        digits = f"91{digits}"
    return f"+{digits}" if digits else ""

def configured_admin_phone_numbers() -> set:
    raw = os.environ.get("ADMIN_PHONE_NUMBERS", "+918919859974")
    return {
        normalize_phone_number(phone)
        for phone in raw.split(",")
        if normalize_phone_number(phone)
    }

def configured_test_login_phones() -> set:
    raw = os.environ.get("TEST_LOGIN_PHONES", "+919043055445")
    return {
        normalize_phone_number(phone)
        for phone in raw.split(",")
        if normalize_phone_number(phone)
    }

def app_auth_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def get_app_session_user(token: str, db: Session) -> Optional[User]:
    if not token or not token.startswith("appt_"):
        return None
    session_row = db.query(AppAuthSession).filter(
        AppAuthSession.token_hash == app_auth_token_hash(token),
        AppAuthSession.revoked_at.is_(None),
        AppAuthSession.expires_at > get_ist_time(),
    ).first()
    if not session_row:
        return None
    return db.query(User).filter(User.id == session_row.user_id).first()

def is_administrator(user: User) -> bool:
    admin_emails = {
        value.strip().lower()
        for value in os.environ.get("ADMIN_EMAILS", "").split(",")
        if value.strip()
    }
    is_local_admin = env_flag("ALLOW_SANDBOX_AUTH") and user.id == 1
    is_email_admin = bool(user.email and user.email.lower() in admin_emails)
    is_phone_admin = normalize_phone_number(user.phone) in configured_admin_phone_numbers()
    return is_local_admin or is_email_admin or is_phone_admin

@app.post("/api/auth/test-login")
def test_login(req: TestLoginRequest, db: Session = Depends(get_db)):
    if not env_flag("TEST_LOGIN_ENABLED", True):
        raise HTTPException(status_code=403, detail="Test login is not enabled")

    phone = normalize_phone_number(req.phone)
    expected_password = os.environ.get("TEST_LOGIN_PASSWORD", "123456")
    if phone not in configured_test_login_phones() or req.password != expected_password:
        raise HTTPException(status_code=401, detail="Invalid test login")

    user = db.query(User).filter(User.phone == phone).first()
    if not user:
        user = User(
            email=f"test-{phone.lstrip('+')}@local.gdd",
            phone=phone,
            name="Testing User",
            subscription_status="active",
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    token = f"appt_{secrets.token_urlsafe(48)}"
    db.add(AppAuthSession(
        user_id=user.id,
        token_hash=app_auth_token_hash(token),
        purpose="test_login",
        expires_at=get_ist_time() + timedelta(days=30),
        created_at=get_ist_time(),
    ))
    db.commit()

    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": 60 * 60 * 24 * 30,
        "user": {
            "id": user.id,
            "phone": user.phone,
            "name": user.name,
            "test_account": True,
        },
    }

def get_base_symbol(symbol: str) -> str:
    s = str(symbol or "").upper().strip()
    if not s:
        return ""
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

def safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

def safe_isoformat(value) -> Optional[str]:
    if not value:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)

MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12
}

def parse_option_symbol(symbol: str) -> dict:
    s = str(symbol or "").upper().strip()
    if not s:
        return {"is_option": False}
    if ":" in s:
        s = s.split(":")[-1]
    if s.startswith("OPTIDX_"):
        s = s[len("OPTIDX_"):]

    match_optidx = re.match(r'^([A-Z]+)_([0-9]{1,2}[A-Z]{3}[0-9]{4})_(CE|PE)_(\d+(?:\.\d+)?)$', s)
    if match_optidx:
        underlying = match_optidx.group(1)
        expiry_str = match_optidx.group(2)
        opt_type = match_optidx.group(3)
        strike = int(float(match_optidx.group(4)))
        try:
            expiry_date = datetime.strptime(expiry_str, "%d%b%Y").date()
        except Exception:
            expiry_date = None
        if underlying in ["BNF", "BANKNIFTY"]:
            underlying = "BANKNIFTY"
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
    s = str(symbol or "").upper().strip()
    if not s:
        return ""

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

def close_position_entry(pos: Position, index_exit_price: float, db: Session, reason: Optional[str] = None) -> float:
    parse_res = parse_option_symbol(pos.symbol)
    if reason:
        pos.exit_reason = reason

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

    if str(pos.real_or_paper or "PAPER").upper() == "LIVE":
        from backend.credentials import AppCredentialsManager
        mgr = AppCredentialsManager(db, user_id=pos.user_id)
        active_broker = mgr.get_active_broker()
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

    pos.exit_price = round(exit_price, 2)
    pos.exit_time = get_ist_time()
    pos.status = "CLOSED"

    entry_price = safe_float(pos.entry_price)
    qty = safe_float(pos.qty)
    if str(pos.direction or "").upper() == "LONG":
        pos.pnl = round((pos.exit_price - entry_price) * qty, 2)
    else:
        pos.pnl = round((entry_price - pos.exit_price) * qty, 2)

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

def safe_close_position_entry(pos: Position, index_exit_price: float, db: Session, trade_log: list, success_msg_template: str, reason: Optional[str] = "SIGNAL_EXIT"):
    try:
        pnl = close_position_entry(pos, index_exit_price, db, reason=reason)
        trade_log.append(success_msg_template.format(pnl=pnl, exit_price=pos.exit_price))
    except Exception as ex:
        trade_log.append(f"User {pos.user_id}: Failed to close {pos.direction} position on {pos.symbol} in {pos.real_or_paper} mode: {ex}")

def parse_float_value(value) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None

def extract_option_price_from_payload(payload: dict, action_norm: str) -> Optional[float]:
    exit_keys = [
        "exit_option_price", "exitOptionPrice", "option_exit_price",
        "optionExitPrice", "exit_premium", "exitPremium",
    ]
    entry_keys = [
        "entry_option_price", "entryOptionPrice", "option_entry_price",
        "optionEntryPrice", "entry_premium", "entryPremium",
    ]
    common_keys = ["option_price", "optionPrice", "option_ltp", "optionLtp", "premium", "ltp"]
    keys = exit_keys + common_keys if action_norm in {"EXIT", "EXIT_LONG", "EXIT_SHORT", "CLOSE", "COVER"} else entry_keys + common_keys

    for key in keys:
        if key in payload:
            parsed_price = parse_float_value(payload.get(key))
            if parsed_price is not None and parsed_price > 0:
                return parsed_price
    return None

def infer_target_exit_action(raw_direction, raw_action, raw_key, body_str: str) -> str:
    context = " ".join([
        str(raw_direction or ""),
        str(raw_action or ""),
        str(raw_key or ""),
        str(body_str or ""),
    ]).upper()
    if re.search(r"\b(LONG|CE)\b", context):
        return "EXIT_LONG"
    if re.search(r"\b(SHORT|PE)\b", context):
        return "EXIT_SHORT"
    return "EXIT"

def get_position_exit_input_price(pos: Position, fallback_price: float, payload_option_price: Optional[float]) -> float:
    if payload_option_price is not None and parse_option_symbol(pos.symbol).get("is_option"):
        return payload_option_price
    return fallback_price

def get_position_underlying(pos: Position) -> str:
    parsed = parse_option_symbol(pos.symbol)
    if parsed.get("is_option"):
        return parsed["underlying"]
    return normalize_symbol(pos.symbol)

def ensure_cutoff_exit_signal(pos: Position, db: Session, now: datetime) -> None:
    entry_signal = db.query(Signal).filter(Signal.id == pos.signal_id).first() if pos.signal_id else None
    signal_symbol = entry_signal.symbol if entry_signal else get_position_underlying(pos)
    day_start = datetime.combine(now.date(), time.min)
    existing = db.query(Signal).filter(
        Signal.symbol == signal_symbol,
        Signal.action == "EXIT",
        Signal.source_name == "SYSTEM_INTRADAY_CUTOFF",
        Signal.timestamp >= day_start,
    ).first()
    if existing:
        return

    db.add(Signal(
        symbol=signal_symbol,
        action="EXIT",
        price=pos.exit_price or pos.entry_price,
        source="SYSTEM",
        source_name="SYSTEM_INTRADAY_CUTOFF",
        raw_payload=json.dumps({"reason": "INTRADAY_CUTOFF", "position_id": pos.id}),
        timestamp=now,
        timeframe=pos.timeframe or "5m",
        trade_type="INTRADAY",
    ))

def square_off_expired_intraday_positions(db: Session, now: Optional[datetime] = None) -> dict:
    current = now or get_ist_time()
    candidates = db.query(Position).filter(Position.status.in_(["OPEN", "PARTIAL"])).all()
    positions = [
        pos for pos in candidates
        if str(pos.trade_type or "INTRADAY").upper() == "INTRADAY"
        and pos.entry_time
        and (
            pos.entry_time.date() < current.date()
            or (pos.entry_time.date() == current.date() and current.time() >= INTRADAY_SQUARE_OFF_TIME)
        )
    ]

    closed_ids = []
    pending_ids = []
    errors = []
    for pos in positions:
        if str(pos.real_or_paper or "PAPER").upper() == "LIVE" and not env_flag("LIVE_TRADING_ENABLED"):
            errors.append({"position_id": pos.id, "error": "Live trading is disabled"})
            continue

        underlying = get_position_underlying(pos)
        exit_price = get_live_market_price(underlying)
        if exit_price is None:
            latest_signal = db.query(Signal).filter(Signal.symbol == underlying).order_by(Signal.timestamp.desc()).first()
            exit_price = latest_signal.price if latest_signal else pos.entry_price

        try:
            close_position_entry(pos, float(exit_price), db, reason="INTRADAY_CUTOFF")
            ensure_cutoff_exit_signal(pos, db, current)
            if pos.status == "CLOSED":
                closed_ids.append(pos.id)
            else:
                pending_ids.append(pos.id)
        except Exception as exc:
            errors.append({"position_id": pos.id, "error": str(exc)})

    if closed_ids or pending_ids:
        db.commit()
    return {"closed_position_ids": closed_ids, "pending_position_ids": pending_ids, "errors": errors}

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
    raw_trade_type = payload.get("trade_type") or payload.get("type") or payload.get("style")
    if raw_trade_type and str(raw_trade_type).strip().upper() in ["INTRADAY", "POSITIONAL"]:
        trade_type_str = str(raw_trade_type).strip().upper()
        trade_type_val = trade_type_str
    else:
        # Infer from ALL available context: source_name, orderId, signal_type, source, and raw body
        # resolved source_name is computed later, so compute it inline here too
        _sn = str(payload.get("orderId") or payload.get("signal_type") or "")
        hint_fields = " ".join([
            _sn,
            str(payload.get("source") or ""),
            str(payload.get("name") or ""),
            str(payload.get("strategy") or ""),
            body_str
        ]).upper()
        t_lower = timeframe_str.lower()
        # POSITIONAL keyword in any field takes priority
        if "POSITIONAL" in hint_fields or any(x in t_lower for x in ["1d", "daily", "1w", "weekly", "positional"]):
            trade_type_val = "POSITIONAL"
        elif "INTRADAY" in hint_fields:
            trade_type_val = "INTRADAY"
        else:
            trade_type_val = "INTRADAY"

    print(f"[Webhook] trade_type resolved to '{trade_type_val}' | raw_trade_type={raw_trade_type} | orderId={payload.get('orderId')} | signal_type={payload.get('signal_type')} | timeframe={timeframe_str}")

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
    is_target_hit = False

    if raw_action:
        act_lower = str(raw_action).lower().strip()
        is_target_hit = ("target" in act_lower and "hit" in act_lower) or act_lower in ["tp", "take profit", "target"]
        if is_target_hit:
            action_norm = infer_target_exit_action(raw_dir, raw_action, raw_key, body_str)
        elif act_lower in ["exit_long", "exitlong"]:
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


    payload_option_price = extract_option_price_from_payload(payload, action_norm)

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

        if payload_option_price is not None:
            opt_premium = payload_option_price
        # If the price in payload is at the scale of an option premium
        elif price_val > 0 and price_val < 0.2 * opt_strike:
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

            opt_symbol = f"{underlying_norm} {expiry_date.strftime('%d%b%y').upper()} {opt_strike} {opt_type}"
            opt_premium = payload_option_price if payload_option_price is not None else calculate_option_price_bs(underlying_norm, opt_strike, opt_type, price_val, expiry_date=expiry_date)

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
        auto_mode = os.environ.get("WEBHOOK_AUTO_EXECUTION_MODE", "PAPER").strip().upper()
        mode_val = "LIVE" if auto_mode == "LIVE" and active_broker and env_flag("LIVE_TRADING_ENABLED") else "PAPER"

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
                    exit_input_price = get_position_exit_input_price(p, price_val, payload_option_price)
                    safe_close_position_entry(
                        p,
                        exit_input_price,
                        db,
                        trade_log,
                        "User " + str(user.id) + ": Closed existing " + p.direction + " position on " + p.symbol + " (P&L: {pnl})",
                        reason="TARGET_HIT" if is_target_hit else "SIGNAL_EXIT",
                    )

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
                    exit_input_price = get_position_exit_input_price(p, price_val, payload_option_price)
                    safe_close_position_entry(
                        p,
                        exit_input_price,
                        db,
                        trade_log,
                        "User " + str(user.id) + ": Closed existing " + p.direction + " position on " + p.symbol + " (P&L: {pnl})",
                        reason="TARGET_HIT" if is_target_hit else "SIGNAL_EXIT",
                    )

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
                    parsed_position = parse_option_symbol(p.symbol)
                    option_type = parsed_position.get("opt_type") if parsed_position.get("is_option") else None
                    if user_action in ["EXIT", "CLOSE"]:
                        should_close = True
                    elif user_action == "EXIT_LONG" and p.direction == "LONG" and option_type != "PE":
                        should_close = True
                    elif user_action in ["EXIT_SHORT", "COVER"] and (p.direction == "SHORT" or option_type == "PE"):
                        should_close = True

                    if should_close:
                        exit_input_price = get_position_exit_input_price(p, price_val, payload_option_price)
                        safe_close_position_entry(
                            p,
                            exit_input_price,
                            db,
                            trade_log,
                            "User " + str(user.id) + ": Exited " + p.direction + " position on " + p.symbol + " at {exit_price} (P&L: {pnl})",
                            reason="TARGET_HIT" if is_target_hit else "SIGNAL_EXIT",
                        )
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
    square_off_expired_intraday_positions(db)
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

def build_tradingview_option_ticker(symbol: str) -> Optional[str]:
    parsed = parse_option_symbol(symbol)
    if not parsed.get("is_option") or not parsed.get("expiry_date"):
        return None
    opt_char = "C" if parsed["opt_type"] == "CE" else "P"
    expiry = parsed["expiry_date"].strftime("%y%m%d")
    underlying = parsed["underlying"]
    strike = int(parsed["strike"])
    return f"NSE:{underlying}{expiry}{opt_char}{strike}"

def pick_tradingview_price(values: list) -> Optional[float]:
    for index in [0, 1, 2]:
        try:
            price = values[index]
            if price is not None and float(price) > 0:
                return float(price)
        except (IndexError, TypeError, ValueError):
            pass
    try:
        bid = values[3]
        ask = values[4]
        if bid is not None and ask is not None and float(bid) > 0 and float(ask) > 0:
            return (float(bid) + float(ask)) / 2.0
    except (IndexError, TypeError, ValueError):
        pass
    return None

def get_tradingview_price(symbol: str) -> Optional[float]:
    s = symbol.upper().strip()

    # 1. Map to TradingView ticker and scanner market
    tv_ticker = None
    market = "global"
    parsed_option = parse_option_symbol(s)
    option_ticker = build_tradingview_option_ticker(s)
    if parsed_option.get("is_option") and not option_ticker:
        return None
    if option_ticker:
        tv_ticker = option_ticker
        market = "global"

    if not tv_ticker and s in ["NIFTY", "NIFTY1!", "NIFTY50", "NIFTY 50", "NSE:NIFTY", "NSE:NIFTY1!"]:
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
    elif s in ["GOLD1!", "GOLD", "GOLDM", "GOLDM1!", "MCX:GOLD1!", "MCX:GOLDM1!"]:
        tv_ticker = "MCX:GOLD1!"
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
            tv_ticker = "MCX:GOLD1!"
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
        "columns": ["close", "lp", "last", "bid", "ask"]
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
                return pick_tradingview_price(data_list[0].get("d", []))
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
        is_commodity_proxy = s_upper in ["GOLD1!", "GOLDM1!", "GOLD", "CRUDEOIL", "CRUDE", "SILVER"]

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
    s = str(symbol or "").upper().strip()
    if any(x in s for x in ["GOLD", "XAU", "CRUDE", "NIFTY", "SENSEX", "BSX"]):
        return False
    return any(x in s for x in ["BTC", "ETH", "SOL", "USDT"]) or s in ["ADA", "XRP"]

REPORT_CATEGORY_LABELS = {
    "INDEX_OPTIONS": "Index Options",
    "CRYPTO_FUTURES": "Crypto Futures",
    "MCX_GOLD": "MCX Gold",
    "MCX_CRUDEOIL": "MCX Crudeoil",
    "INDEX_FUTURES": "Index Futures",
    "OTHER_OPTIONS": "Other Options",
    "OTHER": "Other",
}

def get_report_category_code(symbol: str) -> str:
    s = (symbol or "").upper()
    parsed = parse_option_symbol(symbol)
    underlying = parsed.get("underlying") if parsed.get("is_option") else get_base_symbol(symbol)

    if parsed.get("is_option"):
        if underlying in ["BANKNIFTY", "NIFTY", "SENSEX"]:
            return "INDEX_OPTIONS"
        return "OTHER_OPTIONS"

    if is_usd_asset(symbol):
        return "CRYPTO_FUTURES"
    if underlying == "GOLD" or "GOLD" in s or "XAU" in s:
        return "MCX_GOLD"
    if underlying == "CRUDEOIL" or "CRUDE" in s:
        return "MCX_CRUDEOIL"
    if underlying in ["BANKNIFTY", "NIFTY", "SENSEX"]:
        return "INDEX_FUTURES"
    return "OTHER"

def get_report_category_label(symbol: str) -> str:
    return REPORT_CATEGORY_LABELS.get(get_report_category_code(symbol), "Other")

def get_strategy_label_for_position(position: Position, signal: Optional[Signal] = None) -> str:
    source_name = (signal.source_name or "").strip() if signal else ""
    if source_name and source_name.lower() != "webhook strategy alert":
        return source_name

    category = get_report_category_code(position.symbol)
    base = get_base_symbol(position.symbol)
    parsed = parse_option_symbol(position.symbol)
    if parsed.get("is_option"):
        base = parsed.get("underlying") or base

    friendly_base = {
        "BANKNIFTY": "BNF",
        "NIFTY": "Nifty",
        "SENSEX": "Sensex",
        "CRUDEOIL": "Crudeoil",
        "GOLD": "Gold",
    }.get(base, base or "Other")

    if category == "INDEX_OPTIONS":
        return f"{friendly_base} Option Buying"
    if category == "CRYPTO_FUTURES":
        return f"{friendly_base} Crypto Futures"
    if category == "MCX_GOLD":
        return "Gold"
    if category == "MCX_CRUDEOIL":
        return "Crudeoil"
    if category == "INDEX_FUTURES":
        return f"{friendly_base} Futures"
    return friendly_base or "Other Strategy"

def parse_optional_date(value: Optional[str], end_of_day: bool = False) -> Optional[datetime]:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        if "T" not in raw:
            parsed_date = date.fromisoformat(raw[:10])
            return datetime.combine(parsed_date, time.max if end_of_day else time.min)
        parsed_dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed_dt.tzinfo is not None:
            parsed_dt = parsed_dt.astimezone().replace(tzinfo=None)
        return parsed_dt
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date: {value}. Use YYYY-MM-DD.")

def position_activity_time(position: Position) -> datetime:
    return position.exit_time or position.entry_time or datetime.min

@app.get("/api/paper-trades")
def get_paper_trades(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        square_off_expired_intraday_positions(db)
    except Exception as exc:
        db.rollback()
        print(f"[Paper Trades] Intraday square-off skipped due to error: {exc}")

    positions = db.query(Position).filter(Position.user_id == user.id).order_by(Position.entry_time.desc()).all()
    signal_ids = [p.signal_id for p in positions if p.signal_id is not None]
    signals_by_id = {}
    if signal_ids:
        signals_by_id = {
            s.id: s
            for s in db.query(Signal).filter(Signal.id.in_(signal_ids)).all()
        }

    # Calculate stats
    closed_positions = [p for p in positions if str(p.status or "").upper() == "CLOSED"]
    open_positions = [p for p in positions if str(p.status or "").upper() in {"OPEN", "PARTIAL", "PENDING"}]

    # Calculate open positions details & accumulate PnL separately
    positions_data = []
    total_pnl_inr = 0.0
    total_pnl_usd = 0.0

    # Process positions. A single malformed legacy row should not blank the full ledger.
    row_errors = []
    for p in positions:
        try:
            qty = safe_float(p.qty)
            entry_price = safe_float(p.entry_price)
            symbol = str(p.symbol or "UNKNOWN").strip() or "UNKNOWN"
            status_value = str(p.status or "OPEN").upper()
            direction = str(p.direction or "LONG").upper()
            real_or_paper = str(p.real_or_paper or "PAPER").upper()
            trade_type = str(p.trade_type or "INTRADAY").upper()
            linked_signal = signals_by_id.get(p.signal_id)
            strategy_name = get_strategy_label_for_position(p, linked_signal)
            report_category_code = get_report_category_code(symbol)
            report_category = REPORT_CATEGORY_LABELS.get(report_category_code, "Other")
            source_name = linked_signal.source_name if linked_signal else None

            if status_value in {"OPEN", "PARTIAL", "PENDING"}:
                try:
                    current_price = get_current_price(symbol, entry_price, db)
                except Exception as exc:
                    print(f"[Paper Trades] Price refresh failed for position {p.id} {symbol}: {exc}")
                    current_price = entry_price
                if direction == "LONG":
                    pnl = (current_price - entry_price) * qty
                else:
                    pnl = (entry_price - current_price) * qty

                if is_usd_asset(symbol):
                    total_pnl_usd += pnl
                else:
                    total_pnl_inr += pnl

                positions_data.append({
                    "id": p.id,
                    "symbol": symbol,
                    "direction": direction,
                    "qty": qty,
                    "entry_price": entry_price,
                    "entry_time": safe_isoformat(p.entry_time),
                    "exit_price": None,
                    "exit_time": None,
                    "status": status_value,
                    "current_price": current_price,
                    "pnl": round(pnl, 2),
                    "real_or_paper": real_or_paper,
                    "signal_id": p.signal_id,
                    "timeframe": p.timeframe,
                    "trade_type": trade_type,
                    "strategy_name": strategy_name,
                    "source_name": source_name,
                    "report_category": report_category,
                    "report_category_code": report_category_code,
                    "exit_reason": p.exit_reason
                })
            else:
                pnl = safe_float(p.pnl)
                if is_usd_asset(symbol):
                    total_pnl_usd += pnl
                else:
                    total_pnl_inr += pnl

                positions_data.append({
                    "id": p.id,
                    "symbol": symbol,
                    "direction": direction,
                    "qty": qty,
                    "entry_price": entry_price,
                    "entry_time": safe_isoformat(p.entry_time),
                    "exit_price": safe_float(p.exit_price) if p.exit_price is not None else None,
                    "exit_time": safe_isoformat(p.exit_time),
                    "status": status_value,
                    "pnl": pnl,
                    "real_or_paper": real_or_paper,
                    "signal_id": p.signal_id,
                    "timeframe": p.timeframe,
                    "trade_type": trade_type,
                    "strategy_name": strategy_name,
                    "source_name": source_name,
                    "report_category": report_category,
                    "report_category_code": report_category_code,
                    "exit_reason": p.exit_reason
                })
        except Exception as exc:
            row_errors.append({"position_id": getattr(p, "id", None), "error": str(exc)})
            print(f"[Paper Trades] Skipping malformed position {getattr(p, 'id', None)}: {exc}")
            continue

    total_trades = len(closed_positions)
    winning_trades = sum(1 for p in closed_positions if safe_float(p.pnl) > 0)
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0

    response = {
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
    if row_errors:
        response["warnings"] = row_errors[:10]
    return response

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
    broker_list = []
    for broker in INDIAN_BROKERS:
        has = mgr.has_credentials(broker['id'])
        masked = mgr.get_masked_info(broker['id']) if has else None
        broker_list.append({
            "id": broker["id"],
            "name": broker["name"],
            "api_name": broker["api_name"],
            "fields": broker["fields"],
            "configured": has,
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

    if not user:
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

class ExecuteOrderRequest(BaseModel):
    signal_id: int
    trade_type: str  # FUTURE or OPTION
    mode: str        # LIVE or PAPER
    lots: float
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

@app.get("/api/broker/status")
def get_broker_status(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    mgr = AppCredentialsManager(db, user_id=user.id)
    active_id = mgr.get_active_broker()

    # Calculate open positions combined live open P&L
    positions = db.query(Position).filter(Position.status == "OPEN", Position.user_id == user.id).all()
    combined_pnl = 0.0
    for p in positions:
        current_price = get_current_price(p.symbol, p.entry_price, db)
        if p.direction == "LONG":
            pnl = (current_price - p.entry_price) * p.qty
        else:
            pnl = (p.entry_price - current_price) * p.qty
        combined_pnl += pnl

    if active_id:
        masked = mgr.get_masked_info(active_id)
        return {
            "status": "linked",
            "broker_id": active_id,
            "broker_name": masked["broker_id"].capitalize(),
            "balance": 245000.00,  # Simulated active margin balance
            "mode": "LIVE",
            "combined_open_pnl": round(combined_pnl, 2)
        }
    else:
        return {
            "status": "sandbox",
            "broker_id": "sandbox",
            "broker_name": "Sandbox Broker",
            "balance": 1000000.00,  # 10 Lakh INR sandbox capital
            "mode": "SANDBOX",
            "combined_open_pnl": round(combined_pnl, 2)
        }

def prepare_aliceblue_order(**kwargs) -> dict:
    raise HTTPException(status_code=501, detail="Alice Blue order preparation is not configured.")

def place_aliceblue_order(**kwargs) -> dict:
    return {"status": "error", "message": "Alice Blue live placement is not configured."}

def place_flattrade_prepared_order(**kwargs) -> dict:
    return {"status": "error", "message": "Flattrade live placement is not configured."}

def resolve_manual_order_details(signal: Signal, trade_type: str, lots: float) -> dict:
    sym_upper = signal.symbol.upper()
    is_crypto = "BTC" in sym_upper or "ETH" in sym_upper or "SOL" in sym_upper or "USD" in sym_upper or "USDT" in sym_upper
    qty = lots if is_crypto else lots * get_lot_size(signal.symbol)

    if trade_type == "OPTION":
        step = 50 if "NIFTY" in sym_upper else 100
        underlying_price = get_live_market_price(signal.symbol) or signal.price
        opt_strike = int(round(underlying_price / step) * step)
        opt_type = "CE" if signal.action in ["LONG", "BUY"] else "PE"
        ist_now = get_ist_time()
        if sym_upper == "BANKNIFTY":
            expiry_date = get_next_monthly_expiry(ist_now, weekday=1)
        elif sym_upper == "NIFTY":
            expiry_date = get_next_weekly_expiry(ist_now, 1)
        elif sym_upper == "SENSEX":
            expiry_date = get_next_weekly_expiry(ist_now, 3)
        else:
            expiry_date = get_next_weekly_expiry(ist_now, 1)
        trade_symbol = f"{signal.symbol} {expiry_date.strftime('%d%b%y').upper()} {opt_strike} {opt_type}"
        live_option_price = get_live_market_price(trade_symbol)
        entry_price = live_option_price if live_option_price is not None and live_option_price > 0 else calculate_option_price_bs(signal.symbol, opt_strike, opt_type, underlying_price, expiry_date=expiry_date)
        direction = "LONG"
    else:
        trade_symbol = signal.symbol
        entry_price = get_live_market_price(signal.symbol) or signal.price
        direction = "LONG" if signal.action in ["LONG", "BUY"] else "SHORT"

    return {
        "symbol": trade_symbol,
        "direction": direction,
        "qty": qty,
        "entry_price": round(float(entry_price), 2),
        "transaction_type": "BUY" if direction == "LONG" else "SELL",
    }

@app.post("/api/broker/order-preview")
def broker_order_preview(req: ExecuteOrderRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if req.mode.upper() != "LIVE":
        raise HTTPException(status_code=400, detail="Preview is only required for live orders.")
    if not env_flag("LIVE_TRADING_ENABLED"):
        raise HTTPException(status_code=503, detail="Live trading is disabled on the server.")

    signal = db.query(Signal).filter(Signal.id == req.signal_id).first()
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")

    mgr = AppCredentialsManager(db, user_id=user.id)
    active_broker = mgr.get_active_broker()
    if not active_broker:
        raise HTTPException(status_code=400, detail="No API Credentials configured.")

    details = resolve_manual_order_details(signal, req.trade_type, req.lots)
    if active_broker == "aliceblue":
        preview = prepare_aliceblue_order(
            user_id=user.id,
            signal=signal,
            details=details,
            lots=req.lots,
            trade_type=req.trade_type,
            db=db,
        )
    else:
        preview = {
            "broker_id": "flattrade",
            "broker_name": "Flattrade",
            "symbol": details["symbol"],
            "trading_symbol": details["symbol"],
            "instrument_id": None,
            "exchange": "NFO",
            "transaction_type": details["transaction_type"],
            "quantity": int(details["qty"]),
            "lot_size": get_lot_size(signal.symbol),
            "lots": req.lots,
            "product": "INTRADAY" if (signal.trade_type or "INTRADAY").upper() == "INTRADAY" else "CARRYFORWARD",
            "order_type": "LIMIT",
            "validity": "DAY",
            "limit_price": details["entry_price"],
        }
    preview["preview_token"] = f"preview_{secrets.token_urlsafe(24)}"
    return preview

@app.post("/api/broker/execute")
def execute_broker_order(req: ExecuteOrderRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    signal = db.query(Signal).filter(Signal.id == req.signal_id).first()
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")

    # Block manual entry executions on exit signals (Sell, Cover, Exit)
    if signal.action.upper() in ["EXIT", "EXIT_LONG", "EXIT_SHORT", "CLOSE", "COVER", "SELL"]:
        raise HTTPException(
            status_code=400,
            detail="This is an exit signal (Sell/Cover). Entries are only allowed on Long or Short signals."
        )

    mgr = None
    active_broker = None

    # Check if user has credentials linked when mode is LIVE
    if req.mode.upper() == "LIVE":
        if not env_flag("LIVE_TRADING_ENABLED"):
            raise HTTPException(
                status_code=503,
                detail="Live trading is disabled on the server."
            )
        mgr = AppCredentialsManager(db, user_id=user.id)
        active_broker = mgr.get_active_broker()
        if not active_broker:
            raise HTTPException(
                status_code=400,
                detail="No API Credentials configured. Please configure your broker credentials under the Auto-Trade tab before executing a live order."
            )

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
    if req.trade_type == "OPTION":
        existing = db.query(Position).filter(
            Position.signal_id == req.signal_id,
            Position.user_id == user.id,
            Position.real_or_paper == req.mode,
            (Position.symbol.like("% CE") | Position.symbol.like("% PE") | Position.symbol.like("%CE%") | Position.symbol.like("%PE%"))
        ).first()
    else:
        # FUTURE
        existing = db.query(Position).filter(
            Position.signal_id == req.signal_id,
            Position.user_id == user.id,
            Position.real_or_paper == req.mode,
            ~Position.symbol.like("% CE"),
            ~Position.symbol.like("% PE"),
            ~Position.symbol.like("%CE%"),
            ~Position.symbol.like("%PE%")
        ).first()

    if existing:
        lots_used = existing.lot_size or 1
        raise HTTPException(
            status_code=400,
            detail=f"A {req.trade_type} trade is already running on this signal with {lots_used} lots. You cannot place another {req.trade_type} trade on the same signal."
        )

    # Calculate Qty based on Lots
    sym_upper = signal.symbol.upper()
    is_crypto = "BTC" in sym_upper or "ETH" in sym_upper or "SOL" in sym_upper or "USD" in sym_upper or "USDT" in sym_upper

    if is_crypto:
        qty = req.lots
    else:
        qty = req.lots * get_lot_size(signal.symbol)

    # Strike and premium / entry price resolution
    if req.trade_type == "OPTION":
        step = 50 if "NIFTY" in sym_upper else 100
        underlying_price = get_live_market_price(signal.symbol) or signal.price
        opt_strike = int(round(underlying_price / step) * step)
        opt_type = "CE" if signal.action in ["LONG", "BUY"] else "PE"

        # Determine expiry date
        ist_now = get_ist_time()
        if sym_upper == "BANKNIFTY":
            expiry_date = get_next_monthly_expiry(ist_now, weekday=1)
        elif sym_upper == "NIFTY":
            expiry_date = get_next_weekly_expiry(ist_now, 1)
        elif sym_upper == "SENSEX":
            expiry_date = get_next_weekly_expiry(ist_now, 3)
        else:
            expiry_date = get_next_weekly_expiry(ist_now, 1)

        trade_symbol = f"{signal.symbol} {expiry_date.strftime('%d%b%y').upper()} {opt_strike} {opt_type}"

        live_option_price = get_live_market_price(trade_symbol)
        entry_price = live_option_price if live_option_price is not None and live_option_price > 0 else calculate_option_price_bs(signal.symbol, opt_strike, opt_type, underlying_price, expiry_date=expiry_date)
        direction = "LONG"
    else:
        trade_symbol = signal.symbol
        entry_price = get_live_market_price(signal.symbol) or signal.price
        direction = "LONG" if signal.action in ["LONG", "BUY"] else "SHORT"

    live_broker_result = None
    if req.mode.upper() == "LIVE":
        prepared_order = {
            "broker_id": active_broker,
            "symbol": trade_symbol,
            "trading_symbol": trade_symbol,
            "transaction_type": "BUY" if direction == "LONG" else "SELL",
            "quantity": int(qty),
            "limit_price": round(entry_price, 2),
            "trade_type": signal.trade_type or "INTRADAY",
        }
        if active_broker == "aliceblue":
            live_broker_result = place_aliceblue_order(
                user_id=user.id,
                prepared_order=prepared_order,
                db=db,
            )
        elif active_broker == "flattrade":
            live_broker_result = place_flattrade_prepared_order(
                user_id=user.id,
                prepared_order=prepared_order,
                db=db,
            )
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported broker: {active_broker}")

        if live_broker_result.get("status") == "error":
            raise HTTPException(status_code=400, detail=f"Broker API Order Failed: {live_broker_result.get('message')}")

    # Create the position
    broker_order_id = live_broker_result.get("broker_order_id") if live_broker_result else None
    new_pos = Position(
        user_id=user.id,
        symbol=trade_symbol,
        direction=direction,
        qty=qty,
        lot_size=int(req.lots),
        entry_price=round(entry_price, 2),
        entry_time=get_ist_time(),
        status="PENDING" if req.mode.upper() == "LIVE" else "OPEN",
        real_or_paper=req.mode,
        signal_id=req.signal_id,
        timeframe=signal.timeframe,
        trade_type=signal.trade_type,
        broker_id=active_broker,
        entry_broker_order_id=broker_order_id,
        entry_order_status="SUBMITTED" if req.mode.upper() == "LIVE" else "FILLED",
    )
    db.add(new_pos)
    db.flush()

    if req.mode.upper() == "LIVE":
        db.add(BrokerOrder(
            user_id=user.id,
            signal_id=req.signal_id,
            position_id=new_pos.id,
            broker_id=active_broker,
            idempotency_key=req.idempotency_key or f"{req.signal_id}-{req.trade_type}-{secrets.token_hex(8)}",
            order_kind="ENTRY",
            symbol=trade_symbol,
            broker_trading_symbol=trade_symbol,
            broker_instrument_id=None,
            transaction_type="BUY" if direction == "LONG" else "SELL",
            quantity=int(qty),
            limit_price=round(entry_price, 2),
            status="SUBMITTED",
            broker_order_id=broker_order_id,
            broker_response=json.dumps(live_broker_result.get("broker_response", live_broker_result)),
            updated_at=get_ist_time(),
        ))
    db.commit()

    return {
        "status": "success",
        "symbol": trade_symbol,
        "qty": qty,
        "entry_price": entry_price,
        "mode": req.mode,
        "order_status": "FILLED" if req.mode.upper() == "PAPER" else "SUBMITTED"
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
def purge_test_data(req: Optional[PurgeRequest] = Body(default=None), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        segment = ((req.segment if req else None) or "ALL").strip().upper()
        if segment not in {"ALL", *REPORT_CATEGORY_LABELS.keys()}:
            raise HTTPException(status_code=400, detail=f"Unsupported purge segment: {segment}")

        start_dt = parse_optional_date(req.start_date if req else None)
        end_dt = parse_optional_date(req.end_date if req else None, end_of_day=True)
        if start_dt and end_dt and start_dt > end_dt:
            raise HTTPException(status_code=400, detail="Start date must be before end date.")

        positions = db.query(Position).filter(Position.user_id == user.id).all()
        matched_positions = []
        for pos in positions:
            activity_time = position_activity_time(pos)
            if start_dt and activity_time < start_dt:
                continue
            if end_dt and activity_time > end_dt:
                continue
            if segment != "ALL" and get_report_category_code(pos.symbol) != segment:
                continue
            matched_positions.append(pos)

        position_ids = [pos.id for pos in matched_positions]
        signal_ids = {pos.signal_id for pos in matched_positions if pos.signal_id is not None}

        num_positions = 0
        if position_ids:
            num_positions = db.query(Position).filter(
                Position.user_id == user.id,
                Position.id.in_(position_ids)
            ).delete(synchronize_session=False)

        num_signals = 0
        if segment == "ALL" and not start_dt and not end_dt:
            num_signals = db.query(Signal).delete(synchronize_session=False)
        elif signal_ids:
            for signal_id in signal_ids:
                still_used = db.query(Position.id).filter(Position.signal_id == signal_id).first()
                if not still_used:
                    num_signals += db.query(Signal).filter(Signal.id == signal_id).delete(synchronize_session=False)

        db.commit()
        return {
            "status": "success",
            "purged_positions": num_positions,
            "purged_signals": num_signals,
            "segment": segment,
            "start_date": req.start_date if req else None,
            "end_date": req.end_date if req else None,
            "detail": "Successfully cleared matching positions and orphaned signals."
        }
    except Exception as e:
        db.rollback()
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=f"Database error during purge: {e}")

@app.get("/api/admin/debug-info")
def get_debug_info(secret: str = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    env_secrets = os.environ.get("VALID_SECRETS")
    if env_secrets:
        VALID_SECRETS = [s.strip() for s in env_secrets.split(",")]
    else:
        VALID_SECRETS = ["TradeSignal2024", "indian_market_5645c3c44e98ddb7ed7aee5f05482e6e9e910031", "8cf895aa0e3387d51d8c6c19f3dea05e02e2839b"]

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

import os
import json
from datetime import datetime, date
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, status, Query, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session


from backend.database import init_db, get_db, Signal, Position, DailyConsent, User
from backend.credentials import AppCredentialsManager, INDIAN_BROKERS, CRYPTO_EXCHANGES

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
    db.close()

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

from datetime import timedelta

def get_ist_time() -> datetime:
    # Indian Standard Time (IST) is UTC + 5:30
    return datetime.utcnow() + timedelta(hours=5, minutes=30)

# Helper to normalize symbols
def normalize_symbol(symbol: str) -> str:
    return symbol.upper().strip()

def extract_strike_from_symbol(symbol: str) -> Optional[float]:
    parts = symbol.split()
    if len(parts) >= 3:
        try:
            return float(parts[1])
        except ValueError:
            pass
    return None

def close_position_entry(pos: Position, index_exit_price: float, db: Session) -> float:
    parts = pos.symbol.split()
    is_option = len(parts) >= 3 and parts[-1] in ["CE", "PE"]
    
    if is_option:
        opt_type = parts[-1]
        strike = float(parts[1])
        price_change = index_exit_price - strike
        delta = 0.5 if opt_type == "CE" else -0.5
        exit_price = max(1.0, pos.entry_price + price_change * delta)
    else:
        exit_price = index_exit_price
        
    pos.exit_price = round(exit_price, 2)
    pos.exit_time = get_ist_time()
    pos.status = "CLOSED"
    
    if pos.direction == "LONG":
        pos.pnl = round((pos.exit_price - pos.entry_price) * pos.qty, 2)
    else:
        pos.pnl = round((pos.entry_price - pos.exit_price) * pos.qty, 2)
        
    return pos.pnl

def open_position_entry(symbol: str, direction: str, entry_price: float, qty: float, db: Session) -> Position:
    new_pos = Position(
        symbol=symbol,
        direction=direction,
        qty=qty,
        entry_price=entry_price,
        entry_time=get_ist_time(),
        status="OPEN"
    )
    db.add(new_pos)
    return new_pos

# Helper to determine qty and lot size based on symbol rules
def calculate_trade_qty(symbol: str) -> float:
    sym = symbol.upper()
    if "BANKNIFTY" in sym:
        return 30.0  # 1 lot Banknifty
    elif "NIFTY" in sym:
        return 65.0  # 1 lot Nifty
    elif "SENSEX" in sym:

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
        if act_lower in ["exit", "close", "cover", "exit_long", "exit_short"]:
            action_norm = "EXIT"
        elif act_lower in ["buy", "sell", "long", "short"]:
            action_norm = "LONG" if act_lower in ["buy", "long"] else "SHORT"
    elif raw_key:
        key_str = str(raw_key).upper()
        if "LONG" in key_str or "BUY" in key_str:
            action_norm = "LONG"
        elif "SHORT" in key_str or "SELL" in key_str:
            action_norm = "SHORT"
        elif "COVER" in key_str or "EXIT" in key_str:
            action_norm = "EXIT"
    elif raw_dir:
        dir_str = str(raw_dir).upper()
        if dir_str in ["LONG", "BUY"]:
            action_norm = "LONG"
        elif dir_str in ["SHORT", "SELL"]:
            action_norm = "SHORT"
    elif body_str:
        # Fallback to search inside raw body text
        body_upper = body_str.upper()
        if "BUY" in body_upper or "LONG" in body_upper:
            action_norm = "LONG"
        elif "SELL" in body_upper or "SHORT" in body_upper:
            action_norm = "SHORT"
        elif "EXIT" in body_upper or "COVER" in body_upper:
            action_norm = "EXIT"
            
    # Resolve source
    source = payload.get("source") or ("TradingView" if (raw_key or "tradingview" in body_str.lower()) else "Scanner")
    source_name = payload.get("orderId") or payload.get("signal_type") or "Webhook Strategy Alert"

    # Check if daily consent is signed for today (based on IST date)
    ist_now = get_ist_time()
    today_str = ist_now.date().isoformat()
    consent = db.query(DailyConsent).filter(DailyConsent.date == today_str).first()
    consent_signed = consent is not None and consent.consent_given
    
    # Process Paper Trade
    # Normalize underlying symbol for options checking
    underlying_norm = symbol_norm
    if underlying_norm.endswith("1!"):
        underlying_norm = underlying_norm[:-2]
    elif underlying_norm.endswith("1"):
        underlying_norm = underlying_norm[:-1]
        
    if underlying_norm in ["BSX", "SENSEX"]:
        underlying_norm = "SENSEX"
        
    # Check for open positions on this symbol or its options
    open_positions = db.query(Position).filter(
        (Position.symbol == symbol_norm) | 
        (Position.symbol == underlying_norm) |
        (Position.symbol.like(f"{symbol_norm} %")) |
        (Position.symbol.like(f"{underlying_norm} %")),
        Position.status == "OPEN"
    ).all()
    
    open_pos_future = next((p for p in open_positions if p.symbol == symbol_norm), None)
    
    # Contextual Exit Action Labels (SELL & COVER instead of EXIT)
    if action_norm == "EXIT" and open_pos_future:
        if open_pos_future.direction == "LONG":
            action_norm = "SELL"
        else:
            action_norm = "COVER"
            
    # Calculate Option details (At-The-Money CE/PE option contract)
    has_options = underlying_norm in ["NIFTY", "BANKNIFTY", "SENSEX"]
    opt_strike = None
    opt_symbol = None
    opt_premium = None
    opt_type = None
    
    if has_options and price_val > 0:
        step = 50 if underlying_norm == "NIFTY" else 100
        opt_strike = int(round(price_val / step) * step)
        
        if action_norm in ["BUY", "LONG"]:
            opt_type = "CE"
            opt_premium = price_val * 0.012 if underlying_norm == "NIFTY" else price_val * 0.015
        elif action_norm in ["SELL", "SHORT"]:
            is_explicit_short_entry = (
                str(raw_action).lower() in ["short", "entry_short"] or
                "SHORT" in str(raw_key).upper() or
                "SHORT" in str(raw_dir).upper() or
                (str(raw_action).lower() == "sell" and not open_pos_future)
            )
            if is_explicit_short_entry:
                opt_type = "PE"
                opt_premium = price_val * 0.012 if underlying_norm == "NIFTY" else price_val * 0.015
                
        if opt_type:
            opt_symbol = f"{underlying_norm} {opt_strike} {opt_type}"
    
    # Save signal in database in IST
    signal_entry = Signal(
        symbol=symbol_norm,
        action=action_norm,
        price=price_val,
        source=source,
        source_name=source_name,
        raw_payload=json.dumps(payload),
        timestamp=ist_now
    )
    db.add(signal_entry)
    db.commit()
    
    trade_log = []
    
    # Execution Logic
    if action_norm in ["BUY", "LONG"]:
        is_explicit_long_entry = (
            str(raw_action).lower() in ["long", "entry_long"] or
            "LONG" in str(raw_key).upper() or
            "LONG" in str(raw_dir).upper() or
            (str(raw_action).lower() == "buy" and not open_pos_future)
        )
        
        if open_positions:
            for p in open_positions:
                pnl = close_position_entry(p, price_val, db)
                trade_log.append(f"Closed existing {p.direction} position on {p.symbol} (P&L: {pnl})")
            
            if not is_explicit_long_entry:
                trade_log.append(f"Covered SHORT position on {symbol_norm} (flat)")
                
        if not open_positions or is_explicit_long_entry:
            # A. Open Future position
            qty = calculate_trade_qty(symbol_norm)
            open_position_entry(symbol_norm, "LONG", price_val, qty, db)
            trade_log.append(f"Opened Future LONG position for {symbol_norm} (Qty: {qty})")
            
            # B. Open Option position
            if opt_symbol and opt_premium:
                open_position_entry(opt_symbol, "LONG", opt_premium, qty, db)
                trade_log.append(f"Opened Option LONG position for {opt_symbol} (Premium: {opt_premium:.2f}, Qty: {qty})")
                
    elif action_norm in ["SELL", "SHORT"]:
        is_explicit_short_entry = (
            str(raw_action).lower() in ["short", "entry_short"] or
            "SHORT" in str(raw_key).upper() or
            "SHORT" in str(raw_dir).upper() or
            (str(raw_action).lower() == "sell" and not open_pos_future)
        )
        
        if open_positions:
            for p in open_positions:
                pnl = close_position_entry(p, price_val, db)
                trade_log.append(f"Closed existing {p.direction} position on {p.symbol} (P&L: {pnl})")
            
            if not is_explicit_short_entry:
                trade_log.append(f"Exited LONG position on {symbol_norm} (flat)")
                
        if not open_positions or is_explicit_short_entry:
            # A. Open Future position
            qty = calculate_trade_qty(symbol_norm)
            open_position_entry(symbol_norm, "SHORT", price_val, qty, db)
            trade_log.append(f"Opened Future SHORT position for {symbol_norm} (Qty: {qty})")
            
            # B. Open Option position (PE Premium is bought, so position direction is LONG)
            if opt_symbol and opt_premium:
                open_position_entry(opt_symbol, "LONG", opt_premium, qty, db)
                trade_log.append(f"Opened Option LONG position for {opt_symbol} (Premium: {opt_premium:.2f}, Qty: {qty})")
                
    elif action_norm in ["EXIT", "EXIT_LONG", "EXIT_SHORT", "CLOSE", "COVER"]:
        if open_positions:
            for p in open_positions:
                pnl = close_position_entry(p, price_val, db)
                trade_log.append(f"Exited {p.direction} position on {p.symbol} at {p.exit_price} (P&L: {pnl})")
        else:
            trade_log.append(f"Received exit signal for {symbol_norm} but no open position existed")
            
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
            "source_name": signal_entry.source_name
        },
        "consent_signed": consent_signed,
        "logs": trade_log
    }
    await manager.broadcast(json.dumps(ws_data))
    
    return {"status": "success", "processed_signals": 1, "actions": trade_log, "consent_signed": consent_signed}


@app.get("/api/signals")
def get_signals(limit: int = Query(50, ge=1, le=100), db: Session = Depends(get_db)):
    signals = db.query(Signal).order_by(Signal.timestamp.desc()).limit(limit).all()
    return [{
        "id": s.id,
        "timestamp": s.timestamp.isoformat(),
        "symbol": s.symbol,
        "action": s.action,
        "price": s.price,
        "source": s.source,
        "source_name": s.source_name
    } for s in signals]

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

def get_live_market_price_data(symbol: str) -> dict:
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
    parts = symbol.split()
    is_option = len(parts) >= 3 and parts[-1] in ["CE", "PE"]
    
    if is_option:
        underlying = parts[0]
        opt_type = parts[-1]
        strike = float(parts[1])
        
        # Get live underlying index price
        live_underlying = get_live_market_price(underlying)
        if live_underlying is None:
            # Fallback to latest signal price in database
            latest_signal = db.query(Signal).filter(Signal.symbol == underlying).order_by(Signal.timestamp.desc()).first()
            live_underlying = latest_signal.price if latest_signal else strike
            
        price_change = live_underlying - strike
        delta = 0.5 if opt_type == "CE" else -0.5
        current_premium = entry_price + price_change * delta
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

@app.get("/api/paper-trades")
def get_paper_trades(db: Session = Depends(get_db)):
    positions = db.query(Position).order_by(Position.entry_time.desc()).all()
    
    # Calculate stats
    closed_positions = [p for p in positions if p.status == "CLOSED"]
    open_positions = [p for p in positions if p.status == "OPEN"]
    
    # Calculate open positions details & accumulate PnL separately
    positions_data = []
    total_pnl_inr = 0.0
    total_pnl_usd = 0.0
    
    # Process open positions
    for p in positions:
        if p.status == "OPEN":
            current_price = get_current_price(p.symbol, p.entry_price, db)
            if p.direction == "LONG":
                pnl = (current_price - p.entry_price) * p.qty
            else:
                pnl = (p.entry_price - current_price) * p.qty
            
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
                "pnl": round(pnl, 2)
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
                "pnl": p.pnl
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
def manual_exit_position(pos_id: int, db: Session = Depends(get_db)):
    pos = db.query(Position).filter(Position.id == pos_id, Position.status == "OPEN").first()
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
def check_consent(db: Session = Depends(get_db)):
    ist_now = get_ist_time()
    today_str = ist_now.date().isoformat()
    consent = db.query(DailyConsent).filter(DailyConsent.date == today_str).first()
    return {
        "consent_signed": consent is not None and consent.consent_given,
        "date": today_str,
        "timestamp": consent.timestamp.isoformat() if consent else None
    }

@app.post("/api/consent")
def sign_consent(req: ConsentRequest, db: Session = Depends(get_db)):
    ist_now = get_ist_time()
    today_str = ist_now.date().isoformat()
    consent = db.query(DailyConsent).filter(DailyConsent.date == today_str).first()
    if consent:
        consent.consent_given = True
        consent.timestamp = ist_now
    else:
        consent = DailyConsent(
            date=today_str,
            agreement_text_version=req.agreement_version,
            consent_given=True,
            timestamp=ist_now
        )
        db.add(consent)
    db.commit()
    return {"status": "success", "signed": True, "date": today_str}

@app.get("/api/credentials")
def get_credentials(db: Session = Depends(get_db)):
    mgr = AppCredentialsManager(db)
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
def save_credentials(req: CredentialRequest, db: Session = Depends(get_db)):
    mgr = AppCredentialsManager(db)
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
def delete_credentials(broker_id: str, db: Session = Depends(get_db)):
    mgr = AppCredentialsManager(db)
    if mgr.delete_credentials(broker_id):
        return {"status": "success", "broker_id": broker_id}
    raise HTTPException(status_code=404, detail="Credentials not found")

@app.get("/api/user")
def get_user_profile(db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == 1).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Calculate days remaining in 5 working-day trial
    # Simple mock check
    trial_days_left = 5
    return {
        "name": user.name,
        "email": user.email,
        "phone": user.phone,
        "subscription_status": user.subscription_status,
        "trial_days_left": trial_days_left
    }

from fastapi.staticfiles import StaticFiles
# Mount static files for the simulator at root
simulator_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "simulator")
if os.path.exists(simulator_dir):
    app.mount("/", StaticFiles(directory=simulator_dir, html=True), name="static")


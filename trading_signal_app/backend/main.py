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
        user.trial_end = datetime.now()
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

# Helper to normalize symbols
def normalize_symbol(symbol: str) -> str:
    return symbol.upper().strip()

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
    if price is None and body_str:
        # Search for a decimal/float number in the body
        import re
        numbers = re.findall(r"\b\d+(?:\.\d+)?\b", body_str)
        if numbers:
            price = numbers[0]
            
    if price is None:
        price_val = 0.0
    else:
        try:
            price_val = float(price)
        except (ValueError, TypeError):
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

    # Check if daily consent is signed for today
    today_str = date.today().isoformat()
    consent = db.query(DailyConsent).filter(DailyConsent.date == today_str).first()
    consent_signed = consent is not None and consent.consent_given
    
    # Save signal in database
    signal_entry = Signal(
        symbol=symbol_norm,
        action=action_norm,
        price=price_val,
        source=source,
        source_name=source_name,
        raw_payload=json.dumps(payload)
    )
    db.add(signal_entry)
    db.commit()
    
    # Process Paper Trade
    # Check for open positions on this symbol
    open_position = db.query(Position).filter(
        Position.symbol == symbol_norm,
        Position.status == "OPEN"
    ).first()
    
    trade_log = []
    
    # Execution Logic
    if action_norm in ["BUY", "LONG"]:
        if open_position:
            # If already open, close it first at current price (forced rollover/reverse)
            open_position.exit_price = price_val
            open_position.exit_time = datetime.utcnow()
            open_position.status = "CLOSED"
            if open_position.direction == "LONG":
                open_position.pnl = (price_val - open_position.entry_price) * open_position.qty
            else:
                open_position.pnl = (open_position.entry_price - price_val) * open_position.qty
            trade_log.append(f"Replaced existing {open_position.direction} position on {symbol_norm}")
        
        # Open a new LONG position
        qty = calculate_trade_qty(symbol_norm)
        new_pos = Position(
            symbol=symbol_norm,
            direction="LONG",
            qty=qty,
            entry_price=price_val,
            status="OPEN"
        )
        db.add(new_pos)
        trade_log.append(f"Opened LONG position for {symbol_norm} (Qty: {qty})")
        
    elif action_norm in ["SELL", "SHORT"]:
        if open_position:
            # Close existing position
            open_position.exit_price = price_val
            open_position.exit_time = datetime.utcnow()
            open_position.status = "CLOSED"
            if open_position.direction == "LONG":
                open_position.pnl = (price_val - open_position.entry_price) * open_position.qty
            else:
                open_position.pnl = (open_position.entry_price - price_val) * open_position.qty
            trade_log.append(f"Replaced existing {open_position.direction} position on {symbol_norm}")
            
        # Open a new SHORT position
        qty = calculate_trade_qty(symbol_norm)
        new_pos = Position(
            symbol=symbol_norm,
            direction="SHORT",
            qty=qty,
            entry_price=price_val,
            status="OPEN"
        )
        db.add(new_pos)
        trade_log.append(f"Opened SHORT position for {symbol_norm} (Qty: {qty})")
        
    elif action_norm in ["EXIT", "EXIT_LONG", "EXIT_SHORT", "CLOSE"]:
        if open_position:
            open_position.exit_price = price_val
            open_position.exit_time = datetime.utcnow()
            open_position.status = "CLOSED"
            if open_position.direction == "LONG":
                open_position.pnl = (price_val - open_position.entry_price) * open_position.qty
            else:
                open_position.pnl = (open_position.entry_price - price_val) * open_position.qty
            trade_log.append(f"Exited {open_position.direction} position on {symbol_norm} at {price_val}")
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

def map_symbol_to_google_finance_ticker(symbol: str) -> str:
    s_upper = symbol.upper().strip()
    if s_upper in ["BTCUSD", "BTC", "BTC-USD"]:
        return "BTC-USD"
    elif s_upper in ["NIFTY", "NIFTY50", "NIFTY 50", "NSE:NIFTY", "NIFTY_50"]:
        return "NIFTY_50:INDEXNSE"
    elif s_upper in ["BANKNIFTY", "NIFTYBANK", "NSE:BANKNIFTY", "NIFTY_BANK"]:
        return "NIFTY_BANK:INDEXNSE"
    elif s_upper in ["SENSEX", "BSESN"]:
        return "SENSEX:INDEXBOM"
    
    # Generic stocks / other tickers fallback
    if s_upper.endswith(".NS"):
        return f"{s_upper[:-3]}:INDEXNSE"
    if s_upper.endswith(".BO"):
        return f"{s_upper[:-3]}:INDEXBOM"
        
    return f"{s_upper}:INDEXNSE"

def get_google_finance_price(symbol: str) -> Optional[float]:
    import urllib.request
    import re
    ticker = map_symbol_to_google_finance_ticker(symbol)
    url = f"https://www.google.com/finance/quote/{ticker}"
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })
        with urllib.request.urlopen(req, timeout=5) as response:
            html = response.read().decode('utf-8')
            match = re.search(r'<div class="gO24Ff">([^<]+)</div>.*?jsname="Pdsbrc"[^>]*><span>([^<]+)</span>', html, re.DOTALL)
            if match:
                price_str = match.group(2)
                price = float(price_str.replace(',', '').replace('$', '').replace('₹', '').strip())
                return price
    except Exception as e:
        print(f"Error fetching live Google Finance price for {ticker}: {e}")
    return None

def map_symbol_to_yahoo_ticker(symbol: str) -> str:
    s_upper = symbol.upper().strip()
    if s_upper in ["BTCUSD", "BTC", "BTC-USD"]:
        return "BTC-USD"
    elif s_upper in ["NIFTY", "NIFTY50", "NIFTY 50", "NSE:NIFTY"]:
        return "^NSEI"
    elif s_upper in ["BANKNIFTY", "NIFTYBANK", "NSE:BANKNIFTY"]:
        return "^NSEBANK"
    
    # Fallback to other cryptos
    if s_upper.endswith("USD"):
        return f"{s_upper[:-3]}-USD"
    if s_upper.endswith("USDT"):
        return f"{s_upper[:-4]}-USD"
        
    return f"{s_upper}.NS"

def get_live_market_price(symbol: str) -> Optional[float]:
    # 1. Try Google Finance first
    gf_price = get_google_finance_price(symbol)
    if gf_price is not None:
        print(f"[Live Pricing] Google Finance fetched {symbol}: {gf_price}")
        return gf_price
        
    # 2. Fallback to Yahoo Finance
    import urllib.request
    import json
    ticker = map_symbol_to_yahoo_ticker(symbol)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read())
            price = data['chart']['result'][0]['meta']['regularMarketPrice']
            print(f"[Live Pricing] Yahoo Finance fallback fetched {symbol}: {price}")
            return float(price)
    except Exception as e:
        print(f"Error fetching live price for {ticker} from Yahoo Finance: {e}")
        return None

def get_current_price(symbol: str, entry_price: float, db: Session) -> float:
    # Try to fetch actual live price
    live_price = get_live_market_price(symbol)
    if live_price is not None:
        return round(live_price, 2)
        
    # Fallback to latest signal price in database
    latest_signal = db.query(Signal).filter(Signal.symbol == symbol).order_by(Signal.timestamp.desc()).first()
    price = latest_signal.price if latest_signal else entry_price
    
    # Fallback simulated tick fluctuation
    import random
    fluctuation = random.uniform(-0.0015, 0.0015)
    simulated_price = price * (1 + fluctuation)
    return round(simulated_price, 2)

@app.get("/api/paper-trades")
def get_paper_trades(db: Session = Depends(get_db)):
    positions = db.query(Position).order_by(Position.entry_time.desc()).all()
    
    # Calculate stats
    closed_positions = [p for p in positions if p.status == "CLOSED"]
    open_positions = [p for p in positions if p.status == "OPEN"]
    
    closed_pnl = sum(p.pnl for p in closed_positions)
    
    # Calculate open positions details
    positions_data = []
    total_open_pnl = 0.0
    
    for p in positions:
        if p.status == "OPEN":
            current_price = get_current_price(p.symbol, p.entry_price, db)
            if p.direction == "LONG":
                pnl = (current_price - p.entry_price) * p.qty
            else:
                pnl = (p.entry_price - current_price) * p.qty
            total_open_pnl += pnl
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
            
    total_pnl = closed_pnl + total_open_pnl
    total_trades = len(closed_positions)
    winning_trades = sum(1 for p in closed_positions if p.pnl > 0)
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
    
    return {
        "positions": positions_data,
        "stats": {
            "total_pnl": round(total_pnl, 2),
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
    
    # Exit at the current simulated price
    exit_price = get_current_price(pos.symbol, pos.entry_price, db)
    
    pos.exit_price = exit_price
    pos.exit_time = datetime.utcnow()
    pos.status = "CLOSED"
    if pos.direction == "LONG":
        pos.pnl = round((exit_price - pos.entry_price) * pos.qty, 2)
    else:
        pos.pnl = round((pos.entry_price - exit_price) * pos.qty, 2)
        
    db.commit()
    return {"status": "success", "pnl": pos.pnl}

@app.get("/api/consent")
def check_consent(db: Session = Depends(get_db)):
    today_str = date.today().isoformat()
    consent = db.query(DailyConsent).filter(DailyConsent.date == today_str).first()
    return {
        "consent_signed": consent is not None and consent.consent_given,
        "date": today_str,
        "timestamp": consent.timestamp.isoformat() if consent else None
    }

@app.post("/api/consent")
def sign_consent(req: ConsentRequest, db: Session = Depends(get_db)):
    today_str = date.today().isoformat()
    consent = db.query(DailyConsent).filter(DailyConsent.date == today_str).first()
    if consent:
        consent.consent_given = True
        consent.timestamp = datetime.utcnow()
    else:
        consent = DailyConsent(
            date=today_str,
            agreement_text_version=req.agreement_version,
            consent_given=True
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


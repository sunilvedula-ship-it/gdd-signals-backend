import os
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, Float, String, DateTime, Boolean, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

# Resolve database connection string from environment variables for production (e.g. Supabase, Render, Railway Postgres)
DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    # Fallback to local SQLite database for development
    DB_PATH = os.path.expanduser('~/.gurudevadatta/trading_app.db')
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    DATABASE_URL = f"sqlite:///{DB_PATH}"

# Render or Railway sometimes sets the PostgreSQL URL with "postgres://" prefix
# which was deprecated in SQLAlchemy 1.4+ (requiring "postgresql://")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Check if SQLite is being used to apply thread connection arguments
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    # Use standard connection pooling for production database
    engine = create_engine(
        DATABASE_URL,
        pool_size=10,
        max_overflow=20,
        pool_recycle=1800,
        pool_pre_ping=True
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    phone = Column(String, unique=True, index=True)
    name = Column(String)
    subscription_status = Column(String, default="trial")  # trial, active, expired
    trial_start = Column(DateTime, default=datetime.utcnow)
    trial_end = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

class Signal(Base):
    __tablename__ = 'signals'
    
    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    symbol = Column(String, index=True)  # BTCUSD, NIFTY, BANKNIFTY, SENSEX, CRUDEOIL, GOLD, Stocks
    action = Column(String)  # LONG, SHORT, EXIT_LONG, EXIT_SHORT, BUY, SELL
    price = Column(Float)
    source = Column(String)  # TV_ALERT, SCANNER
    source_name = Column(String)  # e.g., LEN1: KST-ST, ANDEAN, etc.
    raw_payload = Column(String)  # JSON dump of incoming alert

class Position(Base):
    __tablename__ = 'positions'
    
    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, index=True)
    direction = Column(String)  # LONG, SHORT
    qty = Column(Float)
    lot_size = Column(Integer, default=1)
    entry_price = Column(Float)
    entry_time = Column(DateTime, default=datetime.utcnow)
    exit_price = Column(Float, nullable=True)
    exit_time = Column(DateTime, nullable=True)
    status = Column(String, default="OPEN")  # OPEN, CLOSED
    pnl = Column(Float, default=0.0)
    real_or_paper = Column(String, default="PAPER")  # PAPER, LIVE
    signal_id = Column(Integer, ForeignKey('signals.id'), nullable=True)

class DailyConsent(Base):
    __tablename__ = 'daily_consents'
    
    id = Column(Integer, primary_key=True, index=True)
    date = Column(String, index=True)  # YYYY-MM-DD
    timestamp = Column(DateTime, default=datetime.utcnow)
    user_id = Column(Integer, default=1)
    agreement_text_version = Column(String, default="v1.0")
    consent_given = Column(Boolean, default=True)

class BrokerCredential(Base):
    __tablename__ = 'broker_credentials'
    
    id = Column(Integer, primary_key=True, index=True)
    broker_id = Column(String, unique=True, index=True)  # flattrade, zerodha, delta_exchange, etc.
    api_key = Column(String)
    api_secret = Column(String)
    extra_fields = Column(String)  # JSON string for extra fields (client_id, consumer_key, etc.)
    updated_at = Column(DateTime, default=datetime.utcnow)

def init_db():
    Base.metadata.create_all(bind=engine)
    # Automatically add signal_id column to positions table if it does not exist
    from sqlalchemy import text
    db = SessionLocal()
    try:
        if "postgresql" in DATABASE_URL:
            db.execute(text("ALTER TABLE positions ADD COLUMN IF NOT EXISTS signal_id INTEGER;"))
        else:
            db.execute(text("ALTER TABLE positions ADD COLUMN signal_id INTEGER;"))
        db.commit()
        print("[DB] Successfully verified/added signal_id column to positions table.")
    except Exception as e:
        print(f"[DB] Migration note: {e}")
        pass
    finally:
        db.close()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

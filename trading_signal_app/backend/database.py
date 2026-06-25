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
    supabase_uid = Column(String, unique=True, index=True, nullable=True)
    email = Column(String, unique=True, index=True)
    phone = Column(String, unique=True, index=True)
    name = Column(String)
    subscription_status = Column(String, default="trial")  # trial, active, expired
    trial_start = Column(DateTime, default=datetime.utcnow)
    trial_end = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    muted_symbols = Column(String, default="")  # comma-separated muted symbols

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
    timeframe = Column(String, default="5m", nullable=True)

class Position(Base):
    __tablename__ = 'positions'
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), default=1)
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
    timeframe = Column(String, default="5m", nullable=True)

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
    user_id = Column(Integer, ForeignKey('users.id'), default=1)
    broker_id = Column(String, index=True)  # flattrade, zerodha, delta_exchange, etc.
    api_key = Column(String)
    api_secret = Column(String)
    extra_fields = Column(String)  # JSON string for extra fields (client_id, consumer_key, etc.)
    updated_at = Column(DateTime, default=datetime.utcnow)

def init_db():
    Base.metadata.create_all(bind=engine)
    from sqlalchemy import text
    db = SessionLocal()
    try:
        # Migrate user_id to positions table
        try:
            if "postgresql" in DATABASE_URL:
                db.execute(text("ALTER TABLE positions ADD COLUMN IF NOT EXISTS user_id INTEGER DEFAULT 1;"))
            else:
                db.execute(text("ALTER TABLE positions ADD COLUMN user_id INTEGER DEFAULT 1;"))
            db.commit()
        except Exception:
            db.rollback()

        # Migrate signal_id to positions table
        try:
            if "postgresql" in DATABASE_URL:
                db.execute(text("ALTER TABLE positions ADD COLUMN IF NOT EXISTS signal_id INTEGER;"))
            else:
                db.execute(text("ALTER TABLE positions ADD COLUMN signal_id INTEGER;"))
            db.commit()
        except Exception:
            db.rollback()

        # Migrate user_id to broker_credentials table
        try:
            if "postgresql" in DATABASE_URL:
                db.execute(text("ALTER TABLE broker_credentials ADD COLUMN IF NOT EXISTS user_id INTEGER DEFAULT 1;"))
            else:
                db.execute(text("ALTER TABLE broker_credentials ADD COLUMN user_id INTEGER DEFAULT 1;"))
            db.commit()
        except Exception:
            db.rollback()

        # Migrate supabase_uid and muted_symbols to users table
        try:
            if "postgresql" in DATABASE_URL:
                db.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS supabase_uid VARCHAR(255);"))
                db.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS muted_symbols TEXT DEFAULT '';"))
            else:
                db.execute(text("ALTER TABLE users ADD COLUMN supabase_uid VARCHAR(255);"))
                db.execute(text("ALTER TABLE users ADD COLUMN muted_symbols TEXT DEFAULT '';"))
            db.commit()
        except Exception:
            db.rollback()

        # Drop unique constraint index on broker_credentials.broker_id if it exists
        try:
            db.execute(text("DROP INDEX IF EXISTS ix_broker_credentials_broker_id;"))
            db.execute(text("CREATE INDEX ix_broker_credentials_broker_id ON broker_credentials (broker_id);"))
            db.commit()
        except Exception:
            db.rollback()

        # Migrate timeframe to signals table
        try:
            if "postgresql" in DATABASE_URL:
                db.execute(text("ALTER TABLE signals ADD COLUMN IF NOT EXISTS timeframe VARCHAR(50) DEFAULT '5m';"))
            else:
                db.execute(text("ALTER TABLE signals ADD COLUMN timeframe VARCHAR(50) DEFAULT '5m';"))
            db.commit()
        except Exception:
            db.rollback()

        # Migrate timeframe to positions table
        try:
            if "postgresql" in DATABASE_URL:
                db.execute(text("ALTER TABLE positions ADD COLUMN IF NOT EXISTS timeframe VARCHAR(50) DEFAULT '5m';"))
            else:
                db.execute(text("ALTER TABLE positions ADD COLUMN timeframe VARCHAR(50) DEFAULT '5m';"))
            db.commit()
        except Exception:
            db.rollback()

        # Migrate user_id to daily_consents table
        try:
            if "postgresql" in DATABASE_URL:
                db.execute(text("ALTER TABLE daily_consents ADD COLUMN IF NOT EXISTS user_id INTEGER DEFAULT 1;"))
            else:
                db.execute(text("ALTER TABLE daily_consents ADD COLUMN user_id INTEGER DEFAULT 1;"))
            db.commit()
        except Exception:
            db.rollback()

        # Migrate real_or_paper to positions table
        try:
            if "postgresql" in DATABASE_URL:
                db.execute(text("ALTER TABLE positions ADD COLUMN IF NOT EXISTS real_or_paper VARCHAR(50) DEFAULT 'PAPER';"))
            else:
                db.execute(text("ALTER TABLE positions ADD COLUMN real_or_paper VARCHAR(50) DEFAULT 'PAPER';"))
            db.commit()
        except Exception:
            db.rollback()

        # Migrate lot_size to positions table
        try:
            if "postgresql" in DATABASE_URL:
                db.execute(text("ALTER TABLE positions ADD COLUMN IF NOT EXISTS lot_size INTEGER DEFAULT 1;"))
            else:
                db.execute(text("ALTER TABLE positions ADD COLUMN lot_size INTEGER DEFAULT 1;"))
            db.commit()
        except Exception:
            db.rollback()

        print("[DB] Successfully verified and completed all database migrations.")
    except Exception as e:
        print(f"[DB] Migration error: {e}")
        pass
    finally:
        db.close()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

import os
import json
import base64
import hashlib
from datetime import datetime
from pathlib import Path

# Supported Brokers definitions
INDIAN_BROKERS = [
    {"id": "flattrade",     "name": "Flattrade",         "api_name": "Flattrade Pi API",  "fields": ["api_key", "api_secret"]},
    {"id": "zerodha",       "name": "Zerodha",           "api_name": "Kite Connect",      "fields": ["api_key", "api_secret"]},
    {"id": "kotak",         "name": "Kotak Securities",  "api_name": "Kotak Neo API",      "fields": ["api_key", "api_secret", "consumer_key"]},
    {"id": "angelone",      "name": "Angel One",         "api_name": "SmartAPI",           "fields": ["api_key", "api_secret", "client_id"]},
    {"id": "upstox",        "name": "Upstox",            "api_name": "Upstox API v2",      "fields": ["api_key", "api_secret"]},
    {"id": "dhan",          "name": "Dhan",              "api_name": "DhanHQ API",         "fields": ["api_key", "api_secret"]},
    {"id": "fyers",         "name": "Fyers",             "api_name": "Fyers API v3",       "fields": ["api_key", "api_secret"]},
    {"id": "5paisa",        "name": "5paisa",            "api_name": "Xstream API",        "fields": ["api_key", "api_secret", "client_id"]},
    {"id": "iihl",          "name": "IIFL Securities",   "api_name": "IIFL Markets API",   "fields": ["api_key", "api_secret"]},
    {"id": "motilal",       "name": "Motilal Oswal",     "api_name": "MO API",             "fields": ["api_key", "api_secret", "client_id"]},
    {"id": "groww",         "name": "Groww",             "api_name": "Groww API",           "fields": ["api_key", "api_secret"]},
]

CRYPTO_EXCHANGES = [
    {"id": "delta_exchange", "name": "Delta Exchange", "api_name": "Delta Exchange API", "fields": ["api_key", "api_secret"]},
]

def _get_machine_key():
    import platform
    raw = f"{platform.node()}-{os.getenv('USERNAME', os.getenv('USER', 'default'))}"
    return hashlib.sha256(raw.encode()).digest()

def obfuscate(text):
    key = _get_machine_key()
    data = text.encode('utf-8')
    obfuscated = bytes([data[i] ^ key[i % len(key)] for i in range(len(data))])
    return base64.b64encode(obfuscated).decode('ascii')

def deobfuscate(encoded):
    key = _get_machine_key()
    data = base64.b64decode(encoded.encode('ascii'))
    original = bytes([data[i] ^ key[i % len(key)] for i in range(len(data))])
    return original.decode('utf-8')

class AppCredentialsManager:
    """Manages credentials utilizing SQLite storage instead of a flat file, keeping same obfuscation."""
    
    def __init__(self, db_session, user_id=1):
        self.db = db_session
        self.user_id = user_id
        from backend.database import BrokerCredential
        self.model = BrokerCredential
        
    def save_credentials(self, broker_id, api_key, api_secret, extra=None):
        extra_json = json.dumps(extra or {})
        
        # Obfuscate credentials before saving
        enc_api_key = obfuscate(api_key)
        enc_api_secret = obfuscate(api_secret)
        enc_extra = obfuscate(extra_json)
        
        cred = self.db.query(self.model).filter(
            self.model.broker_id == broker_id,
            self.model.user_id == self.user_id
        ).first()
        
        if cred:
            cred.api_key = enc_api_key
            cred.api_secret = enc_api_secret
            cred.extra_fields = enc_extra
            cred.updated_at = datetime.utcnow()
        else:
            cred = self.model(
                user_id=self.user_id,
                broker_id=broker_id,
                api_key=enc_api_key,
                api_secret=enc_api_secret,
                extra_fields=enc_extra,
                updated_at=datetime.utcnow()
            )
            self.db.add(cred)
        
        self.db.commit()
        return True

    def load_credentials(self, broker_id):
        cred = self.db.query(self.model).filter(
            self.model.broker_id == broker_id,
            self.model.user_id == self.user_id
        ).first()
        if not cred:
            return None
        try:
            return {
                'api_key': deobfuscate(cred.api_key),
                'api_secret': deobfuscate(cred.api_secret),
                'extra': json.loads(deobfuscate(cred.extra_fields)) if cred.extra_fields else {},
                'updated_at': cred.updated_at.isoformat()
            }
        except Exception as e:
            print(f"Error loading credentials for {broker_id}: {e}")
            return None

    def delete_credentials(self, broker_id):
        cred = self.db.query(self.model).filter(
            self.model.broker_id == broker_id,
            self.model.user_id == self.user_id
        ).first()
        if cred:
            self.db.delete(cred)
            self.db.commit()
            return True
        return False

    def has_credentials(self, broker_id):
        cred = self.db.query(self.model).filter(
            self.model.broker_id == broker_id,
            self.model.user_id == self.user_id
        ).first()
        return cred is not None

    def get_masked_info(self, broker_id):
        creds = self.load_credentials(broker_id)
        if not creds:
            return None
        key = creds.get('api_key', '')
        return {
            'broker_id': broker_id,
            'api_key_masked': key[:4] + '****' + key[-4:] if len(key) > 8 else '****',
            'has_secret': bool(creds.get('api_secret')),
            'extra_fields': list(creds.get('extra', {}).keys()),
            'updated_at': creds.get('updated_at', '')
        }
        
    def get_active_broker(self):
        for broker in INDIAN_BROKERS:
            if self.has_credentials(broker['id']):
                return broker['id']
        return None

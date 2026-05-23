import time
import requests

API_URL = "http://127.0.0.1:8000/api/signals/webhook"
SECRET = "TradeSignal2024"

def send_alert(symbol, action, price, source="TradingView Webhook", order_id="StrategyAlert"):
    payload = {
        "auth": SECRET,
        "symbol": symbol,
        "action": action,
        "price": price,
        "orderId": order_id,
        "source": source
    }
    try:
        res = requests.post(API_URL, json=payload)
        if res.status_code == 200:
            print(f"[{symbol}] Sent {action} signal at ₹{price} successfully: {res.json()['actions']}")
        else:
            print(f"Error sending {action} signal for {symbol}: {res.status_code} - {res.text}")
    except Exception as e:
        print(f"Connection failed: {e}")

def main():
    print("=== GuruDevaDatta Signal Webhook Simulation Script ===")
    print("Ensure uvicorn / run_simulator.py is running on http://127.0.0.1:8000 first.\n")
    
    # 1. Simulate Nifty Long Position
    print("1. Simulating NIFTY Long Entry alert...")
    send_alert("NIFTY", "LONG", 23500.0, "TradingView", "TV_Alert_Nifty_Buy")
    time.sleep(2)
    
    # 2. Simulate Banknifty Short Position
    print("2. Simulating BANKNIFTY Short Entry alert...")
    send_alert("BANKNIFTY", "SHORT", 48200.0, "TradingView", "TV_Alert_BNF_Sell")
    time.sleep(2)
    
    # 3. Simulate Crypto Position
    print("3. Simulating BTCUSD Long Entry alert...")
    send_alert("BTCUSD", "LONG", 96500.0, "TradingView", "TV_Alert_BTC_Buy")
    time.sleep(2)
    
    # 4. Simulate exits with profits
    print("\nSimulating price movements and exits...")
    time.sleep(1)
    
    # Nifty exits at 23580 (Gain of 80 points * 65 lot size = +₹5200)
    print("4. Simulating NIFTY Exit alert (Profit)...")
    send_alert("NIFTY", "EXIT", 23580.0, "TradingView", "TV_Alert_Nifty_Exit")
    time.sleep(2)
    
    # Banknifty exits at 48050 (Gain of 150 points * 30 lot size = +₹4500)
    print("5. Simulating BANKNIFTY Exit alert (Profit)...")
    send_alert("BANKNIFTY", "EXIT", 48050.0, "TradingView", "TV_Alert_BNF_Exit")
    time.sleep(2)

    # BTC exits at 96300 (Loss of 200 points * 1 qty = -$200)
    print("6. Simulating BTCUSD Exit alert (Loss)...")
    send_alert("BTCUSD", "EXIT", 96300.0, "TradingView", "TV_Alert_BTC_Exit")
    
    print("\nWebhook alerts completed. Refresh the 'Paper Trade' tab inside your simulator to review statistics!")

if __name__ == '__main__':
    main()

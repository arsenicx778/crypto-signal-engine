import os
import csv
import time
import threading
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

SIGNALS_FILE = "signals.csv"
FIELDNAMES = [
    "timestamp", "signal", "confidence",
    "entry_price", "stop_loss", "take_profit",
    "outcome", "close_price", "close_time",
    "ta_summary", "sentiment_summary",
    "history_summary", "decision_rationale",
    "overrides", "indicators"
]

def get_current_price():
    try:
        r = requests.get(
            "https://api.kraken.com/0/public/Ticker",
            params={"pair": "XBTUSD"},
            timeout=5
        )
        data = r.json()
        return float(list(data["result"].values())[0]["c"][0])
    except:
        return None

def format_indicators(filtered_indicators):
    if not filtered_indicators:
        return "N/A"
    parts = []
    skip = {"close"}
    for k, v in filtered_indicators.items():
        if k in skip:
            continue
        parts.append(f"{k.upper()}: {v}")
    return " | ".join(parts)

def save_signal(signal, overrides, filtered_indicators=None):
    file_exists = os.path.exists(SIGNALS_FILE)
    timestamp   = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    price       = get_current_price()
    indicators  = format_indicators(filtered_indicators or {})

    row = {
        "timestamp":          timestamp,
        "signal":             signal.get("signal"),
        "confidence":         signal.get("confidence"),
        "entry_price":        signal.get("entry_price"),
        "stop_loss":          signal.get("stop_loss"),
        "take_profit":        signal.get("take_profit"),
        "outcome":            "pending",
        "close_price":        None,
        "close_time":         None,
        "ta_summary":         signal["reasoning"].get("ta_summary"),
        "sentiment_summary":  signal["reasoning"].get("sentiment_summary"),
        "history_summary":    signal["reasoning"].get("history_summary"),
        "decision_rationale": signal["reasoning"].get("decision_rationale"),
        "overrides":          " | ".join(overrides) if overrides else None,
        "indicators":         indicators
    }

    with open(SIGNALS_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)

    # ── PRINT OUTPUT ──────────────────────────────────────────────
    print(f"\n{'='*55}")
    print(f"  BTC PRICE:  ${price:,.2f}" if price else "  BTC PRICE:  unavailable")
    print(f"  INDICATORS: {indicators}")
    print(f"  {'─'*51}")
    print(f"  SIGNAL:     {row['signal']}  |  CONFIDENCE: {row['confidence']}%")
    if row['signal'] == 'Buy':
        print(f"  Entry:      ${row['entry_price']:,.2f}")
        print(f"  Stop Loss:  ${row['stop_loss']:,.2f}")
        print(f"  Take Profit:${row['take_profit']:,.2f}")
    print(f"  {'─'*51}")
    print(f"  TA:         {row['ta_summary']}")
    print(f"  Sentiment:  {row['sentiment_summary']}")
    print(f"  History:    {row['history_summary']}")
    print(f"  Decision:   {row['decision_rationale']}")
    if overrides:
        print(f"  Override:   {row['overrides']}")
    print(f"{'='*55}\n")

    return row

def monitor_price(timestamp, stop_loss, take_profit):
    if not stop_loss or not take_profit:
        return

    def _monitor():
        print(f"[MONITOR] Watching | SL:${stop_loss:,.2f} TP:${take_profit:,.2f}")
        while True:
            try:
                r = requests.get(
                    "https://api.kraken.com/0/public/Ticker",
                    params={"pair": "XBTUSD"},
                    timeout=5
                )
                data  = r.json()
                price = float(list(data["result"].values())[0]["c"][0])
                print(f"[MONITOR] BTC: ${price:,.2f} | SL:${stop_loss:,.2f} TP:${take_profit:,.2f}")

                if price <= stop_loss:
                    _update_outcome(timestamp, "L", price)
                    print(f"[MONITOR] STOP LOSS HIT at ${price:,.2f} — trade marked L")
                    break
                elif price >= take_profit:
                    _update_outcome(timestamp, "W", price)
                    print(f"[MONITOR] TAKE PROFIT HIT at ${price:,.2f} — trade marked W")
                    break

                time.sleep(15)
            except Exception as e:
                print(f"[MONITOR] Error: {e}")
                time.sleep(15)

    threading.Thread(target=_monitor, daemon=True).start()

def _update_outcome(timestamp, outcome, close_price):
    rows = []
    with open(SIGNALS_FILE, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["timestamp"] == timestamp:
                row["outcome"]     = outcome
                row["close_price"] = close_price
                row["close_time"]  = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            rows.append(row)

    with open(SIGNALS_FILE, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

if __name__ == "__main__":
    test_signal = {
        "signal": "Buy",
        "confidence": 78,
        "entry_price": 84250.0,
        "stop_loss":   82000.0,
        "take_profit": 87000.0,
        "reasoning": {
            "ta_summary":         "RSI neutral MACD bullish crossover",
            "sentiment_summary":  "positive news sentiment",
            "history_summary":    "3 recent wins on momentum signals",
            "decision_rationale": "strong confluence across indicators"
        }
    }
    test_indicators = {"rsi": 58.3, "macd": 12.4, "atr": 320.0, "close": 84250.0}
    save_signal(test_signal, [], test_indicators)
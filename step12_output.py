import time
import threading
import requests
from dotenv import load_dotenv
from signal_store import append_signal_row, read_latest_signals
from time_utils import now_pacific_str

load_dotenv()
ACTIVE_MONITORS = set()

def get_current_price():
    try:
        r = requests.get(
            "https://api.kraken.com/0/public/Ticker",
            params={"pair": "ETHUSD"},
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
    timestamp   = now_pacific_str()
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
        "indicators":         indicators,
        "tp_adjustments":     0,
        "tp_adjustment_log":  None,
    }

    append_signal_row(row)

    # ── PRINT OUTPUT ──────────────────────────────────────────────
    print(f"\n{'='*55}")
    print(f"  ETH PRICE:  ${price:,.2f}" if price else "  ETH PRICE:  unavailable")
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

def _get_trade_by_timestamp(timestamp):
    rows = read_latest_signals()
    for row in reversed(rows):
        if row.get("timestamp") == timestamp:
            return row
    return None

def monitor_price(timestamp, stop_loss=None, take_profit=None):
    trade = _get_trade_by_timestamp(timestamp)
    if not trade:
        print(f"[MONITOR] No trade found for {timestamp}")
        return
    stop_loss = float(trade.get("stop_loss") or stop_loss or 0)
    take_profit = float(trade.get("take_profit") or take_profit or 0)
    if not stop_loss or not take_profit:
        return
    if timestamp in ACTIVE_MONITORS:
        return

    def _monitor():
        ACTIVE_MONITORS.add(timestamp)
        try:
            print(f"[MONITOR] Watching trade {timestamp}")
            while True:
                latest_trade = _get_trade_by_timestamp(timestamp)
                if not latest_trade:
                    print(f"[MONITOR] Trade {timestamp} no longer found")
                    break
                if latest_trade.get("outcome") != "pending":
                    print(f"[MONITOR] Trade {timestamp} already closed as {latest_trade.get('outcome')}")
                    break

                stop_loss_live = float(latest_trade.get("stop_loss") or 0)
                take_profit_live = float(latest_trade.get("take_profit") or 0)
                if not stop_loss_live or not take_profit_live:
                    print(f"[MONITOR] Trade {timestamp} missing TP/SL")
                    break

                try:
                    r = requests.get(
                        "https://api.kraken.com/0/public/Ticker",
                        params={"pair": "ETHUSD"},
                        timeout=5
                    )
                    data  = r.json()
                    price = float(list(data["result"].values())[0]["c"][0])
                    print(
                        f"[MONITOR] ETH: ${price:,.2f} | SL:${stop_loss_live:,.2f} TP:${take_profit_live:,.2f}"
                    )

                    if price <= stop_loss_live:
                        _update_outcome(timestamp, "L", price)
                        print(f"[MONITOR] STOP LOSS HIT at ${price:,.2f} — trade marked L")
                        break
                    if price >= take_profit_live:
                        _update_outcome(timestamp, "W", price)
                        print(f"[MONITOR] TAKE PROFIT HIT at ${price:,.2f} — trade marked W")
                        break
                except Exception as e:
                    print(f"[MONITOR] Error: {e}")

                time.sleep(15)
        finally:
            ACTIVE_MONITORS.discard(timestamp)

    threading.Thread(target=_monitor, daemon=True).start()

def resume_open_trade_monitor():
    rows = read_latest_signals()
    open_trades = [
        row for row in rows
        if row.get("signal") == "Buy" and row.get("outcome", "pending") == "pending"
    ]
    if not open_trades:
        return
    latest_open_trade = open_trades[-1]
    print(f"[MONITOR] Resuming open trade from {latest_open_trade['timestamp']}")
    monitor_price(latest_open_trade["timestamp"])

def _update_outcome(timestamp, outcome, close_price):
    rows = read_latest_signals()
    for row in reversed(rows):
        if row["timestamp"] == timestamp:
            row["outcome"] = outcome
            row["close_price"] = close_price
            row["close_time"] = now_pacific_str()
            append_signal_row(row)
            break

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

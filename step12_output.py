import time
import threading
import requests
from dotenv import load_dotenv
from signal_store import append_signal_row, read_latest_signals
from time_utils import now_pacific_str
from project_logger import record_trade_outcome

load_dotenv()
ACTIVE_MONITORS = set()  # stores "coin_name:timestamp" keys


def get_current_price(symbol="XETHZUSD"):
    try:
        r = requests.get(
            "https://api.kraken.com/0/public/Ticker",
            params={"pair": symbol},
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


def save_signal(signal, overrides, filtered_indicators=None,
                signals_file=None, symbol="XETHZUSD", coin_name="ETH",
                risk_per_trade=None, reward_per_trade=None,
                capital=None):
    timestamp  = now_pacific_str()
    price      = get_current_price(symbol)
    indicators = format_indicators(filtered_indicators or {})

    # Use explicitly passed amounts; fall back to signal-embedded values
    risk_amount   = risk_per_trade   if risk_per_trade   is not None else signal.get("risk_amount")
    reward_amount = reward_per_trade if reward_per_trade is not None else signal.get("reward_amount")

    row = {
        "timestamp":          timestamp,
        "signal":             signal.get("signal"),
        "direction":          signal.get("direction"),
        "confidence":         signal.get("confidence"),
        "entry_price":        signal.get("entry_price"),
        "stop_loss":          signal.get("stop_loss"),
        "take_profit":        signal.get("take_profit"),
        "risk_amount":        risk_amount,
        "reward_amount":      reward_amount,
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

    append_signal_row(row, signals_file)

    # ── PRINT OUTPUT ──────────────────────────────────────────────
    price_str = f"${price:,.4f}" if price else "unavailable"
    print(f"\n{'='*55}")
    print(f"  {coin_name}  PRICE:  {price_str}")
    print(f"  SIGNAL: {row['signal']} | CONFIDENCE: {row['confidence']}%")
    print(f"{'='*55}")
    if row['signal'] in ('Buy', 'Sell'):
        direction_label = "LONG (Buy)" if row['signal'] == 'Buy' else "SHORT (Sell)"
        _cap_str = f"${float(capital):,.2f}" if capital else "current capital"
        _risk    = float(risk_amount)   if risk_amount   else 0
        _reward  = float(reward_amount) if reward_amount else 0
        print(f"  Direction:  {direction_label}")
        print(f"  RISK:   ${_risk:.2f} (2% of {_cap_str})")
        print(f"  TARGET: ${_reward:.2f} (3% of {_cap_str})")
        print(f"  {'─'*51}")
        print(f"  Entry:      ${float(row['entry_price']):,.4f}")
        if row['signal'] == 'Buy':
            print(f"  SL:         ${float(row['stop_loss']):,.4f}  (below entry)")
            print(f"  TP:         ${float(row['take_profit']):,.4f}  (above entry)")
        else:
            print(f"  SL:         ${float(row['stop_loss']):,.4f}  (above entry — short SL)")
            print(f"  TP:         ${float(row['take_profit']):,.4f}  (below entry — short TP)")
    print(f"  {'─'*51}")
    print(f"  INDICATORS: {indicators}")
    print(f"  TA:         {row['ta_summary']}")
    print(f"  Sentiment:  {row['sentiment_summary']}")
    print(f"  History:    {row['history_summary']}")
    print(f"  Decision:   {row['decision_rationale']}")
    if overrides:
        print(f"  Override:   {row['overrides']}")
    print(f"{'='*55}\n")

    return row


def _get_trade_by_timestamp(timestamp, signals_file=None):
    rows = read_latest_signals(signals_file)
    for row in reversed(rows):
        if row.get("timestamp") == timestamp:
            return row
    return None


def monitor_price(timestamp, stop_loss=None, take_profit=None,
                  symbol="XETHZUSD", signals_file=None, coin_name="ETH",
                  direction="LONG"):
    trade = _get_trade_by_timestamp(timestamp, signals_file)
    if not trade:
        print(f"[MONITOR:{coin_name}] No trade found for {timestamp}")
        return
    stop_loss   = float(trade.get("stop_loss")   or stop_loss   or 0)
    take_profit = float(trade.get("take_profit") or take_profit or 0)
    # Infer direction from the stored direction field if available
    stored_dir = (trade.get("direction") or direction or "LONG").upper()
    is_short = stored_dir == "SHORT"
    if not stop_loss or not take_profit:
        return

    monitor_key = f"{coin_name}:{timestamp}"
    if monitor_key in ACTIVE_MONITORS:
        return

    def _monitor():
        ACTIVE_MONITORS.add(monitor_key)
        try:
            dir_label = "SHORT" if is_short else "LONG"
            print(f"[MONITOR:{coin_name}] Watching {dir_label} trade {timestamp}")
            while True:
                latest_trade = _get_trade_by_timestamp(timestamp, signals_file)
                if not latest_trade:
                    print(f"[MONITOR:{coin_name}] Trade {timestamp} no longer found")
                    break
                if latest_trade.get("outcome") != "pending":
                    print(f"[MONITOR:{coin_name}] Trade {timestamp} already closed as {latest_trade.get('outcome')}")
                    break

                sl_live = float(latest_trade.get("stop_loss")   or 0)
                tp_live = float(latest_trade.get("take_profit") or 0)
                if not sl_live or not tp_live:
                    print(f"[MONITOR:{coin_name}] Trade {timestamp} missing TP/SL")
                    break

                try:
                    r = requests.get(
                        "https://api.kraken.com/0/public/Ticker",
                        params={"pair": symbol},
                        timeout=5
                    )
                    data  = r.json()
                    price = float(list(data["result"].values())[0]["c"][0])
                    print(f"[MONITOR:{coin_name}] ${price:,.4f} | SL:${sl_live:,.4f} TP:${tp_live:,.4f} [{dir_label}]")

                    if is_short:
                        # Short: win when price falls to TP, lose when price rises to SL
                        if price <= tp_live:
                            _update_outcome(timestamp, "W", price, signals_file, coin_name)
                            print(f"[MONITOR:{coin_name}] SHORT TAKE PROFIT HIT at ${price:,.4f} — trade marked W")
                            break
                        if price >= sl_live:
                            _update_outcome(timestamp, "L", price, signals_file, coin_name)
                            print(f"[MONITOR:{coin_name}] SHORT STOP LOSS HIT at ${price:,.4f} — trade marked L")
                            break
                    else:
                        # Long: win when price rises to TP, lose when price falls to SL
                        if price <= sl_live:
                            _update_outcome(timestamp, "L", price, signals_file, coin_name)
                            print(f"[MONITOR:{coin_name}] STOP LOSS HIT at ${price:,.4f} — trade marked L")
                            break
                        if price >= tp_live:
                            _update_outcome(timestamp, "W", price, signals_file, coin_name)
                            print(f"[MONITOR:{coin_name}] TAKE PROFIT HIT at ${price:,.4f} — trade marked W")
                            break
                except Exception as e:
                    print(f"[MONITOR:{coin_name}] Error: {e}")

                time.sleep(15)
        finally:
            ACTIVE_MONITORS.discard(monitor_key)

    threading.Thread(target=_monitor, daemon=True).start()


def resume_open_trade_monitor(signals_file=None, symbol="XETHZUSD", coin_name="ETH"):
    rows = read_latest_signals(signals_file)
    open_trades = [
        row for row in rows
        if row.get("signal") in ("Buy", "Sell") and row.get("outcome", "pending") == "pending"
    ]
    if not open_trades:
        return
    for trade in open_trades:
        sig = trade.get("signal")
        direction = "SHORT" if sig == "Sell" else "LONG"
        print(f"[MONITOR:{coin_name}] Resuming open {direction} trade from {trade['timestamp']}")
        monitor_price(trade["timestamp"], symbol=symbol,
                      signals_file=signals_file, coin_name=coin_name,
                      direction=direction)


def _update_outcome(timestamp, outcome, close_price, signals_file=None, coin_name="ETH"):
    rows = read_latest_signals(signals_file)
    for row in reversed(rows):
        if row["timestamp"] == timestamp:
            row["outcome"]     = outcome
            row["close_price"] = close_price
            row["close_time"]  = now_pacific_str()
            append_signal_row(row, signals_file)
            direction = row.get("direction") or ("SHORT" if row.get("signal") == "Sell" else "LONG")
            # Use the stored amounts from the opening row — correct amounts at time of entry
            stored_risk   = row.get("risk_amount")
            stored_reward = row.get("reward_amount")
            risk_amt   = float(stored_risk)   if stored_risk   else None
            reward_amt = float(stored_reward) if stored_reward else None
            record_trade_outcome(outcome, row.get("confidence"),
                                 row.get("entry_price"), close_price,
                                 coin=coin_name, direction=direction,
                                 risk_amount=risk_amt, reward_amount=reward_amt)
            break


if __name__ == "__main__":
    test_signal = {
        "signal": "Buy",
        "confidence": 78,
        "entry_price": 1800.0,
        "stop_loss":   1780.0,
        "take_profit": 1830.0,
        "reasoning": {
            "ta_summary":         "RSI neutral MACD bullish crossover",
            "sentiment_summary":  "positive news sentiment",
            "history_summary":    "3 recent wins on momentum signals",
            "decision_rationale": "strong confluence across indicators"
        }
    }
    test_indicators = {"rsi": 58.3, "macd": 12.4, "atr": 20.0, "close": 1800.0}
    save_signal(test_signal, [], test_indicators)

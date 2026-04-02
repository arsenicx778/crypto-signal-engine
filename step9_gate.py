import os
import csv
from datetime import date

SIGNALS_FILE     = "signals.csv"
MAX_DAILY_CALLS  = 300
CAPITAL          = 1000.0
RISK_PCT         = 0.02

def count_todays_calls():
    if not os.path.exists(SIGNALS_FILE):
        return 0
    try:
        today = str(date.today())
        count = 0
        with open(SIGNALS_FILE, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("timestamp", "").startswith(today):
                    count += 1
        return count
    except:
        return 0

def get_current_capital():
    if not os.path.exists(SIGNALS_FILE):
        return CAPITAL
    try:
        capital = CAPITAL
        with open(SIGNALS_FILE, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                outcome     = row.get("outcome", "pending")
                entry       = float(row.get("entry_price") or 0)
                sl          = float(row.get("stop_loss") or 0)
                tp          = float(row.get("take_profit") or 0)
                close_price = float(row.get("close_price") or 0)

                if outcome == "W" and close_price and entry:
                    pct_gain = (close_price - entry) / entry
                    capital += capital * pct_gain
                elif outcome == "L" and close_price and entry:
                    pct_loss = (entry - close_price) / entry
                    capital -= capital * pct_loss
        return round(capital, 2)
    except:
        return CAPITAL

def get_open_trade():
    if not os.path.exists(SIGNALS_FILE):
        return None
    try:
        with open(SIGNALS_FILE, "r") as f:
            reader = csv.DictReader(f)
            rows   = list(reader)
        open_trades = [
            r for r in rows
            if r.get("signal") == "Buy" and r.get("outcome", "pending") == "pending"
        ]
        return open_trades[-1] if open_trades else None
    except:
        return None

def get_todays_stats():
    if not os.path.exists(SIGNALS_FILE):
        return {"wins": 0, "losses": 0, "pending": 0}
    try:
        today = str(date.today())
        wins = losses = pending = 0
        with open(SIGNALS_FILE, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if not row.get("timestamp", "").startswith(today):
                    continue
                outcome = row.get("outcome", "pending")
                if outcome == "W":
                    wins += 1
                elif outcome == "L":
                    losses += 1
                elif outcome == "pending" and row.get("signal") == "Buy":
                    pending += 1
        return {"wins": wins, "losses": losses, "pending": pending}
    except:
        return {"wins": 0, "losses": 0, "pending": 0}

def pre_signal_gate():
    # Cost cap check
    calls_today = count_todays_calls()
    if calls_today >= MAX_DAILY_CALLS:
        return {
            "proceed":    False,
            "reason":     f"Daily cost cap reached: {calls_today} calls today",
            "open_trade": None
        }

    # Single trade rule — block new Buy if trade already open
    open_trade = get_open_trade()
    if open_trade:
        return {
            "proceed":    False,
            "reason":     f"Trade already open since {open_trade.get('timestamp')} — waiting for outcome",
            "open_trade": open_trade
        }

    # Get capital and today stats for context
    capital = get_current_capital()
    risk_amount = round(capital * RISK_PCT, 2)
    stats   = get_todays_stats()

    print(f"[GATE] Capital: ${capital:,.2f} | Risk/trade: ${risk_amount} | Today: {stats['wins']}W {stats['losses']}L")

    return {
        "proceed":      True,
        "reason":       "All checks passed",
        "open_trade":   None,
        "capital":      capital,
        "risk_amount":  risk_amount,
        "stats":        stats
    }

if __name__ == "__main__":
    result = pre_signal_gate()
    print(result)

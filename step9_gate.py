from datetime import date
from signal_store import read_latest_signals

MAX_DAILY_CALLS  = 1500
CAPITAL          = 1000.0
RISK_PCT         = 0.02

def count_todays_calls():
    try:
        today = str(date.today())
        count = 0
        for row in read_latest_signals():
            if row.get("timestamp", "").startswith(today):
                count += 1
        return count
    except:
        return 0

def get_current_capital():
    try:
        capital = CAPITAL
        for row in read_latest_signals():
            outcome = row.get("outcome", "pending")
            entry = float(row.get("entry_price") or 0)
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

def get_open_trades():
    try:
        rows = read_latest_signals()
        return [
            r for r in rows
            if r.get("signal") == "Buy" and r.get("outcome", "pending") == "pending"
        ]
    except:
        return []

def get_open_trade():
    """Returns the most recent open trade, or None. Kept for backward compatibility."""
    trades = get_open_trades()
    return trades[-1] if trades else None

def get_todays_stats():
    try:
        today = str(date.today())
        wins = losses = pending = 0
        for row in read_latest_signals():
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

    # Max 2 concurrent trades — block if limit reached
    open_trades = get_open_trades()
    if len(open_trades) >= 2:
        return {
            "proceed":    False,
            "reason":     f"2 trades already open — waiting for an outcome before adding more",
            "open_trade": open_trades[-1]
        }

    # Get capital and today stats for context
    capital = get_current_capital()
    risk_amount = round(capital * RISK_PCT, 2)
    stats   = get_todays_stats()

    print(f"[GATE] Capital: ${capital:,.2f} | Risk/trade: ${risk_amount} | Open: {len(open_trades)}/2 | Today: {stats['wins']}W {stats['losses']}L")

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

import threading
from datetime import date
from signal_store import read_latest_signals

MAX_DAILY_CALLS  = 8000
CAPITAL          = 1000.0   # per-coin starting capital
RISK_PERCENT     = 0.02     # 2% of coin capital per trade
REWARD_PERCENT   = 0.03     # 3% of coin capital per win (1.5:1)

COIN_CSV = {
    "ETH":  "eth_signals.csv",
    "SOL":  "sol_signals.csv",
    "LINK": "link_signals.csv",
    "XRP":  "xrp_signals.csv",
}

_gate_lock = threading.Lock()
gate_state = {
    "ETH":  {"open_trades": 0, "open_longs": 0, "open_shorts": 0, "capital": 1000},
    "SOL":  {"open_trades": 0, "open_longs": 0, "open_shorts": 0, "capital": 1000},
    "LINK": {"open_trades": 0, "open_longs": 0, "open_shorts": 0, "capital": 1000},
    "XRP":  {"open_trades": 0, "open_longs": 0, "open_shorts": 0, "capital": 1000},
}


def get_risk_reward(coin_name):
    """Return (risk, reward) as 2% and 3% of the coin's current capital."""
    capital = gate_state[coin_name]["capital"]
    risk    = round(capital * RISK_PERCENT,  2)
    reward  = round(capital * REWARD_PERCENT, 2)
    return risk, reward


def count_todays_calls(signals_file=None):
    try:
        today = str(date.today())
        count = 0
        for row in read_latest_signals(signals_file):
            if row.get("timestamp", "").startswith(today):
                count += 1
        return count
    except:
        return 0


def get_current_capital(signals_file=None, capital_start=CAPITAL):
    """Replay all closed trades using their stored risk/reward amounts.
    Falls back to percentage of running capital for legacy rows without stored amounts."""
    try:
        capital = capital_start
        for row in read_latest_signals(signals_file):
            outcome = row.get("outcome", "pending")
            if outcome not in ("W", "L"):
                continue
            stored_reward = row.get("reward_amount")
            stored_risk   = row.get("risk_amount")
            if outcome == "W":
                reward = float(stored_reward) if stored_reward else round(capital * REWARD_PERCENT, 2)
                capital += reward
            else:
                risk = float(stored_risk) if stored_risk else round(capital * RISK_PERCENT, 2)
                capital -= risk
        return round(capital, 2)
    except:
        return capital_start


def get_open_trades(signals_file=None):
    """Returns all open trades (Buy + Sell) that are still pending."""
    try:
        rows = read_latest_signals(signals_file)
        return [
            r for r in rows
            if r.get("signal") in ("Buy", "Sell") and r.get("outcome", "pending") == "pending"
        ]
    except:
        return []


def get_open_longs(signals_file=None):
    try:
        rows = read_latest_signals(signals_file)
        return [
            r for r in rows
            if r.get("signal") == "Buy" and r.get("outcome", "pending") == "pending"
        ]
    except:
        return []


def get_open_shorts(signals_file=None):
    try:
        rows = read_latest_signals(signals_file)
        return [
            r for r in rows
            if r.get("signal") == "Sell" and r.get("outcome", "pending") == "pending"
        ]
    except:
        return []


def get_open_trade(signals_file=None):
    """Returns the most recent open trade, or None. Kept for backward compatibility."""
    trades = get_open_trades(signals_file)
    return trades[-1] if trades else None


def get_todays_stats(signals_file=None):
    try:
        today = str(date.today())
        wins = losses = pending_long = pending_short = 0
        for row in read_latest_signals(signals_file):
            if not row.get("timestamp", "").startswith(today):
                continue
            outcome = row.get("outcome", "pending")
            sig = row.get("signal")
            if outcome == "W":
                wins += 1
            elif outcome == "L":
                losses += 1
            elif outcome == "pending" and sig == "Buy":
                pending_long += 1
            elif outcome == "pending" and sig == "Sell":
                pending_short += 1
        return {"wins": wins, "losses": losses,
                "pending": pending_long + pending_short,
                "pending_long": pending_long, "pending_short": pending_short}
    except:
        return {"wins": 0, "losses": 0, "pending": 0, "pending_long": 0, "pending_short": 0}


def _build_display_line():
    """Read all 4 coin CSVs and return a combined status string."""
    parts = []
    for name in ["ETH", "SOL", "LINK", "XRP"]:
        s = gate_state[name]
        parts.append(f"{name}: ${s['capital']:,.0f} | L:{s['open_longs']} S:{s['open_shorts']}")
    return " | ".join(parts)


def is_fully_blocked(signals_file=None):
    """Cheap check: returns (blocked: bool, open_longs: int, open_shorts: int).
    Called before any Haiku steps — no capital calc, no stats, no API calls."""
    open_longs  = get_open_longs(signals_file)
    open_shorts = get_open_shorts(signals_file)
    total = len(open_longs) + len(open_shorts)
    return total >= 2, len(open_longs), len(open_shorts)


def pre_signal_gate(signals_file=None, coin_name="ETH", capital_start=CAPITAL):
    # Cost cap check
    calls_today = count_todays_calls(signals_file)
    if calls_today >= MAX_DAILY_CALLS:
        return {
            "proceed":    False,
            "reason":     f"Daily cost cap reached: {calls_today} calls today",
            "open_trade": None,
        }

    open_longs  = get_open_longs(signals_file)
    open_shorts = get_open_shorts(signals_file)
    open_trades = open_longs + open_shorts
    capital     = get_current_capital(signals_file, capital_start)

    # Update shared gate_state atomically and print combined status
    with _gate_lock:
        gate_state[coin_name]["open_trades"]  = len(open_trades)
        gate_state[coin_name]["open_longs"]   = len(open_longs)
        gate_state[coin_name]["open_shorts"]  = len(open_shorts)
        gate_state[coin_name]["capital"]      = capital
        print(f"[GATE] {_build_display_line()}")

    # Max 2 concurrent positions per coin (1 long + 1 short, or 2 longs, or 2 shorts)
    if len(open_trades) >= 2:
        return {
            "proceed":     False,
            "reason":      f"2 {coin_name} positions already open (L:{len(open_longs)} S:{len(open_shorts)}) — waiting for outcome",
            "open_trade":  open_trades[-1],
            "open_longs":  len(open_longs),
            "open_shorts": len(open_shorts),
        }

    stats = get_todays_stats(signals_file)

    # Percentage-based risk/reward from current capital via get_risk_reward
    risk_amount, reward_amount = get_risk_reward(coin_name)

    return {
        "proceed":       True,
        "reason":        "All checks passed",
        "open_trade":    None,
        "open_longs":    len(open_longs),
        "open_shorts":   len(open_shorts),
        "capital":       capital,
        "risk_amount":   risk_amount,
        "reward_amount": reward_amount,
        "stats":         stats,
    }


if __name__ == "__main__":
    result = pre_signal_gate()
    print(result)

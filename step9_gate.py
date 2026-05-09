import threading
from datetime import date
from trade_store import get_trade_store
from config import RISK_PERCENT, REWARD_PERCENT

MAX_DAILY_CALLS  = 8000
CAPITAL          = 1000.0   # per-coin starting capital

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


def count_todays_calls(coin_name):
    try:
        store = get_trade_store()
        today = str(date.today())
        return store.count_todays_signals(coin_name, today)
    except:
        return 0


def get_current_capital(coin_name, capital_start=CAPITAL):
    """Replay all closed trades from SQLite to compute current capital."""
    try:
        store = get_trade_store()
        return store.get_current_capital(coin_name, capital_start)
    except:
        return capital_start


def get_open_trades(coin_name):
    """Returns all open trades (Buy + Sell) that are still pending."""
    try:
        store = get_trade_store()
        return [
            t for t in store.get_pending_trades(coin=coin_name)
            if t.get("signal") in ("Buy", "Sell")
        ]
    except:
        return []


def get_open_longs(coin_name):
    try:
        store = get_trade_store()
        return [
            t for t in store.get_pending_trades(coin=coin_name)
            if t.get("signal") == "Buy"
        ]
    except:
        return []


def get_open_shorts(coin_name):
    try:
        store = get_trade_store()
        return [
            t for t in store.get_pending_trades(coin=coin_name)
            if t.get("signal") == "Sell"
        ]
    except:
        return []


def get_open_trade(coin_name):
    """Returns the most recent open trade, or None. Kept for backward compatibility."""
    trades = get_open_trades(coin_name)
    return trades[-1] if trades else None


def get_todays_stats(coin_name):
    try:
        today = str(date.today())
        store = get_trade_store()
        wins = losses = pending_long = pending_short = 0
        for trade in store.get_all_trades(coin=coin_name):
            ts = trade.get("timestamp", "")
            if not ts.startswith(today):
                continue
            state = trade.get("state", "")
            sig   = trade.get("signal")
            outcome = trade.get("outcome")
            if state == "CLOSED" and outcome == "W":
                wins += 1
            elif state == "CLOSED" and outcome == "L":
                losses += 1
            elif state == "PENDING" and sig == "Buy":
                pending_long += 1
            elif state == "PENDING" and sig == "Sell":
                pending_short += 1
        return {"wins": wins, "losses": losses,
                "pending": pending_long + pending_short,
                "pending_long": pending_long, "pending_short": pending_short}
    except:
        return {"wins": 0, "losses": 0, "pending": 0, "pending_long": 0, "pending_short": 0}


def _build_display_line():
    parts = []
    for name in ["ETH", "SOL", "LINK", "XRP"]:
        s = gate_state[name]
        parts.append(f"{name}: ${s['capital']:,.0f} | L:{s['open_longs']} S:{s['open_shorts']}")
    return " | ".join(parts)


def is_fully_blocked(coin_name):
    """Cheap check: returns (blocked: bool, open_longs: int, open_shorts: int).
    Called before any Haiku steps — no capital calc, no stats, no API calls."""
    open_longs  = get_open_longs(coin_name)
    open_shorts = get_open_shorts(coin_name)
    total = len(open_longs) + len(open_shorts)
    return total >= 2, len(open_longs), len(open_shorts)


def pre_signal_gate(coin_name="ETH", capital_start=CAPITAL, **kwargs):
    # Accept and ignore legacy signals_file kwarg for compatibility
    # Cost cap check
    calls_today = count_todays_calls(coin_name)
    if calls_today >= MAX_DAILY_CALLS:
        return {
            "proceed":    False,
            "reason":     f"Daily cost cap reached: {calls_today} calls today",
            "open_trade": None,
        }

    open_longs  = get_open_longs(coin_name)
    open_shorts = get_open_shorts(coin_name)
    open_trades = open_longs + open_shorts
    capital     = get_current_capital(coin_name, capital_start)

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

    stats = get_todays_stats(coin_name)

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
    result = pre_signal_gate("ETH")
    print(result)

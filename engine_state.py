import json
import os
import tempfile

from time_utils import now_pacific_str
from trade_store import get_trade_store

STATE_FILE = "engine_state.json"
COINS = ["ETH", "SOL", "XRP", "LINK"]
DEFAULT_CAPITAL = 1000.0


def _default_coin_state():
    return {
        "capital": DEFAULT_CAPITAL,
        "open_longs": 0,
        "open_shorts": 0,
        "last_learn_cycle": 0,
        "consecutive_losses": 0,
        "last_signal_direction": "",
        "last_signal_time": "",
    }


def _default_state():
    return {
        "last_updated": "",
        "cycle_counter": 0,
        "coins": {coin: _default_coin_state() for coin in COINS},
    }


def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return _default_state()
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
        # Ensure all coins have entries with all expected keys
        if "coins" not in state:
            state["coins"] = {}
        for coin in COINS:
            if coin not in state["coins"]:
                state["coins"][coin] = _default_coin_state()
            else:
                defaults = _default_coin_state()
                for key, val in defaults.items():
                    state["coins"][coin].setdefault(key, val)
        state.setdefault("cycle_counter", 0)
        state.setdefault("last_updated", "")
        return state
    except (json.JSONDecodeError, OSError):
        return _default_state()


def save_state(state: dict):
    state["last_updated"] = now_pacific_str()
    dir_name = os.path.dirname(os.path.abspath(STATE_FILE)) or "."
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=dir_name, delete=False, suffix=".tmp"
        ) as tmp:
            json.dump(state, tmp, indent=2)
            tmp_path = tmp.name
        os.replace(tmp_path, STATE_FILE)
    except OSError:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def reconcile_state_with_db(state: dict) -> dict:
    """Reconcile engine state from SQLite (replaces reconcile_state_with_csv)."""
    store = get_trade_store()

    for coin in COINS:
        coin_state = state.setdefault("coins", {})
        if coin not in coin_state:
            coin_state[coin] = _default_coin_state()
        cs = coin_state[coin]

        try:
            completed_rows = store.get_completed_trades(coin=coin)
        except Exception:
            continue

        # Recalculate capital from completed trades
        capital = DEFAULT_CAPITAL
        completed = []
        for trade in completed_rows:
            outcome = str(trade.get("outcome", "")).strip()
            try:
                risk   = float(trade.get("risk_amount",   0) or 0)
                reward = float(trade.get("reward_amount", 0) or 0)
            except (ValueError, TypeError):
                continue
            if outcome == "W":
                capital += reward
                completed.append("W")
            elif outcome == "L":
                capital -= risk
                completed.append("L")

        cs["capital"] = max(0.0, capital)

        # Count open trades by direction
        open_longs = open_shorts = 0
        try:
            pending = store.get_pending_trades(coin=coin)
        except Exception:
            pending = []
        for trade in pending:
            direction = str(trade.get("direction", "")).strip().upper()
            if direction == "LONG":
                open_longs += 1
            elif direction == "SHORT":
                open_shorts += 1

        cs["open_longs"]  = open_longs
        cs["open_shorts"] = open_shorts

        # Consecutive losses from tail of completed trades
        consecutive_losses = 0
        for result in reversed(completed):
            if result == "L":
                consecutive_losses += 1
            else:
                break
        cs["consecutive_losses"] = consecutive_losses

    return state


def reconcile_state_with_csv(state: dict, coin_csv_map: dict = None) -> dict:
    """Backward-compatible alias — now reads from SQLite."""
    return reconcile_state_with_db(state)


def get_coin_state(state: dict, coin: str) -> dict:
    return state.get("coins", {}).get(coin, _default_coin_state())


def update_coin_capital(
    state: dict, coin: str, outcome: str, risk: float, reward: float
) -> dict:
    if "coins" not in state:
        state["coins"] = {}
    if coin not in state["coins"]:
        state["coins"][coin] = _default_coin_state()

    cs = state["coins"][coin]
    if outcome == "W":
        cs["capital"] = max(0.0, cs["capital"] + reward)
        cs["consecutive_losses"] = 0
    elif outcome == "L":
        cs["capital"] = max(0.0, cs["capital"] - risk)
        cs["consecutive_losses"] = cs.get("consecutive_losses", 0) + 1

    return state


def increment_cycle(state: dict) -> dict:
    state["cycle_counter"] = state.get("cycle_counter", 0) + 1
    return state

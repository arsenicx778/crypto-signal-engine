import os
import json
import threading
from datetime import date
from trade_store import get_trade_store
from config import (
    RISK_PERCENT, REWARD_PERCENT, ENABLE_SHORTS,
    SCALP_RSI_LONG_MAX, SCALP_RSI_SHORT_MIN,
    MIN_DI_GAP, MIN_BB_WIDTH, LONG_ADX_MIN, LONG_ADX_MAX,
    ATR_MULTIPLIER_STOP, ATR_MULTIPLIER_TP,
    PER_COIN_LIVE_CONFIG, CONFIDENCE_THRESHOLD,
)

MAX_DAILY_CALLS  = 8000
CAPITAL          = 1000.0   # per-coin starting capital

_gate_lock = threading.Lock()
gate_state = {
    "ETH":  {"open_trades": 0, "open_longs": 0, "open_shorts": 0, "capital": 1000},
    "SOL":  {"open_trades": 0, "open_longs": 0, "open_shorts": 0, "capital": 1000},
    "LINK": {"open_trades": 0, "open_longs": 0, "open_shorts": 0, "capital": 1000},
    "XRP":  {"open_trades": 0, "open_longs": 0, "open_shorts": 0, "capital": 1000},
}

# Capital cache — invalidated when a trade closes (via invalidate_capital_cache)
_capital_cache: dict[str, float] = {}

def invalidate_capital_cache(coin: str):
    """Call this whenever a trade closes so the next cycle replays fresh capital."""
    _capital_cache.pop(coin, None)


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
    """Return cached capital, replaying from DB only when cache is cold."""
    if coin_name in _capital_cache:
        return _capital_cache[coin_name]
    try:
        store = get_trade_store()
        capital = store.get_current_capital(coin_name, capital_start)
    except:
        capital = capital_start
    _capital_cache[coin_name] = capital
    return capital


def _get_pending_trades(coin_name):
    """Single DB fetch of all PENDING trades for this coin."""
    try:
        return get_trade_store().get_pending_trades(coin=coin_name)
    except:
        return []


def get_open_trades(coin_name):
    return [t for t in _get_pending_trades(coin_name) if t.get("signal") in ("Buy", "Sell")]


def get_open_longs(coin_name):
    return [t for t in _get_pending_trades(coin_name) if t.get("signal") == "Buy"]


def get_open_shorts(coin_name):
    return [t for t in _get_pending_trades(coin_name) if t.get("signal") == "Sell"]


def get_open_trade(coin_name):
    trades = get_open_trades(coin_name)
    return trades[-1] if trades else None


def get_todays_stats(coin_name):
    try:
        return get_trade_store().get_todays_stats_db(coin_name, str(date.today()))
    except:
        return {"wins": 0, "losses": 0, "pending": 0, "pending_long": 0, "pending_short": 0}


def _build_display_line():
    parts = []
    for name in ["ETH", "SOL", "LINK", "XRP"]:
        s = gate_state[name]
        parts.append(f"{name}: ${s['capital']:,.0f} | L:{s['open_longs']} S:{s['open_shorts']}")
    return " | ".join(parts)


def compute_atr_sizing(all_indicators: dict, coin_name: str = "ETH") -> dict | None:
    """
    Compute ATR-based SL/TP distances before the brain call.
    Returns a sizing dict or None if ATR or close price is unavailable.
    """
    atr = None
    close = None
    for k, v in (all_indicators or {}).items():
        ku = k.strip().upper()
        if ku == "ATR":
            try:
                atr = float(v)
            except (TypeError, ValueError):
                pass
        elif ku == "CLOSE":
            try:
                close = float(v)
            except (TypeError, ValueError):
                pass

    if not atr or not close:
        return None

    coin_cfg = PER_COIN_LIVE_CONFIG.get(coin_name, {})
    sl_mult  = coin_cfg.get("ATR_SL_MULTIPLIER", ATR_MULTIPLIER_STOP)
    tp_mult  = coin_cfg.get("ATR_TP_MULTIPLIER", ATR_MULTIPLIER_TP)

    sl_dist = round(atr * sl_mult, 4)
    tp_dist = round(atr * tp_mult, 4)
    rr      = round(tp_dist / sl_dist, 3) if sl_dist else 0.0
    breakeven_wr = round(100.0 / (1.0 + rr), 1) if rr else 50.0

    return {
        "entry":        round(close, 4),
        "long_sl":      round(close - sl_dist, 4),
        "long_tp":      round(close + tp_dist, 4),
        "short_sl":     round(close + sl_dist, 4),
        "short_tp":     round(close - tp_dist, 4),
        "atr":          atr,
        "sl_dist":      sl_dist,
        "tp_dist":      tp_dist,
        "sl_mult":      sl_mult,
        "tp_mult":      tp_mult,
        "rr":           rr,
        "breakeven_wr": breakeven_wr,
    }


def technical_hard_gate(all_indicators: dict, coin_name: str) -> dict:
    """
    Deterministic pre-LLM technical filter. Runs before any model calls.
    Returns {'proceed': bool, 'reason': str}.
    Checks DI gap, ADX, BB_WIDTH, RSI extremes, candle confirmation,
    and STRONG_AVOID learning patterns.
    """
    ind      = all_indicators or {}
    rsi      = ind.get("rsi")
    di_plus  = ind.get("di_plus")
    di_minus = ind.get("di_minus")
    adx      = ind.get("adx")
    bb_width = ind.get("bb_width")
    close    = ind.get("close")
    prev_close = ind.get("prev_close")
    macd     = ind.get("macd")

    # DI gap
    if di_plus is not None and di_minus is not None:
        gap = abs(float(di_plus) - float(di_minus))
        if gap < MIN_DI_GAP:
            return {"proceed": False, "reason": f"DI gap {gap:.1f} < {MIN_DI_GAP} minimum"}

    # ADX window
    if adx is not None:
        adx_f    = float(adx)
        coin_cfg = PER_COIN_LIVE_CONFIG.get(coin_name, {})
        adx_min  = coin_cfg.get("ADX_MIN", LONG_ADX_MIN)
        adx_max  = coin_cfg.get("ADX_MAX", LONG_ADX_MAX)
        if adx_f < adx_min:
            return {"proceed": False, "reason": f"ADX {adx_f:.1f} below {adx_min} minimum"}
        if adx_f > adx_max:
            return {"proceed": False, "reason": f"ADX {adx_f:.1f} above {adx_max} maximum"}

    # BB width squeeze
    if bb_width is not None and float(bb_width) < MIN_BB_WIDTH:
        return {"proceed": False, "reason": f"BB_WIDTH {float(bb_width):.4f} < {MIN_BB_WIDTH} (squeeze)"}

    # RSI + DI: check whether both directions are simultaneously blocked
    if rsi is not None and di_plus is not None and di_minus is not None:
        rsi_f = float(rsi)
        dip   = float(di_plus)
        dim   = float(di_minus)
        long_blocked  = (rsi_f > SCALP_RSI_LONG_MAX) or (dim > dip)
        short_blocked = (rsi_f < SCALP_RSI_SHORT_MIN) or (dip > dim) or (not ENABLE_SHORTS)
        if long_blocked and short_blocked:
            return {"proceed": False,
                    "reason": f"RSI {rsi_f:.1f} and DI state block all valid directions"}

    # Candle confirmation (per-coin opt-in)
    coin_cfg = PER_COIN_LIVE_CONFIG.get(coin_name, {})
    if coin_cfg.get("REQUIRE_CANDLE_CONFIRMATION", False):
        if close is not None and prev_close is not None:
            if float(close) == float(prev_close):
                return {"proceed": False, "reason": "flat candle — no directional confirmation"}

    # STRONG_AVOID shortcut: if any pattern's max-possible confidence can't survive
    # the learning penalty, skip the brain call entirely (saves a Sonnet call)
    lpath = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         f"{coin_name.lower()}_learning.json")
    if (os.path.exists(lpath) and rsi is not None and di_plus is not None
            and di_minus is not None and adx is not None and macd is not None):
        try:
            with open(lpath) as f:
                ldata = json.loads(f.read().strip() or "{}")
            wp = ldata.get("weighted_patterns", [])
            if wp:
                rsi_f  = float(rsi)
                dip_f  = float(di_plus)
                dim_f  = float(di_minus)
                adx_f  = float(adx)
                macd_f = float(macd)
                dirs   = ["LONG"] if not ENABLE_SHORTS else ["LONG", "SHORT"]
                for direction in dirs:
                    if direction == "LONG"  and dim_f > dip_f: continue
                    if direction == "SHORT" and rsi_f < 35:    continue
                    rsi_tag  = "rsi_low"  if rsi_f < 40 else ("rsi_high" if rsi_f > 65 else "rsi_mid")
                    gap_tag  = "gap_strong" if abs(dip_f - dim_f) >= 15 else "gap_weak"
                    adx_tag  = "adx_strong" if adx_f >= 27 else "adx_weak"
                    macd_tag = "macd_pos"   if macd_f >= 0  else "macd_neg"
                    key      = f"{direction}|{rsi_tag}|{gap_tag}|{adx_tag}|{macd_tag}"
                    matched  = next((p for p in wp if p.get("key") == key), None)
                    if matched and matched.get("penalty_tag") == "STRONG_AVOID":
                        raw_count = matched.get("raw_count", 0)
                        penalty   = 25 if raw_count >= 5 else 12.5
                        # 84 is the practical brain confidence ceiling
                        if 84 - penalty < CONFIDENCE_THRESHOLD:
                            return {"proceed": False,
                                    "reason": f"STRONG_AVOID pattern {key} — max adjusted conf "
                                              f"{84-penalty:.0f} < {CONFIDENCE_THRESHOLD} threshold"}
        except Exception:
            pass  # unreadable learning file — let the brain decide

    return {"proceed": True, "reason": "All technical checks passed"}


def is_fully_blocked(coin_name):
    """Cheap check: returns (blocked: bool, open_longs: int, open_shorts: int).
    Called before any Haiku steps — single pending query, no capital replay."""
    pending = _get_pending_trades(coin_name)
    open_longs  = [t for t in pending if t.get("signal") == "Buy"]
    open_shorts = [t for t in pending if t.get("signal") == "Sell"]
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

    # Single pending fetch — split into longs/shorts from one query
    pending     = _get_pending_trades(coin_name)
    open_longs  = [t for t in pending if t.get("signal") == "Buy"]
    open_shorts = [t for t in pending if t.get("signal") == "Sell"]
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

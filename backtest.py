"""
backtest.py

Variant-testing backtest engine for the scalping strategy.
Tests combinations of parameters to find optimal configuration per coin.

Two-phase approach:
  Phase 1: Test each variant axis independently (13 runs per coin, 52 total)
  Phase 2: Combined best-from-each-axis run (4 runs total)

Time stop modes:
  A_SYMMETRIC:  at TIME_STOP_MINUTES close at market regardless of P&L
  B_PROFIT_ONLY: at TIME_STOP_MINUTES close only if profitable; else hold to SL/TP (hard cap 24h)
  C_NONE:        no time stop; run to SL/TP (hard cap 24h)

Primary backtest timeframe: 5-minute candles
HTF trend (1h, daily) used for directional filter only.
"""

import pandas as pd
import numpy as np
from typing import Optional
from config import (
    VARIANT_CONFIG,
    TIME_STOP_MINUTES,
    MAX_HOLD_HOURS_NO_STOP,
    MIN_DI_GAP,
    SCALP_RSI_LONG_MAX,
    SCALP_RSI_SHORT_MIN,
    MIN_BB_WIDTH,
    ENABLE_SHORTS,
)

# ── Defaults (index 0 of each variant axis = the default) ────────────────────
_DEFAULTS = {
    "time_stop_mode":              "B_PROFIT_ONLY",
    "atr_tp_multiplier":           1.0,
    "atr_sl_multiplier":           0.83,
    "adx_range":                   (18, 50),
    "require_candle_confirmation": True,
}

# Primary backtest resolution: 30-minute candles
_CANDLE_MINUTES    = 30
_MAX_HOLD_CANDLES  = int(MAX_HOLD_HOURS_NO_STOP * 60 / _CANDLE_MINUTES)  # 24h = 48 candles
_TIME_STOP_CANDLES = int(TIME_STOP_MINUTES / _CANDLE_MINUTES)             # 60min = 2 candles


# ── Entry logic ───────────────────────────────────────────────────────────────

def _direction(row: pd.Series, adx_min: float, adx_max: float) -> Optional[str]:
    di_plus  = row.get("di_plus")
    di_minus = row.get("di_minus")
    adx      = row.get("adx")
    trend_1h = row.get("htf_trend_1h", "NEUTRAL")

    if pd.isna(di_plus) or pd.isna(di_minus) or pd.isna(adx):
        return None
    if adx < adx_min or adx > adx_max:
        return None

    gap = di_plus - di_minus
    if gap > MIN_DI_GAP and trend_1h in ("BULLISH", "NEUTRAL"):
        return "LONG"
    if ENABLE_SHORTS and -gap > MIN_DI_GAP and trend_1h in ("BEARISH", "NEUTRAL"):
        return "SHORT"
    return None


def _candle_confirms(row: pd.Series, direction: str) -> bool:
    """Bullish candle for LONG (close > open), bearish for SHORT."""
    o = row.get("open")
    c = row.get("close")
    if pd.isna(o) or pd.isna(c):
        return True  # can't determine — allow
    if direction == "LONG":
        return float(c) >= float(o)
    return float(c) <= float(o)


def _passes_filters(row: pd.Series, direction: str,
                    require_confirmation: bool) -> bool:
    rsi      = row.get("rsi")
    bb_width = row.get("bb_width")
    trend_d  = row.get("htf_trend_daily", "NEUTRAL")

    if pd.isna(rsi):
        return False

    if direction == "LONG":
        if trend_d == "BEARISH":
            return False
        if float(rsi) > SCALP_RSI_LONG_MAX:
            return False

    if direction == "SHORT":
        if trend_d == "BULLISH":
            return False
        if float(rsi) < SCALP_RSI_SHORT_MIN:
            return False

    if bb_width is not None and not pd.isna(bb_width):
        if float(bb_width) < MIN_BB_WIDTH:
            return False

    if require_confirmation and not _candle_confirms(row, direction):
        return False

    return True


# ── Trade resolution ──────────────────────────────────────────────────────────

def _resolve_trade(entry: float, sl: float, tp: float,
                   direction: str,
                   future: pd.DataFrame,
                   time_stop_mode: str) -> tuple:
    """
    Scan forward candle by candle.
    Returns (outcome, candles_held, exit_price, exit_reason).
    exit_reason: 'TP', 'SL', 'TIME_STOP', 'MAX_HOLD'
    """
    time_stop_candle = _TIME_STOP_CANDLES
    max_candle       = min(_MAX_HOLD_CANDLES, len(future))

    for i in range(max_candle):
        frow = future.iloc[i]
        h = float(frow["high"])
        l = float(frow["low"])

        # Check SL/TP on this candle
        if direction == "LONG":
            if l <= sl:
                return "L", i + 1, sl, "SL"
            if h >= tp:
                return "W", i + 1, tp, "TP"
        else:
            if h >= sl:
                return "L", i + 1, sl, "SL"
            if l <= tp:
                return "W", i + 1, tp, "TP"

        # Time stop check at the appointed candle
        if i + 1 == time_stop_candle:
            close_price = float(frow["close"])
            if time_stop_mode == "A_SYMMETRIC":
                # Close at market regardless of direction
                if direction == "LONG":
                    outcome = "W" if close_price > entry else "L"
                else:
                    outcome = "W" if close_price < entry else "L"
                return outcome, i + 1, close_price, "TIME_STOP"

            elif time_stop_mode == "B_PROFIT_ONLY":
                # Close only if in profit
                in_profit = (
                    (direction == "LONG"  and close_price > entry) or
                    (direction == "SHORT" and close_price < entry)
                )
                if in_profit:
                    return "W", i + 1, close_price, "TIME_STOP"
                # else continue holding until SL/TP or MAX_HOLD

            # C_NONE: no time stop action — just continue

    # Hit max hold — close at last candle's close
    exit_price = float(future.iloc[max_candle - 1]["close"])
    if direction == "LONG":
        outcome = "W" if exit_price > entry else "L"
    else:
        outcome = "W" if exit_price < entry else "L"
    return outcome, max_candle, exit_price, "MAX_HOLD"


# ── Single variant run ────────────────────────────────────────────────────────

def run_backtest_variant(coin_name: str, df: pd.DataFrame,
                         time_stop_mode: str,
                         atr_tp_multiplier: float,
                         atr_sl_multiplier: float,
                         adx_range: tuple,
                         require_candle_confirmation: bool) -> pd.DataFrame:
    """
    Run a single variant configuration on 5-minute candles.

    df must have: timestamp, open, high, low, close, volume,
                  rsi, adx, di_plus, di_minus, atr, macd, bb_width,
                  htf_trend_daily, htf_trend_1h
    """
    df = df.reset_index(drop=True)
    adx_min, adx_max = adx_range

    needed = ["rsi", "adx", "di_plus", "di_minus", "atr"]
    missing = [c for c in needed if c not in df.columns]
    if missing:
        print(f"[BACKTEST:{coin_name}] Missing columns: {missing}. Aborting.")
        return pd.DataFrame()

    valid_mask  = df[needed].notna().all(axis=1)
    if not valid_mask.any():
        print(f"[BACKTEST:{coin_name}] All indicator rows are NaN.")
        return pd.DataFrame()
    valid_start = int(valid_mask.idxmax())

    trades = []
    in_trade = False
    trade_end_idx = -1

    for idx in range(valid_start, len(df) - 1):
        if in_trade and idx <= trade_end_idx:
            continue
        in_trade = False

        row = df.iloc[idx]
        if row[needed].isna().any():
            continue

        direction = _direction(row, adx_min, adx_max)
        if direction is None:
            continue

        if not _passes_filters(row, direction, require_candle_confirmation):
            continue

        atr = float(row["atr"])
        if atr <= 0:
            continue

        entry    = float(row["close"])
        sl_dist  = atr * atr_sl_multiplier
        tp_dist  = sl_dist * (atr_tp_multiplier / atr_sl_multiplier)

        # TP distance is sl_dist * (tp_mult / sl_mult) so the ratio is preserved
        # but each multiplier independently scales off ATR:
        # actual SL distance = atr * atr_sl_multiplier
        # actual TP distance = atr * atr_tp_multiplier
        tp_dist = atr * atr_tp_multiplier

        if direction == "LONG":
            sl = entry - sl_dist
            tp = entry + tp_dist
        else:
            sl = entry + sl_dist
            tp = entry - tp_dist

        future = df.iloc[idx + 1: idx + 1 + _MAX_HOLD_CANDLES]
        if future.empty:
            break

        outcome, candles_held, exit_price, exit_reason = _resolve_trade(
            entry, sl, tp, direction, future, time_stop_mode
        )

        duration_minutes = candles_held * _CANDLE_MINUTES

        if direction == "LONG":
            pnl_pct = (exit_price - entry) / entry * 100
        else:
            pnl_pct = (entry - exit_price) / entry * 100

        trades.append({
            "timestamp":        row["timestamp"],
            "direction":        direction,
            "entry":            round(entry, 6),
            "sl":               round(sl, 6),
            "tp":               round(tp, 6),
            "exit_price":       round(exit_price, 6),
            "outcome":          outcome,
            "exit_reason":      exit_reason,
            "candles_held":     candles_held,
            "duration_minutes": duration_minutes,
            "pnl_pct":          round(pnl_pct, 4),
            "rsi_at_entry":     round(float(row["rsi"]), 2),
            "adx_at_entry":     round(float(row["adx"]), 2),
            "di_plus":          round(float(row["di_plus"]), 2),
            "di_minus":         round(float(row["di_minus"]), 2),
            "macd":             round(float(row.get("macd", 0) or 0), 4),
            "htf_daily":        row.get("htf_trend_daily", "NEUTRAL"),
            "htf_1h":           row.get("htf_trend_1h", "NEUTRAL"),
        })

        in_trade = True
        trade_end_idx = idx + candles_held

    return pd.DataFrame(trades)


# ── Metrics ───────────────────────────────────────────────────────────────────

def compute_metrics(trades_df: pd.DataFrame, total_candles: int = 0) -> dict:
    """Full metrics dict for one variant run."""
    if trades_df.empty:
        return {
            "trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
            "max_drawdown_pct": 0.0, "avg_duration_minutes": 0.0,
            "tp_hits": 0, "sl_hits": 0, "time_stops": 0, "max_hold_exits": 0,
            "concurrency_util_pct": 0.0, "avg_daily_trades": 0.0,
        }

    wins   = trades_df[trades_df["outcome"] == "W"]
    losses = trades_df[trades_df["outcome"] == "L"]
    total  = len(trades_df)

    win_rate = len(wins) / total * 100

    gross_profit = wins["pnl_pct"].sum()   if not wins.empty   else 0.0
    gross_loss   = abs(losses["pnl_pct"].sum()) if not losses.empty else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    cum_pnl     = trades_df["pnl_pct"].cumsum()
    rolling_max = cum_pnl.cummax()
    max_dd      = (cum_pnl - rolling_max).min()

    avg_duration = trades_df["duration_minutes"].mean()

    tp_hits    = (trades_df["exit_reason"] == "TP").sum()
    sl_hits    = (trades_df["exit_reason"] == "SL").sum()
    time_stops = (trades_df["exit_reason"] == "TIME_STOP").sum()
    max_holds  = (trades_df["exit_reason"] == "MAX_HOLD").sum()

    # Concurrency utilisation: fraction of candles where a trade was open
    concurrency_util = 0.0
    if total_candles > 0 and "candles_held" in trades_df.columns:
        total_held = trades_df["candles_held"].sum()
        concurrency_util = min(total_held / total_candles * 100, 100.0)

    # Average daily trades based on actual candle resolution
    days_covered = total_candles * _CANDLE_MINUTES / 60 / 24 if total_candles > 0 else 1.0
    avg_daily = total / days_covered if days_covered > 0 else 0.0

    return {
        "trades":                total,
        "win_rate":              round(win_rate, 1),
        "profit_factor":         round(profit_factor, 2),
        "max_drawdown_pct":      round(max_dd, 2),
        "avg_duration_minutes":  round(avg_duration, 1),
        "tp_hits":               int(tp_hits),
        "sl_hits":               int(sl_hits),
        "time_stops":            int(time_stops),
        "max_hold_exits":        int(max_holds),
        "concurrency_util_pct":  round(concurrency_util, 1),
        "avg_daily_trades":      round(avg_daily, 2),
    }


# ── Variant test runner ───────────────────────────────────────────────────────

def _run_one(coin: str, df: pd.DataFrame, params: dict) -> dict:
    trades = run_backtest_variant(coin, df, **params)
    metrics = compute_metrics(trades, total_candles=len(df))
    return {"params": params, "metrics": metrics, "trades": trades}


def run_phase1(coin_name: str, df: pd.DataFrame) -> dict:
    """
    Phase 1: test each axis independently holding others at default.
    Returns dict keyed by axis name, each containing list of result dicts.
    """
    results: dict = {}

    for axis, values in VARIANT_CONFIG.items():
        axis_results = []
        for val in values:
            params = dict(_DEFAULTS)
            params[axis] = val
            r = _run_one(coin_name, df, params)
            r["variant_value"] = val
            axis_results.append(r)
        results[axis] = axis_results

    return results


def pick_winners(phase1_results: dict) -> dict:
    """
    For each axis pick the value with the highest profit factor.
    Tie-break: win rate.
    Returns dict axis -> winning_value.
    """
    winners = {}
    for axis, axis_results in phase1_results.items():
        best = max(
            axis_results,
            key=lambda r: (
                r["metrics"]["profit_factor"],
                r["metrics"]["win_rate"],
            )
        )
        winners[axis] = best["variant_value"]
    return winners


def run_phase2(coin_name: str, df: pd.DataFrame, winners: dict) -> dict:
    """Phase 2: single run with all winning values combined."""
    params = {
        "time_stop_mode":              winners["time_stop_mode"],
        "atr_tp_multiplier":           winners["atr_tp_multiplier"],
        "atr_sl_multiplier":           winners["atr_sl_multiplier"],
        "adx_range":                   winners["adx_range"],
        "require_candle_confirmation": winners["require_candle_confirmation"],
    }
    return _run_one(coin_name, df, params)


# ── Report formatting ─────────────────────────────────────────────────────────

def _fmt_val(v) -> str:
    if isinstance(v, tuple):
        return f"{v[0]}-{v[1]}"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        return f"{v}"
    return str(v)


def _axis_label(axis: str) -> str:
    return {
        "time_stop_mode":              "Time stop mode",
        "atr_tp_multiplier":           "ATR TP multiplier",
        "atr_sl_multiplier":           "ATR SL multiplier",
        "adx_range":                   "ADX range",
        "require_candle_confirmation": "Candle confirmation",
    }.get(axis, axis)


_DEFAULT_LABELS = {
    "time_stop_mode":              f"B_PROFIT_ONLY",
    "atr_tp_multiplier":           "1.0",
    "atr_sl_multiplier":           "0.83",
    "adx_range":                   "18-50",
    "require_candle_confirmation": "True",
}


def print_phase1_report(coin_name: str, phase1_results: dict, winners: dict):
    print(f"\n{'═' * 70}")
    print(f"  {coin_name} variant testing — phase 1 results")
    print(f"{'═' * 70}")

    for axis, axis_results in phase1_results.items():
        default_label = _DEFAULT_LABELS.get(axis, "?")
        print(f"\n  {_axis_label(axis)} (default: {default_label})")

        # header
        vals    = [r["variant_value"] for r in axis_results]
        headers = [_fmt_val(v) for v in vals]
        col_w   = max(10, max(len(h) for h in headers) + 2)
        header_line = "  " + " ".join(f"{h:>{col_w}}" for h in headers)
        print(header_line)

        rows = [
            ("Trades",        lambda m: str(m["trades"])),
            ("Win rate",      lambda m: f"{m['win_rate']:.1f}%"),
            ("Profit factor", lambda m: f"{m['profit_factor']:.2f}"),
            ("Max drawdown",  lambda m: f"{m['max_drawdown_pct']:.1f}%"),
            ("Avg duration",  lambda m: f"{m['avg_duration_minutes']:.0f}min"),
            ("Concur util",   lambda m: f"{m['concurrency_util_pct']:.0f}%"),
        ]
        for label, fmt_fn in rows:
            cells = [fmt_fn(r["metrics"]) for r in axis_results]
            line = f"  {label:<14}" + " ".join(f"{c:>{col_w}}" for c in cells)
            print(line)

        winner_val = winners[axis]
        print(f"  WINNER: {_fmt_val(winner_val)}")

    print()


def print_phase2_report(coin_name: str, winners: dict, phase2_result: dict):
    m = phase2_result["metrics"]
    print(f"\n  {coin_name} phase 2 — combined best variants:")
    print(f"    Time stop:           {_fmt_val(winners['time_stop_mode'])}")
    print(f"    ATR TP multiplier:   {winners['atr_tp_multiplier']}")
    print(f"    ATR SL multiplier:   {winners['atr_sl_multiplier']}")
    print(f"    ADX range:           {_fmt_val(winners['adx_range'])}")
    print(f"    Candle confirmation: {winners['require_candle_confirmation']}")
    print(f"\n  {coin_name} phase 2 result:")
    print(f"    Trades:         {m['trades']}")
    print(f"    Win rate:       {m['win_rate']:.1f}%")
    print(f"    Profit factor:  {m['profit_factor']:.2f}")
    print(f"    Max drawdown:   {m['max_drawdown_pct']:.1f}%")
    print(f"    Avg duration:   {m['avg_duration_minutes']:.0f} min")
    print(f"    TP hits:        {m['tp_hits']}  SL hits: {m['sl_hits']}  "
          f"Time stops: {m['time_stops']}  Max hold: {m['max_hold_exits']}")
    print(f"    Concur util:    {m['concurrency_util_pct']:.0f}%")
    print(f"    Avg daily:      {m['avg_daily_trades']:.1f} trades/day")


# ── Legacy interface (run_bootstrap.py compatibility) ─────────────────────────

def run_backtest(coin_name: str, df: pd.DataFrame) -> pd.DataFrame:
    """Runs with default variant params. Used by run_bootstrap.py for learning file generation."""
    return run_backtest_variant(coin_name, df, **_DEFAULTS)


def summarize_backtest(coin_name: str, trades_df: pd.DataFrame) -> dict:
    m = compute_metrics(trades_df)
    wins   = (trades_df["outcome"] == "W").sum() if not trades_df.empty else 0
    losses = (trades_df["outcome"] == "L").sum() if not trades_df.empty else 0
    return {
        "coin":             coin_name,
        "trades":           m["trades"],
        "wins":             int(wins),
        "losses":           int(losses),
        "win_rate":         m["win_rate"],
        "profit_factor":    m["profit_factor"],
        "max_drawdown_pct": m["max_drawdown_pct"],
        "avg_win_pct":      round(trades_df[trades_df["outcome"] == "W"]["pnl_pct"].mean(), 3)
                            if not trades_df.empty and wins > 0 else 0.0,
        "avg_loss_pct":     round(trades_df[trades_df["outcome"] == "L"]["pnl_pct"].mean(), 3)
                            if not trades_df.empty and losses > 0 else 0.0,
    }


def regime_breakdown(trades_df: pd.DataFrame, df: pd.DataFrame) -> dict:
    if trades_df.empty:
        return {"by_regime": [], "by_adx": []}

    def _adx_bucket(adx):
        if pd.isna(adx): return "ADX 15-20"
        if adx < 20:     return "ADX 15-20"
        if adx < 25:     return "ADX 20-25"
        if adx < 28:     return "ADX 25-28"
        return "ADX 28+"

    regime_col = "htf_daily"
    if regime_col not in trades_df.columns:
        trades_df = trades_df.copy()
        trades_df[regime_col] = "NEUTRAL"

    by_regime = []
    for regime in ["BULLISH", "BEARISH", "NEUTRAL"]:
        subset = trades_df[trades_df[regime_col] == regime]
        t = len(subset)
        if t == 0: continue
        w  = (subset["outcome"] == "W").sum()
        l  = (subset["outcome"] == "L").sum()
        wr = w / t * 100
        gp = subset[subset["outcome"] == "W"]["pnl_pct"].sum()
        gl = abs(subset[subset["outcome"] == "L"]["pnl_pct"].sum())
        pf = gp / gl if gl > 0 else float("inf")
        by_regime.append({"regime": regime, "trades": t, "wins": int(w), "losses": int(l),
                           "win_rate": round(wr, 1), "profit_factor": round(pf, 2)})

    trades_with_adx = trades_df.copy()
    trades_with_adx["adx_bucket"] = trades_with_adx["adx_at_entry"].apply(_adx_bucket)
    by_adx = []
    for bucket in ["ADX 15-20", "ADX 20-25", "ADX 25-28", "ADX 28+"]:
        subset = trades_with_adx[trades_with_adx["adx_bucket"] == bucket]
        t = len(subset)
        if t == 0: continue
        w  = (subset["outcome"] == "W").sum()
        l  = (subset["outcome"] == "L").sum()
        wr = w / t * 100
        by_adx.append({"bucket": bucket, "trades": t, "wins": int(w), "losses": int(l),
                        "win_rate": round(wr, 1)})

    return {"by_regime": by_regime, "by_adx": by_adx}


def format_regime_report(coin_name: str, summary: dict,
                          breakdown: dict, timeframe_label: str):
    n  = summary.get("trades", 0)
    wr = summary.get("win_rate", 0.0)
    pf = summary.get("profit_factor", 0.0)
    dd = summary.get("max_drawdown_pct", 0.0)
    print(f"\n  {coin_name} — {timeframe_label} ({n} simulated trades)")
    print(f"    Overall WR: {wr:.1f}%  PF: {pf:.2f}  MaxDD: {dd:.1f}%")
    for r in breakdown.get("by_regime", []):
        flag = "  LOW" if r["win_rate"] < 45 else ""
        print(f"      {r['regime']:<10}  {r['trades']:4d} trades  "
              f"WR={r['win_rate']:.1f}%  PF={r['profit_factor']:.2f}{flag}")
    for a in breakdown.get("by_adx", []):
        flag = "  LOW" if a["win_rate"] < 45 else ""
        print(f"      {a['bucket']:<12}  {a['trades']:4d} trades  WR={a['win_rate']:.1f}%{flag}")


if __name__ == "__main__":
    print("backtest.py — import and call run_phase1/run_phase2 to use.")

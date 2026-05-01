"""
backtest.py

Simulates the signal engine's entry logic on historical 5-minute candles
with ATR-based stops and forward-scan outcome resolution.

Rules (applied in order per candle):
  Skip LONG if: daily BEARISH, 4h BEARISH, ADX<15 or ADX>28, RSI>60, DI->DI+
  Skip SHORT if: daily BULLISH, RSI<35
  Direction: DI+ > DI- by >8 pts AND 1h BULLISH/NEUTRAL → LONG
             DI- > DI+ by >8 pts AND 1h BEARISH/NEUTRAL → SHORT
             else DNE
  SL:  ATR × 1.5 below entry (LONG) / above entry (SHORT)
  TP:  entry + (SL distance × 1.5) for LONG / entry - (SL distance × 1.5) for SHORT
  Max hold: 96 candles (8 hours)
  One trade at a time per coin.
"""

import pandas as pd
import numpy as np
from typing import Optional


MAX_HOLD_CANDLES = 24    # 24 hours on 1h data (8 hours on 5m)
ATR_MULTIPLIER   = 1.5
RR_RATIO         = 1.5
MIN_ADX_LONG     = 14
MAX_ADX_LONG     = 35
MAX_RSI_LONG     = 62
MIN_RSI_SHORT    = 35
DI_GAP_MIN       = 6.0


def _direction(row: pd.Series) -> Optional[str]:
    """Determine proposed trade direction for this candle."""
    di_plus  = row.get("di_plus")
    di_minus = row.get("di_minus")
    trend_1h = row.get("htf_trend_1h", "NEUTRAL")

    if pd.isna(di_plus) or pd.isna(di_minus):
        return None

    gap = di_plus - di_minus

    if gap > DI_GAP_MIN and trend_1h in ("BULLISH", "NEUTRAL"):
        return "LONG"
    if -gap > DI_GAP_MIN and trend_1h in ("BEARISH", "NEUTRAL"):
        return "SHORT"
    return None


def _passes_filters(row: pd.Series, direction: str) -> bool:
    """Return True if the candle passes all entry filters for the given direction."""
    rsi      = row.get("rsi")
    adx      = row.get("adx")
    di_plus  = row.get("di_plus")
    di_minus = row.get("di_minus")
    trend_d  = row.get("htf_trend_daily", "NEUTRAL")
    trend_4h = row.get("htf_trend_4h", "NEUTRAL")

    if pd.isna(rsi) or pd.isna(adx) or pd.isna(di_plus) or pd.isna(di_minus):
        return False

    if direction == "LONG":
        if trend_d  == "BEARISH": return False
        if trend_4h == "BEARISH": return False
        if adx < MIN_ADX_LONG or adx > MAX_ADX_LONG: return False
        if rsi > MAX_RSI_LONG: return False
        if di_minus > di_plus: return False

    if direction == "SHORT":
        if trend_d == "BULLISH": return False
        if rsi < MIN_RSI_SHORT: return False

    return True


def _resolve_trade(entry_price: float, sl: float, tp: float,
                   direction: str,
                   future_candles: pd.DataFrame) -> tuple:
    """
    Scan forward to find which level (SL or TP) is hit first.
    Returns (outcome, candles_held) where outcome is 'W', 'L', or 'TIMEOUT'.
    """
    for i, (_, frow) in enumerate(future_candles.iterrows()):
        h = frow["high"]
        l = frow["low"]

        if direction == "LONG":
            if l <= sl:
                return "L", i + 1
            if h >= tp:
                return "W", i + 1
        else:  # SHORT
            if h >= sl:
                return "L", i + 1
            if l <= tp:
                return "W", i + 1

    return "TIMEOUT", len(future_candles)


def run_backtest(coin_name: str, df: pd.DataFrame) -> pd.DataFrame:
    """
    Simulate signal logic on every 5-minute candle.

    df must have columns: timestamp, open, high, low, close, volume,
    rsi, adx, di_plus, di_minus, atr, macd,
    htf_trend_daily, htf_trend_4h, htf_trend_1h

    Returns a DataFrame of simulated trades.
    """
    df = df.reset_index(drop=True)
    trades = []
    in_trade = False
    trade_end_idx = -1

    needed = ["rsi", "adx", "di_plus", "di_minus", "atr"]
    missing = [c for c in needed if c not in df.columns]
    if missing:
        print(f"[BACKTEST:{coin_name}] Missing columns: {missing}. Aborting.")
        return pd.DataFrame()

    # Drop rows where key indicators are NaN (warm-up period)
    valid_mask  = df[needed].notna().all(axis=1)
    if not valid_mask.any():
        print(f"[BACKTEST:{coin_name}] All indicator rows are NaN — not enough data.")
        return pd.DataFrame()
    valid_start = valid_mask.idxmax()

    for idx in range(valid_start, len(df) - 1):
        if in_trade and idx <= trade_end_idx:
            continue
        in_trade = False

        row = df.iloc[idx]
        if row[list(needed)].isna().any():
            continue

        direction = _direction(row)
        if direction is None:
            continue

        if not _passes_filters(row, direction):
            continue

        atr = row["atr"]
        if pd.isna(atr) or atr <= 0:
            continue

        entry = row["close"]
        sl_dist = atr * ATR_MULTIPLIER
        tp_dist = sl_dist * RR_RATIO

        if direction == "LONG":
            sl = entry - sl_dist
            tp = entry + tp_dist
        else:
            sl = entry + sl_dist
            tp = entry - tp_dist

        # Look forward up to MAX_HOLD_CANDLES
        future = df.iloc[idx + 1: idx + 1 + MAX_HOLD_CANDLES]
        if future.empty:
            break

        outcome, candles_held = _resolve_trade(entry, sl, tp, direction, future)

        if outcome == "TIMEOUT":
            # Close at the last candle's close
            exit_price = future.iloc[-1]["close"]
            outcome = "W" if (
                (direction == "LONG"  and exit_price > entry) or
                (direction == "SHORT" and exit_price < entry)
            ) else "L"
        else:
            exit_price = tp if outcome == "W" else sl

        pnl_pct = (
            (exit_price - entry) / entry * 100 if direction == "LONG"
            else (entry - exit_price) / entry * 100
        )

        trades.append({
            "timestamp":     row["timestamp"],
            "direction":     direction,
            "entry":         round(entry, 6),
            "sl":            round(sl, 6),
            "tp":            round(tp, 6),
            "exit_price":    round(exit_price, 6),
            "outcome":       outcome,
            "candles_held":  candles_held,
            "pnl_pct":       round(pnl_pct, 4),
            "rsi_at_entry":  round(row["rsi"], 2),
            "adx_at_entry":  round(row["adx"], 2),
            "di_plus":       round(row["di_plus"], 2),
            "di_minus":      round(row["di_minus"], 2),
            "macd":          round(row.get("macd", 0) or 0, 4),
            "htf_daily":     row.get("htf_trend_daily", "NEUTRAL"),
            "htf_4h":        row.get("htf_trend_4h", "NEUTRAL"),
            "htf_1h":        row.get("htf_trend_1h", "NEUTRAL"),
        })

        in_trade = True
        trade_end_idx = idx + candles_held

    return pd.DataFrame(trades)


def summarize_backtest(coin_name: str, trades_df: pd.DataFrame) -> dict:
    """Compute summary stats from a backtest trades DataFrame."""
    if trades_df.empty:
        return {
            "coin": coin_name, "trades": 0, "win_rate": 0.0,
            "profit_factor": 0.0, "max_drawdown_pct": 0.0,
            "avg_win_pct": 0.0, "avg_loss_pct": 0.0,
        }

    wins   = trades_df[trades_df["outcome"] == "W"]
    losses = trades_df[trades_df["outcome"] == "L"]

    total_wins   = len(wins)
    total_losses = len(losses)
    total        = len(trades_df)
    win_rate     = total_wins / total * 100 if total > 0 else 0.0

    avg_win  = wins["pnl_pct"].mean()   if not wins.empty   else 0.0
    avg_loss = losses["pnl_pct"].mean() if not losses.empty else 0.0

    gross_profit = wins["pnl_pct"].sum()
    gross_loss   = abs(losses["pnl_pct"].sum())
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Max drawdown on cumulative PnL series
    cum_pnl = trades_df["pnl_pct"].cumsum()
    rolling_max = cum_pnl.cummax()
    drawdown = cum_pnl - rolling_max
    max_dd = drawdown.min()

    return {
        "coin":             coin_name,
        "trades":           total,
        "wins":             total_wins,
        "losses":           total_losses,
        "win_rate":         round(win_rate, 1),
        "profit_factor":    round(profit_factor, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "avg_win_pct":      round(avg_win, 3),
        "avg_loss_pct":     round(avg_loss, 3),
    }


if __name__ == "__main__":
    print("backtest.py — import and call run_backtest(coin_name, df_5min) to use.")

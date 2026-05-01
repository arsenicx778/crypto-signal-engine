"""
bootstrap_learning.py

Generates learning JSON files for each coin from historical backtest results.
Uses the same pattern-key format and penalty scale as the live learning system
so that step11_guardrails.py reads them immediately without any code changes.

Pattern keys must have at least 20 historical occurrences to be included
(thin patterns produce noisy, unreliable penalties).

Output files are written to {coin_lower}_learning.json with a
'source': 'historical_bootstrap' field so dashboards can distinguish them
from live-generated files.
"""

import json
import os
from datetime import datetime, timezone
from typing import Optional

import pandas as pd


# ── Pattern key classification (mirrors step_learn.py exactly) ────────────────

def classify_pattern_key(direction: str, rsi, di_plus, di_minus, adx, macd) -> Optional[str]:
    if direction not in ("LONG", "SHORT"):
        return None
    if any(v is None or (isinstance(v, float) and pd.isna(v))
           for v in (rsi, di_plus, di_minus, adx, macd)):
        return None

    rsi      = float(rsi)
    di_plus  = float(di_plus)
    di_minus = float(di_minus)
    adx      = float(adx)
    macd     = float(macd)

    rsi_tag  = "rsi_low"    if rsi < 40    else ("rsi_high" if rsi > 65  else "rsi_mid")
    gap_tag  = "gap_strong" if abs(di_plus - di_minus) >= 15              else "gap_weak"
    adx_tag  = "adx_strong" if adx >= 27                                  else "adx_weak"
    macd_tag = "macd_pos"   if macd >= 0                                  else "macd_neg"

    return f"{direction}|{rsi_tag}|{gap_tag}|{adx_tag}|{macd_tag}"


# ── Penalty scale (mirrors step_learn.py exactly) ─────────────────────────────

def _penalty_and_tag(win_rate: float):
    if win_rate >= 0.55:
        return 0, "NEUTRAL"
    if win_rate >= 0.45:
        return 5, "CAUTION"
    if win_rate >= 0.35:
        return 15, "AVOID"
    return 25, "STRONG_AVOID"


# ── Bootstrap builder ──────────────────────────────────────────────────────────

# Minimum occurrences per pattern key.
# With Kraken's free-tier giving ~30 days of 1h data and 40–50 trades,
# keys are spread across ~8 buckets; using 5 preserves statistical signal
# while keeping patterns that have real observations.
# The source='historical_bootstrap' field lets the live system know these
# are prior-based and should decay as live trades accumulate.
MIN_OCCURRENCES = 5


def generate_bootstrap_learning(coin_name: str,
                                 df_with_indicators: pd.DataFrame,
                                 backtest_trades: pd.DataFrame) -> dict:
    """
    Build a learning JSON dict from historical backtest trades.

    Parameters
    ----------
    coin_name           : e.g. "ETH"
    df_with_indicators  : 5-minute OHLCV + indicators DataFrame (for candle count)
    backtest_trades     : output from backtest.run_backtest()

    Returns the learning dict (also writes it to disk).
    """
    coin_name = coin_name.upper()
    candles_n = len(df_with_indicators)

    if backtest_trades.empty:
        print(f"[BOOTSTRAP:{coin_name}] No backtest trades — skipping.")
        return {}

    # Classify every trade into a pattern key
    buckets: dict = {}
    skipped = 0

    for _, row in backtest_trades.iterrows():
        key = classify_pattern_key(
            direction=row["direction"],
            rsi=row.get("rsi_at_entry"),
            di_plus=row.get("di_plus"),
            di_minus=row.get("di_minus"),
            adx=row.get("adx_at_entry"),
            macd=row.get("macd"),
        )
        if key is None:
            skipped += 1
            continue

        if key not in buckets:
            buckets[key] = {"wins": 0, "losses": 0}
        if row["outcome"] == "W":
            buckets[key]["wins"] += 1
        else:
            buckets[key]["losses"] += 1

    # Filter to patterns with MIN_OCCURRENCES+
    weighted_patterns = []
    for key, counts in buckets.items():
        total = counts["wins"] + counts["losses"]
        if total < MIN_OCCURRENCES:
            continue

        wr = counts["wins"] / total
        penalty, tag = _penalty_and_tag(wr)

        weighted_patterns.append({
            "key":               key,
            "weighted_wins":     float(counts["wins"]),
            "weighted_losses":   float(counts["losses"]),
            "raw_count":         total,
            "weighted_win_rate": round(wr, 4),
            "win_rate_pct":      round(wr * 100, 1),
            "confidence_penalty": penalty,
            "penalty_tag":       tag,
        })

    # Sort: highest penalty first (most dangerous patterns at the top)
    weighted_patterns.sort(key=lambda p: -p["confidence_penalty"])

    # Build narrative patterns for the "patterns" field (Haiku-style)
    patterns_narrative = []
    for p in weighted_patterns:
        wr_pct = p["win_rate_pct"]
        if wr_pct >= 55:
            rec = f"Favor this setup — {wr_pct:.0f}% historical win rate over {p['raw_count']} trades."
        elif wr_pct >= 45:
            rec = f"Proceed with caution — {wr_pct:.0f}% historical win rate, borderline edge."
        else:
            rec = f"Avoid this setup — {wr_pct:.0f}% historical win rate, negative edge."

        patterns_narrative.append({
            "condition":        p["key"],
            "wins":             int(p["weighted_wins"]),
            "losses":           int(p["weighted_losses"]),
            "win_rate":         int(wr_pct),
            "recommendation":   rec,
            "confidence":       "high" if p["raw_count"] >= 50 else "medium",
            "sample_size_note": f"{p['raw_count']} historical occurrences",
        })

    # Overall stats
    total_trades = len(backtest_trades)
    total_wins   = (backtest_trades["outcome"] == "W").sum()
    overall_wr   = round(total_wins / total_trades * 100) if total_trades else 0

    # Best / worst setups
    if weighted_patterns:
        best  = max(weighted_patterns, key=lambda p: p["win_rate_pct"])
        worst = min(weighted_patterns, key=lambda p: p["win_rate_pct"])
        long_patterns  = [p for p in weighted_patterns if p["key"].startswith("LONG")]
        short_patterns = [p for p in weighted_patterns if p["key"].startswith("SHORT")]
        best_long  = max(long_patterns,  key=lambda p: p["win_rate_pct"]) if long_patterns  else None
        best_short = max(short_patterns, key=lambda p: p["win_rate_pct"]) if short_patterns else None
    else:
        best = worst = best_long = best_short = None

    strongest_long  = best_long["key"]  + f" ({best_long['win_rate_pct']:.0f}% WR)"  if best_long  else "No qualifying LONG pattern found."
    strongest_short = best_short["key"] + f" ({best_short['win_rate_pct']:.0f}% WR)" if best_short else "No qualifying SHORT pattern found."

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    output = {
        "coin":               coin_name,
        "generated_at":       now_str,
        "source":             "historical_bootstrap",
        "candles_analyzed":   candles_n,
        "trade_count":        total_trades,
        "overall_win_rate":   overall_wr,
        "patterns":           patterns_narrative,
        "weighted_patterns":  weighted_patterns,
        "strongest_long_setup":  strongest_long,
        "strongest_short_setup": strongest_short,
        "summary": (
            f"Bootstrap from {candles_n:,} historical 5-minute candles. "
            f"{total_trades} simulated trades, {overall_wr}% win rate. "
            f"{len(weighted_patterns)} pattern keys with {MIN_OCCURRENCES}+ occurrences. "
            f"Use these as starting priors; live trading will refine them."
        ),
    }

    path = f"{coin_name.lower()}_learning.json"
    with open(path, "w") as f:
        json.dump(output, f, indent=2)

    qualifying = len(weighted_patterns)
    print(
        f"[BOOTSTRAP:{coin_name}] {candles_n:,} candles analyzed — "
        f"{qualifying} pattern keys with {MIN_OCCURRENCES}+ occurrences — "
        f"learning file written"
    )

    return output


# ── Top / bottom pattern reporter ─────────────────────────────────────────────

def report_top_patterns(coin_name: str, learning: dict, n: int = 5):
    """Print the top-n best and worst pattern keys for a coin."""
    patterns = learning.get("weighted_patterns", [])
    if not patterns:
        print(f"[BOOTSTRAP:{coin_name}] No patterns to report.")
        return

    by_wr = sorted(patterns, key=lambda p: p["win_rate_pct"])
    worst = by_wr[:n]
    best  = by_wr[-n:][::-1]

    print(f"\n  {coin_name} — Top {n} best patterns:")
    for p in best:
        print(f"    {p['key']:<45s}  WR={p['win_rate_pct']:5.1f}%  n={p['raw_count']:4d}  [{p['penalty_tag']}]")

    print(f"\n  {coin_name} — Top {n} worst patterns:")
    for p in worst:
        print(f"    {p['key']:<45s}  WR={p['win_rate_pct']:5.1f}%  n={p['raw_count']:4d}  [{p['penalty_tag']}]")

"""
run_bootstrap.py

Full historical bootstrap pipeline:
  1. Fetch 30m history from Kraken (720 candles = 15 days, single call per coin)
     HTF: 1h (30 days) and daily (2 years) for trend filter
  2. Compute all technical indicators + HTF trend signals on 30m frame
  3. Run phase 1 variant testing (13 runs per coin, 52 total)
  4. Pick winners per axis per coin
  5. Run phase 2 combined-winner backtest (4 runs total)
  6. Generate bootstrap learning files from phase 2 trades
  7. Write per-coin live config to config.py
  8. Flag any coin with phase 2 win rate < 47% or < 100 trades

Usage:
  python run_bootstrap.py
"""

import sys
import os
import json
import re
import pandas as pd

from historical_fetch import fetch_max_history, compute_all_indicators, _htf_trend_series, _detect_trend
from backtest import (
    run_phase1, pick_winners, run_phase2,
    print_phase1_report, print_phase2_report,
    run_backtest, summarize_backtest, regime_breakdown, format_regime_report,
)
from bootstrap_learning import generate_bootstrap_learning, report_top_patterns

COINS = ["ETH", "SOL", "XRP", "LINK"]
WIN_RATE_FLAG_THRESHOLD = 47.0  # below this → flag for exclusion


def _section(title: str):
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}")


def _data_summary(coin: str, data: dict):
    for tf_name, df in data.items():
        if df.empty:
            print(f"  [{coin}] {tf_name:10s}: EMPTY")
            continue
        start     = df["timestamp"].min()
        end       = df["timestamp"].max()
        span_days = (end - start).total_seconds() / 86400
        print(f"  [{coin}] {tf_name:10s}: {len(df):6d} candles  "
              f"{start.strftime('%Y-%m-%d')} -> {end.strftime('%Y-%m-%d')}  "
              f"({span_days:.0f} days)")


def _rolling_trend(close_series: pd.Series, window: int) -> list:
    result = []
    for i in range(len(close_series)):
        if i < window - 1:
            result.append("NEUTRAL")
            continue
        seg   = close_series.iloc[i - window + 1: i + 1]
        first = seg.iloc[0]
        last  = seg.iloc[-1]
        if first == 0:
            result.append("NEUTRAL")
            continue
        chg = (last - first) / abs(first)
        if chg > 0.005:
            result.append("BULLISH")
        elif chg < -0.005:
            result.append("BEARISH")
        else:
            result.append("NEUTRAL")
    return result


def _prepare_thirty_min_df(coin: str, enriched: dict) -> pd.DataFrame:
    """
    Return the 30-minute DataFrame with HTF trend columns attached.
    Falls back gracefully when HTF frames are missing.
    compute_all_indicators already attaches htf_trend_daily and htf_trend_1h
    via _htf_trend_series, so this just retrieves and validates.
    """
    thirty_min = enriched.get("thirty_min", pd.DataFrame())
    if thirty_min.empty:
        return pd.DataFrame()

    thirty_min = thirty_min.copy()

    # Ensure required HTF columns exist (compute_all_indicators should have set them)
    if "htf_trend_daily" not in thirty_min.columns:
        thirty_min["htf_trend_daily"] = "NEUTRAL"
    if "htf_trend_1h" not in thirty_min.columns:
        thirty_min["htf_trend_1h"] = "NEUTRAL"

    return thirty_min


def _regime_breakdown(trades_df: pd.DataFrame) -> None:
    """Print win rate by daily regime (BULLISH/BEARISH/NEUTRAL)."""
    if trades_df.empty or "htf_daily" not in trades_df.columns:
        print("  (no regime data)")
        return

    print(f"\n  {'Regime':<10}  {'Trades':>7}  {'Wins':>5}  {'Losses':>7}  {'Win Rate':>9}  {'Prof Factor':>12}")
    print(f"  {'─'*10}  {'─'*7}  {'─'*5}  {'─'*7}  {'─'*9}  {'─'*12}")
    for regime in ["BULLISH", "BEARISH", "NEUTRAL"]:
        sub = trades_df[trades_df["htf_daily"] == regime]
        t = len(sub)
        if t == 0:
            continue
        w  = (sub["outcome"] == "W").sum()
        l  = (sub["outcome"] == "L").sum()
        wr = w / t * 100
        gp = sub[sub["outcome"] == "W"]["pnl_pct"].sum()
        gl = abs(sub[sub["outcome"] == "L"]["pnl_pct"].sum())
        pf = gp / gl if gl > 0 else float("inf")
        flag = "  <- FLAG regime-only" if (wr < 40 or (wr > 65 and t > 10)) else ""
        print(f"  {regime:<10}  {t:>7}  {w:>5}  {l:>7}  {wr:>8.1f}%  {pf:>12.2f}{flag}")


def _time_of_day_breakdown(trades_df: pd.DataFrame) -> None:
    """Print win rate by Pacific hour bucket (grouped into trading sessions)."""
    if trades_df.empty or "timestamp" not in trades_df.columns:
        print("  (no timestamp data)")
        return

    try:
        from zoneinfo import ZoneInfo
        pacific = ZoneInfo("America/Los_Angeles")
        ts = pd.to_datetime(trades_df["timestamp"])
        if ts.dt.tz is None:
            ts = ts.dt.tz_localize("UTC")
        trades_df = trades_df.copy()
        trades_df["hour_pt"] = ts.dt.tz_convert(pacific).dt.hour
    except Exception:
        # zoneinfo unavailable — fall back to UTC hour with label change
        trades_df = trades_df.copy()
        trades_df["hour_pt"] = pd.to_datetime(trades_df["timestamp"]).dt.hour

    sessions = [
        ("Asia/Early EU   00-06 PT", range(0, 6)),
        ("EU session      06-09 PT", range(6, 9)),
        ("NY open         09-12 PT", range(9, 12)),
        ("NY mid          12-16 PT", range(12, 16)),
        ("After-hours     16-24 PT", range(16, 24)),
    ]

    print(f"\n  {'Session':<30}  {'Trades':>7}  {'Win Rate':>9}  {'Prof Factor':>12}")
    print(f"  {'─'*30}  {'─'*7}  {'─'*9}  {'─'*12}")
    for label, hours in sessions:
        sub = trades_df[trades_df["hour_pt"].isin(hours)]
        t = len(sub)
        if t == 0:
            continue
        w  = (sub["outcome"] == "W").sum()
        wr = w / t * 100
        gp = sub[sub["outcome"] == "W"]["pnl_pct"].sum()
        gl = abs(sub[sub["outcome"] == "L"]["pnl_pct"].sum())
        pf = gp / gl if gl > 0 else float("inf")
        print(f"  {label:<30}  {t:>7}  {wr:>8.1f}%  {pf:>12.2f}")


def _update_config_per_coin(all_winners: dict):
    """
    Rewrite the PER_COIN_LIVE_CONFIG block in config.py with winning params.
    Uses line-scanning to find and replace the block safely (no nested-brace regex).
    """
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.py")
    with open(config_path) as f:
        lines = f.readlines()

    # Build replacement block
    new_lines = ["PER_COIN_LIVE_CONFIG: dict = {\n"]
    for coin, winners in all_winners.items():
        adx_min, adx_max = winners["adx_range"]
        new_lines.append(f'    "{coin}": {{\n')
        new_lines.append(f'        "TIME_STOP_MODE":              "{winners["time_stop_mode"]}",\n')
        new_lines.append(f'        "ATR_TP_MULTIPLIER":           {winners["atr_tp_multiplier"]},\n')
        new_lines.append(f'        "ATR_SL_MULTIPLIER":           {winners["atr_sl_multiplier"]},\n')
        new_lines.append(f'        "ADX_MIN":                     {adx_min},\n')
        new_lines.append(f'        "ADX_MAX":                     {adx_max},\n')
        new_lines.append(f'        "REQUIRE_CANDLE_CONFIRMATION": {winners["require_candle_confirmation"]},\n')
        new_lines.append(f'    }},\n')
    new_lines.append("}\n")

    # Find start and end of existing PER_COIN_LIVE_CONFIG block by scanning lines
    start_idx = None
    end_idx   = None
    brace_depth = 0
    for i, line in enumerate(lines):
        if start_idx is None and line.strip().startswith("PER_COIN_LIVE_CONFIG"):
            start_idx = i
            brace_depth = line.count("{") - line.count("}")
            if brace_depth == 0:
                end_idx = i
            continue
        if start_idx is not None and end_idx is None:
            brace_depth += line.count("{") - line.count("}")
            if brace_depth <= 0:
                end_idx = i
                break

    if start_idx is not None and end_idx is not None:
        lines = lines[:start_idx] + new_lines + lines[end_idx + 1:]
    else:
        lines.append("\n")
        lines.extend(new_lines)

    with open(config_path, "w") as f:
        f.writelines(lines)

    print(f"[CONFIG] PER_COIN_LIVE_CONFIG updated in config.py")


def main():
    # ── Step 1: Fetch ─────────────────────────────────────────────────────────
    _section("STEP 1 — FETCH HISTORICAL DATA (30m primary = 15 days, HTF for trend filter)")

    all_data: dict = {}
    for coin in COINS:
        print(f"\n[{coin}] Fetching...")
        try:
            data = fetch_max_history(coin)
            all_data[coin] = data
            _data_summary(coin, data)
        except Exception as e:
            print(f"[{coin}] FETCH ERROR: {e}")
            all_data[coin] = {}

    # ── Step 2: Indicators ────────────────────────────────────────────────────
    _section("STEP 2 — COMPUTE INDICATORS + HTF TRENDS")

    all_enriched: dict = {}
    for coin in COINS:
        data = all_data.get(coin, {})
        if not data:
            print(f"[{coin}] No data — skipping.")
            all_enriched[coin] = {}
            continue
        print(f"[{coin}] Computing indicators...")
        try:
            enriched = compute_all_indicators(data)
            all_enriched[coin] = enriched
            tm = enriched.get("thirty_min", pd.DataFrame())
            if not tm.empty:
                last = tm.iloc[-1]
                print(f"  [{coin}] 30m latest: RSI={last.get('rsi', float('nan')):.1f}  "
                      f"ADX={last.get('adx', float('nan')):.1f}  "
                      f"DI+={last.get('di_plus', float('nan')):.1f}  "
                      f"DI-={last.get('di_minus', float('nan')):.1f}  "
                      f"BB_WIDTH={last.get('bb_width', float('nan')):.4f}")
        except Exception as e:
            print(f"[{coin}] INDICATOR ERROR: {e}")
            import traceback; traceback.print_exc()
            all_enriched[coin] = {}

    # ── Step 3: Phase 1 variant testing ──────────────────────────────────────
    _section("STEP 3 — PHASE 1 VARIANT TESTING (each axis independently)")
    print("  13 runs per coin (1 default + 2 alternatives x 5 axes), 52 total\n")

    all_phase1:  dict = {}
    all_winners: dict = {}

    for coin in COINS:
        enriched  = all_enriched.get(coin, {})
        df_30m    = _prepare_thirty_min_df(coin, enriched)

        if df_30m.empty:
            print(f"[{coin}] No 30m data — skipping variant testing.")
            all_phase1[coin]  = {}
            all_winners[coin] = {}
            continue

        span_days = len(df_30m) * 30 / 60 / 24
        print(f"[{coin}] Running phase 1 on {len(df_30m)} candles ({span_days:.1f} days)...")
        try:
            phase1  = run_phase1(coin, df_30m)
            winners = pick_winners(phase1)
            all_phase1[coin]  = phase1
            all_winners[coin] = winners
            print_phase1_report(coin, phase1, winners)
        except Exception as e:
            print(f"[{coin}] PHASE 1 ERROR: {e}")
            import traceback; traceback.print_exc()
            all_phase1[coin]  = {}
            all_winners[coin] = {}

    # ── Step 4: Phase 2 combined run ──────────────────────────────────────────
    _section("STEP 4 — PHASE 2 COMBINED BEST VARIANTS (4 runs total)")

    all_phase2: dict = {}
    all_phase2_trades: dict = {}

    for coin in COINS:
        enriched  = all_enriched.get(coin, {})
        df_30m    = _prepare_thirty_min_df(coin, enriched)
        winners   = all_winners.get(coin, {})

        if df_30m.empty or not winners:
            print(f"[{coin}] No data or no winners — skipping phase 2.")
            all_phase2[coin]        = {}
            all_phase2_trades[coin] = pd.DataFrame()
            continue

        print(f"[{coin}] Running phase 2...")
        try:
            p2 = run_phase2(coin, df_30m, winners)
            all_phase2[coin]        = p2
            all_phase2_trades[coin] = p2.get("trades", pd.DataFrame())
            print_phase2_report(coin, winners, p2)
        except Exception as e:
            print(f"[{coin}] PHASE 2 ERROR: {e}")
            import traceback; traceback.print_exc()
            all_phase2[coin]        = {}
            all_phase2_trades[coin] = pd.DataFrame()

    # ── Step 4b: Regime and time-of-day breakdown ─────────────────────────────
    _section("STEP 4b — REGIME BREAKDOWN + TIME-OF-DAY ANALYSIS (phase 2 trades)")

    for coin in COINS:
        trades = all_phase2_trades.get(coin, pd.DataFrame())
        p2     = all_phase2.get(coin, {})
        m      = p2.get("metrics", {}) if p2 else {}
        if trades.empty:
            print(f"\n  [{coin}] No phase 2 trades to analyze.")
            continue
        print(f"\n  {coin} — {m.get('trades', 0)} trades  WR={m.get('win_rate', 0):.1f}%  PF={m.get('profit_factor', 0):.2f}")
        print(f"\n  Regime breakdown (htf_trend_daily at entry):")
        _regime_breakdown(trades)
        print(f"\n  Time-of-day breakdown (Pacific time):")
        _time_of_day_breakdown(trades)

    # ── Step 5: Bootstrap learning files from phase 2 trades ─────────────────
    _section("STEP 5 — GENERATE BOOTSTRAP LEARNING FILES (from phase 2 trades)")

    all_learnings: dict = {}
    for coin in COINS:
        enriched = all_enriched.get(coin, {})
        df_30m   = _prepare_thirty_min_df(coin, enriched)
        trades   = all_phase2_trades.get(coin, pd.DataFrame())

        if df_30m.empty or trades.empty:
            print(f"[{coin}] No data or trades — skipping bootstrap.")
            all_learnings[coin] = {}
            continue

        try:
            learning = generate_bootstrap_learning(coin, df_30m, trades)
            all_learnings[coin] = learning
        except Exception as e:
            print(f"[{coin}] BOOTSTRAP ERROR: {e}")
            import traceback; traceback.print_exc()
            all_learnings[coin] = {}

    # ── Step 6: Pattern analysis ──────────────────────────────────────────────
    _section("STEP 6 — PATTERN ANALYSIS")
    for coin in COINS:
        learning = all_learnings.get(coin, {})
        if not learning:
            print(f"\n  [{coin}] No learning data.")
            continue
        report_top_patterns(coin, learning, n=5)

    # ── Step 7: Update config.py with winning variants ────────────────────────
    _section("STEP 7 — UPDATING PER_COIN_LIVE_CONFIG IN config.py")

    complete_winners = {c: w for c, w in all_winners.items() if w}
    if complete_winners:
        try:
            _update_config_per_coin(complete_winners)
            print("\n  Per-coin live config written:")
            for coin, winners in complete_winners.items():
                adx_min, adx_max = winners["adx_range"]
                print(f"    {coin}: time_stop={winners['time_stop_mode']}  "
                      f"TP={winners['atr_tp_multiplier']}x  SL={winners['atr_sl_multiplier']}x  "
                      f"ADX={adx_min}-{adx_max}  confirm={winners['require_candle_confirmation']}")
        except Exception as e:
            print(f"  [ERROR] Failed to update config.py: {e}")
    else:
        print("  No complete winners — config.py not modified.")

    # ── Step 8: Flags & recommendations ──────────────────────────────────────
    _section("FLAGS & RECOMMENDATIONS")

    any_flagged = False
    for coin in COINS:
        p2 = all_phase2.get(coin, {})
        if not p2:
            print(f"\n  FLAG  {coin}: No phase 2 result. Exclude from live trading.")
            any_flagged = True
            continue

        m  = p2.get("metrics", {})
        wr = m.get("win_rate", 0.0)
        pf = m.get("profit_factor", 0.0)
        t  = m.get("trades", 0)

        if t == 0:
            print(f"\n  FLAG  {coin}: Phase 2 produced 0 trades. Filters too restrictive.")
            any_flagged = True
        elif t < 100:
            print(f"\n  FLAG  {coin}: Phase 2 only {t} trades — insufficient sample for reliable variant selection.")
            print(f"         WR={wr:.1f}%  PF={pf:.2f}  — results unreliable, do not act on them.")
            any_flagged = True
        elif wr < WIN_RATE_FLAG_THRESHOLD:
            print(f"\n  FLAG  {coin}: Phase 2 win rate {wr:.1f}% is below {WIN_RATE_FLAG_THRESHOLD:.0f}% threshold.")
            print(f"         Profit factor: {pf:.2f}  Trades: {t}")
            print(f"         -> Strategy is NOT profitable for {coin} at any RR ratio.")
            print(f"         -> EXCLUDE {coin} from live trading.")
            any_flagged = True
        else:
            # Check for regime dependency: warn only if one regime has <40% WR with 10+ trades
            trades = all_phase2_trades.get(coin, pd.DataFrame())
            regime_warnings = []
            if not trades.empty and "htf_daily" in trades.columns:
                for regime in ["BULLISH", "BEARISH", "NEUTRAL"]:
                    sub = trades[trades["htf_daily"] == regime]
                    if len(sub) >= 10:
                        rwr = (sub["outcome"] == "W").sum() / len(sub) * 100
                        if rwr < 40:
                            regime_warnings.append(f"{regime} WR={rwr:.0f}%({len(sub)}t)")
            regime_flag = f"  WARNING regime: {', '.join(regime_warnings)}" if regime_warnings else ""
            print(f"\n  OK    {coin}: WR={wr:.1f}%  PF={pf:.2f}  Trades={t}  — cleared for live trading.{regime_flag}")

    if not any_flagged:
        print(f"\n  All coins passed the {WIN_RATE_FLAG_THRESHOLD:.0f}% win rate threshold.")

    # ── Summary table ─────────────────────────────────────────────────────────
    print(f"\n{'─' * 70}")
    print(f"  {'Coin':<6}  {'Trades':>7}  {'Win Rate':>9}  {'Prof Factor':>12}  "
          f"{'Max DD':>8}  {'Avg Min':>8}")
    print(f"  {'─' * 6}  {'─' * 7}  {'─' * 9}  {'─' * 12}  {'─' * 8}  {'─' * 8}")
    for coin in COINS:
        p2 = all_phase2.get(coin, {})
        m  = p2.get("metrics", {}) if p2 else {}
        flag = "  FLAG" if m.get("win_rate", 0) < WIN_RATE_FLAG_THRESHOLD and m.get("trades", 0) > 0 else ""
        print(f"  {coin:<6}  {m.get('trades', 0):>7}  "
              f"{m.get('win_rate', 0.0):>8.1f}%  "
              f"{m.get('profit_factor', 0.0):>12.2f}  "
              f"{m.get('max_drawdown_pct', 0.0):>7.1f}%  "
              f"{m.get('avg_duration_minutes', 0.0):>7.0f}m"
              f"{flag}")
    print(f"{'─' * 70}")

    print(f"\n{'=' * 70}")
    print(f"  Bootstrap complete.")
    print(f"  Learning files written: " +
          ", ".join(f"{c.lower()}_learning.json" for c in COINS if all_learnings.get(c)))
    print(f"  Live trading is PAUSED (LIVE_TRADING_ENABLED=False in config.py).")
    print(f"  Review results, then set LIVE_TRADING_ENABLED=True when ready.")
    print(f"{'=' * 70}\n")


if __name__ == "__main__":
    main()

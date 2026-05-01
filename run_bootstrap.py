"""
run_bootstrap.py

Full historical bootstrap pipeline:
  1. Fetch maximum free-tier history from Kraken (with caching)
  2. Compute all technical indicators + HTF trend signals
  3. Run backtest simulation on 5-minute data
  4. Generate bootstrap learning files for the guardrails
  5. Print summary tables and flag under-performing coins

Usage:
  python run_bootstrap.py
"""

import sys
import os
import pandas as pd

from historical_fetch import fetch_max_history, compute_all_indicators
from backtest import run_backtest, summarize_backtest
from bootstrap_learning import generate_bootstrap_learning, report_top_patterns

COINS = ["ETH", "SOL", "XRP", "LINK"]

WIN_RATE_THRESHOLD = 45.0  # Flag coins below this backtest WR


def _section(title: str):
    print(f"\n{'═'*65}")
    print(f"  {title}")
    print(f"{'═'*65}")


def _data_summary(coin: str, data: dict):
    """Show how much data we retrieved per timeframe."""
    for tf_name, df in data.items():
        if df.empty:
            print(f"  [{coin}] {tf_name:10s}: EMPTY — fetch may have failed")
            continue
        start = df["timestamp"].min()
        end   = df["timestamp"].max()
        span_days = (end - start).total_seconds() / 86400
        print(f"  [{coin}] {tf_name:10s}: {len(df):5d} candles  "
              f"{start.strftime('%Y-%m-%d')} → {end.strftime('%Y-%m-%d')}  "
              f"({span_days:.0f} days)")


def main():
    _section("STEP 1 — FETCH HISTORICAL DATA")

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

    _section("STEP 2 — COMPUTE INDICATORS + HTF TRENDS")

    all_enriched: dict = {}
    for coin in COINS:
        data = all_data.get(coin, {})
        if not data:
            print(f"[{coin}] No data — skipping indicators.")
            all_enriched[coin] = {}
            continue
        print(f"[{coin}] Computing indicators...")
        try:
            enriched = compute_all_indicators(data)
            all_enriched[coin] = enriched
            fm = enriched.get("five_min", pd.DataFrame())
            if not fm.empty:
                last = fm.iloc[-1]
                print(f"  [{coin}] 5min latest: RSI={last.get('rsi', float('nan')):.1f}  "
                      f"ADX={last.get('adx', float('nan')):.1f}  "
                      f"DI+={last.get('di_plus', float('nan')):.1f}  "
                      f"DI-={last.get('di_minus', float('nan')):.1f}  "
                      f"daily_trend={last.get('htf_trend_daily', 'N/A')}")
        except Exception as e:
            print(f"[{coin}] INDICATOR ERROR: {e}")
            all_enriched[coin] = {}

    _section("STEP 3 — BACKTEST SIMULATION")
    # Kraken free tier limits 5-min candles to ~2.5 days (720 candles) regardless
    # of 'since'. We use 1-hour data (720 candles ≈ 30 days) as the backtest
    # timeframe so we have enough trades for the 20-occurrence pattern threshold.
    # HTF trends from daily/4h are forward-filled onto the 1h candles.
    print("  Note: using 1-hour candles as backtest timeframe (30 days, ~720 candles).")
    print("        Kraken free tier caps 5m data at ~2.5 days — insufficient for patterns.\n")

    all_trades:    dict = {}
    all_summaries: dict = {}

    for coin in COINS:
        enriched  = all_enriched.get(coin, {})
        one_hour  = enriched.get("one_hour", pd.DataFrame())
        five_min  = enriched.get("five_min", pd.DataFrame())

        # Attach HTF trend columns to the 1-hour frame
        if not one_hour.empty:
            from historical_fetch import _htf_trend_series
            daily     = enriched.get("daily",     pd.DataFrame())
            four_hour = enriched.get("four_hour", pd.DataFrame())
            one_hour["htf_trend_daily"] = _htf_trend_series(daily,     one_hour, "htf_trend_daily", 5)
            one_hour["htf_trend_4h"]    = _htf_trend_series(four_hour, one_hour, "htf_trend_4h",    6)
            # 1h trend from itself (3-bar look-back)
            from historical_fetch import _detect_trend
            trends_1h = []
            for i in range(len(one_hour)):
                window = one_hour["close"].iloc[max(0, i-2):i+1]
                trends_1h.append(_detect_trend(window, 3))
            one_hour["htf_trend_1h"] = trends_1h

        if one_hour.empty:
            print(f"[{coin}] No 1h data — skipping backtest.")
            all_trades[coin]    = pd.DataFrame()
            all_summaries[coin] = {"coin": coin, "trades": 0, "win_rate": 0.0,
                                    "profit_factor": 0.0, "max_drawdown_pct": 0.0}
            continue

        print(f"[{coin}] Running backtest on {len(one_hour)} 1h candles...")
        try:
            trades = run_backtest(coin, one_hour)
            summary = summarize_backtest(coin, trades)
            all_trades[coin]    = trades
            all_summaries[coin] = summary
            print(f"  [{coin}] {len(trades)} simulated trades  "
                  f"WR={summary['win_rate']:.1f}%  "
                  f"PF={summary['profit_factor']:.2f}  "
                  f"MaxDD={summary['max_drawdown_pct']:.1f}%")
        except Exception as e:
            print(f"[{coin}] BACKTEST ERROR: {e}")
            import traceback; traceback.print_exc()
            all_trades[coin]    = pd.DataFrame()
            all_summaries[coin] = {"coin": coin, "trades": 0, "win_rate": 0.0,
                                    "profit_factor": 0.0, "max_drawdown_pct": 0.0}

    # ── Summary table ──────────────────────────────────────────────────────────
    print(f"\n{'─'*65}")
    print(f"  {'Coin':<6} {'Trades':>7} {'WR':>8} {'PF':>7} {'Max DD':>9}")
    print(f"  {'─'*6} {'─'*7} {'─'*8} {'─'*7} {'─'*9}")
    for coin in COINS:
        s = all_summaries.get(coin, {})
        flag = "  ⚠ BELOW THRESHOLD" if s.get("win_rate", 0) < WIN_RATE_THRESHOLD and s.get("trades", 0) > 0 else ""
        print(f"  {coin:<6} {s.get('trades',0):>7,}  "
              f"{s.get('win_rate',0):>6.1f}%  "
              f"{s.get('profit_factor',0):>6.2f}  "
              f"{s.get('max_drawdown_pct',0):>7.1f}%"
              f"{flag}")
    print(f"{'─'*65}")

    _section("STEP 4 — GENERATE BOOTSTRAP LEARNING FILES")

    all_learnings: dict = {}
    for coin in COINS:
        enriched = all_enriched.get(coin, {})
        one_hour = enriched.get("one_hour", pd.DataFrame())
        trades   = all_trades.get(coin, pd.DataFrame())

        if one_hour.empty or trades.empty:
            print(f"[{coin}] Insufficient data — skipping bootstrap.")
            all_learnings[coin] = {}
            continue

        try:
            learning = generate_bootstrap_learning(coin, one_hour, trades)
            all_learnings[coin] = learning
        except Exception as e:
            print(f"[{coin}] BOOTSTRAP ERROR: {e}")
            import traceback; traceback.print_exc()
            all_learnings[coin] = {}

    _section("STEP 5 — PATTERN ANALYSIS")

    for coin in COINS:
        learning = all_learnings.get(coin, {})
        if not learning:
            print(f"\n  [{coin}] No learning data available.")
            continue
        report_top_patterns(coin, learning, n=5)

    _section("FLAGS & RECOMMENDATIONS")

    any_flagged = False
    for coin in COINS:
        s = all_summaries.get(coin, {})
        wr = s.get("win_rate", 0)
        trades_n = s.get("trades", 0)
        if trades_n == 0:
            print(f"\n  ⚠  {coin}: No trades generated in backtest.")
            print(f"       → The filter rules are too restrictive or data is insufficient.")
            print(f"       → Consider widening ADX range ({MIN_ADX_LONG}–{MAX_ADX_LONG}) or DI gap ({DI_GAP_MIN} pts).")
            any_flagged = True
        elif wr < WIN_RATE_THRESHOLD:
            print(f"\n  ⚠  {coin}: backtest WR {wr:.1f}% is below {WIN_RATE_THRESHOLD:.0f}% threshold.")
            pf = s.get("profit_factor", 0)
            dd = s.get("max_drawdown_pct", 0)
            print(f"       Profit factor: {pf:.2f}  |  Max drawdown: {dd:.1f}%")
            # Coin-specific guidance
            if wr < 35:
                print(f"       → DO NOT GO LIVE with {coin} on these rules.")
                print(f"       → Suggested fix: tighten HTF trend filter — only trade when")
                print(f"         daily AND 4h trend agree with direction.")
            else:
                print(f"       → Marginal. Consider adding sentiment filter or tightening ADX range.")
                print(f"       → Review worst pattern keys above and hard-block them.")
            any_flagged = True

    if not any_flagged:
        print(f"\n  ✓  All coins passed the {WIN_RATE_THRESHOLD:.0f}% win rate threshold.")
        print(f"     Bootstrap learning files are active and guardrails will use them.")

    print(f"\n{'═'*65}")
    print(f"  Bootstrap complete.")
    print(f"  Learning files written: " +
          ", ".join(f"{c.lower()}_learning.json" for c in COINS
                    if all_learnings.get(c)))
    print(f"  Live trading is PAUSED (LIVE_TRADING_ENABLED=False in config.py).")
    print(f"  Review results, adjust rules if needed, then set LIVE_TRADING_ENABLED=True.")
    print(f"{'═'*65}\n")


# Import constants used in flag messages
from backtest import MIN_ADX_LONG, MAX_ADX_LONG, DI_GAP_MIN

if __name__ == "__main__":
    main()

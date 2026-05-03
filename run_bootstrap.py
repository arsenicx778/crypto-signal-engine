"""
run_bootstrap.py

Full historical bootstrap pipeline:
  1. Fetch maximum free-tier history from Kraken (with caching)
  2. Compute all technical indicators + HTF trend signals
  3. Run backtest simulation on 4-hour candles (primary) and daily candles (secondary)
  4. Generate bootstrap learning files for the guardrails
  5. Print summary tables, regime breakdowns and flag under-performing coins

Usage:
  python run_bootstrap.py
"""

import sys
import os
import pandas as pd

from historical_fetch import fetch_max_history, compute_all_indicators
from backtest import (
    run_backtest, run_backtest_daily, summarize_backtest,
    regime_breakdown, format_regime_report
)
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


def _rolling_trend(close_series: pd.Series, window: int) -> list:
    """Compute a simple rolling trend: BULLISH/BEARISH/NEUTRAL over `window` bars."""
    result = []
    for i in range(len(close_series)):
        if i < window - 1:
            result.append("NEUTRAL")
            continue
        seg = close_series.iloc[i - window + 1: i + 1]
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
            four_hour = enriched.get("four_hour", pd.DataFrame())
            if not four_hour.empty:
                last = four_hour.iloc[-1]
                print(f"  [{coin}] 4h latest: RSI={last.get('rsi', float('nan')):.1f}  "
                      f"ADX={last.get('adx', float('nan')):.1f}  "
                      f"DI+={last.get('di_plus', float('nan')):.1f}  "
                      f"DI-={last.get('di_minus', float('nan')):.1f}  "
                      f"daily_trend={last.get('htf_trend_daily', 'N/A')}")
        except Exception as e:
            print(f"[{coin}] INDICATOR ERROR: {e}")
            all_enriched[coin] = {}

    _section("STEP 3 — BACKTEST SIMULATION")
    print("  Note: using 4-hour candles (120 days) as primary backtest timeframe.")
    print("        Also running daily candles as secondary backtest.\n")

    all_trades:         dict = {}
    all_summaries:      dict = {}
    all_trades_daily:   dict = {}
    all_summaries_daily: dict = {}

    for coin in COINS:
        enriched  = all_enriched.get(coin, {})
        four_hour = enriched.get("four_hour", pd.DataFrame())
        daily_df  = enriched.get("daily",     pd.DataFrame())
        one_hour  = enriched.get("one_hour",  pd.DataFrame())

        # ── Attach HTF trend columns to 4-hour frame ──────────────────────────
        if not four_hour.empty:
            from historical_fetch import _htf_trend_series, _detect_trend

            # htf_trend_daily: forward-fill from daily trend onto 4h timestamps
            if not daily_df.empty:
                four_hour["htf_trend_daily"] = _htf_trend_series(
                    daily_df, four_hour, "htf_trend_daily", 5
                )
            else:
                four_hour["htf_trend_daily"] = "NEUTRAL"

            # htf_trend_1h: forward-fill from 1h trend onto 4h timestamps
            if not one_hour.empty:
                # Compute 1h self-trend first if not present
                if "htf_trend_1h" not in one_hour.columns:
                    trends_1h = []
                    for i in range(len(one_hour)):
                        window = one_hour["close"].iloc[max(0, i-2):i+1]
                        trends_1h.append(_detect_trend(window, 3))
                    one_hour["htf_trend_1h"] = trends_1h
                four_hour["htf_trend_1h"] = _htf_trend_series(
                    one_hour, four_hour, "htf_trend_1h", 3
                )
            else:
                four_hour["htf_trend_1h"] = "NEUTRAL"

            # htf_trend_4h: 4-bar rolling self-trend on 4h candle
            four_hour["htf_trend_4h"] = _rolling_trend(four_hour["close"], 4)

        # ── Primary backtest on 4h candles ────────────────────────────────────
        if four_hour.empty:
            print(f"[{coin}] No 4h data — skipping primary backtest.")
            all_trades[coin]    = pd.DataFrame()
            all_summaries[coin] = {"coin": coin, "trades": 0, "win_rate": 0.0,
                                    "profit_factor": 0.0, "max_drawdown_pct": 0.0}
        else:
            print(f"[{coin}] Running 4h backtest on {len(four_hour)} candles...")
            try:
                trades = run_backtest(coin, four_hour)
                summary = summarize_backtest(coin, trades)
                all_trades[coin]    = trades
                all_summaries[coin] = summary
                print(f"  [{coin}] 4h: {len(trades)} simulated trades  "
                      f"WR={summary['win_rate']:.1f}%  "
                      f"PF={summary['profit_factor']:.2f}  "
                      f"MaxDD={summary['max_drawdown_pct']:.1f}%")

                # Regime breakdown
                bd = regime_breakdown(trades, four_hour)
                format_regime_report(coin, summary, bd, "4-hour (120 days)")

            except Exception as e:
                print(f"[{coin}] BACKTEST ERROR: {e}")
                import traceback; traceback.print_exc()
                all_trades[coin]    = pd.DataFrame()
                all_summaries[coin] = {"coin": coin, "trades": 0, "win_rate": 0.0,
                                        "profit_factor": 0.0, "max_drawdown_pct": 0.0}

        # ── Secondary backtest on daily candles ───────────────────────────────
        if daily_df.empty:
            print(f"[{coin}] No daily data — skipping daily backtest.")
            all_trades_daily[coin]    = pd.DataFrame()
            all_summaries_daily[coin] = {"coin": coin, "trades": 0, "win_rate": 0.0,
                                          "profit_factor": 0.0, "max_drawdown_pct": 0.0}
        else:
            # Prepare daily df with 3-bar rolling self-trend
            daily_enriched = daily_df.copy()
            daily_enriched["htf_trend_daily"] = _rolling_trend(daily_enriched["close"], 3)
            daily_enriched["htf_trend_1h"]    = "NEUTRAL"
            daily_enriched["htf_trend_4h"]    = "NEUTRAL"

            print(f"[{coin}] Running daily backtest on {len(daily_enriched)} candles...")
            try:
                trades_d  = run_backtest_daily(coin, daily_enriched)
                summary_d = summarize_backtest(coin, trades_d)
                all_trades_daily[coin]    = trades_d
                all_summaries_daily[coin] = summary_d
                print(f"  [{coin}] daily: {len(trades_d)} simulated trades  "
                      f"WR={summary_d['win_rate']:.1f}%  "
                      f"PF={summary_d['profit_factor']:.2f}  "
                      f"MaxDD={summary_d['max_drawdown_pct']:.1f}%")

                bd_d = regime_breakdown(trades_d, daily_enriched)
                format_regime_report(coin, summary_d, bd_d, "daily (720 days)")

            except Exception as e:
                print(f"[{coin}] DAILY BACKTEST ERROR: {e}")
                import traceback; traceback.print_exc()
                all_trades_daily[coin]    = pd.DataFrame()
                all_summaries_daily[coin] = {"coin": coin, "trades": 0, "win_rate": 0.0,
                                              "profit_factor": 0.0, "max_drawdown_pct": 0.0}

    # ── Combined summary table ─────────────────────────────────────────────────
    print(f"\n{'─'*80}")
    print(f"  {'Coin':<6} {'4h Trades':>10} {'4h WR':>8} {'4h PF':>7} "
          f"{'D Trades':>10} {'D WR':>8} {'D PF':>7}")
    print(f"  {'─'*6} {'─'*10} {'─'*8} {'─'*7} {'─'*10} {'─'*8} {'─'*7}")
    for coin in COINS:
        s  = all_summaries.get(coin, {})
        sd = all_summaries_daily.get(coin, {})
        flag4 = " ⚠" if s.get("win_rate", 0) < WIN_RATE_THRESHOLD and s.get("trades", 0) > 0 else ""
        flagd = " ⚠" if sd.get("win_rate", 0) < WIN_RATE_THRESHOLD and sd.get("trades", 0) > 0 else ""
        print(f"  {coin:<6} {s.get('trades',0):>10,}  "
              f"{s.get('win_rate',0):>6.1f}%{flag4}  "
              f"{s.get('profit_factor',0):>6.2f}  "
              f"{sd.get('trades',0):>10,}  "
              f"{sd.get('win_rate',0):>6.1f}%{flagd}  "
              f"{sd.get('profit_factor',0):>6.2f}")
    print(f"{'─'*80}")

    _section("STEP 4 — GENERATE BOOTSTRAP LEARNING FILES")

    all_learnings: dict = {}
    for coin in COINS:
        enriched  = all_enriched.get(coin, {})
        four_hour = enriched.get("four_hour", pd.DataFrame())
        trades    = all_trades.get(coin, pd.DataFrame())

        if four_hour.empty or trades.empty:
            print(f"[{coin}] Insufficient 4h data or no trades — skipping bootstrap.")
            all_learnings[coin] = {}
            continue

        try:
            learning = generate_bootstrap_learning(coin, four_hour, trades)
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
        s  = all_summaries.get(coin, {})
        sd = all_summaries_daily.get(coin, {})
        wr4  = s.get("win_rate", 0)
        wrd  = sd.get("win_rate", 0)
        t4   = s.get("trades", 0)
        td   = sd.get("trades", 0)

        if t4 == 0:
            print(f"\n  ⚠  {coin}: No 4h trades generated in backtest.")
            print(f"       → Filter rules may be too restrictive or data is insufficient.")
            print(f"       → Consider widening ADX range ({MIN_ADX_LONG}–{MAX_ADX_LONG}) or DI gap ({DI_GAP_MIN} pts).")
            any_flagged = True
        elif wr4 < WIN_RATE_THRESHOLD:
            print(f"\n  ⚠  {coin}: 4h backtest WR {wr4:.1f}% is below {WIN_RATE_THRESHOLD:.0f}% threshold.")
            pf = s.get("profit_factor", 0)
            dd = s.get("max_drawdown_pct", 0)
            print(f"       Profit factor: {pf:.2f}  |  Max drawdown: {dd:.1f}%")
            if wr4 < 35:
                print(f"       → DO NOT GO LIVE with {coin} on these rules.")
                print(f"       → Suggested fix: tighten HTF trend filter — only trade when")
                print(f"         daily AND 4h trend agree with direction.")
            else:
                print(f"       → Marginal. Consider adding sentiment filter or tightening ADX range.")
                print(f"       → Review worst pattern keys above and hard-block them.")
            any_flagged = True

        if td == 0:
            print(f"\n  ⚠  {coin}: No daily trades generated in backtest.")
            any_flagged = True
        elif wrd < WIN_RATE_THRESHOLD:
            print(f"\n  ⚠  {coin}: daily backtest WR {wrd:.1f}% is below {WIN_RATE_THRESHOLD:.0f}% threshold.")
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

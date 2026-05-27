"""
experiment/runner.py — A/B/C/D variant test runner.

Runs all four variants on ETH in shadow (paper) mode every cycle.
Saves signals to trades.db with variant tag. Monitors price to resolve W/L.
Original main.py, step10_brain.py, step11_guardrails.py are NOT modified.

To stop: kill this process. To disable permanently: set EXPERIMENT_ACTIVE=False
in experiment/config.py, or delete the experiment/ folder.
"""

import sys
import os
import json
import time
import threading
import signal as signal_module
from datetime import datetime

# Add engine root to path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from dotenv import load_dotenv
load_dotenv()

# Engine pipeline imports (originals — read-only)
from step1_fetch import fetch_candles, fetch_news
from step2_validate import validate_data
from step3_compute import compute_indicators
from step7_sentiment import score_sentiment
from step8_history import load_history
from step9_gate import pre_signal_gate, compute_atr_sizing
from time_utils import now_pacific_str

# Experiment-local imports
from experiment.config import EXPERIMENT_ACTIVE, EXPERIMENT_COIN, VARIANTS, CYCLE_INTERVAL_SECONDS
from experiment.variant_brain import generate_signal_for_variant
from experiment.variant_guards import apply_guardrails_for_variant
from experiment.results import (
    save_experiment_signal,
    close_experiment_trade,
    get_pending_experiment_trades,
)

_RUNNING = True


def _stop_handler(signum, frame):
    global _RUNNING
    print("\n[EXPERIMENT] Stopping experiment runner...")
    _RUNNING = False


signal_module.signal(signal_module.SIGINT,  _stop_handler)
signal_module.signal(signal_module.SIGTERM, _stop_handler)


def _build_metadata(signal_data: dict, filtered_indicators: dict, variant: str) -> str:
    """Build JSON metadata string for the trade row."""
    reasoning = signal_data.get("reasoning", {})
    ind_str = " | ".join(
        f"{k}: {v}" for k, v in filtered_indicators.items() if k != "momentum_context"
    )
    meta = {
        "ta_summary":         reasoning.get("ta_summary", ""),
        "sentiment_summary":  reasoning.get("sentiment_summary", ""),
        "history_summary":    reasoning.get("history_summary", ""),
        "decision_rationale": reasoning.get("decision_rationale", ""),
        "indicators":         ind_str,
        "variant":            variant,
        "tp_adjustments":     0,
        "tp_adjustment_log":  None,
    }
    return json.dumps(meta)


def _monitor_price_shadow(
    trade_id: int,
    variant: str,
    coin_name: str,
    symbol: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    direction: str,
):
    """
    Shadow price monitor — polls Kraken price and closes trade
    when SL or TP is hit. Runs in a daemon thread.
    """
    import requests

    KRAKEN_PAIRS = {
        "ETH": "XETHZUSD",
        "SOL": "SOLUSD",
        "LINK": "LINKUSD",
        "XRP": "XXRPZUSD",
    }
    pair = KRAKEN_PAIRS.get(coin_name, symbol)
    url  = f"https://api.kraken.com/0/public/Ticker?pair={pair}"

    print(
        f"[EXPERIMENT:{variant}] monitoring trade_id={trade_id} "
        f"{direction} entry={entry_price} SL={stop_loss} TP={take_profit}"
    )

    max_iterations = 480  # 24 hours at 3-min polls
    iterations = 0

    while _RUNNING and iterations < max_iterations:
        time.sleep(180)
        iterations += 1
        try:
            resp = requests.get(url, timeout=10)
            data = resp.json()
            result = data.get("result", {})
            ticker = next(iter(result.values()), {})
            price = float(ticker.get("c", [0])[0])
        except Exception as e:
            print(f"[EXPERIMENT:{variant}] price fetch error: {e}")
            continue

        if price <= 0:
            continue

        hit_sl = hit_tp = False
        if direction == "LONG":
            hit_sl = price <= stop_loss
            hit_tp = price >= take_profit
        else:  # SHORT
            hit_sl = price >= stop_loss
            hit_tp = price <= take_profit

        if hit_tp or hit_sl:
            outcome    = "W" if hit_tp else "L"
            close_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            close_experiment_trade(trade_id, price, close_time, outcome)
            print(
                f"[EXPERIMENT:{variant}] trade_id={trade_id} CLOSED "
                f"{'TP HIT' if hit_tp else 'SL HIT'} @ {price} → {outcome}"
            )
            return

    # Time-stop: close at last known price as a loss if never resolved
    if iterations >= max_iterations:
        close_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        try:
            resp  = requests.get(url, timeout=10)
            data  = resp.json()
            result = data.get("result", {})
            ticker = next(iter(result.values()), {})
            price  = float(ticker.get("c", [0])[0])
        except Exception:
            price = entry_price
        close_experiment_trade(trade_id, price, close_time, "L")
        print(f"[EXPERIMENT:{variant}] trade_id={trade_id} TIME-STOP closed as L @ {price}")


def run_variant_cycle(
    variant: str,
    variant_cfg: dict,
    coin: dict,
    candles_result: dict,
    news_result: dict,
    sentiment_data: dict,
):
    """
    Run one full pipeline cycle for a single variant.
    Candles, news, and sentiment are pre-fetched and shared across variants
    to avoid redundant API calls.
    """
    coin_name = coin["name"]
    symbol    = coin["symbol"]
    label     = variant_cfg["label"]

    print(f"\n[EXPERIMENT:{variant}] {label} — {coin_name} cycle starting")

    # STEP 2 — Validate (shared data already fetched)
    validation = validate_data(candles_result, news_result)
    if not validation["valid"]:
        print(f"[EXPERIMENT:{variant}] validation failed: {validation['errors']}")
        return

    # STEP 3 — Compute all indicators
    compute_result = compute_indicators(candles_result["data"])
    if not compute_result["success"]:
        print(f"[EXPERIMENT:{variant}] compute failed: {compute_result['error']}")
        return
    all_indicators = compute_result["data"]

    # STEP 4 — ATR sizing (before brain, same as live engine)
    sizing = compute_atr_sizing(all_indicators, coin_name)
    if not sizing:
        print(f"[EXPERIMENT:{variant}] ATR sizing unavailable — skipping")
        return

    # STEP 5 — History
    raw_history = load_history(n=10, coin_name=coin_name)

    # STEP 6 — Gate
    capital_start = coin["capital"]
    gate = pre_signal_gate(coin_name=coin_name, capital_start=capital_start)
    capital       = gate.get("capital", capital_start)
    risk_amount   = gate.get("risk_amount",  round(capital * 0.015, 2))
    reward_amount = gate.get("reward_amount", round(capital * 0.020, 2))

    # STEP 7 — Brain (routed per variant)
    signal_result = generate_signal_for_variant(
        variant             = variant,
        variant_cfg         = variant_cfg,
        filtered_indicators = all_indicators,
        sentiment           = sentiment_data,
        raw_history         = raw_history,
        capital             = capital,
        risk_amount         = risk_amount,
        reward_amount       = reward_amount,
        pre_sizing          = sizing,
        coin_name           = coin_name,
        coin_symbol         = symbol,
    )
    if not signal_result["success"]:
        print(f"[EXPERIMENT:{variant}] brain failed: {signal_result.get('error')}")
        return

    # STEP 8 — Guardrails (with variant-specific overrides)
    guarded = apply_guardrails_for_variant(
        variant             = variant,
        variant_cfg         = variant_cfg,
        signal_result       = signal_result,
        filtered_indicators = all_indicators,
        coin_name           = coin_name,
    )

    sig_data  = guarded["data"]
    sig       = sig_data.get("signal", "Do Not Enter")
    direction = "LONG" if sig == "Buy" else ("SHORT" if sig == "Sell" else None)
    sig_data["direction"] = direction

    # STEP 9 — Inject pre-computed SL/TP (replaces old apply_atr_stops call)
    if sig == "Buy":
        sig_data["entry_price"] = sizing["entry"]
        sig_data["stop_loss"]   = sizing["long_sl"]
        sig_data["take_profit"] = sizing["long_tp"]
    elif sig == "Sell":
        sig_data["entry_price"] = sizing["entry"]
        sig_data["stop_loss"]   = sizing["short_sl"]
        sig_data["take_profit"] = sizing["short_tp"]

    confidence   = sig_data.get("confidence", 0)
    entry_price  = sig_data.get("entry_price")
    stop_loss    = sig_data.get("stop_loss")
    take_profit  = sig_data.get("take_profit")
    timestamp    = now_pacific_str()
    metadata     = _build_metadata(sig_data, all_indicators, variant)

    # Save to DB
    trade_id = save_experiment_signal(
        variant      = variant,
        coin         = coin_name,
        signal       = sig,
        direction    = direction,
        confidence   = confidence,
        entry_price  = entry_price,
        stop_loss    = stop_loss,
        take_profit  = take_profit,
        risk_amount  = risk_amount,
        reward_amount= reward_amount,
        metadata     = metadata,
        timestamp    = timestamp,
    )

    print(
        f"[EXPERIMENT:{variant}] saved trade_id={trade_id} "
        f"signal={sig} conf={confidence} "
        f"entry={entry_price} SL={stop_loss} TP={take_profit}"
    )

    # Start shadow price monitor for active trades
    if sig in ("Buy", "Sell") and trade_id and entry_price and stop_loss and take_profit:
        t = threading.Thread(
            target=_monitor_price_shadow,
            args=(trade_id, variant, coin_name, symbol,
                  entry_price, stop_loss, take_profit, direction),
            daemon=True,
        )
        t.start()


def run_all_variants():
    """Fetch shared data once then run all 4 variants in parallel threads."""
    if not EXPERIMENT_ACTIVE:
        print("[EXPERIMENT] EXPERIMENT_ACTIVE=False — runner is disabled")
        return

    coin = EXPERIMENT_COIN
    active_variants = {k: v for k, v in VARIANTS.items() if not v.get("disabled")}
    disabled_keys   = [k for k, v in VARIANTS.items() if v.get("disabled")]
    print(f"\n[EXPERIMENT] {'='*50}")
    print(f"[EXPERIMENT] Starting cycle — {now_pacific_str()}")
    print(f"[EXPERIMENT] Coin: {coin['name']} | Active: {list(active_variants.keys())}"
          + (f" | Disabled: {disabled_keys}" if disabled_keys else ""))

    # Fetch once, share across all variants
    candles_result = fetch_candles(symbol=coin["symbol"])
    news_result    = fetch_news()
    sentiment      = score_sentiment(news_result.get("data", {}), coin_name=coin["name"])
    sentiment_data = sentiment.get("data", {})

    threads = []
    for variant, variant_cfg in active_variants.items():
        t = threading.Thread(
            target=run_variant_cycle,
            args=(variant, variant_cfg, coin, candles_result, news_result, sentiment_data),
            daemon=True,
        )
        t.start()
        threads.append(t)

    for t in threads:
        t.join(timeout=120)

    print(f"[EXPERIMENT] Cycle complete — {now_pacific_str()}")


def main():
    print("[EXPERIMENT] A/B/C/D runner starting")
    print(f"[EXPERIMENT] Variants: {list(VARIANTS.keys())}")
    print(f"[EXPERIMENT] Coin: {EXPERIMENT_COIN['name']} only")
    print(f"[EXPERIMENT] Cycle interval: {CYCLE_INTERVAL_SECONDS}s")
    print(f"[EXPERIMENT] Press Ctrl+C to stop\n")

    while _RUNNING:
        try:
            run_all_variants()
        except Exception as e:
            print(f"[EXPERIMENT] Unhandled error in cycle: {e}")
        if _RUNNING:
            print(f"[EXPERIMENT] Sleeping {CYCLE_INTERVAL_SECONDS}s until next cycle...")
            time.sleep(CYCLE_INTERVAL_SECONDS)

    print("[EXPERIMENT] Runner stopped cleanly.")


if __name__ == "__main__":
    main()

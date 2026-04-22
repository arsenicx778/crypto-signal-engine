import sys
import signal as signal_module
import threading
from apscheduler.schedulers.blocking import BlockingScheduler
from step_learn import run_learning_cycle
from engine_state import (
    load_state, save_state, reconcile_state_with_csv, increment_cycle
)
from step1_fetch import fetch_candles, fetch_news
from step2_validate import validate_data
from step3_compute import compute_indicators
from step4_merge import merge_indicators
from step5_select import select_indicators
from step6_filter import filter_indicators
from step7_sentiment import score_sentiment
from step8_history import load_history, summarize_history
from step9_gate import pre_signal_gate
from step10_brain import generate_signal
from step11_guardrails import apply_guardrails
from step12_output import save_signal, monitor_price, resume_open_trade_monitor
from step_tp_adjust import run_tp_adjustment
from time_utils import now_pacific_str

# ── Coin config ───────────────────────────────────────────────────────────────
COINS = [
    {"symbol": "XETHZUSD", "name": "ETH",  "capital": 1000},
    {"symbol": "SOLUSD",   "name": "SOL",  "capital": 1000},
    {"symbol": "AVAXUSD",  "name": "AVAX", "capital": 1000},
    {"symbol": "XXRPZUSD", "name": "XRP",  "capital": 1000},
]

COIN_CSV = {
    "ETH":  "eth_signals.csv",
    "SOL":  "sol_signals.csv",
    "AVAX": "avax_signals.csv",
    "XRP":  "xrp_signals.csv",
}

COIN_CSV_MAP = COIN_CSV

SCHEDULER = None


def stop_engine(reason):
    global SCHEDULER
    print(f"[STOP] {reason}")
    if SCHEDULER and SCHEDULER.running:
        SCHEDULER.shutdown(wait=False)


def run_cycle(coin):
    symbol      = coin["symbol"]
    coin_name   = coin["name"]
    signals_file = COIN_CSV[coin_name]

    print(f"\n[{now_pacific_str()}] [{coin_name}] Starting cycle...")

    # STEP 1 — Fetch (candles per coin, news shared)
    candles_result = fetch_candles(symbol=symbol)
    news_result    = fetch_news()

    # STEP 2 — Validate
    validation = validate_data(candles_result, news_result)
    if not validation["valid"]:
        print(f"[{coin_name}][SKIP] Validation failed: {validation['errors']}")
        return

    # STEP 3 — Compute all indicators
    compute_result = compute_indicators(candles_result["data"])
    if not compute_result["success"]:
        print(f"[{coin_name}][SKIP] Compute failed: {compute_result['error']}")
        return

    # STEP 4 — Merge
    merged = merge_indicators(compute_result)

    # STEP 5 — Claude selects relevant indicators
    selection = select_indicators(merged["data"])

    # STEP 6 — Filter to selected only
    filtered = filter_indicators(merged["data"], selection)

    # STEP 7 — Score sentiment
    sentiment = score_sentiment(news_result["data"])

    # STEP 8 — Load and summarize history (coin-specific CSV)
    history         = load_history(n=10, signals_file=signals_file)
    history_summary = summarize_history(history)

    # ── TP ADJUSTMENT CHECK ─────────────────────────────────────────
    run_tp_adjustment(sentiment["data"], signals_file=signals_file, symbol=symbol)

    capital_start = coin["capital"]

    # STEP 9 — Pre-signal gate (per-coin, independent)
    # risk/reward are now calculated as % of current capital inside gate
    gate = pre_signal_gate(
        signals_file=signals_file,
        coin_name=coin_name,
        capital_start=capital_start,
    )

    if not gate["proceed"]:
        print(f"[{coin_name}][SKIP] Gate blocked: {gate['reason']}")
        return

    capital        = gate.get("capital", capital_start)
    risk_amount    = gate.get("risk_amount",   round(capital * 0.02, 2))
    reward_amount  = gate.get("reward_amount", round(capital * 0.03, 2))

    # STEP 10 — Signal brain
    signal_result = generate_signal(
        filtered["data"],
        sentiment["data"],
        history_summary["data"],
        capital,
        risk_amount,
        reward_amount=reward_amount,
        coin_name=coin_name,
        coin_symbol=symbol,
    )
    if not signal_result["success"]:
        print(f"[{coin_name}][ERROR] Brain failed: {signal_result['error']}")
        return  # One coin failure does not stop other coins

    # STEP 11 — Guardrails (pass indicators for Sell-specific checks)
    guarded = apply_guardrails(signal_result, filtered_indicators=filtered["data"])

    # Attach direction field before saving
    sig = guarded["data"]["signal"]
    if sig == "Buy":
        guarded["data"]["direction"] = "LONG"
    elif sig == "Sell":
        guarded["data"]["direction"] = "SHORT"
    else:
        guarded["data"]["direction"] = None

    # STEP 12 — Save output (coin-specific CSV)
    row = save_signal(
        guarded["data"],
        guarded["overrides"],
        filtered["data"],
        signals_file=signals_file,
        symbol=symbol,
        coin_name=coin_name,
        risk_per_trade=risk_amount,
        reward_per_trade=reward_amount,
        capital=capital,
    )

    # Start price monitor for Buy and Sell signals
    if sig in ("Buy", "Sell"):
        direction = "SHORT" if sig == "Sell" else "LONG"
        monitor_price(
            row["timestamp"],
            guarded["data"]["stop_loss"],
            guarded["data"]["take_profit"],
            symbol=symbol,
            signals_file=signals_file,
            coin_name=coin_name,
            direction=direction,
        )

    print(f"[{coin_name}][DONE] Cycle complete.")


def run_all_cycles():
    """Run all four coin cycles in parallel, wait for all to finish."""
    global engine_state
    threads = []

    def run_coin_safe(coin):
        try:
            run_cycle(coin)
        except Exception as e:
            print(f"[{coin['name']}][ERROR] Unhandled cycle error: {e}")

    for coin in COINS:
        t = threading.Thread(target=run_coin_safe, args=(coin,), daemon=True)
        threads.append(t)

    for t in threads:
        t.start()

    for t in threads:
        t.join()

    engine_state = increment_cycle(engine_state)
    run_learning_cycle([c["name"] for c in COINS], engine_state["cycle_counter"],
                       every_n_cycles=1)
    save_state(engine_state)


if __name__ == "__main__":
    print("Starting AI Crypto Day Trading Signal Engine...")
    print("Coins: ETH | SOL | AVAX | XRP")
    print("Strategy: small frequent wins | 2% risk per trade | 1.5:1 reward:risk")
    print("─" * 55)

    # Load and reconcile engine state
    engine_state = load_state()
    engine_state = reconcile_state_with_csv(engine_state, COIN_CSV_MAP)
    save_state(engine_state)
    print(f"[STATE] Loaded. Cycle: {engine_state['cycle_counter']}")
    for coin in COINS:
        cs = engine_state["coins"].get(coin["name"], {})
        print(f"[STATE] {coin['name']}: capital=${cs.get('capital', 1000):.2f} "
              f"L:{cs.get('open_longs', 0)} S:{cs.get('open_shorts', 0)}")

    # Shutdown handler
    def shutdown_handler(sig, frame):
        print("\n[STATE] Saving state on shutdown...")
        save_state(engine_state)
        print("[STATE] Done. Exiting.")
        sys.exit(0)
    signal_module.signal(signal_module.SIGINT, shutdown_handler)
    signal_module.signal(signal_module.SIGTERM, shutdown_handler)

    # Resume open trade monitors for all coins on startup
    for coin in COINS:
        resume_open_trade_monitor(
            signals_file=COIN_CSV[coin["name"]],
            symbol=coin["symbol"],
            coin_name=coin["name"],
        )

    run_all_cycles()

    scheduler = BlockingScheduler()
    scheduler.add_job(
        run_all_cycles,
        "interval",
        minutes=3,
        misfire_grace_time=120,
        max_instances=1,
        coalesce=True
    )
    print("Scheduler started — running every 3 minutes. Press Ctrl+C to stop.")
    scheduler.start()

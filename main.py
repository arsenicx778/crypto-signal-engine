from apscheduler.schedulers.blocking import BlockingScheduler
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

SCHEDULER = None


def stop_engine(reason):
    global SCHEDULER
    print(f"[STOP] {reason}")
    if SCHEDULER and SCHEDULER.running:
        SCHEDULER.shutdown(wait=False)


def run_cycle():
    print(f"\n[{now_pacific_str()}] Starting cycle...")

    # STEP 1 — Fetch
    candles_result = fetch_candles()
    news_result    = fetch_news()

    # STEP 2 — Validate
    validation = validate_data(candles_result, news_result)
    if not validation["valid"]:
        print(f"[SKIP] Validation failed: {validation['errors']}")
        return True

    # STEP 3 — Compute all indicators
    compute_result = compute_indicators(candles_result["data"])
    if not compute_result["success"]:
        print(f"[SKIP] Compute failed: {compute_result['error']}")
        return True

    # STEP 4 — Merge
    merged = merge_indicators(compute_result)

    # STEP 5 — Claude selects relevant indicators
    selection = select_indicators(merged["data"])

    # STEP 6 — Filter to selected only
    filtered = filter_indicators(merged["data"], selection)

    # STEP 7 — Score sentiment
    sentiment = score_sentiment(news_result["data"])

    # STEP 8 — Load and summarize history
    history         = load_history(n=10)
    history_summary = summarize_history(history)

    # ── TP ADJUSTMENT CHECK ──────────────────────────────────────────
    # If a trade is open check if strong sentiment warrants moving TP up
    run_tp_adjustment(sentiment["data"])

    # STEP 9 — Pre-signal gate
    # Blocks new signals if trade already open or cost cap hit
    gate = pre_signal_gate()

    if not gate["proceed"]:
        print(f"[SKIP] Gate blocked: {gate['reason']}")
        return True

    capital     = gate.get("capital", 1000.0)
    risk_amount = gate.get("risk_amount", 20.0)

    # STEP 10 — Signal brain
    signal_result = generate_signal(
        filtered["data"],
        sentiment["data"],
        history_summary["data"],
        capital,
        risk_amount
    )
    if not signal_result["success"]:
        stop_engine(f"Brain failed: {signal_result['error']}")
        return False

    # STEP 11 — Guardrails
    guarded = apply_guardrails(signal_result)

    # STEP 12 — Save output
    row = save_signal(guarded["data"], guarded["overrides"], filtered["data"])

    # Start price monitor for Buy signals
    if guarded["data"]["signal"] == "Buy":
        monitor_price(
            row["timestamp"],
            guarded["data"]["stop_loss"],
            guarded["data"]["take_profit"]
        )

    print("[DONE] Cycle complete.")
    return True

if __name__ == "__main__":
    print("Starting AI Crypto Day Trading Signal Engine...")
    print("Strategy: small frequent wins | 2% risk per trade | 1:1 reward:risk")
    print("─" * 55)
    resume_open_trade_monitor()
    if not run_cycle():
        print("Engine stopped.")
        raise SystemExit(1)

    scheduler = BlockingScheduler()
    SCHEDULER = scheduler
    scheduler.add_job(run_cycle, "interval", minutes=5)
    print("Scheduler started — running every 5 minutes. Press Ctrl+C to stop.")
    try:
        scheduler.start()
    except KeyboardInterrupt:
        print("Engine stopped.")

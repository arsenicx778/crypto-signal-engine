from dotenv import load_dotenv
load_dotenv()
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
from step7_sentiment import score_sentiment
from step8_history import load_history
from step9_gate import (
    pre_signal_gate, is_fully_blocked,
    technical_hard_gate, compute_atr_sizing,
)
from step10_brain import generate_signal
from step11_guardrails import apply_guardrails
from step12_output import save_signal, monitor_price, resume_open_trade_monitor, start_reconcile_loop
from step_tp_adjust import run_tp_adjustment
from time_utils import now_pacific_str

# ── Coin config ───────────────────────────────────────────────────────────────
COINS = [
    {"symbol": "XETHZUSD", "name": "ETH",  "capital": 1000},
    {"symbol": "SOLUSD",   "name": "SOL",  "capital": 1000},
    {"symbol": "LINKUSD",  "name": "LINK", "capital": 1000},
    {"symbol": "XXRPZUSD", "name": "XRP",  "capital": 1000},
]

# Kept for backward-compat references (CSV paths, legacy helpers)
COIN_CSV = {
    "ETH":  "eth_signals.csv",
    "SOL":  "sol_signals.csv",
    "LINK": "link_signals.csv",
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
    from config import LIVE_TRADING_ENABLED, LIVE_TRADING_PAUSE_REASON
    symbol        = coin["symbol"]
    coin_name     = coin["name"]
    signals_file  = COIN_CSV[coin_name]  # kept for compat args only
    capital_start = coin["capital"]

    if not LIVE_TRADING_ENABLED:
        print(f"[CYCLE:{coin_name}] PAUSED — {LIVE_TRADING_PAUSE_REASON}")
        return

    from config import SUSPENDED_COINS
    if coin_name in SUSPENDED_COINS:
        print(f"[CYCLE:{coin_name}] SUSPENDED — skipping")
        return

    print(f"\n[{now_pacific_str()}] [{coin_name}] Starting cycle...")

    # ── STEP 1: Fetch ─────────────────────────────────────────────────────────
    candles_result = fetch_candles(symbol=symbol)
    news_result    = fetch_news()

    # ── STEP 2: Validate ──────────────────────────────────────────────────────
    validation = validate_data(candles_result, news_result)
    if not validation["valid"]:
        print(f"[{coin_name}][SKIP] Validation failed: {validation['errors']}")
        return

    # ── EARLY GATE: position cap (no API calls consumed) ──────────────────────
    # Pass coin_name (not signals_file) — is_fully_blocked queries SQLite by coin.
    _blocked, _early_l, _early_s = is_fully_blocked(coin_name)
    if _blocked:
        print(f"[CYCLE:{coin_name}] positions full ({_early_l + _early_s}/2) — skip, no API calls")
        return

    # ── STEP 3: Compute all indicators ────────────────────────────────────────
    compute_result = compute_indicators(candles_result["data"])
    if not compute_result["success"]:
        print(f"[{coin_name}][SKIP] Compute failed: {compute_result['error']}")
        return
    all_indicators = compute_result["data"]

    # ── STEP 4: Technical hard gate (deterministic, no LLM) ──────────────────
    tech = technical_hard_gate(all_indicators, coin_name)
    if not tech["proceed"]:
        print(f"[CYCLE:{coin_name}] tech-gate DNE — {tech['reason']}")
        return

    # ── STEP 5: ATR sizing (pre-computed before brain) ────────────────────────
    sizing = compute_atr_sizing(all_indicators, coin_name)
    if not sizing:
        print(f"[{coin_name}][SKIP] ATR sizing failed — missing ATR or close price")
        return
    print(f"[CYCLE:{coin_name}] sizing: entry={sizing['entry']} "
          f"SL±{sizing['sl_dist']} TP±{sizing['tp_dist']} R:R={sizing['rr']}")

    # ── STEP 6: Sentiment (first LLM call — cached 15 min) ───────────────────
    sentiment      = score_sentiment(news_result["data"], coin_name=coin_name)
    _sent_cached   = sentiment.get("cached", False) or sentiment.get("data", {}).get("cached", False)

    # ── TP ADJUSTMENT on existing open long ───────────────────────────────────
    run_tp_adjustment(sentiment["data"], coin_name=coin_name, symbol=symbol)

    # ── STEP 7: Position / capital gate ──────────────────────────────────────
    gate = pre_signal_gate(coin_name=coin_name, capital_start=capital_start)
    if not gate["proceed"]:
        print(f"[{coin_name}][SKIP] Gate: {gate['reason']}")
        return

    capital      = gate.get("capital", capital_start)
    risk_amount  = gate.get("risk_amount",  round(capital * 0.015, 2))
    reward_amount= gate.get("reward_amount", round(capital * 0.020, 2))
    _open_l      = gate.get("open_longs", 0)
    _open_s      = gate.get("open_shorts", 0)

    # ── STEP 8: Load raw trade history (no LLM) ───────────────────────────────
    raw_history = load_history(n=10, coin_name=coin_name)

    # ── STEP 9: Brain — direction + confidence only ───────────────────────────
    signal_result = generate_signal(
        all_indicators=all_indicators,
        sentiment=sentiment["data"],
        raw_history=raw_history,
        capital=capital,
        risk_amount=risk_amount,
        reward_amount=reward_amount,
        pre_sizing=sizing,
        coin_name=coin_name,
        coin_symbol=symbol,
    )
    if not signal_result["success"]:
        print(f"[{coin_name}][ERROR] Brain failed: {signal_result['error']}")
        return

    _brain_sig  = signal_result["data"].get("signal", "?")
    _brain_conf = signal_result["data"].get("confidence", 0)

    # ── STEP 10: Guardrails — confidence + learning penalty ───────────────────
    guarded = apply_guardrails(
        signal_result,
        all_indicators=all_indicators,
        coin_name=coin_name,
    )

    sig = guarded["data"]["signal"]

    # ── STEP 11: Inject pre-computed SL/TP + direction ───────────────────────
    if sig == "Buy":
        guarded["data"]["entry_price"] = sizing["entry"]
        guarded["data"]["stop_loss"]   = sizing["long_sl"]
        guarded["data"]["take_profit"] = sizing["long_tp"]
        guarded["data"]["direction"]   = "LONG"
    elif sig == "Sell":
        guarded["data"]["entry_price"] = sizing["entry"]
        guarded["data"]["stop_loss"]   = sizing["short_sl"]
        guarded["data"]["take_profit"] = sizing["short_tp"]
        guarded["data"]["direction"]   = "SHORT"
    else:
        guarded["data"]["direction"]   = None

    # ── STEP 12: Save + monitor ───────────────────────────────────────────────
    row = save_signal(
        guarded["data"],
        guarded["overrides"],
        all_indicators,
        signals_file=signals_file,
        symbol=symbol,
        coin_name=coin_name,
        risk_per_trade=risk_amount,
        reward_per_trade=reward_amount,
        capital=capital,
    )

    if sig in ("Buy", "Sell"):
        monitor_price(
            row["timestamp"],
            guarded["data"]["stop_loss"],
            guarded["data"]["take_profit"],
            symbol=symbol,
            signals_file=signals_file,
            coin_name=coin_name,
            direction=guarded["data"]["direction"],
        )

    # ── Cycle summary ─────────────────────────────────────────────────────────
    _guard_log   = guarded["data"].get("guardrail_log", [])
    _guard_blocked = any("BLOCKED" in l for l in _guard_log)
    _guard_conf  = guarded["data"].get("confidence", 0)
    _guard_label = "guard(BLOCKED)" if _guard_blocked else f"guard(PASS {_guard_conf}%)"
    if sig in ("Buy", "Sell"):
        _dir   = "BUY" if sig == "Buy" else "SELL"
        _ep    = guarded["data"].get("entry_price")
        _sl    = guarded["data"].get("stop_loss")
        _tp    = guarded["data"].get("take_profit")
        _out   = f"output({_dir} @{_ep} SL:{_sl} TP:{_tp})"
    else:
        _out   = "output(DNE)"
    _sent_label = f"sentiment({'cache' if _sent_cached else 'fresh'})"
    print(
        f"[CYCLE:{coin_name}] fetch→validate→compute→tech-gate→sizing→"
        f"{_sent_label}→gate({_open_l}/{_open_s})→"
        f"brain({_brain_sig} {_brain_conf}%)→{_guard_label}→{_out}"
    )
    print(f"[{coin_name}][DONE] Cycle complete.")


def run_all_cycles():
    """Run all coin cycles in parallel, wait for all to finish."""
    global engine_state
    threads = []

    def run_coin_safe(coin):
        try:
            run_cycle(coin)
        except Exception as e:
            import traceback
            print(f"[{coin['name']}][ERROR] Unhandled cycle error: {e}")
            traceback.print_exc()

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
    print("Coins: ETH | SOL | LINK | XRP")
    from config import RISK_PERCENT, REWARD_RISK_RATIO
    print(f"Strategy: scalping | {RISK_PERCENT*100:.1f}% risk | {REWARD_RISK_RATIO}:1 R:R")
    print("─" * 55)

    engine_state = load_state()
    engine_state = reconcile_state_with_csv(engine_state, COIN_CSV_MAP)
    save_state(engine_state)
    print(f"[STATE] Loaded. Cycle: {engine_state['cycle_counter']}")
    for coin in COINS:
        cs = engine_state["coins"].get(coin["name"], {})
        print(f"[STATE] {coin['name']}: capital=${cs.get('capital', 1000):.2f} "
              f"L:{cs.get('open_longs', 0)} S:{cs.get('open_shorts', 0)}")

    def shutdown_handler(sig, frame):
        print("\n[STATE] Saving state on shutdown...")
        save_state(engine_state)
        print("[STATE] Done. Exiting.")
        sys.exit(0)
    signal_module.signal(signal_module.SIGINT,  shutdown_handler)
    signal_module.signal(signal_module.SIGTERM, shutdown_handler)

    for coin in COINS:
        resume_open_trade_monitor(
            signals_file=COIN_CSV[coin["name"]],
            symbol=coin["symbol"],
            coin_name=coin["name"],
        )

    start_reconcile_loop([
        {"name": c["name"], "symbol": c["symbol"], "signals_file": COIN_CSV[c["name"]]}
        for c in COINS
    ])

    run_all_cycles()

    scheduler = BlockingScheduler()
    scheduler.add_job(
        run_all_cycles,
        "interval",
        minutes=3,
        misfire_grace_time=120,
        max_instances=1,
        coalesce=True,
    )
    print("Scheduler started — running every 3 minutes. Press Ctrl+C to stop.")
    scheduler.start()

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
from step9_gate import pre_signal_gate, is_fully_blocked
from step10_brain import generate_signal, apply_atr_stops
from step11_guardrails import apply_guardrails
from step12_output import save_signal, monitor_price, resume_open_trade_monitor
from step_tp_adjust import run_tp_adjustment
from time_utils import now_pacific_str

# ── Coin config ───────────────────────────────────────────────────────────────
COINS = [
    {"symbol": "XETHZUSD", "name": "ETH",  "capital": 1000},
    {"symbol": "SOLUSD",   "name": "SOL",  "capital": 1000},
    {"symbol": "LINKUSD",  "name": "LINK", "capital": 1000},
    {"symbol": "XXRPZUSD", "name": "XRP",  "capital": 1000},
]

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
    symbol      = coin["symbol"]
    coin_name   = coin["name"]
    signals_file = COIN_CSV[coin_name]

    if not LIVE_TRADING_ENABLED:
        print(f"[CYCLE:{coin_name}] PAUSED — {LIVE_TRADING_PAUSE_REASON}")
        return

    print(f"\n[{now_pacific_str()}] [{coin_name}] Starting cycle...")
    _flow = {}  # collects per-step labels for the summary line

    # STEP 1 — Fetch (candles per coin, news shared)
    candles_result = fetch_candles(symbol=symbol)
    news_result    = fetch_news()
    _flow["fetch"] = "fetch"

    # STEP 2 — Validate
    validation = validate_data(candles_result, news_result)
    if not validation["valid"]:
        print(f"[{coin_name}][SKIP] Validation failed: {validation['errors']}")
        print(f"[CYCLE:{coin_name}] fetch→validate(FAIL)")
        return
    _flow["validate"] = "validate"

    # EARLY GATE — check position count before any Haiku calls
    _blocked, _early_l, _early_s = is_fully_blocked(signals_file)
    if _blocked:
        print(f"[CYCLE:{coin_name}] gate({_early_l + _early_s}/2 FULL) → skipped — no API calls made")
        return

    # STEP 3 — Compute all indicators
    compute_result = compute_indicators(candles_result["data"])
    if not compute_result["success"]:
        print(f"[{coin_name}][SKIP] Compute failed: {compute_result['error']}")
        print(f"[CYCLE:{coin_name}] fetch→validate→compute(FAIL)")
        return

    # STEP 4 — Merge
    merged = merge_indicators(compute_result)

    # STEP 5 — Claude selects relevant indicators
    selection = select_indicators(merged["data"], coin_name=coin_name)

    # STEP 6 — Filter to selected only
    filtered = filter_indicators(merged["data"], selection)
    _flow["compute"] = "compute"

    # STEP 7 — Score sentiment
    sentiment = score_sentiment(news_result["data"], coin_name=coin_name)
    _sent_cached = sentiment.get("cached", False) or sentiment.get("data", {}).get("cached", False)
    _flow["sentiment"] = f"sentiment({'cache' if _sent_cached else 'fresh'})"

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
        _open_l = gate.get("open_longs", "?")
        _open_s = gate.get("open_shorts", "?")
        print(f"[CYCLE:{coin_name}] fetch→validate→compute→{_flow['sentiment']}→gate({_open_l}/{_open_s} BLOCKED: {gate['reason']})")
        return

    capital        = gate.get("capital", capital_start)
    risk_amount    = gate.get("risk_amount",   round(capital * 0.02, 2))
    reward_amount  = gate.get("reward_amount", round(capital * 0.03, 2))
    _open_l = gate.get("open_longs", 0)
    _open_s = gate.get("open_shorts", 0)
    _flow["gate"] = f"gate({_open_l}/{_open_s})"

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
        print(f"[CYCLE:{coin_name}] fetch→validate→compute→{_flow['sentiment']}→{_flow['gate']}→brain(FAIL)")
        return  # One coin failure does not stop other coins

    # STEP 10b — Override SL/TP with ATR-based sizing
    signal_result = apply_atr_stops(signal_result, filtered["data"])

    _brain_sig  = signal_result["data"].get("signal", "?")
    _brain_conf = signal_result["data"].get("confidence", 0)
    # read dne_miss from learning file for brain label
    try:
        import os, json as _json
        _lp = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           f"{coin_name.lower()}_learning.json")
        _ld = _json.load(open(_lp)) if os.path.exists(_lp) else {}
        _dne_miss = _ld.get("dne_analysis", {}).get("miss_rate")
        _dne_str = f" DNE {_dne_miss*100:.0f}%" if _dne_miss is not None else ""
    except Exception:
        _dne_str = ""
    if _brain_sig == "Do Not Enter":
        _flow["brain"] = f"brain(DNE{_dne_str})"
    else:
        _dir = "BUY" if _brain_sig == "Buy" else "SELL"
        _flow["brain"] = f"brain({_dir} {_brain_conf}%)"

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

    # build guard label for summary
    _glog = guarded["data"].get("guardrail_log", [])
    _guard_key = next((l.replace("pattern_key=","") for l in _glog if l.startswith("pattern_key=")), None)
    _guard_match = next((l for l in _glog if l.startswith("pattern_matched:")), None)
    _guard_blocked = any("BLOCKED" in l for l in _glog)
    _guard_passed  = any("PASSED" in l for l in _glog)
    if _guard_match:
        # extract penalty and adjusted from the match log line
        import re as _re
        _pen  = _re.search(r"penalty=(\S+)", _guard_match)
        _adj_line = next((l for l in _glog if l.startswith("confidence_decay:")), "")
        _adj  = _re.search(r"adjusted=(\S+)", _adj_line)
        _pen_val = _pen.group(1) if _pen else "?"
        _adj_val = _adj.group(1) if _adj else "?"
        _tag_line = next((l for l in _glog if "penalty_tag" in l), "")
        _tag  = _re.search(r"tag=(\S+)\]", _tag_line) or _re.search(r"penalty_tag.*?=(\w+)", _tag_line)
        _tag_val = _tag.group(1) if _tag else "?"
        _outcome = "BLOCKED" if _guard_blocked else f"PASSED → {_adj_val}%"
        _flow["guard"] = f"guard({_guard_key} {_tag_val} -{_pen_val}pts → {_outcome})"
    elif _guard_key and _guard_key != "None":
        _outcome = "BLOCKED" if _guard_blocked else f"PASSED {guarded['data'].get('confidence',0)}%"
        _flow["guard"] = f"guard(no match → {_outcome})"
    else:
        _flow["guard"] = "guard(no key)"

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
        _ep = guarded["data"].get("entry_price")
        _sl = guarded["data"].get("stop_loss")
        _tp = guarded["data"].get("take_profit")
        _flow["output"] = f"output({'LONG' if sig=='Buy' else 'SHORT'} @{_ep} SL:{_sl} TP:{_tp})"
    else:
        _flow["output"] = "output(DNE)"

    # ── CYCLE SUMMARY ─────────────────────────────────────────────────────────
    print(
        f"[CYCLE:{coin_name}] fetch→validate→compute→"
        f"{_flow['sentiment']}→{_flow['gate']}→"
        f"{_flow['brain']}→{_flow['guard']}→{_flow['output']}"
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
    print("Coins: ETH | SOL | LINK | XRP")
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

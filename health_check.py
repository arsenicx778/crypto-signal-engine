"""
health_check.py — Engine health report. No API calls, no network. Reads disk only.
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trade_store import get_trade_store

COINS = ["ETH", "SOL", "XRP", "LINK"]
STARTING_CAPITAL = 1000.0
NOW = datetime.now()
NOW_STR = NOW.strftime("%Y-%m-%d %H:%M")

# ── helpers ────────────────────────────────────────────────────────────────────

def _unpack_meta(trade: dict) -> dict:
    row = dict(trade)
    try:
        meta = json.loads(row.get("metadata") or "{}")
    except Exception:
        meta = {}
    for k in ("ta_summary", "sentiment_summary", "history_summary",
              "decision_rationale", "overrides", "indicators"):
        row.setdefault(k, meta.get(k))
    if row.get("state") == "PENDING":
        row["outcome"] = "pending"
    return row


def load_rows(coin):
    """Return all trades for coin from SQLite with metadata unpacked."""
    store = get_trade_store()
    return [_unpack_meta(t) for t in store.get_all_trades(coin=coin)]


def completed_trades(rows):
    """Rows with W or L outcome AND a non-empty close_time — never pending."""
    return [r for r in rows if r.get("outcome") in ("W", "L")
            and str(r.get("close_time", "")).strip()]


def open_trades(rows):
    """Pending Buy/Sell rows (actual trades, not Do Not Enter)."""
    return [r for r in rows if r.get("outcome") == "pending"
            and r.get("signal") in ("Buy", "Sell")]


def compute_capital(completed):
    cap = STARTING_CAPITAL
    for r in completed:
        try:
            risk = float(r.get("risk_amount") or 0)
            reward = float(r.get("reward_amount") or 0)
        except (ValueError, TypeError):
            risk, reward = 20.0, 30.0
        if r["outcome"] == "W":
            cap += reward
        else:
            cap -= risk
    return cap


def win_rate(completed):
    if not completed:
        return 0.0
    return sum(1 for r in completed if r["outcome"] == "W") / len(completed) * 100


def hours_open(ts_str):
    try:
        ts = datetime.fromisoformat(str(ts_str).replace("Z", ""))
        delta = NOW - ts
        return max(0.0, delta.total_seconds() / 3600)
    except Exception:
        return 0.0


def parse_float(v):
    try:
        return float(v or 0)
    except (ValueError, TypeError):
        return None


def fmt_status(val, good_thresh, warn_thresh, higher_is_better=True):
    if higher_is_better:
        if val >= good_thresh:
            return "✓"
        if val >= warn_thresh:
            return "⚠"
        return "✗"
    else:
        if val <= good_thresh:
            return "✓"
        if val <= warn_thresh:
            return "⚠"
        return "✗"


def load_learn_state(coin):
    path = f"{coin.lower()}_learn_state.json"
    if not os.path.exists(path):
        return {"last_run_time": None, "last_trade_count": 0, "processed_trade_keys": []}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {"last_run_time": None, "last_trade_count": 0, "processed_trade_keys": []}


def load_learning(coin):
    path = f"{coin.lower()}_learning.json"
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def load_engine_state():
    path = "engine_state.json"
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def all_timestamps(coin_rows_map):
    all_ts = []
    for rows in coin_rows_map.values():
        for r in rows:
            ts = str(r.get("timestamp", "")).strip()
            if ts:
                all_ts.append(ts)
    return sorted(all_ts)

# ── gather data ────────────────────────────────────────────────────────────────

coin_rows = {c: load_rows(c) for c in COINS}
coin_completed = {c: completed_trades(coin_rows[c]) for c in COINS}
coin_open = {c: open_trades(coin_rows[c]) for c in COINS}
coin_capital = {c: compute_capital(coin_completed[c]) for c in COINS}
coin_wr = {c: win_rate(coin_completed[c]) for c in COINS}
coin_wl = {c: (sum(1 for r in coin_completed[c] if r["outcome"]=="W"),
               sum(1 for r in coin_completed[c] if r["outcome"]=="L"))
           for c in COINS}

all_ts = all_timestamps(coin_rows)
total_completed = sum(len(coin_completed[c]) for c in COINS)
total_w = sum(coin_wl[c][0] for c in COINS)
total_l = sum(coin_wl[c][1] for c in COINS)
overall_wr = (total_w / (total_w + total_l) * 100) if (total_w + total_l) > 0 else 0.0

cutoff_24h = (NOW - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
signals_24h = sum(1 for ts in all_ts if ts >= cutoff_24h)

last_cycle_ts = all_ts[-1] if all_ts else "unknown"
earliest_ts = all_ts[0] if all_ts else None
days_running = 0
first_short_ts = None
if earliest_ts:
    try:
        d0 = datetime.fromisoformat(earliest_ts)
        days_running = max(0, (NOW - d0).days)
    except Exception:
        pass
for c in COINS:
    for r in coin_rows[c]:
        if r.get("direction") == "SHORT" and r.get("signal") in ("Buy", "Sell"):
            ts = str(r.get("timestamp", "")).strip()
            if ts and (first_short_ts is None or ts < first_short_ts):
                first_short_ts = ts

engine_state = load_engine_state()

# ── print report ───────────────────────────────────────────────────────────────

SEP = "-" * 62
print(f"\nENGINE HEALTH REPORT  {NOW_STR}")
print(SEP)

# ── PORTFOLIO ──────────────────────────────────────────────────────────────────
print("PORTFOLIO")
print(f"  {'Coin':<6} {'Capital':>9} {'W':>4} {'L':>4} {'WR%':>6} {'P/L':>9}")
print(f"  {'-'*6} {'-'*9} {'-'*4} {'-'*4} {'-'*6} {'-'*9}")
total_cap = 0.0
for c in COINS:
    w, l = coin_wl[c]
    wr = coin_wr[c]
    cap = coin_capital[c]
    total_cap += cap
    pl = cap - STARTING_CAPITAL
    status = "✓" if cap >= STARTING_CAPITAL else "⚠" if cap >= 900 else "✗"
    print(f"  {c:<6} ${cap:>8.2f} {w:>4} {l:>4} {wr:>5.1f}% {'+' if pl>=0 else ''}{pl:>8.2f} {status}")
print(f"  {'TOTAL':<6} ${total_cap:>8.2f} {total_w:>4} {total_l:>4} {overall_wr:>5.1f}%")

# ── OPEN POSITIONS ─────────────────────────────────────────────────────────────
print(SEP)
print("OPEN POSITIONS")
any_open = False
for c in COINS:
    for r in coin_open[c]:
        any_open = True
        direction = r.get("direction") or ("LONG" if r.get("signal")=="Buy" else "SHORT")
        ep = parse_float(r.get("entry_price"))
        sl = parse_float(r.get("stop_loss"))
        tp = parse_float(r.get("take_profit"))
        hrs = hours_open(r.get("timestamp",""))
        last_ep = parse_float(coin_rows[c][-1].get("entry_price")) if coin_rows[c] else None
        if last_ep and ep:
            in_profit = (last_ep > ep) if direction == "LONG" else (last_ep < ep)
            profit_str = "~profit" if in_profit else "~loss"
        else:
            profit_str = "unknown"
        ep_s = f"{ep:.4f}" if ep else "?"
        sl_s = f"{sl:.4f}" if sl else "?"
        tp_s = f"{tp:.4f}" if tp else "?"
        print(f"  {c} {direction:<5} ep={ep_s} sl={sl_s} tp={tp_s} {hrs:.1f}h ({profit_str} est.)")
if not any_open:
    print("  No open positions.")

# ── LEARNING STATUS ────────────────────────────────────────────────────────────
print(SEP)
print("LEARNING STATUS")
for c in COINS:
    state = load_learn_state(c)
    last_run = state.get("last_run_time")
    last_count = state.get("last_trade_count", 0)
    current_count = len(coin_completed[c])
    stale = (current_count - last_count) > 5
    stale_str = " ⚠ STALE" if stale else ""

    if last_run:
        try:
            lr = datetime.fromisoformat(str(last_run).replace("Z", ""))
            lr_str = lr.strftime("%m-%d %H:%M")
        except Exception:
            lr_str = str(last_run)[:16]
    else:
        lr_str = "never"

    learning = load_learning(c)
    has_learning = "✓" if learning else "✗ missing"
    print(f"  {c}: last_run={lr_str}  analyzed={last_count}  now={current_count}{stale_str}  learning={has_learning}")

    if learning:
        wps = sorted(
            learning.get("weighted_patterns", []),
            key=lambda p: p.get("confidence_penalty", 0),
            reverse=True
        )[:3]
        if wps:
            for p in wps:
                key = p.get("key","?")
                tag = p.get("penalty_tag","?")
                wwr = p.get("weighted_win_rate", 0)
                rc = p.get("raw_count", 0)
                pen = p.get("confidence_penalty", 0)
                print(f"    [{tag}] {key}  wWR={wwr:.0%} n={rc} pen={pen}")

# ── ENGINE HEALTH ──────────────────────────────────────────────────────────────
print(SEP)
print("ENGINE HEALTH")
print(f"  Last cycle:     {last_cycle_ts}")
estimated_cycles = signals_24h  # one cycle ≈ one signal row per coin set
print(f"  Signals 24h:    {signals_24h}  (~{max(1, signals_24h//len(COINS))} cycles/coin)")
print(f"  Circuit breaker check (last 20 completed trades):")
for c in COINS:
    last20 = coin_completed[c][-20:]
    wr20 = win_rate(last20)
    flag = " ✗ BELOW THRESHOLD" if (last20 and wr20 < 35) else (" ⚠ warning" if (last20 and wr20 < 45) else "")
    n = len(last20)
    print(f"    {c}: WR={wr20:.1f}% over {n} trades{flag}")

# ── COST ESTIMATE ──────────────────────────────────────────────────────────────
print(SEP)
print("COST ESTIMATE")
cycles_24h = max(1, signals_24h // max(1, len(COINS)))
sonnet_calls = signals_24h
haiku_calls = max(0, total_completed // 3)

sentiment_hit_rate = 0.80

sonnet_in_cost = sonnet_calls * 3000 / 1e6 * 3.00
sonnet_out_cost = sonnet_calls * 500 / 1e6 * 15.00
haiku_in_cost = haiku_calls * 500 / 1e6 * 0.25
haiku_out_cost = haiku_calls * 500 / 1e6 * 1.25
total_cost = sonnet_in_cost + sonnet_out_cost + haiku_in_cost + haiku_out_cost

print(f"  Sonnet calls 24h: ~{sonnet_calls}  Haiku calls (total): ~{haiku_calls}")
print(f"  Sentiment cache hit rate: ~{sentiment_hit_rate*100:.0f}% (estimated)")
print(f"  Sonnet cost: input ${sonnet_in_cost:.4f} + output ${sonnet_out_cost:.4f}")
print(f"  Haiku  cost: input ${haiku_in_cost:.4f} + output ${haiku_out_cost:.4f}")
print(f"  Estimated total: ${total_cost:.4f}")

# ── GO-LIVE PROGRESS ───────────────────────────────────────────────────────────
print(SEP)
print("GO-LIVE PROGRESS")

wr_gap = 60.0 - overall_wr
wr_icon = "✓" if overall_wr >= 60 else ("⚠" if overall_wr >= 45 else "✗")
print(f"  {wr_icon} Win rate ≥60%:        current={overall_wr:.1f}%  gap={wr_gap:+.1f}%")

tc_gap = 200 - total_completed
tc_icon = "✓" if total_completed >= 200 else "✗"
print(f"  {tc_icon} Trades ≥200:          current={total_completed}  remaining={max(0,tc_gap)}")

days_icon = "✓" if days_running >= 7 else "⚠" if days_running >= 3 else "✗"
print(f"  {days_icon} 7 stable days:       running={days_running}d  gap={max(0,7-days_running)}d")

has_shorts = any(
    r.get("direction") == "SHORT" and r.get("signal") in ("Buy", "Sell")
    for c in COINS for r in coin_rows[c]
)
if has_shorts and first_short_ts:
    try:
        fd = datetime.fromisoformat(first_short_ts)
        days_shorts = (NOW - fd).days
        short_icon = "✓" if days_shorts >= 7 else "⚠"
        print(f"  {short_icon} Shorts 1 week:       first_short={first_short_ts[:10]}  days={days_shorts}")
    except Exception:
        print(f"  ⚠ Shorts 1 week:       first_short={first_short_ts[:10]}")
else:
    print(f"  ✗ Shorts 1 week:       no short trades found")

profitable = [c for c in COINS if coin_capital[c] >= STARTING_CAPITAL]
losing = [c for c in COINS if coin_capital[c] < STARTING_CAPITAL]
profit_icon = "✓" if not losing else ("⚠" if len(losing) <= 1 else "✗")
print(f"  {profit_icon} All coins profitable: above={profitable}  below={losing}")

cloud_env = os.environ.get("CLOUD_DEPLOYED") or os.environ.get("FLY_APP_NAME") or os.environ.get("RENDER_SERVICE_ID")
cloud_icon = "✓" if cloud_env else "✗"
cloud_str = cloud_env if cloud_env else "NOT STARTED"
print(f"  {cloud_icon} Cloud deployed:      {cloud_str}")

print(SEP)

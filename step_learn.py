"""
step_learn.py

Reinforcement learning layer for the signal engine.

HOW IT WORKS:
  Called from main.py every N cycles (default 10, i.e. every 30 minutes).
  For each coin it checks whether enough NEW completed trades have closed
  since the last time Haiku was called. If yes it calls Haiku once,
  extracts patterns from recent outcomes, and writes coin_learning.json.
  The brain (step10) reads that file every cycle at zero extra cost.

DECISION FLOW:
  1. Read eth_learn_state.json  (tracks last run time + processed trade keys)
  2. Load normalized completed trades from the coin CSV
  3. new_trades = completed trades whose keys were not processed before
  4. If new_trades >= MIN_NEW_TRADES → call Haiku → update files
  5. If new_trades < MIN_NEW_TRADES → skip, brain uses cached learning

FILES WRITTEN:
  eth_learning.json    (patterns, read by step10 and dashboard)
  eth_learn_state.json (internal state, tracks when we last ran)

PERMANENT RULES (hardcoded, never learned away):
  - Never Buy when DI- > DI+           (directional premise violation)
  - Never Sell when RSI < 35           (exhausted move, no short edge)

Everything else is soft and learned from outcomes.
"""

import os
import math
import json
from datetime import datetime
from typing import Optional
from signal_store import read_latest_signals
from time_utils import now_pacific

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()

# ── configuration ─────────────────────────────────────────────────────────────

MIN_NEW_TRADES = 3
MAX_TRADES_TO_ANALYZE = 40
MIN_TOTAL_TRADES_FOR_LEARNING = 10

# ── coin config ───────────────────────────────────────────────────────────────

COIN_CSV = {
    "ETH":  "eth_signals.csv",
    "SOL":  "sol_signals.csv",
    "XRP":  "xrp_signals.csv",
    "AVAX": "avax_signals.csv",
    "LINK": "link_signals.csv",
}

# ── helpers ───────────────────────────────────────────────────────────────────

def state_path(coin: str) -> str:
    return f"{coin.lower()}_learn_state.json"

def learning_path(coin: str) -> str:
    return f"{coin.lower()}_learning.json"

def read_state(coin: str) -> dict:
    path = state_path(coin)
    if not os.path.exists(path):
        return {"last_run_time": None, "last_trade_count": 0, "processed_trade_keys": []}
    try:
        with open(path) as f:
            state = json.load(f)
            state.setdefault("last_run_time", None)
            state.setdefault("last_trade_count", 0)
            state.setdefault("processed_trade_keys", [])
            return state
    except Exception:
        return {"last_run_time": None, "last_trade_count": 0, "processed_trade_keys": []}

def write_state(coin: str, trade_count: int, processed_trade_keys: Optional[list] = None):
    state = {
        "last_run_time": now_pacific().isoformat(),
        "last_trade_count": trade_count,
        "processed_trade_keys": (processed_trade_keys or [])[-500:],
    }
    with open(state_path(coin), "w") as f:
        json.dump(state, f, indent=2)

def completed_trade_key(row: dict) -> str:
    return str(row.get("timestamp", "")).strip()

def load_completed_trades(csv_file: str) -> list:
    if not os.path.exists(csv_file):
        return []
    try:
        rows = read_latest_signals(csv_file)
    except Exception as e:
        print(f"[LEARN] Error reading CSV: {e}")
        return []
    return [
        row for row in rows
        if row.get("signal") in ("Buy", "Sell")
        and str(row.get("outcome", "")).strip() in ("W", "L")
        and completed_trade_key(row)
    ]

def get_new_completed_trade_keys(state: dict, completed_rows: list) -> list:
    keys = [completed_trade_key(row) for row in completed_rows]
    processed = {
        str(key).strip()
        for key in state.get("processed_trade_keys", [])
        if str(key).strip()
    }
    if processed:
        return [key for key in keys if key not in processed]

    last_count = state.get("last_trade_count", 0)
    try:
        baseline = int(last_count or 0)
    except (TypeError, ValueError):
        baseline = 0
    if baseline < 0 or baseline > len(keys):
        baseline = 0
    return keys[baseline:]

def load_recent_completed_trades(csv_file: str, limit: int) -> list:
    completed = []
    for row in load_completed_trades(csv_file):
        outcome = row.get("outcome", "").strip()
        indicators = row.get("indicators", "")
        parsed = parse_indicators(indicators)
        if not is_trade_usable_for_learning(row, parsed):
            continue
        sentiment = parse_sentiment(row.get("sentiment_summary", ""))
        direction = row.get("direction", "").strip()
        if not direction:
            signal = row.get("signal", "").strip()
            direction = "LONG" if signal == "Buy" else "SHORT" if signal == "Sell" else "DNE"
        completed.append({
            "timestamp":  row.get("timestamp", ""),
            "direction":  direction,
            "confidence": safe_float(row.get("confidence", 0)),
            "outcome":    outcome,
            "rsi":        parsed.get("RSI"),
            "adx":        parsed.get("ADX"),
            "di_plus":    parsed.get("DI_PLUS"),
            "di_minus":   parsed.get("DI_MINUS"),
            "macd":       parsed.get("MACD"),
            "bb_width":   parsed.get("BB_WIDTH"),
            "sentiment":  sentiment,
            "indicators": row.get("indicators", ""),
        })
    return completed[-limit:]

def parse_indicators(indicator_str: str) -> dict:
    result = {}
    if not indicator_str:
        return result
    for part in indicator_str.split("|"):
        part = part.strip()
        if ":" in part:
            key, _, val = part.partition(":")
            try:
                result[key.strip()] = float(val.strip())
            except ValueError:
                pass
    if "DI_PLUS" not in result and "DI+" in result:
        result["DI_PLUS"] = result["DI+"]
    if "DI_MINUS" not in result and "DI-" in result:
        result["DI_MINUS"] = result["DI-"]
    return result

def parse_sentiment(sentiment_text: str) -> float:
    import re
    if not sentiment_text:
        return None
    match = re.search(r"sentiment[^\n]*?([+-]\d+\.\d+)", sentiment_text, re.IGNORECASE)
    if not match:
        match = re.search(r"\(([+-]\d+\.\d+)\)", sentiment_text)
    if not match:
        match = re.search(r"([+-]\d+\.\d+)", sentiment_text)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    return None

def safe_float(val) -> float:
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0

def is_trade_usable_for_learning(row: dict, parsed_indicators: dict) -> bool:
    direction = str(row.get("direction", "")).strip().upper()
    if direction not in ("LONG", "SHORT"):
        signal = str(row.get("signal", "")).strip()
        direction = "LONG" if signal == "Buy" else "SHORT" if signal == "Sell" else ""
    if direction not in ("LONG", "SHORT"):
        return False

    confidence = safe_float(row.get("confidence", 0))
    if confidence <= 0:
        return False

    # Skip corrupted legacy rows that do not contain enough technical context to learn from.
    required_any = ("RSI", "DI_PLUS", "DI_MINUS")
    if any(parsed_indicators.get(key) is None for key in required_any):
        return False

    return True

# ── time decay ────────────────────────────────────────────────────────────────

def decay_weight(trade_ts: str, now: datetime) -> float:
    """Half-life of 7 days: a trade 7 days old counts 0.5x, 14 days counts 0.25x."""
    if not trade_ts:
        return 1.0
    try:
        ts = datetime.fromisoformat(str(trade_ts).replace("Z", "+00:00"))
        if ts.tzinfo is not None and now.tzinfo is None:
            ts = ts.replace(tzinfo=None)
        elif ts.tzinfo is None and now.tzinfo is not None:
            ts = ts.replace(tzinfo=now.tzinfo)
        days_old = max(0.0, (now - ts).total_seconds() / 86400.0)
    except Exception:
        return 1.0
    return math.exp(-days_old * math.log(2) / 7)

# ── pattern key classification ────────────────────────────────────────────────

def classify_pattern_key(direction: str, rsi, di_plus, di_minus, adx, macd) -> Optional[str]:
    """Return a pattern key in the format step11 can match."""
    if direction not in ("LONG", "SHORT"):
        return None
    if rsi is None or di_plus is None or di_minus is None or adx is None or macd is None:
        return None

    rsi = float(rsi)
    di_plus = float(di_plus)
    di_minus = float(di_minus)
    adx = float(adx)
    macd = float(macd)

    if rsi < 40:
        rsi_tag = "rsi_low"
    elif rsi > 65:
        rsi_tag = "rsi_high"
    else:
        rsi_tag = "rsi_mid"

    gap_tag = "gap_strong" if abs(di_plus - di_minus) >= 15 else "gap_weak"
    adx_tag = "adx_strong" if adx >= 27 else "adx_weak"
    macd_tag = "macd_pos" if macd >= 0 else "macd_neg"

    return f"{direction}|{rsi_tag}|{gap_tag}|{adx_tag}|{macd_tag}"

# ── weighted pattern builder ──────────────────────────────────────────────────

def build_weighted_patterns(trades: list) -> list:
    """Group completed trades by pattern key, apply time-decay weights, return pattern dicts."""
    now = now_pacific().replace(tzinfo=None)
    buckets = {}  # key → {weighted_wins, weighted_losses, raw_count}

    for t in trades:
        key = classify_pattern_key(
            t.get("direction"),
            t.get("rsi"), t.get("di_plus"), t.get("di_minus"),
            t.get("adx"), t.get("macd"),
        )
        if not key:
            continue
        w = decay_weight(t.get("timestamp", ""), now)
        if key not in buckets:
            buckets[key] = {"weighted_wins": 0.0, "weighted_losses": 0.0, "raw_count": 0}
        if t.get("outcome") == "W":
            buckets[key]["weighted_wins"] += w
        else:
            buckets[key]["weighted_losses"] += w
        buckets[key]["raw_count"] += 1

    patterns = []
    for key, b in buckets.items():
        total_w = b["weighted_wins"] + b["weighted_losses"]
        if total_w == 0:
            continue
        wr = b["weighted_wins"] / total_w

        if wr >= 0.55:
            penalty, tag = 0, "NEUTRAL"
        elif wr >= 0.45:
            penalty, tag = 5, "CAUTION"
        elif wr >= 0.35:
            penalty, tag = 15, "AVOID"
        else:
            penalty, tag = 25, "STRONG_AVOID"

        patterns.append({
            "key": key,
            "weighted_wins": round(b["weighted_wins"], 3),
            "weighted_losses": round(b["weighted_losses"], 3),
            "raw_count": b["raw_count"],
            "weighted_win_rate": round(wr, 4),
            "win_rate_pct": round(wr * 100, 1),
            "confidence_penalty": penalty,
            "penalty_tag": tag,
        })

    return patterns

# ── regime fingerprint ────────────────────────────────────────────────────────

def compute_regime(trades: list) -> dict:
    """Summarise market regime from the last 20 completed trades."""
    recent = trades[-20:]
    adx_vals, rsi_vals, bb_vals = [], [], []

    for t in recent:
        parsed = parse_indicators(t.get("indicators", ""))
        adx = parsed.get("ADX") or t.get("adx")
        rsi = parsed.get("RSI") or t.get("rsi")
        bb  = parsed.get("BB_WIDTH") or t.get("bb_width")
        if adx is not None:
            adx_vals.append(float(adx))
        if rsi is not None:
            rsi_vals.append(float(rsi))
        if bb is not None:
            bb_vals.append(float(bb))

    def _avg(lst):
        return round(sum(lst) / len(lst), 4) if lst else None

    return {
        "avg_adx":      _avg(adx_vals),
        "avg_rsi":      _avg(rsi_vals),
        "avg_bb_width": _avg(bb_vals),
        "n_trades":     len(recent),
        "captured_at":  now_pacific().isoformat(),
    }

# ── DNE outcome tracking ──────────────────────────────────────────────────────

def evaluate_dne_signals(csv_rows: list) -> dict:
    """
    For every DNE row, look at the next 6 rows in timestamp order.
    If a W trade follows → missed_opportunity, else → correct_dne.
    """
    sorted_rows = sorted(csv_rows, key=lambda r: str(r.get("timestamp", "")))

    missed = 0
    correct = 0

    for i, row in enumerate(sorted_rows):
        if str(row.get("signal", "")).strip() != "Do Not Enter":
            continue
        window = sorted_rows[i + 1: i + 7]
        won_after = any(
            str(r.get("outcome", "")).strip() == "W"
            for r in window
            if str(r.get("signal", "")).strip() in ("Buy", "Sell")
        )
        if won_after:
            missed += 1
        else:
            correct += 1

    total = missed + correct
    miss_rate = round(missed / total, 4) if total > 0 else 0.0

    return {
        "missed_opportunity": missed,
        "correct_dne": correct,
        "total_dne": total,
        "miss_rate": miss_rate,
    }

# ── compact trade formatter ───────────────────────────────────────────────────

def format_trades_for_haiku(trades: list) -> str:
    lines = []
    for t in trades:
        def fmt(v, decimals=1):
            return f"{v:.{decimals}f}" if v is not None else "?"
        sentiment_str = f"{t['sentiment']:+.2f}" if t['sentiment'] is not None else "?"
        line = (
            f"{t['direction']},"
            f"{int(t['confidence'])},"
            f"{t['outcome']},"
            f"RSI:{fmt(t['rsi'])},"
            f"ADX:{fmt(t['adx'])},"
            f"DI+:{fmt(t['di_plus'])},"
            f"DI-:{fmt(t['di_minus'])},"
            f"SENT:{sentiment_str},"
            f"MACD:{fmt(t['macd'], 2)},"
            f"BB:{fmt(t['bb_width'], 3) if t['bb_width'] else '?'}"
        )
        lines.append(line)
    return "\n".join(lines)

# ── haiku call ────────────────────────────────────────────────────────────────

def call_haiku_for_patterns(coin: str, trades: list, dne_analysis: dict = None) -> dict:
    import anthropic

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    trade_text = format_trades_for_haiku(trades)
    total = len(trades)
    wins = sum(1 for t in trades if t["outcome"] == "W")
    losses = total - wins

    dne_block = ""
    if dne_analysis and dne_analysis.get("total_dne", 0) > 0:
        dne_block = (
            f"\nDNE SIGNAL ANALYSIS (last run):\n"
            f"  Total DNE signals: {dne_analysis['total_dne']}\n"
            f"  Missed opportunities (W trade followed within 6 rows): {dne_analysis['missed_opportunity']}\n"
            f"  Correct DNEs (no W followed): {dne_analysis['correct_dne']}\n"
            f"  Miss rate: {dne_analysis['miss_rate']*100:.1f}%\n"
            f"Please comment on whether the DNE signals appear too conservative "
            f"(high miss rate) or appropriately cautious.\n"
        )

    prompt = f"""You are analyzing recent trading outcomes for {coin} to identify patterns.

TRADE DATA (most recent {total} completed trades, oldest first):
Format: DIRECTION,CONFIDENCE,OUTCOME,RSI,ADX,DI+,DI-,SENTIMENT,MACD,BB_WIDTH
{trade_text}

Overall: {wins}W {losses}L out of {total} trades.
{dne_block}
Analyze these outcomes and identify specific conditions that correlate with wins versus losses.
Focus on: direction combined with sentiment thresholds, RSI ranges, ADX thresholds,
DI gap size patterns, MACD direction, BB_WIDTH, any other clear pattern.

PERMANENT RULES (do not question these regardless of data):
- Buy only when DI+ > DI-
- Never Sell when RSI < 35

Return ONLY valid JSON, no other text, no markdown:
{{
  "trade_count": {total},
  "overall_win_rate": {round(wins/total*100) if total > 0 else 0},
  "patterns": [
    {{
      "condition": "short description of pattern condition",
      "wins": <integer>,
      "losses": <integer>,
      "win_rate": <integer 0-100>,
      "recommendation": "one sentence on what the brain should do",
      "confidence": "high|medium|low",
      "sample_size_note": "note if fewer than 5 observations"
    }}
  ],
  "strongest_long_setup": "brief description of best long conditions",
  "strongest_short_setup": "brief description of best short conditions or insufficient edge",
  "summary": "2-3 sentence summary of key learnings for the brain"
}}

Include 3-6 patterns. Only include a pattern if it has at least 3 observations.
Be specific with numbers (e.g. sentiment > 0.50 not high sentiment).
"""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[LEARN:{coin}] Haiku returned invalid JSON: {e}")
        return None
    except Exception as e:
        print(f"[LEARN:{coin}] Haiku call failed: {e}")
        return None

# ── write learning file ───────────────────────────────────────────────────────

def write_learning(coin: str, patterns: dict, weighted_patterns: list = None,
                   regime: dict = None, dne_analysis: dict = None):
    now = now_pacific().strftime("%Y-%m-%d %H:%M PT")
    output = {
        "coin": coin,
        "generated_at": now,
        "trade_count": patterns.get("trade_count", 0),
        "overall_win_rate": patterns.get("overall_win_rate", 0),
        "patterns": patterns.get("patterns", []),
        "weighted_patterns": weighted_patterns or [],
        "strongest_long_setup": patterns.get("strongest_long_setup", ""),
        "strongest_short_setup": patterns.get("strongest_short_setup", ""),
        "summary": patterns.get("summary", ""),
    }
    if regime is not None:
        output["regime"] = regime
    if dne_analysis is not None:
        output["dne_analysis"] = dne_analysis

    path = learning_path(coin)
    with open(path, "w") as f:
        json.dump(output, f, indent=2)

    history_path = f"{coin.lower()}_learning_history.json"
    history = []
    if os.path.exists(history_path):
        try:
            with open(history_path) as f:
                history = json.load(f)
        except Exception:
            history = []
    history.append({
        "timestamp": now,
        "trade_count": patterns.get("trade_count", 0),
        "overall_win_rate": patterns.get("overall_win_rate", 0),
        "summary": patterns.get("summary", ""),
    })
    history = history[-20:]
    with open(history_path, "w") as f:
        json.dump(history, f, indent=2)

    print(f"[LEARN:{coin}] Updated: {patterns.get('trade_count',0)} trades, "
          f"{patterns.get('overall_win_rate',0)}% WR, "
          f"{len(patterns.get('patterns',[]))} patterns, "
          f"{len(weighted_patterns or [])} weighted pattern keys")

# ── brain injection ───────────────────────────────────────────────────────────

def format_learning_for_brain(coin: str) -> str:
    path = learning_path(coin)
    if not os.path.exists(path):
        return ""
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        return ""
    if not data.get("patterns"):
        return ""

    lines = [
        f"REINFORCEMENT LEARNINGS ({data['coin']}, {data['trade_count']} trades, updated {data['generated_at']}):",
        f"Overall win rate: {data['overall_win_rate']}%",
        "",
    ]
    for p in data["patterns"]:
        wr = p.get("win_rate", 0)
        w = p.get("wins", 0)
        l = p.get("losses", 0)
        conf = p.get("confidence", "")
        note = f" [{p['sample_size_note']}]" if p.get("sample_size_note") else ""
        if wr <= 25:
            signal = "AVOID"
        elif wr <= 40:
            signal = "CAUTION"
        elif wr >= 70:
            signal = "FAVOR"
        else:
            signal = "NEUTRAL"
        lines.append(f"  [{signal}] {p['condition']}: {w}W {l}L ({wr}%) [{conf} confidence]{note}")
        lines.append(f"    → {p['recommendation']}")
    lines.append("")
    if data.get("strongest_long_setup"):
        lines.append(f"Best long setup: {data['strongest_long_setup']}")
    if data.get("strongest_short_setup"):
        lines.append(f"Best short setup: {data['strongest_short_setup']}")
    lines.append("")
    lines.append(f"Summary: {data['summary']}")
    lines.append("")
    lines.append(
        "These are observed patterns from recent trades. Weight them in your decision "
        "but use your judgment. Permanent rules (DI directional rule, RSI < 35 blocks Sell) "
        "always take precedence."
    )
    return "\n".join(lines)

# ── main entry points ─────────────────────────────────────────────────────────

def run_learning_for_coin(coin: str) -> bool:
    csv_file = COIN_CSV.get(coin)
    if not csv_file or not os.path.exists(csv_file):
        return False
    state = read_state(coin)
    completed_rows = load_completed_trades(csv_file)
    current_count = len(completed_rows)
    if current_count < MIN_TOTAL_TRADES_FOR_LEARNING:
        print(f"[LEARN:{coin}] Only {current_count} completed trades, need {MIN_TOTAL_TRADES_FOR_LEARNING}. Skipping.")
        return False
    new_trade_keys = get_new_completed_trade_keys(state, completed_rows)
    new_trades = len(new_trade_keys)
    if new_trades < MIN_NEW_TRADES:
        print(f"[LEARN:{coin}] Only {new_trades} new trades since last run, need {MIN_NEW_TRADES}. Skipping.")
        return False
    trades = load_recent_completed_trades(csv_file, MAX_TRADES_TO_ANALYZE)
    skipped_malformed = current_count - len(trades) if current_count >= len(trades) else 0
    print(
        f"[LEARN:{coin}] {new_trades} new trades found. "
        f"Using {len(trades)} analyzable completed trades"
        + (f", skipped {skipped_malformed} malformed legacy rows." if skipped_malformed else ".")
    )
    if not trades:
        return False

    # Build derived analytics before calling Haiku
    weighted_patterns = build_weighted_patterns(trades)
    regime = compute_regime(trades)

    # DNE analysis needs all CSV rows (not just completed trades)
    try:
        all_rows = read_latest_signals(csv_file)
    except Exception:
        all_rows = []
    dne_analysis = evaluate_dne_signals(all_rows)

    patterns = call_haiku_for_patterns(coin, trades, dne_analysis=dne_analysis)
    if not patterns:
        return False
    write_learning(coin, patterns,
                   weighted_patterns=weighted_patterns,
                   regime=regime,
                   dne_analysis=dne_analysis)
    write_state(
        coin,
        current_count,
        [completed_trade_key(row) for row in completed_rows],
    )
    return True

def run_learning_cycle(coins: list, cycle_number: int, every_n_cycles: int = 10):
    if cycle_number % every_n_cycles != 0:
        return
    print(f"[LEARN] Cycle {cycle_number}: checking for new completed trades...")
    for coin in coins:
        try:
            run_learning_for_coin(coin)
        except Exception as e:
            print(f"[LEARN:{coin}] Error: {e}")

# ── standalone runner ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    coin = sys.argv[1].upper() if len(sys.argv) > 1 else "ETH"
    print(f"[LEARN] Running standalone learner for {coin}...")
    result = run_learning_for_coin(coin)
    if result:
        print("\nLearning output:\n")
        print(format_learning_for_brain(coin))
    else:
        print("Skipped (see reason above)")

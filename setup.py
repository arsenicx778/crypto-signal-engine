import os

files = {}

files["step1_fetch.py"] = '''
import os
import requests
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

KRAKEN_BASE = "https://api.kraken.com/0/public"
CRYPTOPANIC_KEY = os.getenv("CRYPTOPANIC_API_KEY")

def fetch_candles_kraken(symbol="XBTUSD", interval=1, limit=200):
    try:
        url    = f"{KRAKEN_BASE}/OHLC"
        params = {"pair": symbol, "interval": interval}
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data     = response.json()
        pair_key = list(data["result"].keys())[0]
        raw      = data["result"][pair_key][-limit:]
        df = pd.DataFrame(raw, columns=[
            "timestamp","open","high","low",
            "close","vwap","volume","trades"
        ])
        df["close"]     = df["close"].astype(float)
        df["high"]      = df["high"].astype(float)
        df["low"]       = df["low"].astype(float)
        df["open"]      = df["open"].astype(float)
        df["volume"]    = df["volume"].astype(float)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
        return {"success": True, "source": "kraken", "data": df}
    except Exception as e:
        return {"success": False, "source": "kraken", "error": str(e), "data": None}

def fetch_candles():
    result = fetch_candles_kraken()
    if result["success"]:
        return {"success": True, "data": result["data"]}
    return {"success": False, "error": result["error"], "data": None}

def fetch_news():
    try:
        url = "https://cryptopanic.com/api/v1/posts/"
        params = {
            "auth_token": CRYPTOPANIC_KEY,
            "currencies": "BTC",
            "kind": "news",
            "limit": 20
        }
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        headlines = [item["title"] for item in data.get("results", [])]
        return {"success": True, "data": headlines}
    except Exception as e:
        return {"success": False, "error": str(e), "data": []}
'''.strip()

files["step2_validate.py"] = '''
import pandas as pd
from datetime import datetime, timedelta

def validate_data(candles_result, news_result):
    errors = []
    if not candles_result["success"]:
        errors.append(f"Candles fetch failed: {candles_result['error']}")
    else:
        df = candles_result["data"]
        if df is None or len(df) < 50:
            errors.append(f"Not enough candles: got {len(df) if df is not None else 0}, need 50+")
        else:
            latest = df["timestamp"].iloc[-1]
            age = datetime.utcnow() - latest.to_pydatetime().replace(tzinfo=None)
            if age > timedelta(minutes=5):
                errors.append(f"Candle data is stale: {age} old")
    if not news_result["success"]:
        print(f"[WARN] News fetch failed: {news_result['error']} — continuing without news")
    if errors:
        for e in errors:
            print(f"[ABORT] {e}")
        return {"valid": False, "errors": errors}
    return {"valid": True, "errors": []}
'''.strip()

files["step3_compute.py"] = '''
import ta
import pandas as pd

def compute_indicators(df):
    try:
        close  = df["close"]
        high   = df["high"]
        low    = df["low"]
        volume = df["volume"]
        indicators = {}
        indicators["rsi"]         = round(ta.momentum.RSIIndicator(close, window=14).rsi().iloc[-1], 4)
        indicators["ema_20"]      = round(ta.trend.EMAIndicator(close, window=20).ema_indicator().iloc[-1], 4)
        indicators["ema_50"]      = round(ta.trend.EMAIndicator(close, window=50).ema_indicator().iloc[-1], 4)
        macd_obj = ta.trend.MACD(close, window_fast=12, window_slow=26, window_sign=9)
        indicators["macd"]        = round(macd_obj.macd().iloc[-1], 4)
        indicators["macd_signal"] = round(macd_obj.macd_signal().iloc[-1], 4)
        indicators["macd_hist"]   = round(macd_obj.macd_diff().iloc[-1], 4)
        bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)
        indicators["bb_upper"]    = round(bb.bollinger_hband().iloc[-1], 4)
        indicators["bb_lower"]    = round(bb.bollinger_lband().iloc[-1], 4)
        indicators["bb_mid"]      = round(bb.bollinger_mavg().iloc[-1], 4)
        indicators["atr"]         = round(ta.volatility.AverageTrueRange(high, low, close, window=14).average_true_range().iloc[-1], 4)
        indicators["vwap"]        = round(ta.volume.VolumeWeightedAveragePrice(high, low, close, volume).volume_weighted_average_price().iloc[-1], 4)
        indicators["adx"]         = round(ta.trend.ADXIndicator(high, low, close, window=14).adx().iloc[-1], 4)
        indicators["obv"]         = round(ta.volume.OnBalanceVolumeIndicator(close, volume).on_balance_volume().iloc[-1], 4)
        indicators["close"]       = round(close.iloc[-1], 4)
        return {"success": True, "data": indicators}
    except Exception as e:
        return {"success": False, "error": str(e), "data": None}
'''.strip()

files["step4_merge.py"] = '''
def merge_indicators(compute_result):
    if not compute_result["success"]:
        return {"success": False, "error": compute_result["error"], "data": None}
    return {"success": True, "data": compute_result["data"]}
'''.strip()

files["step5_select.py"] = '''
import os
import json
import anthropic
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic()

CANDIDATE_INDICATORS = [
    "rsi", "ema_20", "ema_50", "macd", "macd_signal",
    "macd_hist", "bb_upper", "bb_lower", "bb_mid",
    "atr", "vwap", "adx", "obv"
]

def select_indicators(all_indicators):
    try:
        indicators_text = "\\n".join(
            f"- {k}: {v}" for k, v in all_indicators.items() if k != "close"
        )
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            system="""You are a technical indicator selector for Bitcoin trading.
Given all computed indicator values, select between 1 and 5 indicators
that are most relevant for generating a trading signal right now.
Avoid selecting redundant indicators that measure the same thing.
Output ONLY valid JSON in this exact format with no other text:
{
  "selected": ["indicator1", "indicator2"],
  "count": 2,
  "reason": "one sentence explaining why"
}""",
            messages=[{
                "role": "user",
                "content": f"Current Bitcoin indicator values:\\n{indicators_text}\\n\\nSelect the most relevant indicators."
            }]
        )
        raw = response.content[0].text.strip()
        clean = raw.removeprefix("```json").removesuffix("```").strip()
        result = json.loads(clean)
        valid = [i for i in result["selected"] if i in CANDIDATE_INDICATORS]
        result["selected"] = valid
        result["count"] = len(valid)
        return {"success": True, "data": result}
    except Exception as e:
        print(f"[WARN] Indicator selection failed: {e} — using defaults")
        return {
            "success": True,
            "data": {
                "selected": ["rsi", "macd", "macd_signal", "atr"],
                "count": 4,
                "reason": "fallback defaults"
            }
        }
'''.strip()

files["step6_filter.py"] = '''
def filter_indicators(all_indicators, selection_result):
    selected_keys = selection_result["data"]["selected"]
    filtered = {k: v for k, v in all_indicators.items() if k in selected_keys or k == "close"}
    return {"success": True, "data": filtered, "reason": selection_result["data"]["reason"]}
'''.strip()

files["step7_sentiment.py"] = '''
import json
import anthropic
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic()

def score_sentiment(headlines):
    if not headlines:
        return {"success": True, "data": {"news_score": 0.0, "headline_count": 0}}
    try:
        headlines_text = "\\n".join(f"- {h}" for h in headlines[:20])
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            system="""You are a crypto news sentiment scorer.
Score the overall sentiment of these Bitcoin headlines.
Output ONLY valid JSON with no other text:
{
  "news_score": 0.0,
  "headline_count": 0
}
news_score must be a float between -1.0 (very bearish) and +1.0 (very bullish).""",
            messages=[{
                "role": "user",
                "content": f"Score these Bitcoin headlines:\\n{headlines_text}"
            }]
        )
        raw = response.content[0].text.strip()
        clean = raw.removeprefix("```json").removesuffix("```").strip()
        result = json.loads(clean)
        result["headline_count"] = len(headlines)
        return {"success": True, "data": result}
    except Exception as e:
        print(f"[WARN] Sentiment scoring failed: {e} — using neutral")
        return {"success": True, "data": {"news_score": 0.0, "headline_count": 0}}
'''.strip()

files["step8_history.py"] = '''
import os
import csv
import anthropic
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic()

SIGNALS_FILE = "signals.csv"

def load_history(n=10):
    if not os.path.exists(SIGNALS_FILE):
        return []
    try:
        with open(SIGNALS_FILE, "r") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        return rows[-n:] if len(rows) >= n else rows
    except Exception as e:
        print(f"[WARN] Could not load history: {e}")
        return []

def summarize_history(history):
    if not history:
        return {"success": True, "data": "No signal history yet — this is the first signal."}
    try:
        history_text = "\\n".join(
            f"[{r.get('timestamp','')}] {r.get('signal','')} -> {r.get('outcome','pending')} "
            f"(confidence: {r.get('confidence','')}%)"
            for r in history
        )
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            system="""You are a trading pattern analyst.
Summarize the win/loss pattern from recent signals in ONE sentence.
Focus on what conditions led to wins vs losses.
Output plain text only. Maximum 30 words.""",
            messages=[{"role": "user", "content": f"Recent signal history:\\n{history_text}"}]
        )
        return {"success": True, "data": response.content[0].text.strip()}
    except Exception as e:
        print(f"[WARN] History summarization failed: {e}")
        return {"success": True, "data": "Could not summarize history."}
'''.strip()

files["step9_gate.py"] = '''
import os
import csv
from datetime import date

SIGNALS_FILE = "signals.csv"
MAX_DAILY_CLAUDE_CALLS = 200

def count_todays_calls():
    if not os.path.exists(SIGNALS_FILE):
        return 0
    try:
        today = str(date.today())
        count = 0
        with open(SIGNALS_FILE, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("timestamp", "").startswith(today):
                    count += 1
        return count
    except:
        return 0

def get_open_trades():
    if not os.path.exists(SIGNALS_FILE):
        return []
    try:
        with open(SIGNALS_FILE, "r") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        return [r for r in rows if r.get("signal") == "Buy" and r.get("outcome", "pending") == "pending"]
    except:
        return []

def pre_signal_gate():
    calls_today = count_todays_calls()
    if calls_today >= MAX_DAILY_CLAUDE_CALLS:
        return {"proceed": False, "reason": f"Daily cost cap reached: {calls_today} calls today"}
    return {"proceed": True, "reason": "All checks passed", "open_trades": get_open_trades()}
'''.strip()

files["step10_brain.py"] = '''
import json
import anthropic
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic()

def generate_signal(filtered_indicators, sentiment, history_summary, open_trades):
    try:
        indicators_text = "\\n".join(f"  {k}: {v}" for k, v in filtered_indicators.items())
        if open_trades:
            trades_text = "\\n".join(
                f"  Trade {i+1}: entry={r.get('entry_price','?')} SL={r.get('stop_loss','?')} TP={r.get('take_profit','?')}"
                for i, r in enumerate(open_trades)
            )
        else:
            trades_text = "  None"

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=800,
            system="""You are a disciplined Bitcoin trading signal engine.
RULES:
- If confidence is below 70 output Hold regardless of other factors
- Cite specific indicator values in your reasoning
- Stop loss and take profit are required when signal is Buy
- Never hedge — commit to a clear decision
- Multiple open trades are allowed — each is tracked separately
Output ONLY valid JSON with no other text:
{
  "signal": "Buy" or "Hold",
  "confidence": 0-100,
  "entry_price": float or null,
  "stop_loss": float or null,
  "take_profit": float or null,
  "reasoning": {
    "ta_summary": "one sentence on indicators",
    "sentiment_summary": "one sentence on news sentiment",
    "history_summary": "one sentence on recent W/L pattern",
    "decision_rationale": "one sentence tying it together"
  }
}""",
            messages=[{
                "role": "user",
                "content": f"""BITCOIN SIGNAL REQUEST
Selected indicators:
{indicators_text}
News sentiment score: {sentiment.get('news_score', 0.0)} (range -1.0 to +1.0)
Headlines analyzed: {sentiment.get('headline_count', 0)}
Recent W/L pattern: {history_summary}
Currently open trades:
{trades_text}
Generate trading signal now."""
            }]
        )
        raw = response.content[0].text.strip()
        clean = raw.removeprefix("```json").removesuffix("```").strip()
        result = json.loads(clean)
        return {"success": True, "data": result}
    except Exception as e:
        print(f"[ERROR] Brain failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "data": {
                "signal": "Hold",
                "confidence": 0,
                "entry_price": None,
                "stop_loss": None,
                "take_profit": None,
                "reasoning": {
                    "ta_summary": "error",
                    "sentiment_summary": "error",
                    "history_summary": "error",
                    "decision_rationale": f"Brain failed: {e}"
                }
            }
        }
'''.strip()

files["step11_guardrails.py"] = '''
def apply_guardrails(signal_result):
    signal = signal_result["data"]
    overrides = []
    if signal["confidence"] < 70:
        overrides.append(f"Confidence {signal['confidence']}% below 70% threshold")
        signal["signal"] = "Hold"
        signal["entry_price"] = None
        signal["stop_loss"] = None
        signal["take_profit"] = None
    if overrides:
        signal["reasoning"]["decision_rationale"] += " [OVERRIDDEN: " + " | ".join(overrides) + "]"
    return {"success": True, "data": signal, "overrides": overrides}
'''.strip()

files["step12_output.py"] = '''
import os
import csv
import time
import threading
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

SIGNALS_FILE = "signals.csv"
FIELDNAMES = [
    "timestamp", "signal", "confidence",
    "entry_price", "stop_loss", "take_profit",
    "outcome", "close_price", "close_time",
    "ta_summary", "sentiment_summary",
    "history_summary", "decision_rationale", "overrides"
]

def save_signal(signal, overrides):
    file_exists = os.path.exists(SIGNALS_FILE)
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    row = {
        "timestamp":          timestamp,
        "signal":             signal.get("signal"),
        "confidence":         signal.get("confidence"),
        "entry_price":        signal.get("entry_price"),
        "stop_loss":          signal.get("stop_loss"),
        "take_profit":        signal.get("take_profit"),
        "outcome":            "pending",
        "close_price":        None,
        "close_time":         None,
        "ta_summary":         signal["reasoning"].get("ta_summary"),
        "sentiment_summary":  signal["reasoning"].get("sentiment_summary"),
        "history_summary":    signal["reasoning"].get("history_summary"),
        "decision_rationale": signal["reasoning"].get("decision_rationale"),
        "overrides":          " | ".join(overrides) if overrides else None
    }
    with open(SIGNALS_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)
    print(f"\\n{'='*50}")
    print(f"SIGNAL: {row['signal']}  |  CONFIDENCE: {row['confidence']}%")
    print(f"Entry: {row['entry_price']}  SL: {row['stop_loss']}  TP: {row['take_profit']}")
    print(f"Reason: {row['decision_rationale']}")
    if overrides:
        print(f"Overrides: {row['overrides']}")
    print(f"{'='*50}\\n")
    return row

def monitor_price(timestamp, stop_loss, take_profit):
    if not stop_loss or not take_profit:
        return
    def _monitor():
        print(f"[MONITOR] Watching trade from {timestamp} | SL:{stop_loss} TP:{take_profit}")
        while True:
            try:
                r = requests.get("https://api.kraken.com/0/public/Ticker", params={"pair": "XBTUSD"}, timeout=5)
                data = r.json()
                pair_key = list(data["result"].keys())[0]
                price = float(data["result"][pair_key]["c"][0])
                if price <= stop_loss:
                    _update_outcome(timestamp, "L", price)
                    print(f"[MONITOR] STOP LOSS HIT at {price}")
                    break
                elif price >= take_profit:
                    _update_outcome(timestamp, "W", price)
                    print(f"[MONITOR] TAKE PROFIT HIT at {price}")
                    break
                time.sleep(15)
            except Exception as e:
                print(f"[MONITOR] Error: {e}")
                time.sleep(15)
    threading.Thread(target=_monitor, daemon=True).start()

def _update_outcome(timestamp, outcome, close_price):
    rows = []
    with open(SIGNALS_FILE, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["timestamp"] == timestamp:
                row["outcome"]     = outcome
                row["close_price"] = close_price
                row["close_time"]  = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            rows.append(row)
    with open(SIGNALS_FILE, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
'''.strip()

files["main.py"] = '''
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
from step12_output import save_signal, monitor_price
from datetime import datetime

def run_cycle():
    print(f"\\n[{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}] Starting cycle...")
    candles_result = fetch_candles()
    news_result    = fetch_news()
    validation = validate_data(candles_result, news_result)
    if not validation["valid"]:
        print(f"[SKIP] Validation failed: {validation['errors']}")
        return
    compute_result = compute_indicators(candles_result["data"])
    if not compute_result["success"]:
        print(f"[SKIP] Compute failed: {compute_result['error']}")
        return
    merged     = merge_indicators(compute_result)
    selection  = select_indicators(merged["data"])
    filtered   = filter_indicators(merged["data"], selection)
    sentiment  = score_sentiment(news_result["data"])
    history    = load_history(n=10)
    history_summary = summarize_history(history)
    gate = pre_signal_gate()
    if not gate["proceed"]:
        print(f"[SKIP] Gate blocked: {gate['reason']}")
        return
    signal_result = generate_signal(
        filtered["data"],
        sentiment["data"],
        history_summary["data"],
        gate["open_trades"]
    )
    guarded = apply_guardrails(signal_result)
    row = save_signal(guarded["data"], guarded["overrides"])
    if guarded["data"]["signal"] == "Buy":
        monitor_price(row["timestamp"], guarded["data"]["stop_loss"], guarded["data"]["take_profit"])
    print("[DONE] Cycle complete.")

if __name__ == "__main__":
    print("Starting AI Crypto Signal Engine...")
    run_cycle()
    scheduler = BlockingScheduler()
    scheduler.add_job(run_cycle, "interval", minutes=1)
    print("Scheduler started — running every 1 minute. Press Ctrl+C to stop.")
    try:
        scheduler.start()
    except KeyboardInterrupt:
        print("Engine stopped.")
'''.strip()

# Write all files
for filename, content in files.items():
    with open(filename, "w") as f:
        f.write(content)
    print(f"Written: {filename}")

print("\nAll files written successfully.")
print("Next step: add your API keys to .env then run: python3.11 main.py")
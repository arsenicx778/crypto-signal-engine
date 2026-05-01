import os
import json
import time
import anthropic
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic()

MANDATORY_INDICATORS = ["di_plus", "di_minus"]

CANDIDATE_INDICATORS = [
    "rsi", "ema_20", "ema_50", "macd", "macd_signal",
    "macd_hist", "bb_upper", "bb_lower", "bb_mid", "bb_width",
    "atr", "vwap", "adx", "obv"
]

_CACHE_TTL_SECONDS = 15 * 60  # 15 minutes — matches sentiment cache TTL

# keyed by coin_name → {"result": ..., "fetched_at": float}
_indicator_cache: dict = {}


def select_indicators(all_indicators, coin_name: str = "ETH"):
    now = time.monotonic()
    cached = _indicator_cache.get(coin_name)
    if cached:
        age_seconds = now - cached["fetched_at"]
        if age_seconds < _CACHE_TTL_SECONDS:
            age_minutes = age_seconds / 60
            print(f"[STEP5:{coin_name}] indicator selection cache hit ({age_minutes:.0f}m old)")
            return cached["result"]

    print(f"[STEP5:{coin_name}] indicator selection fresh call")
    try:
        indicators_text = "\n".join(
            f"- {k}: {v}" for k, v in all_indicators.items() if k != "close"
        )
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            system="""You are a technical indicator selector for Ethereum trading.
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
                "content": f"Current Ethereum indicator values:\n{indicators_text}\n\nSelect the most relevant indicators."
            }]
        )
        raw = response.content[0].text.strip()
        clean = raw.removeprefix("```json").removesuffix("```").strip()
        result = json.loads(clean)
        valid = [i for i in result["selected"] if i in CANDIDATE_INDICATORS]
        merged = MANDATORY_INDICATORS + [i for i in valid if i not in MANDATORY_INDICATORS]
        result["selected"] = merged
        result["count"] = len(merged)
        out = {"success": True, "data": result}
        _indicator_cache[coin_name] = {"result": out, "fetched_at": now}
        return out
    except Exception as e:
        print(f"[WARN] Indicator selection failed: {e} — using defaults")
        return {
            "success": True,
            "data": {
                "selected": ["di_plus", "di_minus", "rsi", "macd", "macd_signal", "atr"],
                "count": 6,
                "reason": "fallback defaults"
            }
        }

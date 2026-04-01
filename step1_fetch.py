import os
import requests
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

KRAKEN_BASE     = "https://api.kraken.com/0/public"
CRYPTOPANIC_KEY = os.getenv("CRYPTOPANIC_API_KEY")

# ── KRAKEN ──────────────────────────────────────────────────────────
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

# ── PRIMARY FETCH ───────────────────────────────────────────────────
def fetch_candles():
    result = fetch_candles_kraken()
    if result["success"]:
        print(f"[FETCH] Candles from Kraken — {len(result['data'])} candles")
        return result
    print(f"[ABORT] Kraken fetch failed: {result['error']}")
    return {"success": False, "source": "none", "error": result["error"], "data": None}

# ── NEWS ────────────────────────────────────────────────────────────
def fetch_news():
    if not CRYPTOPANIC_KEY or CRYPTOPANIC_KEY.strip() == "":
        return {"success": False, "error": "No API key set", "data": []}
    try:
        url    = "https://cryptopanic.com/api/free/v1/posts/"
        params = {
            "auth_token": CRYPTOPANIC_KEY,
            "currencies": "BTC",
            "kind":       "news",
            "limit":      20
        }
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data      = response.json()
        headlines = [item["title"] for item in data.get("results", [])]
        return {"success": True, "data": headlines}
    except Exception as e:
        return {"success": False, "error": str(e), "data": []}

# ── TEST ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Testing candle fetch with auto fallback...")
    result = fetch_candles()
    if result["success"]:
        print(f"Source: {result['source']}")
        print(f"Candles: {len(result['data'])}")
        print(result["data"].tail(3))
    else:
        print(f"Both sources failed: {result['error']}")

    print("\nTesting news fetch...")
    news = fetch_news()
    if news["success"]:
        print(f"Headlines: {len(news['data'])}")
        for h in news["data"][:3]:
            print(f"  - {h}")
    else:
        print(f"News: {news['error']}")
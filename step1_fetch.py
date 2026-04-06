import os
import re
import requests
import pandas as pd
import xml.etree.ElementTree as ET
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

KRAKEN_BASE       = "https://api.kraken.com/0/public"
COINDESK_RSS      = "https://www.coindesk.com/arc/outboundfeeds/rss/"
COINTELEGRAPH_RSS = "https://cointelegraph.com/rss"

# ── KRAKEN ──────────────────────────────────────────────────────────
def fetch_candles_kraken(symbol="ETHUSD", interval=1, limit=200):
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
def _parse_rss_headlines(content):
    """Parse ETH-related headlines from RSS XML content. Returns a list of title strings."""
    root = ET.fromstring(content)
    items = root.findall(".//item")
    headlines = []
    for item in items:
        title_el = item.find("title")
        if title_el is None or not title_el.text:
            continue
        title = title_el.text.strip()
        title_lower = title.lower()
        if (
            "ethereum" in title_lower
            or re.search(r"\bether\b", title_lower)
            or re.search(r"\beth\b", title_lower)
        ):
            headlines.append(title)
    return headlines

def fetch_news():
    coindesk_headlines = []
    cointelegraph_headlines = []

    try:
        response = requests.get(COINDESK_RSS, timeout=10)
        response.raise_for_status()
        coindesk_headlines = _parse_rss_headlines(response.content)
    except Exception as e:
        print(f"[WARN] CoinDesk RSS failed: {e}")

    try:
        response = requests.get(COINTELEGRAPH_RSS, timeout=10)
        response.raise_for_status()
        cointelegraph_headlines = _parse_rss_headlines(response.content)
    except Exception as e:
        print(f"[WARN] CoinTelegraph RSS failed: {e}")

    # Combine and deduplicate by title, preserving order
    seen = set()
    combined = []
    for title in coindesk_headlines + cointelegraph_headlines:
        if title not in seen:
            seen.add(title)
            combined.append(title)
        if len(combined) == 20:
            break

    if not combined and not coindesk_headlines and not cointelegraph_headlines:
        return {"success": False, "error": "Both news sources failed", "data": []}

    return {"success": True, "data": combined}

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

"""
historical_fetch.py

Fetches maximum free-tier historical OHLCV data from Kraken across four
timeframes for each coin, caches results, and computes all technical
indicators plus higher-timeframe trend signals.

Timeframes per coin:
  1440m  (daily)   — 720 candles ≈ 2 years, single call
  240m   (4-hour)  — 720 candles ≈ 120 days, single call
  60m    (1-hour)  — 720 candles ≈ 30 days, single call
  5m     (5-min)   — paginated up to 10 calls ≈ 25 days

Cache invalidation:
  5m  cache → 24 hours
  all others → 7 days
"""

import os
import time
import json
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone

try:
    import ta
    _TA_OK = True
except ImportError:
    _TA_OK = False
    print("[FETCH] WARNING: 'ta' library not installed. Run: pip install ta")

CACHE_DIR = "cache"

KRAKEN_SYMBOLS = {
    "ETH":  "XETHZUSD",
    "SOL":  "SOLUSD",
    "XRP":  "XXRPZUSD",
    "LINK": "LINKUSD",
}

KRAKEN_BASE = "https://api.kraken.com/0/public/OHLC"

INTERVALS = {
    "daily":     1440,
    "four_hour":  240,
    "one_hour":    60,
    "five_min":     5,
}

CACHE_TTL = {
    1440: 7 * 86400,
    240:  7 * 86400,
    60:   7 * 86400,
    5:        86400,
}


# ── Cache helpers ──────────────────────────────────────────────────────────────

def _cache_path(coin: str, interval: int) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{coin}_{interval}m.csv")


def _cache_valid(path: str, ttl: int) -> bool:
    if not os.path.exists(path):
        return False
    age = time.time() - os.path.getmtime(path)
    return age < ttl


def _load_cache(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["timestamp"])
    return df.sort_values("timestamp").reset_index(drop=True)


def _save_cache(df: pd.DataFrame, path: str):
    df.to_csv(path, index=False)


# ── Kraken fetch helpers ───────────────────────────────────────────────────────

def _ohlcv_columns():
    return ["timestamp", "open", "high", "low", "close", "vwap", "volume", "trades"]


def _parse_kraken_ohlcv(raw: list) -> pd.DataFrame:
    df = pd.DataFrame(raw, columns=_ohlcv_columns())
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="s", utc=True)
    df["timestamp"] = df["timestamp"].dt.tz_localize(None)
    return df[["timestamp", "open", "high", "low", "close", "volume"]].sort_values("timestamp")


def _fetch_single(symbol: str, interval: int) -> pd.DataFrame:
    params = {"pair": symbol, "interval": interval}
    resp = requests.get(KRAKEN_BASE, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("error"):
        raise ValueError(f"Kraken error: {data['error']}")
    pair_key = [k for k in data["result"] if k != "last"][0]
    return _parse_kraken_ohlcv(data["result"][pair_key])


def _fetch_paginated_5m(symbol: str, max_calls: int = 10) -> pd.DataFrame:
    """
    Paginate 5-minute candles backwards.

    Kraken OHLC with interval=5 returns up to 720 candles ending at 'now'
    when called without 'since'.  To go further back we set 'since' to the
    earliest timestamp we already have minus 720*5 minutes, which asks Kraken
    to start a new 720-candle window from that earlier point.
    """
    all_frames = []

    # First call — no since, gets the most recent 720 candles
    for call_n in range(max_calls):
        params = {"pair": symbol, "interval": 5}

        if all_frames:
            # Ask for candles starting well before our earliest row
            earliest_unix = int(all_frames[-1]["timestamp"].min().timestamp())
            # Step back one full window (720 candles × 5 min × 60 s)
            params["since"] = earliest_unix - (720 * 5 * 60)

        try:
            resp = requests.get(KRAKEN_BASE, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"[FETCH] 5m page {call_n+1} error: {e}")
            break

        if data.get("error"):
            print(f"[FETCH] 5m page {call_n+1} Kraken error: {data['error']}")
            break

        pair_key = [k for k in data["result"] if k != "last"][0]
        raw = data["result"][pair_key]
        if not raw:
            break

        df = _parse_kraken_ohlcv(raw)

        # Stop if this batch doesn't extend our history further back
        if all_frames:
            current_earliest = all_frames[-1]["timestamp"].min()
            new_earliest     = df["timestamp"].min()
            if new_earliest >= current_earliest:
                break  # no new data going back

        all_frames.append(df)
        print(f"[FETCH] 5m page {call_n+1}: {len(df)} candles, "
              f"from {df['timestamp'].min().strftime('%Y-%m-%d %H:%M')}")

        if call_n < max_calls - 1:
            time.sleep(1)

    if not all_frames:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    combined = pd.concat(all_frames).drop_duplicates("timestamp").sort_values("timestamp")
    return combined.reset_index(drop=True)


# ── Main fetch function ────────────────────────────────────────────────────────

def fetch_max_history(coin_symbol: str) -> dict:
    """
    Fetch maximum historical OHLCV data for a coin across four timeframes.

    coin_symbol: one of ETH, SOL, XRP, LINK

    Returns dict with keys: daily, four_hour, one_hour, five_min
    Each value is a DataFrame(timestamp, open, high, low, close, volume).
    """
    kraken_sym = KRAKEN_SYMBOLS.get(coin_symbol.upper())
    if not kraken_sym:
        raise ValueError(f"Unknown coin: {coin_symbol}")

    result = {}
    timeframe_map = [
        ("daily",     INTERVALS["daily"]),
        ("four_hour", INTERVALS["four_hour"]),
        ("one_hour",  INTERVALS["one_hour"]),
        ("five_min",  INTERVALS["five_min"]),
    ]

    for tf_name, interval in timeframe_map:
        cache_path = _cache_path(coin_symbol.upper(), interval)
        ttl = CACHE_TTL[interval]

        if _cache_valid(cache_path, ttl):
            df = _load_cache(cache_path)
            label = "2y daily" if interval == 1440 else f"{len(df)} candles"
            print(f"[FETCH:{coin_symbol}] loaded {interval}m candles from cache ({label})")
            result[tf_name] = df
            continue

        try:
            if interval == 5:
                df = _fetch_paginated_5m(kraken_sym, max_calls=10)
            else:
                df = _fetch_single(kraken_sym, interval)

            _save_cache(df, cache_path)
            label = "2y of daily data" if interval == 1440 else f"{len(df)} candles"
            print(f"[FETCH:{coin_symbol}] fetched {len(df)} fresh {interval}m candles from Kraken ({label})")
            result[tf_name] = df

            # small pause between calls to be polite to the API
            time.sleep(0.5)

        except Exception as e:
            print(f"[FETCH:{coin_symbol}] {interval}m fetch failed: {e}")
            # Return empty frame so caller can handle gracefully
            result[tf_name] = pd.DataFrame(
                columns=["timestamp", "open", "high", "low", "close", "volume"]
            )

    return result


# ── Trend detection ────────────────────────────────────────────────────────────

def _detect_trend(closes: pd.Series, lookback: int) -> str:
    """
    Higher-highs / lower-lows trend across the last `lookback` closes.
    Returns BULLISH, BEARISH, or NEUTRAL.
    """
    if len(closes) < lookback:
        return "NEUTRAL"
    vals = closes.iloc[-lookback:].tolist()
    higher = all(vals[i] > vals[i - 1] for i in range(1, len(vals)))
    lower  = all(vals[i] < vals[i - 1] for i in range(1, len(vals)))
    if higher:
        return "BULLISH"
    if lower:
        return "BEARISH"
    return "NEUTRAL"


def _htf_trend_series(htf_df: pd.DataFrame, five_min_df: pd.DataFrame,
                       col_name: str, lookback: int) -> pd.Series:
    """
    Compute a rolling trend label on htf_df and forward-fill it onto the
    5-minute timestamp index.
    """
    if htf_df.empty or five_min_df.empty:
        return pd.Series("NEUTRAL", index=five_min_df.index)

    trends = []
    for i in range(len(htf_df)):
        window = htf_df["close"].iloc[max(0, i - lookback + 1): i + 1]
        trends.append(_detect_trend(window, lookback))

    htf_trend = pd.Series(trends, index=htf_df["timestamp"].values)
    htf_trend = htf_trend.reindex(
        htf_trend.index.union(five_min_df["timestamp"].values)
    ).ffill().reindex(five_min_df["timestamp"].values)
    htf_trend.index = five_min_df.index
    return htf_trend.fillna("NEUTRAL")


# ── Indicator computation ──────────────────────────────────────────────────────

def _compute_indicators_df(df: pd.DataFrame) -> pd.DataFrame:
    """Add RSI, MACD, ADX, DI+, DI-, BB_WIDTH, ATR columns to a copy of df."""
    if df.empty or not _TA_OK:
        return df

    df = df.copy()
    close = df["close"]
    high  = df["high"]
    low   = df["low"]

    # RSI 14
    df["rsi"] = ta.momentum.rsi(close, window=14)

    # MACD 12/26/9
    macd_obj = ta.trend.MACD(close, window_slow=26, window_fast=12, window_sign=9)
    df["macd"]        = macd_obj.macd()
    df["macd_signal"] = macd_obj.macd_signal()
    df["macd_hist"]   = macd_obj.macd_diff()

    # ADX + DI
    df["adx"]      = ta.trend.adx(high, low, close, window=14)
    df["di_plus"]  = ta.trend.adx_pos(high, low, close, window=14)
    df["di_minus"] = ta.trend.adx_neg(high, low, close, window=14)

    # Bollinger Band Width (BB 20,2)
    bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)
    df["bb_width"] = (bb.bollinger_hband() - bb.bollinger_lband()) / bb.bollinger_mavg()

    # ATR 14
    df["atr"] = ta.volatility.average_true_range(high, low, close, window=14)

    return df


def compute_all_indicators(data_dict: dict) -> dict:
    """
    Run technical indicators on each timeframe and attach HTF trend signals
    to the 5-minute DataFrame.

    Returns the same dict with each DataFrame having indicator columns added,
    and the five_min DataFrame having htf_trend_daily, htf_trend_4h,
    htf_trend_1h columns.
    """
    result = {}

    for tf_name, df in data_dict.items():
        result[tf_name] = _compute_indicators_df(df)

    # Compute rolling trend on each HTF and forward-fill onto 5-min
    five_min = result.get("five_min", pd.DataFrame())
    if not five_min.empty:
        daily     = result.get("daily",     pd.DataFrame())
        four_hour = result.get("four_hour", pd.DataFrame())
        one_hour  = result.get("one_hour",  pd.DataFrame())

        five_min["htf_trend_daily"] = _htf_trend_series(daily,     five_min, "htf_trend_daily", 5)
        five_min["htf_trend_4h"]    = _htf_trend_series(four_hour, five_min, "htf_trend_4h",    6)
        five_min["htf_trend_1h"]    = _htf_trend_series(one_hour,  five_min, "htf_trend_1h",    3)

        result["five_min"] = five_min

    return result


# ── Quick sanity check ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    for coin in ("ETH", "SOL", "XRP", "LINK"):
        print(f"\n{'─'*50}")
        print(f"Fetching {coin}...")
        data = fetch_max_history(coin)
        for tf, df in data.items():
            print(f"  {tf:10s}: {len(df)} rows, "
                  f"{df['timestamp'].min()} → {df['timestamp'].max()}")
        print(f"Computing indicators for {coin}...")
        enriched = compute_all_indicators(data)
        fm = enriched.get("five_min")
        if fm is not None and not fm.empty:
            print(f"  5min sample: rsi={fm['rsi'].iloc[-1]:.1f} "
                  f"adx={fm['adx'].iloc[-1]:.1f} "
                  f"daily_trend={fm['htf_trend_daily'].iloc[-1]}")

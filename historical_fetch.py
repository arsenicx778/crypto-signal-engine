"""
historical_fetch.py

Fetches maximum free-tier historical OHLCV data from Kraken.

Kraken OHLC API hard limit: exactly 720 candles regardless of interval or since parameter.
Effective history per interval:
  1440m (daily)   — 720 candles = 720 days (~2 years)
  240m  (4-hour)  — 720 candles = 120 days
  60m   (1-hour)  — 720 candles = 30 days
  30m             — 720 candles = 15 days  ← primary backtest timeframe
  5m              — 720 candles = 2.5 days (too shallow for backtesting)

Primary backtest timeframe: 30m (720 candles = 15 days, ~180-350 simulated trades)
HTF trend filter: 1h (direction), daily (regime context)

Cache invalidation:
  30m cache → 24 hours
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
    "daily":      1440,
    "four_hour":   240,
    "one_hour":     60,
    "thirty_min":   30,   # primary backtest timeframe — 15 days of data
}

CACHE_TTL = {
    1440: 7 * 86400,
    240:  7 * 86400,
    60:   7 * 86400,
    30:       86400,   # refresh daily
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


_5M_TARGET_DAYS   = 60
_5M_MAX_CALLS     = 25
_5M_CALL_DELAY    = 0.4   # seconds between paginated calls
_5M_CANDLES_WINDOW = 720  # Kraken returns up to 720 per call


def _fetch_paginated_5m(symbol: str, max_calls: int = _5M_MAX_CALLS) -> pd.DataFrame:
    """
    Paginate 5-minute candles backwards, targeting ~60 days of data.

    Strategy: first call gets the most recent 720 candles (~2.5 days).
    Each subsequent call sets 'since' to the earliest unix timestamp we have
    minus one full window so Kraken returns the next earlier batch.
    Stop when:
      - Kraken returns fewer than 100 candles (no more history available)
      - We have >= 60 days of data
      - max_calls exhausted
    """
    all_frames: list = []
    target_seconds = _5M_TARGET_DAYS * 86400

    for call_n in range(max_calls):
        params: dict = {"pair": symbol, "interval": 5}

        if all_frames:
            earliest_unix = int(
                pd.concat(all_frames)["timestamp"].min().timestamp()
            )
            params["since"] = earliest_unix - (_5M_CANDLES_WINDOW * 5 * 60)

        try:
            resp = requests.get(KRAKEN_BASE, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"[FETCH] 5m page {call_n + 1} error: {e}")
            break

        if data.get("error"):
            print(f"[FETCH] 5m page {call_n + 1} Kraken error: {data['error']}")
            break

        pair_key = [k for k in data["result"] if k != "last"][0]
        raw = data["result"][pair_key]

        if not raw or len(raw) < 100:
            # Fewer than 100 candles — Kraken has no more history for this symbol
            if raw:
                df_tail = _parse_kraken_ohlcv(raw)
                all_frames.append(df_tail)
                print(f"[FETCH] 5m page {call_n + 1}: {len(raw)} candles (end of history) "
                      f"from {df_tail['timestamp'].min().strftime('%Y-%m-%d %H:%M')}")
            break

        df = _parse_kraken_ohlcv(raw)

        # Stop if this batch doesn't extend our history further back
        if all_frames:
            combined_so_far = pd.concat(all_frames)
            current_earliest = combined_so_far["timestamp"].min()
            new_earliest = df["timestamp"].min()
            if new_earliest >= current_earliest:
                break

        all_frames.append(df)
        combined_check = pd.concat(all_frames)
        span = (combined_check["timestamp"].max() - combined_check["timestamp"].min()).total_seconds()
        print(f"[FETCH] 5m page {call_n + 1}: {len(df)} candles "
              f"from {df['timestamp'].min().strftime('%Y-%m-%d %H:%M')} "
              f"(total span: {span / 86400:.1f} days)")

        if span >= target_seconds:
            print(f"[FETCH] 5m target of {_5M_TARGET_DAYS} days reached after {call_n + 1} calls")
            break

        if call_n < max_calls - 1:
            time.sleep(_5M_CALL_DELAY)

    if not all_frames:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    combined = pd.concat(all_frames).drop_duplicates("timestamp").sort_values("timestamp")
    return combined.reset_index(drop=True)


# ── Main fetch function ────────────────────────────────────────────────────────

def fetch_max_history(coin_symbol: str) -> dict:
    """
    Fetch maximum historical OHLCV data for a coin.

    coin_symbol: one of ETH, SOL, XRP, LINK

    Returns dict with keys: daily, four_hour, one_hour, thirty_min
    Each value is a DataFrame(timestamp, open, high, low, close, volume).

    Note: Kraken OHLC is hard-capped at 720 candles regardless of interval.
    30m candles give 15 days of backtest data (~180-350 simulated trades).
    """
    kraken_sym = KRAKEN_SYMBOLS.get(coin_symbol.upper())
    if not kraken_sym:
        raise ValueError(f"Unknown coin: {coin_symbol}")

    result = {}
    timeframe_map = [
        ("daily",       INTERVALS["daily"]),
        ("four_hour",   INTERVALS["four_hour"]),
        ("one_hour",    INTERVALS["one_hour"]),
        ("thirty_min",  INTERVALS["thirty_min"]),
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


def _htf_trend_series(htf_df: pd.DataFrame, base_df: pd.DataFrame,
                       col_name: str, lookback: int) -> pd.Series:
    """
    Compute a rolling trend label on htf_df and forward-fill it onto the
    base_df timestamp index (30m, 5m, or any other resolution).
    """
    if htf_df.empty or base_df.empty:
        return pd.Series("NEUTRAL", index=base_df.index)

    trends = []
    for i in range(len(htf_df)):
        window = htf_df["close"].iloc[max(0, i - lookback + 1): i + 1]
        trends.append(_detect_trend(window, lookback))

    htf_trend = pd.Series(trends, index=htf_df["timestamp"].values)
    htf_trend = htf_trend.reindex(
        htf_trend.index.union(base_df["timestamp"].values)
    ).ffill().reindex(base_df["timestamp"].values)
    htf_trend.index = base_df.index
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
    to the thirty_min DataFrame.

    Returns the same dict with each DataFrame having indicator columns added,
    and the thirty_min DataFrame having htf_trend_daily and htf_trend_1h columns.
    """
    result = {}

    for tf_name, df in data_dict.items():
        result[tf_name] = _compute_indicators_df(df)

    # Forward-fill HTF trend onto 30m base frame
    thirty_min = result.get("thirty_min", pd.DataFrame())
    if not thirty_min.empty:
        daily    = result.get("daily",    pd.DataFrame())
        one_hour = result.get("one_hour", pd.DataFrame())

        thirty_min["htf_trend_daily"] = _htf_trend_series(daily,    thirty_min, "htf_trend_daily", 5)
        thirty_min["htf_trend_1h"]    = _htf_trend_series(one_hour, thirty_min, "htf_trend_1h",    3)
        # 4h trend not meaningful at 30m resolution — set neutral
        thirty_min["htf_trend_4h"]    = "NEUTRAL"

        result["thirty_min"] = thirty_min

    return result


# ── Quick sanity check ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    for coin in ("ETH", "SOL", "XRP", "LINK"):
        print(f"\n{'─'*50}")
        print(f"Fetching {coin}...")
        data = fetch_max_history(coin)
        for tf, df in data.items():
            print(f"  {tf:12s}: {len(df)} rows, "
                  f"{df['timestamp'].min()} -> {df['timestamp'].max()}")
        print(f"Computing indicators for {coin}...")
        enriched = compute_all_indicators(data)
        tm = enriched.get("thirty_min")
        if tm is not None and not tm.empty:
            print(f"  30min sample: rsi={tm['rsi'].iloc[-1]:.1f} "
                  f"adx={tm['adx'].iloc[-1]:.1f} "
                  f"daily_trend={tm['htf_trend_daily'].iloc[-1]}")

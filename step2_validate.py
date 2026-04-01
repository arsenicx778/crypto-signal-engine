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
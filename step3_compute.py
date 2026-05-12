import ta
import pandas as pd


def compute_momentum_context(df):
    close     = df["close"]
    rsi_series = ta.momentum.RSIIndicator(close, window=14).rsi()
    adx_obj   = ta.trend.ADXIndicator(df["high"], df["low"], close, window=14)
    adx_series = adx_obj.adx()
    macd_hist  = ta.trend.MACD(close, window_fast=12, window_slow=26, window_sign=9).macd_diff()
    di_plus    = adx_obj.adx_pos()
    di_minus   = adx_obj.adx_neg()
    volume     = df["volume"]

    def direction(series, periods):
        recent = series.iloc[-periods:]
        slope  = recent.iloc[-1] - recent.iloc[0]
        if slope > 0: return "rising"
        if slope < 0: return "falling"
        return "flat"

    def acceleration(series, short=3, long=6):
        recent_change = series.iloc[-1] - series.iloc[-short]
        prior_change  = series.iloc[-short] - series.iloc[-long]
        if prior_change == 0:
            return "accelerating" if recent_change > 0 else "steady"
        if recent_change > prior_change * 1.2: return "accelerating"
        if recent_change < prior_change * 0.8: return "decelerating"
        return "steady"

    session_high     = close.iloc[-48:].max()
    session_low      = close.iloc[-48:].min()
    current          = close.iloc[-1]
    session_position = (
        (current - session_low) / (session_high - session_low) * 100
        if session_high != session_low else 50.0
    )

    avg_volume_20  = volume.iloc[-20:].mean()
    current_volume = volume.iloc[-1]
    if current_volume > avg_volume_20 * 1.2:
        volume_context = "above avg"
    elif current_volume < avg_volume_20 * 0.8:
        volume_context = "below avg"
    else:
        volume_context = "normal"

    di_crossover = None
    if di_plus.iloc[-1] > di_minus.iloc[-1] and di_plus.iloc[-3] < di_minus.iloc[-3]:
        di_crossover = "DI+ just crossed above DI- (bullish crossover 3 candles ago)"
    elif di_minus.iloc[-1] > di_plus.iloc[-1] and di_minus.iloc[-3] < di_plus.iloc[-3]:
        di_crossover = "DI- just crossed above DI+ (bearish crossover 3 candles ago)"

    pos = round(session_position, 1)
    return {
        "rsi_6":               direction(rsi_series, 6),
        "rsi_15":              direction(rsi_series, 15),
        "adx_6":               direction(adx_series, 6),
        "macd_hist_accel":     acceleration(macd_hist),
        "di_trend":            "DI+ dominant" if di_plus.iloc[-1] > di_minus.iloc[-1] else "DI- dominant",
        "di_crossover":        di_crossover,
        "session_position_pct": pos,
        "session_high":        round(float(session_high), 4),
        "session_low":         round(float(session_low), 4),
        "volume_context":      volume_context,
        "price_vs_session":    "upper third" if pos > 66 else "lower third" if pos < 33 else "middle",
    }


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
        bb_mid_val = indicators["bb_mid"]
        indicators["bb_width"]    = round((indicators["bb_upper"] - indicators["bb_lower"]) / bb_mid_val, 6) if bb_mid_val else 0.0
        indicators["atr"]         = round(ta.volatility.AverageTrueRange(high, low, close, window=14).average_true_range().iloc[-1], 4)
        indicators["vwap"]        = round(ta.volume.VolumeWeightedAveragePrice(high, low, close, volume).volume_weighted_average_price().iloc[-1], 4)
        adx_obj = ta.trend.ADXIndicator(high, low, close, window=14)
        indicators["adx"]         = round(adx_obj.adx().iloc[-1], 4)
        indicators["di_plus"]     = round(adx_obj.adx_pos().iloc[-1], 4)
        indicators["di_minus"]    = round(adx_obj.adx_neg().iloc[-1], 4)
        indicators["obv"]         = round(ta.volume.OnBalanceVolumeIndicator(close, volume).on_balance_volume().iloc[-1], 4)
        indicators["close"]       = round(close.iloc[-1], 4)
        prev = close.iloc[-2] if len(close) >= 2 else close.iloc[-1]
        indicators["prev_close"]  = round(prev, 4)
        indicators["momentum_context"] = compute_momentum_context(df)
        return {"success": True, "data": indicators}
    except Exception as e:
        return {"success": False, "error": str(e), "data": None}
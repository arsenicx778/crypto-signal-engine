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
        return {"success": True, "data": indicators}
    except Exception as e:
        return {"success": False, "error": str(e), "data": None}
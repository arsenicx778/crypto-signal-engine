# ── Strategy variant space — backtest tests all combinations ──────────────────
VARIANT_CONFIG = {
    "time_stop_mode":              ["A_SYMMETRIC", "B_PROFIT_ONLY", "C_NONE"],
    "atr_tp_multiplier":           [0.8, 1.0, 1.2],
    "atr_sl_multiplier":           [0.7, 0.83, 1.0],
    "adx_range":                   [(18, 50), (20, 45), (25, 55)],
    "require_candle_confirmation": [True, False],
}

# ── Live engine parameters (set after backtest analysis) ──────────────────────
LIVE_TIME_STOP_MODE              = "B_PROFIT_ONLY"
LIVE_ATR_TP_MULTIPLIER           = 2.0
LIVE_ATR_SL_MULTIPLIER           = 1.5
LIVE_ADX_MIN                     = 18
LIVE_ADX_MAX                     = 50
LIVE_REQUIRE_CANDLE_CONFIRMATION = True

# ── ATR-based stop and target ─────────────────────────────────────────────────
# SL = 1.5×ATR pushes stops outside single-bar noise (was 0.83×).
# TP = 2.0×ATR gives R:R = 1.33, requiring 43% WR to break even.
ATR_MULTIPLIER_STOP  = 1.5
ATR_MULTIPLIER_TP    = 2.0
REWARD_RISK_RATIO    = 1.33

# ── Risk per trade ────────────────────────────────────────────────────────────
RISK_PERCENT    = 0.015
REWARD_PERCENT  = RISK_PERCENT * REWARD_RISK_RATIO

# ── Entry quality filters ─────────────────────────────────────────────────────
LONG_ADX_MIN          = 18
LONG_ADX_MAX          = 50
SHORT_ADX_MIN         = 18
SHORT_ADX_MAX         = 50
MIN_DI_GAP            = 8
CONFIDENCE_THRESHOLD  = 65
SCALP_RSI_LONG_MAX    = 65
SCALP_RSI_SHORT_MIN   = 35
MIN_BB_WIDTH          = 0.008
MIN_MACD_MOMENTUM     = 0.05

# ── Per-coin live config from variant testing ─────────────────────────────────
PER_COIN_LIVE_CONFIG: dict = {
    "ETH": {
        "ATR_SL_MULTIPLIER":           1.5,
        "ATR_TP_MULTIPLIER":           2.0,
        "REQUIRE_CANDLE_CONFIRMATION": True,
    },
    "SOL": {
        "ATR_SL_MULTIPLIER":           1.5,
        "ATR_TP_MULTIPLIER":           2.0,
        "REQUIRE_CANDLE_CONFIRMATION": True,
    },
    "XRP": {
        "ATR_SL_MULTIPLIER":           1.5,
        "ATR_TP_MULTIPLIER":           2.0,
        "REQUIRE_CANDLE_CONFIRMATION": True,
    },
    "LINK": {
        "ATR_SL_MULTIPLIER":           1.5,
        "ATR_TP_MULTIPLIER":           2.0,
        "REQUIRE_CANDLE_CONFIRMATION": False,
    },
}

# ── Fixed parameters not under variant testing ────────────────────────────────
TIME_STOP_MINUTES         = 60
MAX_HOLD_HOURS_NO_STOP    = 24
MAX_CONCURRENT_TRADES     = 2
CYCLE_INTERVAL_SECONDS    = 180
ENABLE_SHORTS             = True

# ── Operational flags ──────────────────────────────────────────────────────────
LIVE_TRADING_ENABLED        = True
LIVE_TRADING_PAUSE_REASON   = ""
SUSPENDED_COINS             = ["SOL", "LINK", "XRP"]  # suspended during A/B/C/D experiment
LIVE_LEARNING_ENABLED       = True
HISTORICAL_LEARNING_ENABLED = True

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
LIVE_ATR_TP_MULTIPLIER           = 1.0
LIVE_ATR_SL_MULTIPLIER           = 0.83
LIVE_ADX_MIN                     = 18
LIVE_ADX_MAX                     = 50
LIVE_REQUIRE_CANDLE_CONFIRMATION = True

# ── ATR-based stop and target ─────────────────────────────────────────────────
ATR_MULTIPLIER_STOP  = 0.83
ATR_MULTIPLIER_TP    = 1.0
REWARD_RISK_RATIO    = 1.2

# ── Risk per trade ────────────────────────────────────────────────────────────
RISK_PERCENT    = 0.015
REWARD_PERCENT  = RISK_PERCENT * REWARD_RISK_RATIO

# ── Entry quality filters ─────────────────────────────────────────────────────
LONG_ADX_MIN          = 18
LONG_ADX_MAX          = 50
SHORT_ADX_MIN         = 18
SHORT_ADX_MAX         = 50
MIN_DI_GAP            = 8
CONFIDENCE_THRESHOLD  = 62
SCALP_RSI_LONG_MAX    = 65
SCALP_RSI_SHORT_MIN   = 35
MIN_BB_WIDTH          = 0.003
MIN_MACD_MOMENTUM     = 0.05

# ── Per-coin live config from variant testing ─────────────────────────────────
PER_COIN_LIVE_CONFIG: dict = {
    "ETH": {
        "ATR_TP_MULTIPLIER":           1.0,
        "ATR_SL_MULTIPLIER":           1.0,
        "ADX_MIN":                     20,
        "ADX_MAX":                     45,
        "REQUIRE_CANDLE_CONFIRMATION": True,
    },
    "SOL": {
        "ATR_TP_MULTIPLIER":           1.2,
        "ATR_SL_MULTIPLIER":           0.83,
        "ADX_MIN":                     18,
        "ADX_MAX":                     50,
        "REQUIRE_CANDLE_CONFIRMATION": True,
    },
    "XRP": {
        "ATR_TP_MULTIPLIER":           1.2,
        "ATR_SL_MULTIPLIER":           0.83,
        "ADX_MIN":                     20,
        "ADX_MAX":                     45,
        "REQUIRE_CANDLE_CONFIRMATION": True,
    },
    "LINK": {
        "ATR_TP_MULTIPLIER":           1.0,
        "ATR_SL_MULTIPLIER":           1.0,
        "ADX_MIN":                     25,
        "ADX_MAX":                     55,
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
SUSPENDED_COINS             = []
LIVE_LEARNING_ENABLED       = False
HISTORICAL_LEARNING_ENABLED = True

"""
Experiment configuration — A/B/C/D variant test.
This file is the only place to change experiment settings.
Delete this folder when the experiment is over.
"""

EXPERIMENT_ACTIVE = True

# Coin used for all variants (ETH only during experiment)
EXPERIMENT_COIN = {"symbol": "XETHZUSD", "name": "ETH", "capital": 1000}

# Variant definitions
VARIANTS = {
    "A": {
        "label":            "Control — Sonnet + learning ON",
        "brain_model":      "claude-sonnet-4-20250514",
        "disable_learning": False,
    },
    "B": {
        "label":            "GPT-5.4 Brain",
        "brain_model":      "gpt-5.4",
        "disable_learning": False,
        "disabled":         True,  # paused 2026-05-24 — 0 trades in 149 cycles (too cautious)
    },
    "C": {
        "label":            "Sonnet + learning OFF",
        "brain_model":      "claude-sonnet-4-20250514",
        "disable_learning": True,
    },
}

# Cycle interval in seconds (matches live engine)
CYCLE_INTERVAL_SECONDS = 180

# Minimum trades before variant comparison is considered meaningful
MIN_TRADES_FOR_COMPARISON = 30

"""
variant_guards.py — Guardrail overlay per variant.

Calls the original step11_guardrails.apply_guardrails() unchanged,
then applies variant-specific additional rules on top.

Original step11_guardrails.py is never modified.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import step11_guardrails as _guards


def apply_guardrails_for_variant(
    variant: str,
    variant_cfg: dict,
    signal_result: dict,
    filtered_indicators: dict,
    coin_name: str = "ETH",
) -> dict:
    """
    Run standard guardrails for the given variant.
    Returns same schema as step11_guardrails.apply_guardrails().
    """
    return _guards.apply_guardrails(signal_result,
                                    all_indicators=filtered_indicators,
                                    coin_name=coin_name)

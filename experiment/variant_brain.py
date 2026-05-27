"""
variant_brain.py — Model routing per variant.

Variant A: Sonnet brain via step10_brain.generate_signal() — learning ON
Variant B: GPT brain via step10_brain.build_prompts() — IDENTICAL prompt, only model swaps
Variant C: Sonnet brain via step10_brain.generate_signal() — learning OFF

Original step10_brain.py exposes build_prompts() / parse_brain_response() so all
three variants share the exact same prompt and the only experimental difference
is the model behind the API call.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import step10_brain as _brain

_openai_client = None


def _get_openai():
    global _openai_client
    if _openai_client is None:
        from openai import OpenAI
        _openai_client = OpenAI()
    return _openai_client


def _call_gpt_brain(
    all_indicators: dict,
    sentiment: dict,
    raw_history: list,
    capital: float,
    risk_amount: float,
    reward_amount: float,
    pre_sizing: dict,
    coin_name: str,
    coin_symbol: str,
    model: str,
    learning_override: str = None,
) -> dict:
    """
    Send the EXACT same prompt the Sonnet brain receives, but route to GPT.
    Uses step10_brain.build_prompts() + parse_brain_response() so prompt and
    parsing are guaranteed identical to variant A.
    """
    try:
        system_prompt, user_message = _brain.build_prompts(
            all_indicators    = all_indicators,
            sentiment         = sentiment,
            raw_history       = raw_history,
            capital           = capital,
            risk_amount       = risk_amount,
            reward_amount     = reward_amount,
            pre_sizing        = pre_sizing,
            coin_name         = coin_name,
            coin_symbol       = coin_symbol,
            learning_override = learning_override,
        )

        response = _get_openai().chat.completions.create(
            model=model,
            max_completion_tokens=800,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_message},
            ],
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content
        print(f"[VARIANT-B:{coin_name}] {model} responded")
        return _brain.parse_brain_response(raw, coin_name)

    except Exception as e:
        print(f"[VARIANT-B:{coin_name}] GPT brain failed: {e}")
        return _brain._brain_error_response(e)


def generate_signal_for_variant(
    variant: str,
    variant_cfg: dict,
    filtered_indicators: dict,
    sentiment: dict,
    raw_history: list,
    capital: float,
    risk_amount: float,
    reward_amount: float,
    pre_sizing: dict,
    coin_name: str,
    coin_symbol: str,
) -> dict:
    """
    Route signal generation to the correct model based on variant.
    Returns same schema as step10_brain.generate_signal().

    A — Sonnet, learning ON  (control)
    B — GPT brain, learning ON (clean model swap vs A)
    C — Sonnet, learning OFF (isolates the effect of the learning context)
    """
    brain_model      = variant_cfg["brain_model"]
    disable_learning = variant_cfg.get("disable_learning", False)
    learning_override = "" if disable_learning else None

    if brain_model.startswith("gpt"):
        return _call_gpt_brain(
            all_indicators    = filtered_indicators,
            sentiment         = sentiment,
            raw_history       = raw_history,
            capital           = capital,
            risk_amount       = risk_amount,
            reward_amount     = reward_amount,
            pre_sizing        = pre_sizing,
            coin_name         = coin_name,
            coin_symbol       = coin_symbol,
            model             = brain_model,
            learning_override = learning_override,
        )

    return _brain.generate_signal(
        all_indicators    = filtered_indicators,
        sentiment         = sentiment,
        raw_history       = raw_history,
        capital           = capital,
        risk_amount       = risk_amount,
        reward_amount     = reward_amount,
        pre_sizing        = pre_sizing,
        coin_name         = coin_name,
        coin_symbol       = coin_symbol,
        learning_override = learning_override,
    )

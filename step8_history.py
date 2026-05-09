import json
import anthropic
from dotenv import load_dotenv
from trade_store import get_trade_store

load_dotenv()
client = anthropic.Anthropic()


def _unpack_metadata(trade: dict) -> dict:
    """Unpack metadata JSON into top-level keys for backward compat."""
    row = dict(trade)
    try:
        meta = json.loads(row.get("metadata") or "{}")
    except Exception:
        meta = {}
    for key in ("ta_summary", "sentiment_summary", "history_summary",
                "decision_rationale", "overrides", "indicators",
                "tp_adjustments", "tp_adjustment_log"):
        row.setdefault(key, meta.get(key))
    # Map state/outcome to CSV-compatible "outcome" field
    if row.get("state") == "PENDING":
        row["outcome"] = "pending"
    elif row.get("state") == "CLOSED":
        row["outcome"] = row.get("outcome") or "pending"
    elif row.get("state") == "DNE":
        row["outcome"] = "pending"
    return row


def load_history(n=10, coin_name=None, signals_file=None):
    """Load last N trades from SQLite for the given coin."""
    # coin_name preferred; fall back to deriving from signals_file path
    if coin_name is None and signals_file:
        import os
        base = os.path.basename(signals_file or "")
        coin_name = base.replace("_signals.csv", "").upper()
    if not coin_name:
        coin_name = "ETH"
    try:
        store = get_trade_store()
        trades = store.get_recent_trades(coin=coin_name, n=n)
        return [_unpack_metadata(t) for t in trades]
    except Exception as e:
        print(f"[WARN] Could not load history: {e}")
        return []


def summarize_history(history):
    if not history:
        return {"success": True, "data": "No signal history yet — this is the first signal."}
    try:
        history_text = "\n".join(
            f"[{r.get('timestamp','')}] {r.get('signal','')} -> {r.get('outcome','pending')} "
            f"(confidence: {r.get('confidence','')}%)"
            for r in history
        )
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            system="""You are a trading pattern analyst.
Summarize the win/loss pattern from recent signals in ONE sentence.
Focus on what conditions led to wins vs losses.
Output plain text only. Maximum 30 words.""",
            messages=[{"role": "user", "content": f"Recent signal history:\n{history_text}"}]
        )
        return {"success": True, "data": response.content[0].text.strip()}
    except Exception as e:
        print(f"[WARN] History summarization failed: {e}")
        return {"success": True, "data": "Could not summarize history."}

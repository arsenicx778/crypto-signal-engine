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


def format_history_for_brain(history: list) -> str:
    """
    Format the last N closed trades as a compact table for the brain prompt.
    No LLM call — pure formatting. Returns a plain-text string.
    """
    if not history:
        return "(no closed trade history yet)"

    lines = []
    for r in history:
        sig = r.get("signal", "")
        if sig not in ("Buy", "Sell"):
            continue
        outcome = r.get("outcome", "pending")
        if outcome not in ("W", "L"):
            continue
        direction = "LONG" if sig == "Buy" else "SHORT"
        conf = r.get("confidence", "?")
        ts   = str(r.get("timestamp", ""))[:10]

        # Parse indicator snapshot stored in trade metadata
        ind_str = r.get("indicators", "")
        parsed  = {}
        for part in str(ind_str).split("|"):
            if ":" in part:
                k, _, v = part.partition(":")
                try:
                    parsed[k.strip().upper()] = float(v.strip())
                except ValueError:
                    pass

        def _f(key, *aliases):
            val = parsed.get(key)
            for a in aliases:
                if val is None:
                    val = parsed.get(a)
            return f"{val:.1f}" if isinstance(val, float) else "?"

        rsi_s = _f("RSI")
        adx_s = _f("ADX")
        dip_s = _f("DI_PLUS",  "DI+")
        dim_s = _f("DI_MINUS", "DI-")
        lines.append(
            f"  {ts} {direction:5} {outcome}  conf:{conf} "
            f"RSI:{rsi_s} ADX:{adx_s} DI+:{dip_s} DI-:{dim_s}"
        )

    return "\n".join(lines) if lines else "(no closed trade history yet)"


def summarize_history(history):
    """Deprecated — kept for compatibility. Use format_history_for_brain instead."""
    if not history:
        return {"success": True, "data": "No signal history yet."}
    wins   = sum(1 for r in history if r.get("outcome") == "W")
    losses = sum(1 for r in history if r.get("outcome") == "L")
    return {"success": True, "data": f"{wins}W {losses}L in last {len(history)} trades."}

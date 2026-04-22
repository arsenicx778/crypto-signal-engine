import anthropic
from dotenv import load_dotenv
from signal_store import read_latest_signals

load_dotenv()
client = anthropic.Anthropic()

def load_history(n=10, signals_file=None):
    try:
        rows = read_latest_signals(signals_file)
        return rows[-n:] if len(rows) >= n else rows
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

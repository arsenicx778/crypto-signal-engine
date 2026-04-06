import json
import anthropic
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic()

def score_sentiment(headlines):
    if not headlines:
        return {"success": True, "data": {"news_score": 0.0, "headline_count": 0}}
    try:
        headlines_text = "\n".join(f"- {h}" for h in headlines[:20])
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            system="""You are a crypto news sentiment scorer.
Score the overall sentiment of these Ethereum headlines.
Output ONLY valid JSON with no other text:
{
  "news_score": 0.0,
  "headline_count": 0
}
news_score must be a float between -1.0 (very bearish) and +1.0 (very bullish).""",
            messages=[{
                "role": "user",
                "content": f"Score these Ethereum headlines:\n{headlines_text}"
            }]
        )
        raw = response.content[0].text.strip()
        clean = raw.removeprefix("```json").removesuffix("```").strip()
        result = json.loads(clean)
        result["headline_count"] = len(headlines)
        return {"success": True, "data": result}
    except Exception as e:
        print(f"[WARN] Sentiment scoring failed: {e} — using neutral")
        return {"success": True, "data": {"news_score": 0.0, "headline_count": 0}}

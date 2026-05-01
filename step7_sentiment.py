import json
import time
import anthropic
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic()

_CACHE_TTL_SECONDS = 15 * 60  # 15 minutes

# keyed by headlines content hash → {"result": ..., "fetched_at": float, "headlines": list}
# Shared across all coins: same headlines → same cache entry → 1 Haiku call per TTL window
_sentiment_cache: dict = {}


def _headlines_key(headlines: list) -> int:
    """Stable hash of headline title strings. All coins share one news feed so this
    collapses 4 per-coin cache entries into a single shared entry."""
    titles = [h.get("title", "") if isinstance(h, dict) else str(h) for h in headlines]
    return hash(tuple(titles))


def score_sentiment(headlines, coin_name: str = "CRYPTO"):
    if not headlines:
        return {"success": True, "data": {"news_score": 0.0, "headline_count": 0}}

    now = time.monotonic()
    cache_key = _headlines_key(headlines)
    cached = _sentiment_cache.get(cache_key)

    if cached:
        age_seconds = now - cached["fetched_at"]
        if age_seconds < _CACHE_TTL_SECONDS:
            age_minutes = age_seconds / 60
            print(f"[STEP7] sentiment cache hit (shared, {age_minutes:.0f}m old)")
            return cached["result"]
        print(f"[STEP7] sentiment fresh fetch (cache expired {age_seconds/60:.0f}m old)")
    else:
        print(f"[STEP7] sentiment fresh fetch (no cache)")

    try:
        headlines_text = "\n".join(
            f"- {h.get('title', h) if isinstance(h, dict) else h}" for h in headlines[:20]
        )
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
        out = {"success": True, "data": result}
        _sentiment_cache[cache_key] = {
            "result": out,
            "fetched_at": now,
            "headlines": list(headlines),
        }
        return out
    except Exception as e:
        print(f"[WARN] Sentiment scoring failed: {e} — using neutral")
        return {"success": True, "data": {"news_score": 0.0, "headline_count": 0}}

import json
import os
from datetime import datetime

RESEARCH_MEMORY_PATH = os.environ.get("RESEARCH_MEMORY_PATH", "/app/research/research_memory.jsonl")

def save_research_event(topic, query, results, source="google_trends"):
    event = {
        "timestamp": datetime.utcnow().isoformat(),
        "topic": topic,
        "query": query,
        "results": results,
        "source": source,
    }
    os.makedirs(os.path.dirname(RESEARCH_MEMORY_PATH), exist_ok=True)
    with open(RESEARCH_MEMORY_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")

def load_research_memory():
    if not os.path.exists(RESEARCH_MEMORY_PATH):
        return []
    with open(RESEARCH_MEMORY_PATH, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]

# Stub for Google Trends integration (to be implemented)
def fetch_google_trends(topic):
    # TODO: Implement real Google Trends API call or scraping
    # For now, return a dummy result
    return {"topic": topic, "trend_score": 42, "related_queries": [f"{topic} 2026", f"{topic} news"]}
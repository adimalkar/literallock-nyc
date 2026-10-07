"""LiteralLock settings and client factories. Credentials stay in .env only."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
RAW_ROWS_PATH = DATA_DIR / "raw_rows.json"
GROUPS_PATH = DATA_DIR / "groups.json"
SNAPSHOT_PATH = DATA_DIR / "snapshot.json"
SCENARIOS_PATH = DATA_DIR / "scenarios.json"
LESSONS_PATH = DATA_DIR / "lessons.json"

DATASET_ID = "43nn-pn8j"
SOURCE_URL = f"https://data.cityofnewyork.us/resource/{DATASET_ID}.json"
STARTER_REVISION = "AvenueJ/elastic-mistral-hacknight nyc_restaurant_analyst.ipynb blob 0b6687be11ffe0ecef3836dfc193a10d55773805"
PROJECT_OWNER_TAG = "literallock-nyc"

load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    elasticsearch_url: str
    elasticsearch_api_key: str
    elasticsearch_index: str
    mistral_api_key: str
    mistral_chat_model: str
    mistral_embed_model: str
    semantic_backend: str
    inference_id: str
    decision_backend: str
    elastic_timeout_s: float = 30.0
    fetch_timeout_s: float = 30.0
    branch_limit: int = 6


def get_settings() -> Settings:
    index = os.getenv("ELASTICSEARCH_INDEX", "literallock_nyc_inspections")
    if index == "literallock-incidents":  # superseded PAY-incident index name
        index = "literallock_nyc_inspections"
    return Settings(
        elasticsearch_url=os.getenv("ELASTICSEARCH_URL", ""),
        elasticsearch_api_key=os.getenv("ELASTICSEARCH_API_KEY", ""),
        elasticsearch_index=index,
        mistral_api_key=os.getenv("MISTRAL_API_KEY", ""),
        mistral_chat_model=os.getenv("MISTRAL_CHAT_MODEL", "mistral-large-latest"),
        mistral_embed_model=os.getenv("MISTRAL_EMBED_MODEL", "mistral-embed"),
        semantic_backend=os.getenv("SEMANTIC_BACKEND", "semantic_text"),
        inference_id=os.getenv("ELASTIC_INFERENCE_ID", "literallock-embeddings"),
        decision_backend=os.getenv("DECISION_BACKEND", "rules"),
    )


def get_es(settings: Settings | None = None):
    from elasticsearch import Elasticsearch

    s = settings or get_settings()
    if not s.elasticsearch_url or not s.elasticsearch_api_key:
        raise RuntimeError("ELASTICSEARCH_URL and ELASTICSEARCH_API_KEY must be set in .env")
    # Bounded timeout; one transport retry so logical request counts stay meaningful.
    return Elasticsearch(s.elasticsearch_url, api_key=s.elasticsearch_api_key,
                         request_timeout=s.elastic_timeout_s, max_retries=1, retry_on_timeout=False)

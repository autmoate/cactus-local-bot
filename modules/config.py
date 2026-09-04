from dataclasses import dataclass
from os import getenv

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    database_url: str
    cactus_base_url: str
    cactus_model: str
    cactus_auto_start: bool
    confidence_threshold: float
    tool_index_path: str
    embed_model: str
    recall_top_k: int
    behavior: str
    sensors_on: bool
    reminder_lookahead_min: int
    reasoning_level: str
    searxng_url: str
    web_enabled: bool
    timezone: str
    trigger: str
    proactive_gemma: bool
    embed_dim: int
    msg_retention_hours: int


def load_config() -> Config:
    load_dotenv()
    return Config(
        database_url=getenv("DATABASE_URL", "postgresql://cactus:cactus@localhost:5432/cactus"),
        cactus_base_url=getenv("CACTUS_BASE_URL", "http://127.0.0.1:8080/v1"),
        cactus_model=getenv("CACTUS_MODEL", "./models/gemma-4-e2b"),
        cactus_auto_start=getenv("CACTUS_AUTO_START", "0") == "1",
        confidence_threshold=float(getenv("NEEDLE_CONFIDENCE_THRESHOLD", "0.75")),
        tool_index_path=getenv("NEEDLE_TOOL_INDEX", ".cache/needle-tool-index"),
        embed_model=getenv("EMBED_MODEL", ""),
        recall_top_k=int(getenv("RECALL_TOP_K", "5")),
        behavior=getenv("BEHAVIOR", "terse"),
        sensors_on=getenv("SENSORS_ON", "1") == "1",
        reminder_lookahead_min=int(getenv("REMINDER_LOOKAHEAD_MIN", "90")),
        reasoning_level=getenv("REASONING_LEVEL", "none"),
        searxng_url=getenv("SEARXNG_URL", "http://127.0.0.1:8888"),
        web_enabled=getenv("WEB_ENABLED", "0") == "1",
        timezone=getenv("TZ_LOCAL", "Europe/Berlin"),
        trigger=getenv("TRIGGER", "@"),
        proactive_gemma=getenv("PROACTIVE_GEMMA", "1") == "1",
        embed_dim=int(getenv("EMBED_DIM", "1536")),
        msg_retention_hours=int(getenv("MSG_RETENTION_HOURS", "24")),
    )

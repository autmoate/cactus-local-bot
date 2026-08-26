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


def load_config() -> Config:
    load_dotenv()
    return Config(
        database_url=getenv("DATABASE_URL", "postgresql://cactus:cactus@localhost:5432/cactus"),
        cactus_base_url=getenv("CACTUS_BASE_URL", "http://127.0.0.1:8080/v1"),
        cactus_model=getenv("CACTUS_MODEL", "./models/gemma-4-e2b"),
        cactus_auto_start=getenv("CACTUS_AUTO_START", "0") == "1",
        confidence_threshold=float(getenv("NEEDLE_CONFIDENCE_THRESHOLD", "0.75")),
        tool_index_path=getenv("NEEDLE_TOOL_INDEX", ".cache/needle-tool-index"),
    )

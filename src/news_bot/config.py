"""Cấu hình tập trung: env (pydantic-settings) + danh mục nguồn (YAML)."""
from __future__ import annotations

import functools
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # Delivery
    google_chat_webhook_url: str = ""
    google_chat_thread_per_day: bool = True

    # LLM
    openai_api_key: str = ""
    # Tom tat: 24 lan goi/ngay -> chon bac gia trung binh.
    summarizer_model: str = "gpt-5.6-terra"
    # Bien tap: 1 lan goi/ngay, can chat luong nhat.
    editor_model: str = "gpt-6-astra"
    llm_max_concurrency: int = 4

    # Storage
    postgres_dsn: str = "postgresql+psycopg://news:news@localhost:5432/news"
    db_schema: str = "news"

    # Pipeline
    news_timezone: str = "Asia/Ho_Chi_Minh"
    lookback_hours: int = 24
    max_articles_per_source: int = 40
    digest_size: int = 12
    fulltext_max_articles: int = 30
    http_concurrency: int = 8
    user_agent: str = (
        "updating-news-bot/0.1 (+internal daily digest; contact: infra@example.com)"
    )

    # Ops
    log_level: str = "INFO"
    log_json: bool = True
    dry_run: bool = False
    sources_file: Path = REPO_ROOT / "config" / "sources.yaml"


class Source(BaseModel):
    id: str
    publisher: str
    url: str
    category: str | None = None
    strategy: str = "rss"          # rss | sitemap
    fulltext: bool = True
    enabled: bool = True
    weight: float = 1.0
    timeout_s: int = 20
    max_items: int | None = None
    headers: dict[str, str] = Field(default_factory=dict)


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def load_sources(path: Path | str | None = None) -> list[Source]:
    """Đọc sources.yaml, merge `defaults` vào từng entry, bỏ entry disabled."""
    path = Path(path or get_settings().sources_file)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    defaults = data.get("defaults", {}) or {}
    out: list[Source] = []
    for raw in data.get("sources", []) or []:
        merged = {**defaults, **raw}
        src = Source(**merged)
        if src.enabled:
            out.append(src)
    if not out:
        raise ValueError(f"Không có nguồn nào enabled trong {path}")
    return out

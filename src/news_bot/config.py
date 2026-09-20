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
    # false = moi ban tin la mot message rieng o cuoi space. Gom vao thread
    # thi cac message sau nam an trong thread va de bi bo qua - chi tien khi
    # thu nghiem. Mac dinh cho chay that la false.
    google_chat_thread_per_day: bool = False

    # LLM
    # openai | vertex. Vertex dung ADC (gcloud auth application-default login
    # hoac GOOGLE_APPLICATION_CREDENTIALS tro toi key cua service account).
    # Thu tu uu tien, cach nhau dau phay. Provider dau chuoi hong kieu he
    # thong thi tu dong roi xuong cai tiep theo trong cung tien trinh.
    #   gemini = Developer API qua API key. Key khong het han.
    #   vertex = Vertex AI qua ADC. ADC cua tai khoan nguoi dung SE het han.
    #   openai = du phong cuoi.
    llm_providers: str = "gemini,vertex,openai"
    openai_api_key: str = ""
    gemini_api_key: str = ""
    vertex_project: str = ""
    vertex_location: str = "global"
    # -1 = de model tu quyet; 0 = tat han suy luan. Ho Gemini Flash suy luan mac
    # dinh va tieu vai tram token cho mot ban tom tat 200 token.
    vertex_thinking_budget: int = -1
    # Chi ghi de model cho provider DAU chuoi. De trong = dung mac dinh cua
    # tung provider trong llm.PROVIDER_MODELS.
    summarizer_model: str = ""
    editor_model: str = ""
    llm_max_concurrency: int = 4

    # Storage
    postgres_dsn: str = "postgresql+psycopg://news:news@localhost:5432/news"
    db_schema: str = "news"

    # Pipeline
    news_timezone: str = "Asia/Ho_Chi_Minh"
    lookback_hours: int = 24
    max_articles_per_source: int = 40
    # So tin toi da trong tung ban tin. 0 = dung mac dinh cua groups.py
    # (serious 12 + life 6 = 18 tin/ngay).
    digest_size_serious: int = 0
    digest_size_life: int = 0
    # Tran cung cho tong so tin ca ngay, tinh gop moi nhom. Day la rang buoc
    # nguoi dung dat ra (15-20 tin/ngay), khong phai so ky thuat -> chan o buoc
    # render de cau hinh sai trong .env khong the vuot qua.
    digest_total_cap: int = 20
    fulltext_max_articles: int = 30
    http_concurrency: int = 8
    user_agent: str = (
        "updating-news-bot/0.1 (+internal daily digest; contact: infra@example.com)"
    )

    # Thoi tiet (Open-Meteo: mien phi, khong can API key)
    weather_enabled: bool = True
    weather_latitude: float = 21.0278       # Ha Noi
    weather_longitude: float = 105.8342
    weather_place: str = "Hà Nội"

    # Giu du lieu bao nhieu ngay. Qua moc nay, pipeline_run bi xoa va CASCADE
    # keo theo toan bo trace; article khong con ban tin nao tro toi cung bi xoa.
    retention_days: int = 14

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
    group: str = "serious"          # serious | life - xem groups.py
    strategy: str = "rss"          # rss | hf_papers | github_trending
    fulltext: bool = True
    enabled: bool = True
    weight: float = 1.0
    timeout_s: int = 20
    max_items: int | None = None
    # Cua so thoi gian rieng cho nguon nay. None = dung LOOKBACK_HOURS chung.
    # Cac nguon xuat ban thua (FED, blog hang AI, paper) can cua so rong hon,
    # neu khong chung se khong bao gio lot vao ban tin.
    lookback_hours: int | None = None
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

"""Domain models dùng xuyên suốt graph. Tách khỏi SQLAlchemy để node dễ test."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class RawItem(BaseModel):
    """Một entry thô từ RSS/sitemap, chưa fetch fulltext."""
    source_id: str
    publisher: str
    category: str | None = None
    weight: float = 1.0
    title: str
    url: str
    lead: str | None = None
    published_at: datetime | None = None
    image_url: str | None = None
    raw: dict = Field(default_factory=dict)


class Article(BaseModel):
    """Bài đã chuẩn hoá (đã canonical URL, có thể đã có fulltext)."""
    id: int | None = None
    source_id: str
    publisher: str
    category: str | None = None
    weight: float = 1.0
    title: str
    url_canonical: str
    url_original: str
    lead: str | None = None
    body: str | None = None
    author: str | None = None
    lang: str = "vi"
    published_at: datetime | None = None
    image_url: str | None = None
    content_hash: str = ""
    simhash: int = 0
    raw: dict = Field(default_factory=dict)

    @property
    def text_for_llm(self) -> str:
        parts = [self.title]
        if self.lead:
            parts.append(self.lead)
        if self.body:
            parts.append(self.body[:6000])
        return "\n\n".join(p for p in parts if p)


class Summary(BaseModel):
    article_id: int | None = None
    url_canonical: str
    headline: str
    summary: str
    bullets: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    importance: int = 3          # 1..5
    model: str = ""


class DigestItem(BaseModel):
    rank: int
    headline: str
    summary: str
    url: str
    publisher: str
    topics: list[str] = Field(default_factory=list)
    importance: int = 3
    also_at: list[str] = Field(default_factory=list)   # các báo khác cùng đưa tin


class Digest(BaseModel):
    digest_date: str
    headline: str
    overview: str
    items: list[DigestItem] = Field(default_factory=list)
    stats: dict = Field(default_factory=dict)

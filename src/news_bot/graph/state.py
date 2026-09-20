from __future__ import annotations

from typing import Annotated, TypedDict

from ..models import Article, Digest, Summary


def _merge_metrics(left: dict, right: dict) -> dict:
    return {**(left or {}), **(right or {})}


def _append(left: list, right: list) -> list:
    return [*(left or []), *(right or [])]


class GraphState(TypedDict, total=False):
    """State cua pipeline. Cac key dung reducer de node co the chay song song
    ma khong ghi de len nhau (LangGraph merge theo Annotated)."""

    run_id: str
    logical_date: str

    articles: list[Article]      # sau khi load tu DB
    clusters: list[list[Article]]  # nhom bai cung su kien
    shortlist: list[Article]     # da rank, chuan bi goi LLM
    summaries: list[Summary]
    digest: Digest | None
    payload: dict                # body gui Google Chat
    digest_id: int | None

    metrics: Annotated[dict, _merge_metrics]
    errors: Annotated[list[str], _append]
    skip_reason: str | None

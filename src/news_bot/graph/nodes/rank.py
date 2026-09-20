"""Node 3: cham diem heuristic de chon shortlist truoc khi tieu tien LLM.

Chu y: rank bang heuristic (khong goi LLM) la co y. Goi LLM de xep hang 300 bai
moi ngay vua dat vua cham; heuristic loc con ~2x digest_size roi moi de LLM
lam viec no gioi hon la tom tat + bien tap.
"""
from __future__ import annotations

from datetime import UTC, datetime

from langchain_core.runnables import RunnableConfig

from ...config import get_settings
from ...logging_setup import get_logger
from ...models import Article
from ...tracing.store import span
from ..state import GraphState

log = get_logger(__name__)

# Chu de uu tien cho mot team ky thuat/kinh doanh. Sua cho hop team ban.
BOOST_KEYWORDS = {
    "ai": 2.0, "trí tuệ nhân tạo": 2.0, "chatgpt": 1.5, "chip": 1.2,
    "công nghệ": 1.0, "chuyển đổi số": 1.2, "dữ liệu": 0.8,
    "lãi suất": 1.2, "tỷ giá": 1.0, "chứng khoán": 0.8, "gdp": 1.0,
    "xuất khẩu": 0.8, "thuế": 0.8, "startup": 1.2, "đầu tư": 0.6,
}


def _recency_score(published_at: datetime | None, now: datetime) -> float:
    if published_at is None:
        return 0.3
    hours = max((now - published_at.astimezone(UTC)).total_seconds() / 3600, 0)
    # 1.0 khi vua dang, ~0.5 sau 12h, ~0.25 sau 24h
    return 1.0 / (1.0 + hours / 12.0)


def _keyword_score(article: Article) -> float:
    haystack = f"{article.title} {article.lead or ''}".lower()
    return sum(w for kw, w in BOOST_KEYWORDS.items() if kw in haystack)


def score_article(article: Article, cluster_size: int, now: datetime) -> float:
    """Diem = do moi x trong so nguon + do phu song nhieu bao + tu khoa uu tien."""
    recency = _recency_score(article.published_at, now) * 3.0
    corroboration = min(cluster_size - 1, 4) * 1.2   # nhieu bao cung dua => tin quan trong
    depth = 0.5 if (article.body and len(article.body) > 800) else 0.0
    return recency * article.weight + corroboration + _keyword_score(article) + depth


def rank_clusters(state: GraphState, config: RunnableConfig) -> GraphState:
    store = (config or {}).get("configurable", {}).get("trace_store")
    s = get_settings()
    clusters = state.get("clusters") or []
    now = datetime.now(UTC)

    with span(store, "rank_clusters", attributes={"n_clusters": len(clusters)}) as sp:
        scored = []
        for group in clusters:
            head = group[0]
            head.raw = {
                **head.raw,
                "cluster_size": len(group),
                "also_at": sorted({a.publisher for a in group[1:]}),
            }
            scored.append((score_article(head, len(group), now), head))
        scored.sort(key=lambda x: x[0], reverse=True)

        # lay gap doi digest_size de LLM con cho lua chon o buoc compose
        shortlist = [a for _, a in scored[: s.digest_size * 2]]
        for rank_idx, (score, art) in enumerate(scored[: s.digest_size * 2], start=1):
            art.raw = {**art.raw, "heuristic_score": round(score, 3), "heuristic_rank": rank_idx}

        sp["output"] = {
            "shortlist": len(shortlist),
            "top": [(round(sc, 2), a.title[:60]) for sc, a in scored[:5]],
        }
        log.info("rank.done", run_id=state.get("run_id"), shortlist=len(shortlist))
        return {"shortlist": shortlist, "metrics": {"shortlist": len(shortlist)}}

"""Node 3: cham diem heuristic de chon shortlist truoc khi tieu tien LLM.

Chu y: rank bang heuristic (khong goi LLM) la co y. Goi LLM de xep hang 500 bai
moi ngay vua dat vua cham; heuristic loc con ~2x so tin cua tung nhom roi moi de
LLM lam viec no gioi hon la tom tat + bien tap.

Shortlist duoc cat RIENG cho tung nhom. Neu cat chung mot lan roi moi chia nhom
thi nhom `life` se bi nhom `serious` at het cho - tin FED hay paper AI gan nhu
luon ghi diem cao hon tin ra mat xe may.
"""
from __future__ import annotations

from datetime import UTC, datetime

from langchain_core.runnables import RunnableConfig

from ... import groups
from ...config import get_settings
from ...logging_setup import get_logger
from ...models import Article
from ...tracing.store import span
from ..state import GraphState

log = get_logger(__name__)

# Cho chinh "khau vi" ban tin.
BOOST_KEYWORDS: dict[str, dict[str, float]] = {
    "serious": {
        "ai": 1.5, "trí tuệ nhân tạo": 1.8, "llm": 2.0, "mô hình ngôn ngữ": 2.0,
        "transformer": 1.8, "benchmark": 1.2, "open source": 1.0, "paper": 1.2,
        "gpt": 1.5, "gemini": 1.5, "claude": 1.5, "llama": 1.3, "grok": 1.2,
        "reasoning": 1.2, "agent": 1.0, "inference": 1.0, "fine-tun": 1.0,
        "chip": 1.2, "gpu": 1.3, "nvidia": 1.3, "bán dẫn": 1.2,
        "fed": 2.5, "federal reserve": 2.5, "fomc": 2.5, "lãi suất": 2.0,
        "interest rate": 2.0, "inflation": 1.8, "lạm phát": 1.8,
        "monetary policy": 2.0, "chính sách tiền tệ": 2.0, "ecb": 1.5,
        "blackrock": 1.8, "vanguard": 1.5, "hedge fund": 1.5, "quỹ đầu tư": 1.5,
        "etf": 1.3, "ipo": 1.3, "chứng khoán": 1.2, "vn-index": 1.5,
        "s&p 500": 1.3, "nasdaq": 1.3, "tỷ giá": 1.2, "trái phiếu": 1.2,
        "nâng hạng": 1.5, "ftse": 1.3, "msci": 1.3,
        "sanction": 1.2, "trừng phạt": 1.2, "thuế quan": 1.5, "tariff": 1.5,
        "thượng đỉnh": 1.2, "hiệp định": 1.0,
    },
    "life": {
        "ra mắt": 1.5, "mẫu xe": 1.5, "ô tô": 1.2, "xe máy": 1.2,
        "giá xe": 1.5, "đánh giá xe": 1.5, "xe điện": 1.3, "vinfast": 1.2,
        "honda": 1.0, "toyota": 1.0, "yamaha": 1.0,
        # Hai mon duoc quan tam nhat -> diem cao hon phan con lai cua the thao
        "bóng chuyền": 2.5, "bóng đá": 1.8, "tuyển việt nam": 1.8,
        "v-league": 1.5, "ngoại hạng anh": 1.3, "champions league": 1.3,
        "world cup": 1.5, "sea games": 1.3, "vtv cup": 2.0,
        "du lịch": 1.2, "điểm đến": 1.2, "vé máy bay": 1.3, "tour": 0.8,
    },
}

# Tru diem, khong phai loai bo han.
PENALTY_KEYWORDS: dict[str, dict[str, float]] = {
    "life": {"sao việt": 2.0, "lộ ảnh": 2.0, "gây sốt": 1.5, "đại gia": 1.5},
    "serious": {},
}


def _recency_score(published_at: datetime | None, now: datetime) -> float:
    if published_at is None:
        return 0.3
    hours = max((now - published_at.astimezone(UTC)).total_seconds() / 3600, 0)
    # 1.0 khi vua dang, ~0.5 sau 12h, ~0.25 sau 24h
    return 1.0 / (1.0 + hours / 12.0)


def _keyword_score(article: Article) -> float:
    haystack = f"{article.title} {article.lead or ''}".lower()
    boost = sum(w for kw, w in BOOST_KEYWORDS.get(article.group, {}).items()
                if kw in haystack)
    penalty = sum(w for kw, w in PENALTY_KEYWORDS.get(article.group, {}).items()
                  if kw in haystack)
    return boost - penalty


def score_article(article: Article, cluster_size: int, now: datetime) -> float:
    """Diem = do moi x trong so nguon + do phu song nhieu bao + tu khoa uu tien."""
    recency = _recency_score(article.published_at, now) * 3.0
    corroboration = min(cluster_size - 1, 4) * 1.2   # nhieu bao cung dua => tin quan trong
    depth = 0.5 if (article.body and len(article.body) > 800) else 0.0
    return recency * article.weight + corroboration + _keyword_score(article) + depth


def group_size(group: groups.Group) -> int:
    """Kich thuoc ban tin: uu tien cau hinh env, khong co thi lay mac dinh cua nhom."""
    s = get_settings()
    override = getattr(s, f"digest_size_{group.key}", 0)
    return override or group.default_size


def rank_clusters(state: GraphState, config: RunnableConfig) -> GraphState:
    store = (config or {}).get("configurable", {}).get("trace_store")
    clusters = state.get("clusters") or []
    now = datetime.now(UTC)

    with span(store, "rank_clusters", attributes={"n_clusters": len(clusters)}) as sp:
        by_group: dict[str, list[tuple[float, Article]]] = {}
        for cluster in clusters:
            head = cluster[0]
            head.raw = {
                **head.raw,
                "cluster_size": len(cluster),
                "also_at": sorted({a.publisher for a in cluster[1:]}),
            }
            score = score_article(head, len(cluster), now)
            by_group.setdefault(head.group, []).append((score, head))

        shortlist: list[Article] = []
        per_group: dict[str, int] = {}
        for group in groups.NEWS_GROUPS:
            ranked = sorted(by_group.get(group.key, []), key=lambda x: x[0], reverse=True)
            # gap doi so tin de LLM con cho lua chon o buoc bien tap
            keep = ranked[: group_size(group) * 2]
            for idx, (score, art) in enumerate(keep, start=1):
                art.raw = {**art.raw,
                           "heuristic_score": round(score, 3),
                           "heuristic_rank": idx}
            shortlist.extend(a for _, a in keep)
            per_group[group.key] = len(keep)

        sp["output"] = {
            "per_group": per_group,
            "top_serious": [(round(sc, 2), a.title[:55])
                            for sc, a in sorted(by_group.get("serious", []),
                                                key=lambda x: -x[0])[:3]],
            "top_life": [(round(sc, 2), a.title[:55])
                         for sc, a in sorted(by_group.get("life", []),
                                             key=lambda x: -x[0])[:3]],
        }
        log.info("rank.done", run_id=state.get("run_id"), shortlist=len(shortlist),
                 per_group=per_group)
        return {
            "shortlist": shortlist,
            "metrics": {"shortlist": len(shortlist), "shortlist_per_group": per_group},
        }

"""Node 2: gom bai trung/cung su kien thanh cluster.

Hai tang:
  - URL canonical: da xu ly o tang DB (UNIQUE), o day chi phong ho.
  - SimHash hamming <= threshold: bat truong hop 5 bao cung dua 1 tin.

Cluster giu lai "bai dai dien" (bai co body day du nhat, uu tien nguon weight cao),
cac bai con lai tro thanh `also_at` de hien thi "cung dua tin".
"""
from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from ...logging_setup import get_logger
from ...models import Article
from ...tracing.store import span
from ...utils import hamming, normalize_text
from ..state import GraphState

log = get_logger(__name__)

SIMHASH_THRESHOLD = 12   # tren 64 bit; <=12 bit lech ~ cung su kien
TITLE_JACCARD = 0.55


def _title_similar(a: str, b: str) -> bool:
    ta, tb = set(normalize_text(a).split()), set(normalize_text(b).split())
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= TITLE_JACCARD


def _representative(group: list[Article]) -> Article:
    return max(group, key=lambda a: (a.weight, len(a.body or ""), len(a.lead or "")))


def cluster_articles(state: GraphState, config: RunnableConfig) -> GraphState:
    store = (config or {}).get("configurable", {}).get("trace_store")
    articles: list[Article] = state.get("articles") or []

    with span(store, "cluster_articles", attributes={"n_input": len(articles)}) as sp:
        clusters: list[list[Article]] = []
        for art in articles:
            placed = False
            for group in clusters:
                head = group[0]
                near = head.simhash and art.simhash and (
                    hamming(head.simhash, art.simhash) <= SIMHASH_THRESHOLD
                )
                if near or _title_similar(head.title, art.title):
                    group.append(art)
                    placed = True
                    break
            if not placed:
                clusters.append([art])

        # dua ban dai dien len dau moi cluster
        ordered = []
        for group in clusters:
            head = _representative(group)
            ordered.append([head, *(a for a in group if a is not head)])
        clusters = ordered
        clusters.sort(key=len, reverse=True)

        dupes = len(articles) - len(clusters)
        sp["output"] = {"clusters": len(clusters), "duplicates_merged": dupes}
        log.info("dedupe.done", run_id=state.get("run_id"), **sp["output"])
        return {
            "clusters": clusters,
            "metrics": {"clusters": len(clusters), "duplicates_merged": dupes},
        }

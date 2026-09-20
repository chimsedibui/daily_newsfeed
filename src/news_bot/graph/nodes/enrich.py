"""Node 4: fetch fulltext cho rieng shortlist (khong fetch ca vai tram bai).

Quan trong: `fulltext_max_articles` gioi han SO BAI DUOC FETCH, khong phai do dai
shortlist. Cat thang shortlist se xen dung nhom `life` - vi shortlist duoc xep
serious truoc, life sau - va nhom do se im lang ma khong ai biet.
"""
from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from ...config import get_settings
from ...db import repository as repo
from ...logging_setup import get_logger
from ...sources import enrich_fulltext
from ...tracing.store import span
from ..state import GraphState

log = get_logger(__name__)


def enrich_shortlist(state: GraphState, config: RunnableConfig) -> GraphState:
    store = (config or {}).get("configurable", {}).get("trace_store")
    s = get_settings()
    shortlist = state.get("shortlist") or []

    with span(store, "enrich_shortlist", attributes={"n": len(shortlist)}) as sp:
        # Chia deu han muc fetch cho tung nhom de nhom nho khong bi doi hut.
        by_group: dict[str, list] = {}
        for art in shortlist:
            by_group.setdefault(art.group, []).append(art)
        quota = max(s.fulltext_max_articles // max(len(by_group), 1), 1)

        need = []
        for arts in by_group.values():
            need.extend([a for a in arts if not a.body][:quota])

        enriched = enrich_fulltext(need) if need else []
        by_url = {a.url_canonical: a for a in enriched}
        merged = [by_url.get(a.url_canonical, a) for a in shortlist]

        if merged:
            repo.upsert_articles(merged)   # ghi lai body + hash moi

        with_body = sum(1 for a in merged if a.body)
        sp["output"] = {"fetched": len(need), "with_body": with_body,
                        "quota_per_group": quota, "shortlist": len(merged)}
        log.info("enrich.done", run_id=state.get("run_id"), **sp["output"])
        return {
            "shortlist": merged,
            "metrics": {"fulltext_fetched": len(need), "fulltext_ok": with_body},
        }

"""Node 4: fetch fulltext cho rieng shortlist (khong fetch ca 300 bai)."""
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
    shortlist = (state.get("shortlist") or [])[: s.fulltext_max_articles]

    with span(store, "enrich_shortlist", attributes={"n": len(shortlist)}) as sp:
        need = [a for a in shortlist if not a.body]
        enriched = enrich_fulltext(need) if need else []
        by_url = {a.url_canonical: a for a in enriched}
        merged = [by_url.get(a.url_canonical, a) for a in shortlist]

        if merged:
            repo.upsert_articles(merged)   # ghi lai body + hash moi

        with_body = sum(1 for a in merged if a.body)
        sp["output"] = {"fetched": len(need), "with_body": with_body}
        log.info("enrich.done", run_id=state.get("run_id"), **sp["output"])
        return {
            "shortlist": merged,
            "metrics": {"fulltext_fetched": len(need), "fulltext_ok": with_body},
        }

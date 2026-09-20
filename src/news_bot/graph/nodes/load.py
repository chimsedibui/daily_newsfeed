"""Node 1: nap bai trong cua so thoi gian tu Postgres."""
from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from ...config import get_settings
from ...db import repository as repo
from ...logging_setup import get_logger
from ...tracing.store import span
from ..state import GraphState

log = get_logger(__name__)


def load_articles(state: GraphState, config: RunnableConfig) -> GraphState:
    store = (config or {}).get("configurable", {}).get("trace_store")
    s = get_settings()
    with span(store, "load_articles", attributes={"lookback_hours": s.lookback_hours}) as sp:
        articles = repo.fetch_window(s.lookback_hours)
        seen = repo.already_sent_urls(days=7)
        fresh = [a for a in articles if a.url_canonical not in seen]
        sp["output"] = {"loaded": len(articles), "after_seen_filter": len(fresh)}
        log.info("load.done", run_id=state.get("run_id"), **sp["output"])
        return {
            "articles": fresh,
            "metrics": {"articles_loaded": len(articles), "articles_fresh": len(fresh)},
            "skip_reason": None if fresh else "khong co bai moi trong cua so thoi gian",
        }

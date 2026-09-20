"""Node 1: nap bai trong cua so thoi gian tu Postgres."""
from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from ...config import get_settings, load_sources
from ...db import repository as repo
from ...logging_setup import get_logger
from ...tracing.store import span
from ..state import GraphState

log = get_logger(__name__)


def load_articles(state: GraphState, config: RunnableConfig) -> GraphState:
    store = (config or {}).get("configurable", {}).get("trace_store")
    s = get_settings()
    # Cua so doc phai rong bang nguon rong nhat, neu khong bai vua ingest tu
    # nguon xuat ban thua se bi loc ngay o buoc nay.
    window = max(
        [s.lookback_hours] + [x.lookback_hours or 0 for x in load_sources()]
    )
    with span(store, "load_articles", attributes={"lookback_hours": window}) as sp:
        articles = repo.fetch_window(window, limit=1500)
        seen = repo.already_sent_urls(days=7)
        fresh = [a for a in articles if a.url_canonical not in seen]
        sp["output"] = {"loaded": len(articles), "after_seen_filter": len(fresh)}
        log.info("load.done", run_id=state.get("run_id"), **sp["output"])
        return {
            "articles": fresh,
            "metrics": {"articles_loaded": len(articles), "articles_fresh": len(fresh)},
            "skip_reason": None if fresh else "khong co bai moi trong cua so thoi gian",
        }

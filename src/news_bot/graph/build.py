"""Lap rap LangGraph.

    load -> cluster -> rank -> enrich -> summarize -> compose -> render -> END
      |                                     |
      +---- (khong co bai) ---> END         +---- (khong tom tat duoc) ---> END

Moi node deu la ham thuan `(state, config) -> partial state`, nhan TraceStore
qua `config["configurable"]["trace_store"]`. Nho vay node test duoc doc lap
(truyen config={} la tat trace).
"""
from __future__ import annotations

from datetime import date

from langgraph.graph import END, StateGraph

from ..config import get_settings
from ..logging_setup import get_logger
from ..tracing.store import TraceStore
from ..utils import today_in
from .nodes.compose import compose_digests
from .nodes.dedupe import cluster_articles
from .nodes.enrich import enrich_shortlist
from .nodes.load import load_articles
from .nodes.rank import rank_clusters
from .nodes.render import render_payloads
from .nodes.summarize import summarize_articles
from .state import GraphState

log = get_logger(__name__)


def _has_articles(state: GraphState) -> str:
    return "stop" if state.get("skip_reason") else "continue"


def _has_summaries(state: GraphState) -> str:
    return "stop" if not state.get("summaries") else "continue"


def build_graph(checkpointer=None):
    """Tra ve graph da compile. checkpointer=None -> khong luu state trung gian."""
    g = StateGraph(GraphState)
    g.add_node("load", load_articles)
    g.add_node("cluster", cluster_articles)
    g.add_node("rank", rank_clusters)
    g.add_node("enrich", enrich_shortlist)
    g.add_node("summarize", summarize_articles)
    g.add_node("compose", compose_digests)
    g.add_node("render", render_payloads)

    g.set_entry_point("load")
    g.add_conditional_edges("load", _has_articles, {"continue": "cluster", "stop": END})
    g.add_edge("cluster", "rank")
    g.add_edge("rank", "enrich")
    g.add_edge("enrich", "summarize")
    g.add_conditional_edges(
        "summarize", _has_summaries, {"continue": "compose", "stop": END}
    )
    g.add_edge("compose", "render")
    g.add_edge("render", END)
    return g.compile(checkpointer=checkpointer)


def get_checkpointer():
    """Postgres checkpointer neu co cai `langgraph-checkpoint-postgres`, khong thi None.

    Checkpointer cho phep resume graph giua chung - huu ich khi task Airflow bi
    kill sau buoc summarize (buoc dat tien nhat).
    """
    try:
        from langgraph.checkpoint.postgres import PostgresSaver
    except ImportError:
        log.info("checkpointer.disabled", reason="langgraph-checkpoint-postgres chua cai")
        return None
    s = get_settings()
    dsn = s.postgres_dsn.replace("postgresql+psycopg://", "postgresql://")
    saver = PostgresSaver.from_conn_string(dsn).__enter__()
    saver.setup()
    return saver


def run_digest_graph(
    run_id: str,
    logical_date: date | None = None,
    use_checkpointer: bool = False,
) -> dict:
    """Chay graph cho mot run da ton tai (pipeline_run duoc tao truoc do)."""
    store = TraceStore(run_id)
    store.attach()
    checkpointer = get_checkpointer() if use_checkpointer else None
    graph = build_graph(checkpointer)

    initial: GraphState = {
        "run_id": run_id,
        "logical_date": (logical_date or today_in(get_settings().news_timezone)).isoformat(),
        "metrics": {},
        "errors": [],
    }
    config = {
        "configurable": {"trace_store": store, "thread_id": run_id},
        "recursion_limit": 30,
    }
    final = graph.invoke(initial, config=config)
    log.info(
        "graph.done",
        run_id=run_id,
        metrics=final.get("metrics"),
        skip_reason=final.get("skip_reason"),
    )
    return final

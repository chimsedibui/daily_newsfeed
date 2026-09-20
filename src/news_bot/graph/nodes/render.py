"""Node 7: render payload Google Chat va luu ban tin o trang thai `pending`.

Tach render khoi gui la co chu dich: Airflow se gui o mot task rieng, nen neu
webhook loi ta retry duoc ma khong phai goi lai LLM.
"""
from __future__ import annotations

from datetime import date

from langchain_core.runnables import RunnableConfig

from ...db import repository as repo
from ...delivery import build_card_message
from ...logging_setup import get_logger
from ...tracing.store import span
from ..state import GraphState

log = get_logger(__name__)


def render_payload(state: GraphState, config: RunnableConfig) -> GraphState:
    store = (config or {}).get("configurable", {}).get("trace_store")
    digest = state.get("digest")

    with span(store, "render_payload") as sp:
        if digest is None or not digest.items:
            sp["output"] = {"skipped": True}
            return {"payload": {}, "digest_id": None}

        payload = build_card_message(digest)

        # DigestItem.url la url_original; article store khoa theo url_canonical
        # -> map ca hai de khong rot id.
        url_to_id: dict[str, int] = {}
        for a in state.get("shortlist") or []:
            if a.id is not None:
                url_to_id[a.url_canonical] = a.id
                url_to_id[a.url_original] = a.id
        article_ids = [url_to_id[i.url] for i in digest.items if i.url in url_to_id]

        digest_id = None
        if state.get("run_id"):
            digest_id = repo.save_digest(
                run_id=state["run_id"],
                digest_date=date.fromisoformat(digest.digest_date),
                headline=digest.headline,
                overview=digest.overview,
                payload=payload,
                article_ids=article_ids,
                status="pending",
            )

        sp["output"] = {"digest_id": digest_id, "items": len(digest.items)}
        log.info("render.done", run_id=state.get("run_id"), digest_id=digest_id)
        return {"payload": payload, "digest_id": digest_id,
                "metrics": {"digest_id": digest_id}}

"""Node 7: render payload Google Chat cho tung nhom va luu o trang thai `pending`.

Tach render khoi gui la co chu dich: Airflow gui o mot task rieng, nen webhook
loi thi retry duoc ma khong phai goi lai LLM.
"""
from __future__ import annotations

from datetime import date

from langchain_core.runnables import RunnableConfig

from ... import groups
from ...config import get_settings
from ...db import repository as repo
from ...delivery import build_message
from ...logging_setup import get_logger
from ...models import Article, Digest
from ...tracing.store import span
from ..state import GraphState

log = get_logger(__name__)


def _apply_total_cap(digests: list[Digest], cap: int) -> list[Digest]:
    """Chan tong so tin ca ngay.

    Cat tu nhom co `order` lon xuong (life truoc, serious sau) de tin nghiem tuc
    duoc giu lai khi phai hy sinh.
    """
    total = sum(len(d.items) for d in digests)
    if total <= cap:
        return digests

    over = total - cap
    for digest in sorted(digests, key=lambda d: -groups.get(d.group).order):
        if over <= 0:
            break
        drop = min(over, len(digest.items))
        if drop:
            digest.items = digest.items[: len(digest.items) - drop]
            for i, item in enumerate(digest.items, start=1):
                item.rank = i
            over -= drop
    return [d for d in digests if d.items]


def _article_ids(digest: Digest, shortlist: list[Article]) -> list[int]:
    # DigestItem.url la url_original; article store khoa theo url_canonical
    # -> map ca hai de khong rot id.
    url_to_id: dict[str, int] = {}
    for a in shortlist:
        if a.id is not None:
            url_to_id[a.url_canonical] = a.id
            url_to_id[a.url_original] = a.id
    return [url_to_id[i.url] for i in digest.items if i.url in url_to_id]


def render_payloads(state: GraphState, config: RunnableConfig) -> GraphState:
    store = (config or {}).get("configurable", {}).get("trace_store")
    s = get_settings()
    digests: list[Digest] = state.get("digests") or []
    shortlist = state.get("shortlist") or []

    with span(store, "render_payloads", attributes={"n_groups": len(digests)}) as sp:
        if not digests:
            sp["output"] = {"skipped": True}
            return {"digest_ids": []}

        digests = _apply_total_cap(digests, s.digest_total_cap)

        digest_ids: list[int] = []
        rendered: list[tuple[str, int, int]] = []
        for digest in sorted(digests, key=lambda d: groups.get(d.group).order):
            payload = build_message(digest)
            digest_id = None
            if state.get("run_id"):
                digest_id = repo.save_digest(
                    run_id=state["run_id"],
                    group=digest.group,
                    digest_date=date.fromisoformat(digest.digest_date),
                    headline=digest.headline,
                    overview=digest.overview,
                    payload=payload,
                    article_ids=_article_ids(digest, shortlist),
                    status="pending",
                )
                digest_ids.append(digest_id)
            rendered.append((digest.group, len(digest.items), digest_id or -1))

        total = sum(len(d.items) for d in digests)
        sp["output"] = {"rendered": rendered, "total_items": total,
                        "cap": s.digest_total_cap}
        log.info("render.done", run_id=state.get("run_id"), rendered=rendered,
                 total_items=total)
        return {
            "digests": digests,
            "digest_ids": digest_ids,
            "metrics": {"digest_ids": digest_ids, "articles_selected": total},
        }

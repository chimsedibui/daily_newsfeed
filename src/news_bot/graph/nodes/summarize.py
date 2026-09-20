"""Node 5: tom tat tung bai trong shortlist (song song, moi bai 1 lan goi LLM)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from ...config import get_settings
from ...db import repository as repo
from ...logging_setup import get_logger
from ...models import Article, Summary
from ...tracing.store import span
from ...utils import truncate
from ..llm_runner import structured_call
from ..state import GraphState

log = get_logger(__name__)

SYSTEM = """Ban la bien tap vien ban tin noi bo cua mot cong ty cong nghe Viet Nam.
Nhiem vu: doc mot bai bao tieng Viet va viet lai that ngan gon, trung tinh, dung su that.

Quy tac bat buoc:
- CHI dung thong tin co trong bai. Khong suy dien, khong bo sung kien thuc ngoai bai.
- Neu bai khong du thong tin, viet ngan lai; khong bia so lieu.
- Viet bang tieng Viet, giong van bao chi, khong cam than, khong marketing.
- headline: <= 90 ky tu, neu dung su viec, khong giat tit.
- summary: 2-3 cau, tra loi "chuyen gi, voi ai, anh huong the nao".
- bullets: toi da 3 y, moi y <= 120 ky tu, uu tien con so va moc thoi gian.
- importance 1-5: 5 = anh huong rong toi ca nuoc/nganh; 1 = tin ben le.
"""


class ArticleSummary(BaseModel):
    headline: str = Field(description="Tieu de viet lai, toi da 90 ky tu")
    summary: str = Field(description="Tom tat 2-3 cau")
    bullets: list[str] = Field(default_factory=list, description="Toi da 3 gach dau dong")
    topics: list[str] = Field(default_factory=list, description="1-3 the chu de ngan")
    importance: int = Field(ge=1, le=5, description="Muc do quan trong 1-5")


def _prompt_for(article: Article) -> str:
    also = article.raw.get("also_at") or []
    lines = [
        f"Bao: {article.publisher}",
        f"Chuyen muc: {article.category or 'khong ro'}",
        f"Thoi diem dang: {article.published_at.isoformat() if article.published_at else 'khong ro'}",
    ]
    if also:
        lines.append(f"Cac bao khac cung dua tin: {', '.join(also)}")
    lines.append("")
    lines.append(truncate(article.text_for_llm, 8000))
    return "\n".join(lines)


def _summarize_one(article: Article, model: str, store) -> Summary | None:
    parsed = structured_call(
        ArticleSummary,
        system=SYSTEM,
        user=_prompt_for(article),
        model=model,
        purpose="summarize",
        store=store,
        max_tokens=1500,
    )
    if parsed is None:
        return None
    return Summary(
        article_id=article.id,
        url_canonical=article.url_canonical,
        headline=parsed.headline,
        summary=parsed.summary,
        bullets=parsed.bullets[:3],
        topics=parsed.topics[:3],
        importance=parsed.importance,
        model=model,
    )


def summarize_articles(state: GraphState, config: RunnableConfig) -> GraphState:
    store = (config or {}).get("configurable", {}).get("trace_store")
    s = get_settings()
    shortlist = state.get("shortlist") or []

    with span(store, "summarize_articles", attributes={"n": len(shortlist),
                                                       "model": s.summarizer_model}) as sp:
        if s.dry_run:
            summaries = [
                Summary(
                    article_id=a.id, url_canonical=a.url_canonical, headline=a.title,
                    summary=truncate(a.lead or a.title, 240), model="dry-run",
                    importance=3, topics=[a.category or "tin"],
                )
                for a in shortlist
            ]
        else:
            summaries = []
            with ThreadPoolExecutor(max_workers=s.llm_max_concurrency) as pool:
                futures = [
                    pool.submit(_summarize_one, a, s.summarizer_model, store)
                    for a in shortlist
                ]
                for fut in as_completed(futures):
                    result = fut.result()
                    if result is not None:
                        summaries.append(result)

        order = {a.url_canonical: i for i, a in enumerate(shortlist)}
        summaries.sort(key=lambda x: order.get(x.url_canonical, 999))

        if summaries and state.get("run_id"):
            repo.save_summaries(state["run_id"], summaries)

        failed = len(shortlist) - len(summaries)
        sp["output"] = {"summarized": len(summaries), "failed": failed}
        log.info("summarize.done", run_id=state.get("run_id"), **sp["output"])
        return {
            "summaries": summaries,
            "metrics": {"summarized": len(summaries), "summarize_failed": failed},
            "errors": [f"summarize: {failed} bai that bai"] if failed else [],
        }

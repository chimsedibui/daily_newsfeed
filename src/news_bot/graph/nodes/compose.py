"""Node 6: bien tap ban tin - moi nhom mot lan goi LLM rieng.

Vi sao khong gom ca hai nhom vao mot lan goi: prompt cua hai nhom doi lap nhau
("bo qua tin giai tri" vs "day la phan giai tri"). Nhet chung vao mot prompt thi
model phai tu hoa giai, va ket qua la nhom life bi viet bang giong van cua nhom
serious. Hai lan goi o bac gia re nhat van re hon mot lan goi o bac cao hon.
"""
from __future__ import annotations

from datetime import date

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from ... import groups
from ...config import get_settings
from ...logging_setup import get_logger
from ...models import Article, Digest, DigestItem, Summary
from ...tracing.store import span
from ...utils import today_in, truncate
from ..llm_runner import structured_call
from ..state import GraphState
from .rank import group_size

log = get_logger(__name__)

SYSTEM = """Ban la tong bien tap ban tin noi bo hang ngay.
Ban nhan mot danh sach tin da duoc tom tat, moi tin co ma so [id].

Muc tieu cua ban tin nay: {focus}

Nhiem vu:
1. Chon toi da {limit} tin dang doc nhat.
2. Sap xep theo do quan trong giam dan.
3. Viet `headline`: mot dong tieu de cho ca ban tin, <= 80 ky tu.
4. Viet `overview`: 2-3 cau tong quan.

Quy tac:
- CHI duoc dung `id` co trong danh sach. Khong bia id moi.
- Khong lap lai hai tin cung mot su kien.
- Trung tinh, khong danh gia chinh tri, khong suy dien.
- Viet bang tieng Viet, ke ca khi bai goc bang tieng Anh.
"""


class EditorPick(BaseModel):
    id: int = Field(description="id cua tin trong danh sach dau vao")
    reason: str = Field(default="", description="Mot cau ly do chon, ngan")


class EditorOutput(BaseModel):
    headline: str = Field(description="Tieu de ban tin, toi da 80 ky tu")
    overview: str = Field(description="2-3 cau tong quan")
    picks: list[EditorPick] = Field(description="Danh sach tin da chon, theo thu tu")


def _catalog(summaries: list[Summary]) -> str:
    lines = []
    for idx, x in enumerate(summaries):
        lines.append(
            f"[{idx}] ({x.importance}/5) {x.headline}\n"
            f"    {truncate(x.summary, 300)}\n"
            f"    chu de: {', '.join(x.topics) or 'n/a'}"
        )
    return "\n".join(lines)


def _pick_indexes(parsed: EditorOutput | None, summaries: list[Summary],
                  limit: int) -> tuple[list[int], str]:
    """Chon id, chan id ao va id trung. Khong co LLM -> xep theo importance."""
    if parsed is None:
        ranked = sorted(range(len(summaries)), key=lambda i: -summaries[i].importance)
        return ranked[:limit], "fallback"

    seen: set[int] = set()
    chosen: list[int] = []
    for pick in parsed.picks:
        if 0 <= pick.id < len(summaries) and pick.id not in seen:
            seen.add(pick.id)
            chosen.append(pick.id)
    return chosen[:limit], "llm"


def _build_digest(group: groups.Group, summaries: list[Summary],
                  articles: dict[str, Article], digest_date: str,
                  store) -> Digest | None:
    if not summaries:
        return None

    s = get_settings()
    limit = group_size(group)
    parsed = None
    if not s.dry_run:
        parsed = structured_call(
            EditorOutput,
            system=SYSTEM.format(focus=group.editor_focus, limit=limit),
            user=_catalog(summaries),
            model=s.editor_model,
            purpose=f"compose:{group.key}",
            store=store,
            max_tokens=4000,
        )

    chosen, mode = _pick_indexes(parsed, summaries, limit)
    if not chosen:
        return None

    day = date.fromisoformat(digest_date).strftime("%d/%m/%Y")
    if parsed is not None and mode == "llm":
        headline, overview = parsed.headline, parsed.overview
    else:
        headline = f"{group.title} ngày {day}"
        overview = f"{len(chosen)} tin đáng chú ý trong 24 giờ qua."

    items: list[DigestItem] = []
    for rank, idx in enumerate(chosen, start=1):
        x = summaries[idx]
        art = articles.get(x.url_canonical)
        items.append(
            DigestItem(
                rank=rank,
                headline=x.headline,
                summary=x.summary,
                url=art.url_original if art else x.url_canonical,
                publisher=art.publisher if art else "",
                topics=x.topics,
                importance=x.importance,
                also_at=(art.raw.get("also_at") if art else []) or [],
            )
        )

    return Digest(
        group=group.key,
        digest_date=digest_date,
        headline=headline,
        overview=overview,
        items=items,
        stats={"mode": mode, "candidates": len(summaries)},
    )


def compose_digests(state: GraphState, config: RunnableConfig) -> GraphState:
    store = (config or {}).get("configurable", {}).get("trace_store")
    s = get_settings()
    summaries = state.get("summaries") or []
    articles = {a.url_canonical: a for a in (state.get("shortlist") or [])}
    digest_date = state.get("logical_date") or today_in(s.news_timezone).isoformat()

    with span(store, "compose_digests", attributes={"n": len(summaries)}) as sp:
        if not summaries:
            sp["output"] = {"skipped": True}
            return {"digests": [], "skip_reason": "khong co tin nao duoc tom tat"}

        digests: list[Digest] = []
        for group in groups.NEWS_GROUPS:
            in_group = [x for x in summaries
                        if (articles.get(x.url_canonical).group
                            if articles.get(x.url_canonical) else "serious") == group.key]
            digest = _build_digest(group, in_group, articles, digest_date, store)
            if digest is not None:
                digests.append(digest)

        total = sum(len(d.items) for d in digests)
        sp["output"] = {
            "groups": [(d.group, len(d.items), d.stats["mode"]) for d in digests],
            "total_items": total,
        }
        log.info("compose.done", run_id=state.get("run_id"),
                 groups=[(d.group, len(d.items)) for d in digests], total=total)
        return {
            "digests": digests,
            "metrics": {
                "articles_selected": total,
                "compose_modes": {d.group: d.stats["mode"] for d in digests},
            },
            "skip_reason": None if digests else "bien tap khong chon duoc tin nao",
        }

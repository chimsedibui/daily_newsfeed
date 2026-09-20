"""Node 6: bien tap ban tin - chon tin, sap thu tu, viet doan mo dau."""
from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from ...config import get_settings
from ...logging_setup import get_logger
from ...models import Digest, DigestItem
from ...tracing.store import span
from ...utils import today_in, truncate
from ..llm_runner import structured_call
from ..state import GraphState

log = get_logger(__name__)

SYSTEM = """Ban la tong bien tap ban tin noi bo hang ngay cua mot cong ty cong nghe.
Ban nhan mot danh sach tin da duoc tom tat, moi tin co ma so [id].

Nhiem vu:
1. Chon toi da {limit} tin dang doc nhat cho mot doi ngu ky su va kinh doanh o Viet Nam.
2. Sap xep theo do quan trong giam dan.
3. Viet `headline`: mot dong tieu de cho ca ban tin, <= 80 ky tu, neu chu de noi bat nhat.
4. Viet `overview`: 2-3 cau tong quan ve buc tranh tin tuc hom nay.

Quy tac:
- CHI duoc dung `id` co trong danh sach. Khong bia id moi.
- Khong lap lai hai tin cung mot su kien.
- Uu tien: chinh sach/kinh te vi mo > cong nghe & AI > doanh nghiep > con lai.
- Trung tinh, khong danh gia chinh tri, khong suy dien.
- Viet bang tieng Viet.
"""


class EditorPick(BaseModel):
    id: int = Field(description="id cua tin trong danh sach dau vao")
    reason: str = Field(default="", description="Mot cau ly do chon, ngan")


class EditorOutput(BaseModel):
    headline: str = Field(description="Tieu de ban tin, toi da 80 ky tu")
    overview: str = Field(description="2-3 cau tong quan")
    picks: list[EditorPick] = Field(description="Danh sach tin da chon, theo thu tu")


def _catalog(summaries) -> str:
    lines = []
    for idx, x in enumerate(summaries):
        lines.append(
            f"[{idx}] ({x.importance}/5) {x.headline}\n"
            f"    {truncate(x.summary, 300)}\n"
            f"    chu de: {', '.join(x.topics) or 'n/a'}"
        )
    return "\n".join(lines)


def _fallback(summaries, limit: int, digest_date: str) -> Digest:
    """Khong co LLM (dry-run) hoac LLM loi -> van ra duoc ban tin theo importance."""
    ranked = sorted(summaries, key=lambda x: -x.importance)[:limit]
    return Digest(
        digest_date=digest_date,
        headline=f"Ban tin ngay {digest_date}",
        overview=f"{len(ranked)} tin dang chu y trong 24 gio qua.",
        items=[],
        stats={"mode": "fallback"},
    )


def compose_digest(state: GraphState, config: RunnableConfig) -> GraphState:
    store = (config or {}).get("configurable", {}).get("trace_store")
    s = get_settings()
    summaries = state.get("summaries") or []
    shortlist = {a.url_canonical: a for a in (state.get("shortlist") or [])}
    digest_date = state.get("logical_date") or today_in(s.news_timezone).isoformat()

    with span(store, "compose_digest", attributes={"n": len(summaries),
                                                   "model": s.editor_model}) as sp:
        if not summaries:
            sp["output"] = {"skipped": True}
            return {"digest": None, "skip_reason": "khong co tin nao duoc tom tat"}

        parsed = None
        if not s.dry_run:
            parsed = structured_call(
                EditorOutput,
                system=SYSTEM.format(limit=s.digest_size),
                user=_catalog(summaries),
                model=s.editor_model,
                purpose="compose",
                store=store,
                max_tokens=4000,
            )

        if parsed is None:
            base = _fallback(summaries, s.digest_size, digest_date)
            chosen_idx = [
                summaries.index(x)
                for x in sorted(summaries, key=lambda y: -y.importance)[: s.digest_size]
            ]
            headline, overview = base.headline, base.overview
            mode = "fallback"
        else:
            # Chan id ao / trung lap do model tra ve
            seen: set[int] = set()
            chosen_idx = []
            for pick in parsed.picks:
                if 0 <= pick.id < len(summaries) and pick.id not in seen:
                    seen.add(pick.id)
                    chosen_idx.append(pick.id)
            chosen_idx = chosen_idx[: s.digest_size]
            headline, overview = parsed.headline, parsed.overview
            mode = "llm"

        items: list[DigestItem] = []
        for rank, idx in enumerate(chosen_idx, start=1):
            x = summaries[idx]
            art = shortlist.get(x.url_canonical)
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

        digest = Digest(
            digest_date=digest_date,
            headline=headline,
            overview=overview,
            items=items,
            stats={"mode": mode, "candidates": len(summaries)},
        )
        sp["output"] = {"mode": mode, "items": len(items), "headline": headline}
        log.info("compose.done", run_id=state.get("run_id"), mode=mode, items=len(items))
        return {
            "digest": digest,
            "metrics": {"articles_selected": len(items), "compose_mode": mode},
            "skip_reason": None if items else "bien tap khong chon duoc tin nao",
        }

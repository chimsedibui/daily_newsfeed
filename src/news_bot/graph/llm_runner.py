"""Goi LLM co structured output, thu lan luot theo chuoi provider.

`include_raw=True` la mau chot: no tra ve ca AIMessage goc, nho do van doc duoc
usage_metadata de tinh token/chi phi - dieu ma `with_structured_output` tran
khong cho.

Moi lan goi (ke ca lan that bai roi chuyen provider) deu duoc ghi mot dong
llm_call, nen `SELECT provider, count(*) FROM llm_call` la du de biet hom nay
ai phuc vu va ai gay.
"""
from __future__ import annotations

import time
from typing import TypeVar

from pydantic import BaseModel

from .. import llm
from ..logging_setup import get_logger
from ..tracing.store import TraceStore, span
from ..utils import truncate

log = get_logger(__name__)
T = TypeVar("T", bound=BaseModel)

_EMPTY_USAGE = {
    "input_tokens": 0, "output_tokens": 0,
    "cache_read_tokens": 0, "reasoning_tokens": 0,
}


def structured_call(
    schema: type[T],
    system: str,
    user: str,
    *,
    purpose: str,
    store: TraceStore | None,
    max_tokens: int = 4096,
    thinking_budget: int | None = None,
) -> T | None:
    """Tra ve instance cua `schema`, hoac None neu khong provider nao lam duoc.

    `purpose` la "summarize" hoac "compose..." - dung de chon model cho tung
    provider. `thinking_budget=0` tat suy luan (viec tom tat khong can no).
    """
    s = llm.get_settings()
    if thinking_budget is None:
        thinking_budget = s.vertex_thinking_budget

    messages = [("system", system), ("human", user)]
    kind = "compose" if purpose.startswith("compose") else purpose

    chain = llm.provider_chain()
    if not chain:
        log.error("llm.no_provider", purpose=purpose)
        return None

    for provider in chain:
        model = llm.model_for(provider, kind)
        if not model:
            continue

        started = time.perf_counter()
        error: str | None = None
        parsed: T | None = None
        usage = dict(_EMPTY_USAGE)
        raw_text = ""

        try:
            chat = llm.get_chat_model(
                provider, model, max_tokens, thinking_budget
            ).with_structured_output(schema, include_raw=True)
            result = chat.invoke(messages)
            raw = result.get("raw")
            parsed = result.get("parsed")
            if result.get("parsing_error"):
                error = f"parsing_error: {result['parsing_error']!r}"
            if raw is not None:
                usage = llm.extract_usage(raw)
                raw_text = str(parsed) if parsed is not None else str(raw.content)
        except Exception as exc:
            error = repr(exc)
        finally:
            if store is not None:
                store.record_llm(
                    provider=provider,
                    model=model,
                    purpose=purpose,
                    usage=usage,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    cost_usd=llm.estimate_cost_usd(model, **usage),
                    prompt_preview=truncate(user, 1000),
                    output_preview=truncate(raw_text, 1000),
                    error=error,
                )

        if parsed is not None:
            if provider != chain[0]:
                log.info("llm.served_by_fallback", provider=provider, model=model,
                         purpose=purpose)
            return parsed

        log.warning("llm.failed", provider=provider, model=model,
                    purpose=purpose, error=(error or "parsed=None")[:200])
        # Loi he thong -> bo provider nay cho phan con lai cua tien trinh.
        # Loi le te (mot bai khong parse duoc) thi khong, vi bai sau co the on.
        if error:
            llm.mark_down(provider, error)

    return None


__all__ = ["span", "structured_call"]
